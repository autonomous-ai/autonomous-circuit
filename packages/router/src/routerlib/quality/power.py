"""Power delivery: how many milliohms, and is it a tree or a chain?

``verifylib.netclass`` already asks whether a rail's *narrowest* trace can carry
its current — an IPC-2221B width check, per net, on the thinnest segment. That
is necessary and it is not the same question. A rail can be wide enough
everywhere and still droop, because what a load sees is the resistance of the
whole path back to the source, and a router that reaches the last load through
four other pads in series builds four times the resistance of one that fans out.

So this metric turns the routed copper into a resistor network and measures it:

``path_resistance``
    Dijkstra over the net's own copper, edge weight
    ``sheet_resistance * length / width`` with 1oz copper at 0.495 milliohm per
    square, plus ``rho * thickness / (pi * drill * plating)`` through every via
    barrel. The reported number is the **least-resistance path**, which is an
    upper bound on the true effective resistance — parallel copper only ever
    helps — so a bad number here is real and a good one is honest.
``daisy_depth``
    how many *other load pads* sit on the path from the source. A star is 0 for
    everyone. A daisy chain of five is 0,1,2,3. The failure it predicts is not
    droop but coupling: every load downstream of another shares its return and
    its switching noise, and cutting the chain anywhere drops everything after
    it.

**The source.** Nothing in a ``RoutingProblem`` says which pad is the
regulator — it carries copper and nets, not part numbers. So the source is
chosen geometrically and deterministically: the pad with the lowest
*eccentricity*, i.e. the one whose worst path to any other pad is smallest.
That is the best possible source, so every resistance reported is the
**smallest** the layout admits. Pass ``sources={net_id: pad_id}`` to measure the
real one. Above :data:`ECCENTRICITY_PAD_LIMIT` pads the all-pairs search is
skipped and the pad nearest the net's pad centroid is used instead, which is
named in the result rather than hidden.

**Volts need amps, and amps are not in the problem.** ``circuit.json`` knows
what is attached; a routing instance does not. So resistance is always
reported and voltage drop only when the caller supplies
``currents_ma``. The drop is ``I_total * R_worst`` — the whole rail current
through the longest path — which is deliberately the worst case and is labelled
as one.

**No pass mark.** IPC publishes no allowable IR drop; the real limit is the
load's own minimum supply voltage, which lives in a datasheet the router cannot
read. Milliohms and millivolts are reported raw and boards ranked against each
other.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from routerlib.geometry import capsule_gap, disc_capsule, pad_capsule
from routerlib.model import BOTTOM, TOP, Pad, RoutingProblem, RoutingSolution

from routerlib.quality.common import (
    GroundField,
    trace_ohms,
    via_barrel_ohms,
)

#: Above this many pads the source is picked by centroid rather than by an
#: all-pairs eccentricity search. Ground on a real board has forty.
ECCENTRICITY_PAD_LIMIT = 20

#: How close a trace vertex has to be to a pad or a via to count as landing on
#: it. Zero-length: copper either touches or it does not, the same rule
#: ``routerlib.connectivity.TOUCH_TOL_MM`` uses.
TOUCH_TOL_MM = 1e-9


@dataclass(frozen=True)
class NetPower:
    net: str
    net_class: str
    source_pad: str | None
    source_rule: str
    pad_count: int
    reached_pads: int
    unreachable_pads: int
    worst_path_mohm: float | None
    mean_path_mohm: float | None
    #: Worst path between *any* two pads. Source-independent, so two routers
    #: are comparable even when their best source differs.
    diameter_mohm: float | None
    max_daisy_depth: int | None
    chained_pad_fraction: float | None
    current_ma: float | None
    worst_drop_mv: float | None

    def as_dict(self) -> dict:
        return {
            "net": self.net,
            "netClass": self.net_class,
            "sourcePad": self.source_pad,
            "sourceRule": self.source_rule,
            "padCount": self.pad_count,
            "reachedPads": self.reached_pads,
            "unreachablePads": self.unreachable_pads,
            "worstPathMohm": _r(self.worst_path_mohm, 3),
            "meanPathMohm": _r(self.mean_path_mohm, 3),
            "diameterMohm": _r(self.diameter_mohm, 3),
            "maxDaisyDepth": self.max_daisy_depth,
            "chainedPadFraction": _r(self.chained_pad_fraction, 4),
            "currentMa": _r(self.current_ma, 2),
            "worstDropMv": _r(self.worst_drop_mv, 3),
        }


@dataclass(frozen=True)
class PowerResult:
    net_count: int
    worst_path_mohm: float | None
    worst_net: str | None
    max_daisy_depth: int | None
    chained_pad_fraction: float | None
    worst_drop_mv: float | None
    unreachable_pads: int
    nets: tuple[NetPower, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "netCount": self.net_count,
            "worstPathMohm": _r(self.worst_path_mohm, 3),
            "worstNet": self.worst_net,
            "maxDaisyDepth": self.max_daisy_depth,
            "chainedPadFraction": _r(self.chained_pad_fraction, 4),
            "worstDropMv": _r(self.worst_drop_mv, 3),
            "unreachablePads": self.unreachable_pads,
            "nets": [n.as_dict() for n in self.nets],
        }


def _r(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


class _Union:
    __slots__ = ("parent",)

    def __init__(self) -> None:
        self.parent: dict = {}

    def find(self, key):
        self.parent.setdefault(key, key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != root:
            self.parent[key], key = root, self.parent[key]
        return root

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            lo, hi = sorted((ra, rb), key=repr)
            self.parent[hi] = lo


def _drill_of(problem: RoutingProblem, pad: Pad) -> float:
    if pad.drill_id:
        for drill in problem.drills:
            if drill.id == pad.drill_id:
                return min(drill.width_mm, drill.height_mm)
    return problem.rules.via_drill_mm


def _build_graph(problem: RoutingProblem, solution: RoutingSolution, net_id: str):
    """``(union-find, weighted edges, pad -> node key)`` for one net's copper."""
    uf = _Union()
    edges: list[tuple[object, object, float]] = []
    thickness = float(problem.board.thickness_mm)

    pads = [p for p in problem.pads if p.net == net_id]
    pad_key: dict[str, object] = {}
    pad_shapes: list[tuple[Pad, object]] = []
    for pad in pads:
        capsule = pad_capsule(pad)
        pad_shapes.append((pad, capsule))
        pad_key[pad.id] = ("pad", pad.id, pad.layers[0])
        for layer in pad.layers[1:]:
            # A plated hole is one barrel of copper between two lands.
            edges.append(
                (
                    ("pad", pad.id, pad.layers[0]),
                    ("pad", pad.id, layer),
                    via_barrel_ohms(_drill_of(problem, pad), thickness),
                )
            )

    traces = sorted(
        [t for t in list(problem.existing_traces) + list(solution.traces)
         if t.net == net_id],
        key=lambda t: (t.layer, t.id),
    )
    vias = sorted(
        [v for v in list(problem.existing_vias) + list(solution.vias)
         if v.net == net_id],
        key=lambda v: (v.center.x, v.center.y, v.id),
    )
    for via in vias:
        edges.append(
            (("via", via.id, TOP), ("via", via.id, BOTTOM),
             via_barrel_ohms(via.drill_mm, thickness))
        )

    # Vertices, and the resistive edge along each segment.
    vertex_at: dict[tuple[str, float, float], object] = {}
    for ti, trace in enumerate(traces):
        for i, point in enumerate(trace.points):
            key = ("v", ti, i)
            uf.find(key)
            coincident = vertex_at.setdefault((trace.layer, point.x, point.y), key)
            if coincident is not key:
                uf.union(coincident, key)
        for i, (a, b) in enumerate(trace.segments):
            length = a.distance_to(b)
            if length <= 0:
                uf.union(("v", ti, i), ("v", ti, i + 1))
                continue
            edges.append(
                (("v", ti, i), ("v", ti, i + 1), trace_ohms(length, trace.width_mm))
            )

    # Zero-resistance contacts: a vertex landing on a pad or a via.
    for (layer, x, y), key in sorted(vertex_at.items(), key=lambda kv: repr(kv[0])):
        probe = disc_capsule(x, y, 0.0)
        for pad, capsule in pad_shapes:
            if layer not in pad.layers:
                continue
            if capsule_gap(capsule, probe) <= TOUCH_TOL_MM:
                uf.union(key, ("pad", pad.id, layer))
        for via in vias:
            if math.hypot(via.center.x - x, via.center.y - y) <= via.pad_mm / 2.0:
                uf.union(key, ("via", via.id, layer))
    # A via drilled inside a pad shorts to it.
    for via in vias:
        probe = disc_capsule(via.center.x, via.center.y, via.pad_mm)
        for pad, capsule in pad_shapes:
            if capsule_gap(capsule, probe) <= TOUCH_TOL_MM:
                for layer in pad.layers:
                    uf.union(("via", via.id, layer), ("pad", pad.id, layer))

    # A pour is a sheet of copper: everything sitting in it is one node. That
    # understates plane resistance and it is the right call — the alternative
    # is a mesh model this package has no business owning.
    for plane in problem.planes:
        if plane.net != net_id or len(plane.outline) < 3:
            continue
        from routerlib.geometry import PolygonIndex

        outline = PolygonIndex(plane.outline)
        holes = [PolygonIndex(r) for r in plane.holes if len(r) >= 3]
        node = ("plane", plane.id)
        uf.find(node)
        for (layer, x, y), key in sorted(vertex_at.items(), key=lambda kv: repr(kv[0])):
            if layer != plane.layer:
                continue
            if outline.contains(x, y) and not any(h.contains(x, y) for h in holes):
                uf.union(node, key)
        for pad, _ in pad_shapes:
            if plane.layer not in pad.layers:
                continue
            if outline.contains(pad.center.x, pad.center.y) and not any(
                h.contains(pad.center.x, pad.center.y) for h in holes
            ):
                uf.union(node, ("pad", pad.id, plane.layer))
        for via in vias:
            if outline.contains(via.center.x, via.center.y) and not any(
                h.contains(via.center.x, via.center.y) for h in holes
            ):
                uf.union(node, ("via", via.id, plane.layer))

    for a, b, _ in edges:
        uf.find(a)
        uf.find(b)
    return uf, edges, pad_key, pads


def _adjacency(uf: _Union, edges) -> dict:
    graph: dict[object, list[tuple[object, float]]] = {}
    for a, b, ohms in edges:
        ra, rb = uf.find(a), uf.find(b)
        if ra == rb:
            continue
        graph.setdefault(ra, []).append((rb, ohms))
        graph.setdefault(rb, []).append((ra, ohms))
    for node in graph:
        graph[node].sort(key=lambda item: (item[1], repr(item[0])))
    return graph


def _dijkstra(graph: dict, start) -> tuple[dict, dict]:
    dist = {start: 0.0}
    prev: dict = {}
    queue = [(0.0, repr(start), start)]
    seen: set = set()
    while queue:
        d, _, node = heapq.heappop(queue)
        if node in seen:
            continue
        seen.add(node)
        for other, ohms in graph.get(node, ()):
            nd = d + ohms
            if nd < dist.get(other, math.inf) - 1e-15:
                dist[other] = nd
                prev[other] = node
                heapq.heappush(queue, (nd, repr(other), other))
    return dist, prev


def _measure_net(
    problem: RoutingProblem,
    solution: RoutingSolution,
    net,
    *,
    source_pad: str | None,
    current_ma: float | None,
) -> NetPower:
    uf, edges, pad_key, pads = _build_graph(problem, solution, net.id)
    graph = _adjacency(uf, edges)
    roots = {p.id: uf.find(pad_key[p.id]) for p in pads}
    if len(pads) < 2:
        return NetPower(
            net=net.id, net_class=net.net_class, source_pad=None,
            source_rule="single-pad", pad_count=len(pads), reached_pads=len(pads),
            unreachable_pads=0, worst_path_mohm=None, mean_path_mohm=None,
            diameter_mohm=None, max_daisy_depth=None, chained_pad_fraction=None,
            current_ma=current_ma, worst_drop_mv=None,
        )

    root_to_pads: dict[object, list[str]] = {}
    for pad_id, root in sorted(roots.items()):
        root_to_pads.setdefault(root, []).append(pad_id)

    rule = "given"
    if source_pad is None or source_pad not in roots:
        if len(pads) <= ECCENTRICITY_PAD_LIMIT:
            rule = "min-eccentricity"
            best: tuple[float, str] | None = None
            for pad_id in sorted(roots):
                dist, _ = _dijkstra(graph, roots[pad_id])
                reach = [dist[r] for r in root_to_pads if r in dist]
                if not reach:
                    continue
                ecc = max(reach)
                penalty = len(root_to_pads) - len(reach)
                score = (penalty, ecc)
                if best is None or score < best[0]:
                    best = (score, pad_id)
            source_pad = best[1] if best else sorted(roots)[0]
        else:
            rule = "centroid"
            cx = sum(p.center.x for p in pads) / len(pads)
            cy = sum(p.center.y for p in pads) / len(pads)
            source_pad = min(
                pads, key=lambda p: (math.hypot(p.center.x - cx, p.center.y - cy), p.id)
            ).id

    dist, prev = _dijkstra(graph, roots[source_pad])
    source_root = roots[source_pad]
    reached: dict[str, float] = {}
    for pad_id, root in sorted(roots.items()):
        if pad_id == source_pad:
            continue
        if root in dist:
            reached[pad_id] = dist[root]
    unreachable = len(pads) - 1 - len(reached)

    depths: list[int] = []
    pad_roots = set(root_to_pads)
    for pad_id in sorted(reached):
        node = roots[pad_id]
        depth = 0
        guard = 0
        while node in prev and guard < 100000:
            node = prev[node]
            guard += 1
            if node == source_root:
                break
            if node in pad_roots:
                depth += 1
        depths.append(depth)

    worst = max(reached.values(), default=None)
    mean = (sum(reached.values()) / len(reached)) if reached else None

    diameter = None
    if len(root_to_pads) <= ECCENTRICITY_PAD_LIMIT:
        far = 0.0
        for root in sorted(root_to_pads, key=repr):
            d, _ = _dijkstra(graph, root)
            for other in root_to_pads:
                if other in d:
                    far = max(far, d[other])
        diameter = far

    drop = None
    if current_ma is not None and worst is not None:
        drop = (current_ma / 1000.0) * worst * 1000.0  # mA -> A, ohm -> mV

    return NetPower(
        net=net.id,
        net_class=net.net_class,
        source_pad=source_pad,
        source_rule=rule,
        pad_count=len(pads),
        reached_pads=len(reached) + 1,
        unreachable_pads=unreachable,
        worst_path_mohm=None if worst is None else worst * 1000.0,
        mean_path_mohm=None if mean is None else mean * 1000.0,
        diameter_mohm=None if diameter is None else diameter * 1000.0,
        max_daisy_depth=max(depths, default=None),
        chained_pad_fraction=(
            sum(1 for d in depths if d > 0) / len(depths) if depths else None
        ),
        current_ma=current_ma,
        worst_drop_mv=drop,
    )


def measure(
    problem: RoutingProblem,
    solution: RoutingSolution,
    *,
    currents_ma: dict[str, float] | None = None,
    sources: dict[str, str] | None = None,
    include_ground: bool = True,
    ground: GroundField | None = None,
) -> PowerResult:
    currents_ma = currents_ma or {}
    sources = sources or {}
    wanted = ("power", "ground") if include_ground else ("power",)
    nets = [n for n in problem.nets if n.net_class in wanted and n.routable]

    rows = [
        _measure_net(
            problem,
            solution,
            net,
            source_pad=sources.get(net.id),
            current_ma=currents_ma.get(net.id),
        )
        for net in sorted(nets, key=lambda n: n.id)
    ]
    measured = [r for r in rows if r.worst_path_mohm is not None]
    worst = max(measured, key=lambda r: (r.worst_path_mohm, r.net), default=None)
    depths = [r.max_daisy_depth for r in rows if r.max_daisy_depth is not None]
    chained = [
        (r.chained_pad_fraction, r.pad_count)
        for r in rows if r.chained_pad_fraction is not None
    ]
    drops = [r.worst_drop_mv for r in rows if r.worst_drop_mv is not None]
    return PowerResult(
        net_count=len(rows),
        worst_path_mohm=worst.worst_path_mohm if worst else None,
        worst_net=worst.net if worst else None,
        max_daisy_depth=max(depths) if depths else None,
        chained_pad_fraction=(
            sum(f * n for f, n in chained) / sum(n for _, n in chained)
            if chained else None
        ),
        worst_drop_mv=max(drops) if drops else None,
        unreachable_pads=sum(r.unreachable_pads for r in rows),
        nets=tuple(rows),
    )


__all__ = ["ECCENTRICITY_PAD_LIMIT", "NetPower", "PowerResult", "measure"]
