"""Tangle: how far the copper went, and how often it had to cross itself.

The EE's second finding was one word — *"messy"* — and this is the metric that
encodes it. A board looks tangled when nets take long ways round and when
copper crosses copper. Both are countable.

``detour_ratio``
    routed length divided by the **Euclidean MST** over the net's pad centres —
    the same quantity ``routerlib.bench.features_of`` already computes as
    ``mst_length_mm``, so the reference is one the package owns rather than a
    new one. A net routed as a direct spanning tree sits at 1.0; how far above
    it sits is the detour. Half-perimeter wirelength is reported alongside it,
    because HPWL is the standard estimator and the two disagree in a way worth
    seeing on multi-pin nets.

    **Neither is a hard floor, and pretending otherwise would be the mistake.**
    A Steiner tree can beat the MST by up to 13.4%, our copper runs diagonally
    where HPWL assumes rectilinear, and a trace starts at a pad's *edge* while
    both bounds are measured pad-centre to pad-centre. On a short net between
    two large pads the ratio can therefore land below 1.0 — ``matrix-status-led``
    does, at 0.79. So this is a **comparator between boards**, not a distance
    from optimal, and it is reported as one.

``crossings``
    pairs of segments from different nets whose 2D projections intersect. On
    two layers a crossing is only legal if the two are on opposite sides, so
    every one of these is a crossing *resolved by a via* — the topological
    price the layout paid. ``self_crossings`` counts the same thing within one
    net, which is pure waste: a net that crosses itself went somewhere it did
    not need to go.

``bends``
    corners. A net routed as one straight run has none; the same net wandering
    through a channel has twenty. Cheap to compute, and it is a large part of
    what "tangled" looks like to a human.

**Only connected nets are scored.** An unrouted net has no length and would
report a detour ratio of zero, which reads as *perfect*. Nets that are not
connected are counted separately and excluded, so a router cannot improve this
number by giving up — and two routers with different completeness must be
compared on the intersection, which the calibration table does explicitly.

**No pass mark.** There is no published number for an acceptable detour ratio.
1.0 is unreachable in practice and 3.0 is obviously bad; everything between is
a ranking, not a verdict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from routerlib.connectivity import analyse
from routerlib.geometry import GridIndex, segment_capsule, segments_cross
from routerlib.model import RoutingProblem, RoutingSolution


@dataclass(frozen=True)
class NetDetour:
    net: str
    net_class: str
    routed_mm: float
    hpwl_mm: float
    mst_mm: float
    #: routed / MST — the headline.
    detour_ratio: float | None
    #: routed / HPWL — the same question against the other standard estimator.
    hpwl_ratio: float | None
    bends: int
    vias: int

    def as_dict(self) -> dict:
        return {
            "net": self.net,
            "netClass": self.net_class,
            "routedMm": round(self.routed_mm, 4),
            "hpwlMm": round(self.hpwl_mm, 4),
            "mstMm": round(self.mst_mm, 4),
            "detourRatio": _r(self.detour_ratio, 4),
            "hpwlRatio": _r(self.hpwl_ratio, 4),
            "bends": self.bends,
            "vias": self.vias,
        }


@dataclass(frozen=True)
class DetourResult:
    #: Total routed copper over total MST, across connected nets only.
    detour_ratio: float | None
    #: The same, against half-perimeter wirelength.
    hpwl_ratio: float | None
    mean_net_detour_ratio: float | None
    worst_net: str | None
    worst_detour_ratio: float | None
    crossings: int
    self_crossings: int
    bends: int
    bends_per_connected_net: float | None
    scored_nets: int
    skipped_unconnected_nets: int
    nets: tuple[NetDetour, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "detourRatio": _r(self.detour_ratio, 4),
            "hpwlRatio": _r(self.hpwl_ratio, 4),
            "meanNetDetourRatio": _r(self.mean_net_detour_ratio, 4),
            "worstNet": self.worst_net,
            "worstDetourRatio": _r(self.worst_detour_ratio, 4),
            "crossings": self.crossings,
            "selfCrossings": self.self_crossings,
            "bends": self.bends,
            "bendsPerConnectedNet": _r(self.bends_per_connected_net, 3),
            "scoredNets": self.scored_nets,
            "skippedUnconnectedNets": self.skipped_unconnected_nets,
            "nets": [n.as_dict() for n in self.nets],
        }


def _r(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def hpwl_mm(pads) -> float:
    """Half the perimeter of the pad bounding box — the standard lower bound."""
    if len(pads) < 2:
        return 0.0
    xs = [p.center.x for p in pads]
    ys = [p.center.y for p in pads]
    return (max(xs) - min(xs)) + (max(ys) - min(ys))


def mst_mm(pads) -> float:
    """Euclidean MST over the pad centres. Prim's, O(n^2), deterministic."""
    n = len(pads)
    if n < 2:
        return 0.0
    inside = [0]
    outside = list(range(1, n))
    total = 0.0
    while outside:
        best = None
        for i in inside:
            for j in outside:
                d = math.hypot(
                    pads[i].center.x - pads[j].center.x,
                    pads[i].center.y - pads[j].center.y,
                )
                if best is None or d < best[0]:
                    best = (d, j)
        total += best[0]
        inside.append(best[1])
        outside.remove(best[1])
    return total


def _bends(trace) -> int:
    """Corners in a polyline: direction changes, collinear points ignored."""
    points = trace.points
    count = 0
    for i in range(1, len(points) - 1):
        ax = points[i].x - points[i - 1].x
        ay = points[i].y - points[i - 1].y
        bx = points[i + 1].x - points[i].x
        by = points[i + 1].y - points[i].y
        if abs(ax * by - ay * bx) > 1e-9 or (ax * bx + ay * by) < 0:
            count += 1
    return count


def count_crossings(solution: RoutingSolution) -> tuple[int, int]:
    """``(crossings between nets, crossings within one net)``.

    A crossing is two segments on **different layers** whose 2D projections
    intersect — on the same layer that would be a short and
    ``routerlib.drc`` already owns it.
    """
    segments = []
    for trace in sorted(solution.traces, key=lambda t: (t.net, t.layer, t.id)):
        for index, (a, b) in enumerate(trace.segments):
            if a.distance_to(b) <= 0:
                continue
            segments.append((trace.net, trace.layer, a.x, a.y, b.x, b.y))

    grid = GridIndex(cell_mm=4.0)
    for i, (_, _, x0, y0, x1, y1) in enumerate(segments):
        grid.insert(segment_capsule(x0, y0, x1, y1, 0.0), i)

    between = 0
    within = 0
    for i, (net, layer, x0, y0, x1, y1) in enumerate(segments):
        probe = segment_capsule(x0, y0, x1, y1, 0.0)
        for _, j in grid.query(probe, margin=0.0):
            if j <= i:
                continue
            onet, olayer, ox0, oy0, ox1, oy1 = segments[j]
            if layer == olayer:
                continue
            if not segments_cross(x0, y0, x1, y1, ox0, oy0, ox1, oy1):
                continue
            if net == onet:
                within += 1
            else:
                between += 1
    return between, within


def measure(problem: RoutingProblem, solution: RoutingSolution) -> DetourResult:
    connectivity = analyse(problem, solution)
    connected = set(connectivity.connected_nets)

    routed: dict[str, float] = {}
    bends: dict[str, int] = {}
    for trace in solution.traces:
        routed[trace.net] = routed.get(trace.net, 0.0) + trace.length_mm
        bends[trace.net] = bends.get(trace.net, 0) + _bends(trace)
    vias: dict[str, int] = {}
    for via in solution.vias:
        vias[via.net] = vias.get(via.net, 0) + 1

    rows: list[NetDetour] = []
    skipped = 0
    for net in problem.routable_nets:
        length = routed.get(net.id, 0.0)
        if net.id not in connected or length <= 0:
            skipped += 1
            continue
        pads = problem.pads_of(net.id)
        half_perimeter = hpwl_mm(pads)
        spanning = mst_mm(pads)
        rows.append(
            NetDetour(
                net=net.id,
                net_class=net.net_class,
                routed_mm=length,
                hpwl_mm=half_perimeter,
                mst_mm=spanning,
                detour_ratio=(length / spanning) if spanning > 0 else None,
                hpwl_ratio=(length / half_perimeter) if half_perimeter > 0 else None,
                bends=bends.get(net.id, 0),
                vias=vias.get(net.id, 0),
            )
        )
    rows.sort(key=lambda r: r.net)

    ratios = [r for r in rows if r.detour_ratio is not None]
    total_routed = sum(r.routed_mm for r in ratios)
    total_mst = sum(r.mst_mm for r in ratios)
    total_hpwl = sum(r.hpwl_mm for r in rows if r.hpwl_ratio is not None)
    routed_hpwl = sum(r.routed_mm for r in rows if r.hpwl_ratio is not None)
    worst = max(ratios, key=lambda r: (r.detour_ratio, r.net), default=None)
    between, within = count_crossings(solution)
    return DetourResult(
        detour_ratio=(total_routed / total_mst) if total_mst > 0 else None,
        hpwl_ratio=(routed_hpwl / total_hpwl) if total_hpwl > 0 else None,
        mean_net_detour_ratio=(
            sum(r.detour_ratio for r in ratios) / len(ratios) if ratios else None
        ),
        worst_net=worst.net if worst else None,
        worst_detour_ratio=worst.detour_ratio if worst else None,
        crossings=between,
        self_crossings=within,
        bends=sum(r.bends for r in rows),
        bends_per_connected_net=(
            sum(r.bends for r in rows) / len(rows) if rows else None
        ),
        scored_nets=len(rows),
        skipped_unconnected_nets=skipped,
        nets=tuple(rows),
    )


__all__ = ["DetourResult", "NetDetour", "count_crossings", "hpwl_mm", "measure", "mst_mm"]
