"""The floor: pattern routing, no search, no rip-up.

This is deliberately the dumbest thing that works. It exists for two reasons.
First, every later algorithm needs a number to beat, and a floor that was
tuned is not a floor. Second, the harness has to be proven on real output
before anyone trusts a score it prints, and only a real router produces real
output.

How it works, in full:

1. Nets are taken in the problem's own order — ground, then power, then
   differential pairs, then signals, alphabetically inside each class.
2. A net with a poured plane on its layer is not routed at all. Each of its
   pads gets one via into the plane. That is the entire ground strategy, and it
   is the thing the router we ship cannot express: we added a plane with 73
   vias and its trace count did not change by a single connection, because it
   saw 73 obstacles.
3. Every other net becomes a Euclidean minimum spanning tree over its pads, and
   each tree edge is routed independently.
4. An edge is tried against a fixed list of patterns: straight, L both ways,
   Z at three offsets, first on the top layer, then on the bottom with a fanout
   via at each end. The first pattern that is legal wins.
5. **If no pattern is legal, the edge is left unrouted.** It never places copper
   it cannot defend. A pattern router with no rip-up will fail on congested
   boards, and the honest output of that failure is an incomplete board, not a
   complete illegal one.

So its scores should read: legality near perfect, completeness poor on anything
dense. That shape is the point — it is the opposite failure to the one we ship,
and the two together bracket what a real router has to do.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from routerlib.geometry import PolygonIndex
from routerlib.model import (
    BOTTOM,
    TOP,
    Budget,
    BudgetMeter,
    Pad,
    Plane,
    Point,
    RoutingProblem,
    RoutingSolution,
    Trace,
    Via,
)
from routerlib.workspace import Workspace

#: Fractions along the bounding box where a Z pattern turns.
_Z_OFFSETS = (0.5, 0.25, 0.75)

#: How many access points to try per pad when a fanout via is needed. The
#: first is always the direction of travel; then the four axes.
_MAX_ACCESS = 5


@dataclass(frozen=True)
class _Access:
    """A way of getting a net onto ``layer`` at ``point``, and what it costs."""

    point: Point
    stubs: tuple[tuple[str, tuple[Point, ...]], ...] = ()
    vias: tuple[Point, ...] = ()


class PatternRouter:
    """Straight/L/Z pattern routing with fanout vias. No rip-up, no search."""

    name = "baseline-pattern"

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        import time

        started = time.perf_counter()
        meter = budget.meter()
        ws = Workspace(problem)
        traces: list[Trace] = []
        vias: list[Via] = []
        unrouted: list[str] = []
        notes: list[str] = []
        via_seq = 0
        planes_by_net: dict[str, list[Plane]] = {}
        for plane in problem.planes:
            planes_by_net.setdefault(plane.net, []).append(plane)

        for net in problem.nets:
            if not net.routable:
                continue
            pads = sorted(
                problem.pads_of(net.id), key=lambda p: (p.center.x, p.center.y, p.id)
            )
            width = max(net.min_width_mm, problem.rules.min_trace_mm)
            failures = 0

            if net.id in planes_by_net:
                placed, seq = self._stitch_to_plane(
                    ws, problem, net.id, pads, planes_by_net[net.id],
                    width, via_seq, meter, traces, vias
                )
                via_seq = seq
                if placed < len(pads):
                    unrouted.append(net.id)
                continue

            for pad_a, pad_b in _mst_edges(pads):
                if meter.exhausted:
                    notes.append(
                        f"budget exhausted ({meter.stop_reason}) at net {net.name}"
                    )
                    unrouted.append(net.id)
                    break
                ok, via_seq = self._route_edge(
                    ws, problem, net.id, pad_a, pad_b, width, via_seq, meter,
                    traces, vias
                )
                if not ok:
                    failures += 1
            if failures and net.id not in unrouted:
                unrouted.append(net.id)

        if meter.stop_reason == "wall_clock":
            notes.append(
                "hit the wall-clock safety valve — this run is not a comparable "
                "result, only evidence that the router hung"
            )

        return RoutingSolution(
            router=self.name,
            traces=tuple(traces),
            vias=tuple(vias),
            complete=not unrouted,
            unrouted_nets=tuple(sorted(set(unrouted))),
            iterations=meter.iterations,
            nodes_expanded=meter.nodes,
            wall_clock_s=time.perf_counter() - started,
            notes=tuple(notes),
        )

    # -- one edge --------------------------------------------------------

    def _route_edge(
        self,
        ws: Workspace,
        problem: RoutingProblem,
        net_id: str,
        pad_a: Pad,
        pad_b: Pad,
        width: float,
        via_seq: int,
        meter: BudgetMeter,
        traces: list[Trace],
        vias: list[Via],
    ) -> tuple[bool, int]:
        for layer, access_a, access_b, points in _candidates(
            ws, problem, pad_a, pad_b, net_id
        ):
            if not meter.tick():
                return False, via_seq
            plan_stubs = list(access_a.stubs) + list(access_b.stubs)
            plan_vias = list(access_a.vias) + list(access_b.vias)

            if not all(
                ws.via_ok(point, net_id) is True for point in plan_vias
            ):
                continue
            if not all(
                ws.path_ok(stub_layer, stub_points, width, net_id) is True
                for stub_layer, stub_points in plan_stubs
            ):
                continue
            if ws.path_ok(layer, points, width, net_id) is not True:
                continue

            for stub_layer, stub_points in plan_stubs:
                trace = Trace(
                    id=f"{net_id}~{len(traces)}",
                    net=net_id,
                    layer=stub_layer,
                    points=tuple(stub_points),
                    width_mm=width,
                )
                ws.commit_trace(trace)
                traces.append(trace)
            for point in plan_vias:
                via = Via(
                    id=f"v{via_seq}",
                    net=net_id,
                    center=point,
                    drill_mm=problem.rules.via_drill_mm,
                    pad_mm=problem.rules.via_pad_mm,
                )
                via_seq += 1
                ws.commit_via(via)
                vias.append(via)
            trace = Trace(
                id=f"{net_id}~{len(traces)}",
                net=net_id,
                layer=layer,
                points=tuple(points),
                width_mm=width,
            )
            ws.commit_trace(trace)
            traces.append(trace)
            return True, via_seq
        return False, via_seq

    # -- planes ----------------------------------------------------------

    def _stitch_to_plane(
        self,
        ws: Workspace,
        problem: RoutingProblem,
        net_id: str,
        pads: Sequence[Pad],
        planes: Sequence[Plane],
        width: float,
        via_seq: int,
        meter: BudgetMeter,
        traces: list[Trace],
        vias: list[Via],
    ) -> tuple[int, int]:
        """One via per pad into the plane. No traces at all where possible."""
        placed = 0
        shape = {p.id: PolygonIndex(p.outline) for p in planes}
        for pad in pads:
            if meter.exhausted:
                break
            meter.tick()
            plane = next(
                (
                    p
                    for p in planes
                    if pad.reachable_from(p.layer)
                    and shape[p.id].contains(pad.center.x, pad.center.y)
                ),
                None,
            )
            if plane is not None and pad.kind == "plated_hole":
                placed += 1  # the barrel already lands in the plane
                continue
            target = next(
                (p for p in planes
                 if shape[p.id].contains(pad.center.x, pad.center.y)),
                planes[0],
            )
            done = False
            for point in _fanout_points(problem, pad, pad.center):
                meter.tick()
                if not shape[target.id].contains(point.x, point.y):
                    continue
                if ws.via_ok(point, net_id) is not True:
                    continue
                stub = (pad.center, point)
                stub_layer = pad.layers[0]
                if ws.path_ok(stub_layer, stub, width, net_id) is not True:
                    continue
                trace = Trace(
                    id=f"{net_id}~{len(traces)}",
                    net=net_id,
                    layer=stub_layer,
                    points=stub,
                    width_mm=width,
                )
                ws.commit_trace(trace)
                traces.append(trace)
                via = Via(
                    id=f"v{via_seq}",
                    net=net_id,
                    center=point,
                    drill_mm=problem.rules.via_drill_mm,
                    pad_mm=problem.rules.via_pad_mm,
                )
                via_seq += 1
                ws.commit_via(via)
                vias.append(via)
                placed += 1
                done = True
                break
            if not done and plane is not None:
                placed += 1
        return placed, via_seq


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------


def _shapes(a: Point, b: Point) -> Iterator[tuple[Point, ...]]:
    yield (a, b)
    if a.x != b.x and a.y != b.y:
        yield (a, Point(b.x, a.y), b)
        yield (a, Point(a.x, b.y), b)
        for frac in _Z_OFFSETS:
            mid_x = a.x + (b.x - a.x) * frac
            yield (a, Point(mid_x, a.y), Point(mid_x, b.y), b)
        for frac in _Z_OFFSETS:
            mid_y = a.y + (b.y - a.y) * frac
            yield (a, Point(a.x, mid_y), Point(b.x, mid_y), b)


def _fanout_points(problem: RoutingProblem, pad: Pad, toward: Point) -> list[Point]:
    """Where a via may sit beside a pad: far enough out that its pad clears the
    footprint pad, in the direction of travel first, then the four axes."""
    rules = problem.rules
    reach = (
        max(pad.width_mm, pad.height_mm) / 2.0
        + rules.target_clearance_mm
        + rules.via_pad_mm / 2.0
        + 0.05
    )
    dx, dy = toward.x - pad.center.x, toward.y - pad.center.y
    norm = math.hypot(dx, dy)
    directions: list[tuple[float, float]] = []
    if norm > 1e-9:
        directions.append((dx / norm, dy / norm))
    directions += [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)]
    out: list[Point] = []
    seen: set[tuple[float, float]] = set()
    for ux, uy in directions[:_MAX_ACCESS]:
        point = Point(pad.center.x + ux * reach, pad.center.y + uy * reach)
        key = (point.x, point.y)
        if key in seen:
            continue
        seen.add(key)
        out.append(point)
    return out


def _accesses(
    problem: RoutingProblem, pad: Pad, layer: str, toward: Point
) -> list[_Access]:
    if pad.reachable_from(layer):
        return [_Access(pad.center)]
    other = TOP if layer == BOTTOM else BOTTOM
    if not pad.reachable_from(other):
        return []
    return [
        _Access(point, stubs=((other, (pad.center, point)),), vias=(point,))
        for point in _fanout_points(problem, pad, toward)
    ]


def _candidates(
    ws: Workspace,
    problem: RoutingProblem,
    pad_a: Pad,
    pad_b: Pad,
    net_id: str,
) -> Iterator[tuple[str, _Access, _Access, tuple[Point, ...]]]:
    """Every pattern, cheapest first: top layer with no vias, then bottom."""
    for layer in (TOP, BOTTOM):
        list_a = _accesses(problem, pad_a, layer, pad_b.center)
        list_b = _accesses(problem, pad_b, layer, pad_a.center)
        if not list_a or not list_b:
            continue
        for access_a in list_a:
            for access_b in list_b:
                if access_a.point == access_b.point:
                    continue
                for points in _shapes(access_a.point, access_b.point):
                    yield (layer, access_a, access_b, points)


# ---------------------------------------------------------------------------
# Spanning tree
# ---------------------------------------------------------------------------


def _mst_edges(pads: Sequence[Pad]) -> list[tuple[Pad, Pad]]:
    """Prim over pad centres. O(n^2) and deterministic — ties broken by pad id,
    so the same net always produces the same tree."""
    if len(pads) < 2:
        return []
    inside = [pads[0]]
    outside = list(pads[1:])
    edges: list[tuple[Pad, Pad]] = []
    while outside:
        best: tuple[float, str, str] | None = None
        best_pair: tuple[Pad, Pad] | None = None
        for a in inside:
            for b in outside:
                d = a.center.distance_to(b.center)
                key = (d, a.id, b.id)
                if best is None or key < best:
                    best = key
                    best_pair = (a, b)
        assert best_pair is not None
        edges.append(best_pair)
        inside.append(best_pair[1])
        outside.remove(best_pair[1])
    return edges


#: The registry the tournament reads.
ROUTERS = {PatternRouter.name: PatternRouter}


__all__ = ["PatternRouter", "ROUTERS"]
