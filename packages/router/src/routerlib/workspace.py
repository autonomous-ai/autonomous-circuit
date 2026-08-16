"""A mutable working copy of the board, for algorithms to route into.

The problem is immutable; this is the scratchpad. It holds every obstacle
indexed by layer and answers the one question a router asks a few hundred
thousand times: *may I put this copper here?*

Two things make it worth sharing rather than letting each algorithm build its
own:

* **It answers with the same geometry the scorer uses.** Both go through
  :mod:`routerlib.geometry`, which goes through ``circuitpy.checks``. A router
  that passes its own check and fails the scorer would be a bug in the harness,
  not in the algorithm.
* **It designs to a target, not to the floor.** ``clearance`` here defaults to
  ``rules.target_clearance_mm``, deliberately above the fab minimum, because
  the router we ship today aims at the minimum and therefore lands under it.
  That single choice is why four attempts to steer it all failed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from routerlib.geometry import (
    Capsule,
    GridIndex,
    capsule_gap,
    disc_capsule,
    distance_to_polygon,
    PolygonIndex,
    drill_capsule as _drill_capsule,
    keepout_capsule,
    pad_capsule,
    point_in_polygon,
    segment_capsule,
)
from routerlib.model import (
    BOTTOM,
    TOP,
    Drill,
    Pad,
    Point,
    RoutingProblem,
    Trace,
    Via,
)


@dataclass(frozen=True)
class Blocker:
    """Why a placement was refused. Returned instead of a bare ``False`` so a
    router can learn something from a rejection and a test can assert on it."""

    reason: str
    detail: str = ""
    net: str | None = None

    def __bool__(self) -> bool:  # a Blocker is always a refusal
        return False


class Workspace:
    """Obstacles plus committed copper, queryable per layer."""

    def __init__(self, problem: RoutingProblem, *, clearance: float | None = None):
        self.problem = problem
        self.rules = problem.rules
        self.clearance = (
            problem.rules.target_clearance_mm if clearance is None else clearance
        )
        self.traces: list[Trace] = list(problem.existing_traces)
        self.vias: list[Via] = list(problem.existing_vias)
        self.outline = (
            PolygonIndex(problem.board.outline)
            if len(problem.board.outline) >= 3
            else None
        )

        self._copper: dict[str, GridIndex] = {TOP: GridIndex(2.0), BOTTOM: GridIndex(2.0)}
        self._drills = GridIndex(2.0)
        self._smd: dict[str, GridIndex] = {TOP: GridIndex(2.0), BOTTOM: GridIndex(2.0)}
        self._keepouts: dict[str, GridIndex] = {TOP: GridIndex(2.0), BOTTOM: GridIndex(2.0)}

        for pad in problem.pads:
            capsule = pad_capsule(pad)
            for layer in pad.layers:
                self._copper.setdefault(layer, GridIndex(2.0)).insert(
                    capsule, (pad.net, f"pad {pad.id}")
                )
                if pad.is_smd:
                    self._smd.setdefault(layer, GridIndex(2.0)).insert(capsule, pad)
        for drill in problem.drills:
            self._drills.insert(_drill_capsule(drill), drill)
        for keepout in problem.keepouts:
            capsule = keepout_capsule(keepout)
            for layer in keepout.layers:
                self._keepouts.setdefault(layer, GridIndex(2.0)).insert(
                    capsule, keepout
                )
        for trace in self.traces:
            for a, b in trace.segments:
                self._copper[trace.layer].insert(
                    segment_capsule(a.x, a.y, b.x, b.y, trace.width_mm),
                    (trace.net, f"existing {trace.id}"),
                )
        for via in self.vias:
            self._add_via_copper(via)

    # -- queries ---------------------------------------------------------

    def segment_ok(
        self, layer: str, a: Point, b: Point, width: float, net: str
    ) -> bool | Blocker:
        return self._capsule_ok(
            layer, segment_capsule(a.x, a.y, b.x, b.y, width), net, is_via=False
        )

    def via_ok(self, center: Point, net: str, *, drill: float | None = None,
               pad: float | None = None) -> bool | Blocker:
        drill = self.rules.via_drill_mm if drill is None else drill
        pad = self.rules.via_pad_mm if pad is None else pad
        capsule = disc_capsule(center.x, center.y, pad)

        if not self.rules.allow_via_in_pad:
            hole = disc_capsule(center.x, center.y, drill)
            for layer in (TOP, BOTTOM):
                for _, smd in self._smd.get(layer, GridIndex(2.0)).query(hole, 0.05):
                    if capsule_gap(hole, pad_capsule(smd)) < 0.0:
                        return Blocker("via_in_pad", f"SMD pad {smd.id}", smd.net)
        # Hole-to-hole against every other drill, our own net included: two
        # barrels too close break out into each other whatever they carry.
        for _, other in self._drills.query(capsule, self.rules.min_hole_to_hole_mm + 0.1):
            gap = capsule_gap(disc_capsule(center.x, center.y, drill),
                              _drill_capsule(other))
            if gap < self.rules.min_hole_to_hole_mm - 1e-9:
                return Blocker("hole_to_hole", f"drill {other.id} at {gap:.3f}mm")
        for layer in (TOP, BOTTOM):
            verdict = self._capsule_ok(layer, capsule, net, is_via=True,
                                       drill_diameter=drill)
            if verdict is not True:
                return verdict
        return True

    def _capsule_ok(
        self,
        layer: str,
        capsule: Capsule,
        net: str,
        *,
        is_via: bool,
        drill_diameter: float = 0.0,
    ) -> bool | Blocker:
        board = self.problem.board
        # Board edge, measured against the real outline when there is one.
        limit = self.rules.min_edge_clearance_mm
        if self.outline is not None:
            ax, ay, bx, by, _ = capsule
            if not (
                self.outline.contains(ax, ay) and self.outline.contains(bx, by)
            ):
                return Blocker("off_board")
            if self.outline.clearance(capsule, limit) < limit:
                return Blocker("board_edge")
        else:
            x0, y0, x1, y1 = board.bbox
            ax, ay, bx, by, r = capsule
            if (
                min(ax, bx) - r - x0 < limit
                or min(ay, by) - r - y0 < limit
                or x1 - (max(ax, bx) + r) < limit
                or y1 - (max(ay, by) + r) < limit
            ):
                return Blocker("board_edge")

        for _, keepout in self._keepouts.get(layer, GridIndex(2.0)).query(capsule, 0.05):
            if capsule_gap(capsule, keepout_capsule(keepout)) < 0.0:
                return Blocker("keepout", keepout.id)

        margin = self.clearance + 0.05
        for other, payload in self._copper.get(layer, GridIndex(2.0)).query(capsule, margin):
            other_net, label = payload
            if other_net == net and other_net is not None:
                continue
            if capsule_gap(capsule, other) < self.clearance:
                return Blocker("clearance", label, other_net)

        # Copper to drills. The hole's own net is exempt for a plated hole,
        # which is the pipeline's settled rule: every legitimate connection to
        # a plated hole leaves its pad at exactly one annular ring.
        hole_margin = max(
            self.rules.min_pth_to_copper_mm, self.rules.min_npth_to_copper_mm
        ) + 0.1
        for other, drill in self._drills.query(capsule, hole_margin):
            if drill.plated and drill.net and drill.net == net:
                continue
            needed = self.rules.hole_clearance(drill)
            if capsule_gap(capsule, other) < needed:
                return Blocker("hole_clearance", f"drill {drill.id}", drill.net)
            if is_via and drill_diameter:
                gap = capsule_gap(
                    disc_capsule(
                        (capsule[0] + capsule[2]) / 2,
                        (capsule[1] + capsule[3]) / 2,
                        drill_diameter,
                    ),
                    other,
                )
                if gap < self.rules.min_hole_to_hole_mm - 1e-9:
                    return Blocker("hole_to_hole", f"drill {drill.id}")
        return True

    def path_ok(
        self, layer: str, points: Sequence[Point], width: float, net: str
    ) -> bool | Blocker:
        for a, b in zip(points, points[1:]):
            if a == b:
                continue
            verdict = self.segment_ok(layer, a, b, width, net)
            if verdict is not True:
                return verdict
        return True

    # -- mutation --------------------------------------------------------

    def commit_trace(self, trace: Trace) -> None:
        self.traces.append(trace)
        for a, b in trace.segments:
            self._copper.setdefault(trace.layer, GridIndex(2.0)).insert(
                segment_capsule(a.x, a.y, b.x, b.y, trace.width_mm),
                (trace.net, f"trace {trace.id}"),
            )

    def commit_via(self, via: Via) -> None:
        self.vias.append(via)
        self._add_via_copper(via)
        self._drills.insert(
            disc_capsule(via.center.x, via.center.y, via.drill_mm),
            Drill(
                id=via.id,
                center=via.center,
                width_mm=via.drill_mm,
                height_mm=via.drill_mm,
                plated=True,
                net=via.net,
            ),
        )

    def _add_via_copper(self, via: Via) -> None:
        capsule = disc_capsule(via.center.x, via.center.y, via.pad_mm)
        for layer in (TOP, BOTTOM):
            self._copper.setdefault(layer, GridIndex(2.0)).insert(
                capsule, (via.net, f"via {via.id}")
            )


__all__ = ["Blocker", "Workspace"]
