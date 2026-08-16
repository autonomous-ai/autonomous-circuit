"""Shared machinery for the quality metrics: sampling, fields, copper physics.

Everything in :mod:`routerlib.quality` measures a routed board by walking along
its copper and asking a question at each step. This module owns the walking and
the asking, so six metrics cannot disagree about what "0.5mm from ground" means.

Three pieces:

* :func:`walk` — a deterministic march along a set of traces at a fixed step.
  The step is the resolution of every fraction the package reports, so it is a
  ruler input and it is hashed with the rest.
* :class:`LayerField` / :class:`GroundField` — nearest-copper queries. A
  ``GroundField`` answers *how far is the return* at a point, on one layer, in
  three dimensions: laterally along the layer, or across the dielectric to a
  plane or a trace on the other side.
* the copper constants — sheet resistance and via barrel area, the two numbers
  that turn geometry into ohms.

**None of this is a gate.** Nothing here can fail a board; see the package
docstring for why that line is load-bearing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterator, Sequence

from routerlib.geometry import (
    Capsule,
    GridIndex,
    PolygonIndex,
    disc_capsule,
    pad_capsule,
    point_shape_distance,
    segment_capsule,
)
from routerlib.model import BOTTOM, TOP, Plane, RoutingProblem, RoutingSolution, Trace

# ---------------------------------------------------------------------------
# Ruler inputs — every number below changes what the metrics say
# ---------------------------------------------------------------------------

#: Step along a trace when a metric integrates something. 0.5mm is the same
#: step ``verifylib.netclass`` samples a differential pair at, deliberately: two
#: packages reporting "62% coupled" on the same pair should mean the same thing.
SAMPLE_STEP_MM = 0.5

#: How close ground copper on the *other* layer has to be for a sample to count
#: as referenced. Imported in spirit from
#: ``verifylib.netclass.DIFF_PAIR_REFERENCE_MM`` (0.5mm) — about the width of
#: the field a 0.15mm microstrip 1.6mm above its return actually uses on FR-4,
#: so it is a generous test rather than a strict one.
#: ``tests/test_quality.py::test_reference_window_matches_verifylib`` fails if
#: the two ever drift apart.
REFERENCE_MM = 0.5

#: IPC-2221B's copper thickness for 1oz, in mm. The same constant
#: ``verifylib.rules`` uses (``copper_oz * 0.0348``).
COPPER_THICKNESS_MM = 0.0348

#: Resistivity of annealed copper at 20 degC, ohm-mm (1.724e-8 ohm-m).
RHO_CU_OHM_MM = 1.724e-5

#: Sheet resistance of 1oz copper: 0.495 milliohm per square. Everything the
#: power metric computes is this number times an aspect ratio.
SHEET_R_OHM_PER_SQ = RHO_CU_OHM_MM / COPPER_THICKNESS_MM

#: Plating thickness inside a via barrel, mm. JLCPCB publishes 25um; the same
#: figure ``verifylib.netclass.VIA_PLATING_MM`` uses to size a via's current.
VIA_PLATING_MM = 0.025


def via_barrel_ohms(drill_mm: float, board_thickness_mm: float) -> float:
    """Resistance through one plated barrel.

    A barrel is a tube, not a slab: its cross-section is the plating annulus,
    ``pi * drill * plating``. For a 0.3mm drill through 1.6mm of FR-4 that is
    0.0236mm^2 and about **1.2 milliohms** — small next to a long thin trace,
    which is exactly the point of measuring rather than assuming.
    """
    area_mm2 = math.pi * max(drill_mm, 1e-6) * VIA_PLATING_MM
    return RHO_CU_OHM_MM * board_thickness_mm / area_mm2


def trace_ohms(length_mm: float, width_mm: float) -> float:
    """Resistance of a run of copper: sheet resistance times squares."""
    if width_mm <= 0:
        return math.inf
    return SHEET_R_OHM_PER_SQ * length_mm / width_mm


# ---------------------------------------------------------------------------
# Walking the copper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One sample of copper: where it is, how much length it stands for."""

    net: str
    trace_id: str
    layer: str
    x: float
    y: float
    length_mm: float
    width_mm: float
    #: Index of the trace this step came from, and of the step inside it.
    trace_index: int
    step_index: int
    #: True on the first step of a trace, so a metric can reset a run-length
    #: counter without holding the whole series.
    first_of_trace: bool


def walk(traces: Sequence[Trace], step_mm: float = SAMPLE_STEP_MM) -> Iterator[Step]:
    """March along every segment of every trace, midpoint sampling.

    Order is the order the traces are given in, which the callers make
    deterministic by sorting first. A zero-length segment contributes nothing
    and is skipped rather than sampled once with weight zero, because a router
    that emits a hundred duplicate points would otherwise move every average.
    """
    for trace_index, trace in enumerate(traces):
        emitted = 0
        for a, b in trace.segments:
            length = a.distance_to(b)
            if length <= 0:
                continue
            count = max(1, int(math.ceil(length / step_mm)))
            piece = length / count
            for i in range(count):
                t = (i + 0.5) / count
                yield Step(
                    net=trace.net,
                    trace_id=trace.id,
                    layer=trace.layer,
                    x=a.x + (b.x - a.x) * t,
                    y=a.y + (b.y - a.y) * t,
                    length_mm=piece,
                    width_mm=trace.width_mm,
                    trace_index=trace_index,
                    step_index=emitted,
                    first_of_trace=(emitted == 0),
                )
                emitted += 1


def other_layer(layer: str) -> str:
    return BOTTOM if layer == TOP else TOP


# ---------------------------------------------------------------------------
# Nearest-copper queries
# ---------------------------------------------------------------------------

_RINGS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0)


class LayerField:
    """Every piece of copper on one layer, answering "how far is the nearest".

    Uniform-grid buckets with an expanding-ring probe: the first ring that
    contains anything gives a candidate distance ``d``, then one confirming
    query at margin ``d`` returns the true nearest, because a shape closer than
    ``d`` must occupy a bucket inside that margin. **Exact, not approximate** —
    a wider starting ring can only add candidates, never change the minimum,
    which is what makes the two speedups below safe.

    Both exist because ``terminal-keyboard`` is the pathological case: its
    ground copper is sparse and the nearest return is often 20mm away, so the
    naive ring walk escalated through seven queries and then measured most of
    the board at every one of five thousand samples.

    * a **hint** carries the last answer forward, because consecutive samples
      along a trace have nearly the same nearest return
    * a **bounding-box floor** skips the exact distance for any candidate whose
      box is already further than the best so far
    """

    __slots__ = ("_grid", "_count", "_hint")

    def __init__(self, cell_mm: float = 2.0) -> None:
        self._grid = GridIndex(cell_mm=cell_mm)
        self._count = 0
        self._hint = _RINGS[0]

    def add(self, capsule: Capsule, payload=None) -> None:
        from routerlib.geometry import capsule_bbox

        self._grid.insert(capsule, (capsule_bbox(capsule), payload))
        self._count += 1

    def __len__(self) -> int:
        return self._count

    def nearest(self, x: float, y: float) -> tuple[float, object | None]:
        """``(distance to the copper edge, payload)``; ``(inf, None)`` if empty.

        Distance is to the *edge* of the conductor, clamped at zero, which is
        the conservative reading: a point sitting on top of copper is zero away
        from it.
        """
        if not self._count:
            return (math.inf, None)
        probe = (x, y, x, y, 0.0)
        start = self._hint
        for margin in _RINGS:
            if margin < start:
                continue
            hits = list(self._grid.query(probe, margin=margin))
            if not hits:
                continue
            best, payload = self._best(x, y, hits)
            if best > margin:
                hits = list(self._grid.query(probe, margin=best + 1e-9))
                best, payload = self._best(x, y, hits)
            self._hint = max(_RINGS[0], best * 0.75)
            return (best, payload)
        # Nothing within the widest ring: fall back to the whole field once.
        hits = [item for _, item in self._grid._items]  # noqa: SLF001
        return self._best(x, y, hits)

    @staticmethod
    def _best(x: float, y: float, hits) -> tuple[float, object | None]:
        best = math.inf
        payload = None
        for capsule, (bbox, pay) in hits:
            if best <= 0.0:
                break  # already inside copper; the answer clamps to zero
            bx0, by0, bx1, by1 = bbox
            dx = bx0 - x if x < bx0 else (x - bx1 if x > bx1 else 0.0)
            dy = by0 - y if y < by0 else (y - by1 if y > by1 else 0.0)
            if math.isfinite(best) and dx * dx + dy * dy >= best * best:
                continue  # its own box is further than the best exact answer
            d = point_shape_distance(x, y, capsule)
            if d < best:
                best = d
                payload = pay
        return (max(best, 0.0), payload)


def _plane_indexes(planes: Sequence[Plane]) -> list[tuple[Plane, PolygonIndex, list[PolygonIndex]]]:
    out = []
    for plane in planes:
        if len(plane.outline) < 3:
            continue
        out.append(
            (
                plane,
                PolygonIndex(plane.outline),
                [PolygonIndex(ring) for ring in plane.holes if len(ring) >= 3],
            )
        )
    return out


def ground_net_ids(problem: RoutingProblem) -> frozenset[str]:
    """Nets that are the return path. Class first, name only as a fallback.

    ``net_class`` is resolved once at problem-build time by
    ``routerlib.adapters.classify_nets``, so a name test here would be a second
    opinion about something already decided. It exists only for hand-built
    fixtures that never went through the adapter.
    """
    by_class = {n.id for n in problem.nets if n.net_class == "ground"}
    if by_class:
        return frozenset(by_class)
    return frozenset(
        n.id for n in problem.nets if (n.name or "").strip().upper() in
        ("GND", "AGND", "DGND", "VSS", "GROUND")
    )


class GroundField:
    """Where the return current can flow, and how far it is from a point.

    Built once per (problem, solution) and shared by the loop-area, reference
    and differential-pair metrics, so all three agree about what counts as
    ground: any copper on a ground net — pads, routed traces, vias, and poured
    planes — from both the problem and the solution.

    :meth:`separation` is the number the loop-area metric integrates. It is the
    smallest of three distances:

    ==========================  ==================================
    return beside the trace     the lateral gap, on the same layer
    return under the trace      the dielectric thickness
    return under and beside     ``hypot(lateral, thickness)``
    ==========================  ==================================

    **What it is not.** Return current follows the *path back to the source*,
    not the nearest copper at a point. A ground pour makes those the same thing
    and it is why pours exist; on a 2-layer board with routed ground they are
    not, and this metric reads as optimistic there. Stated in the report's
    coverage rather than hidden.
    """

    __slots__ = ("thickness_mm", "clearance_mm", "_fields", "_planes", "_has_ground")

    def __init__(self, problem: RoutingProblem, solution: RoutingSolution) -> None:
        grounds = ground_net_ids(problem)
        self.thickness_mm = float(problem.board.thickness_mm)
        self.clearance_mm = float(problem.rules.min_clearance_mm)
        # 6mm buckets, not 2mm. The query that costs is the confirming one at
        # a 20mm margin on a board whose ground is sparse (terminal-keyboard),
        # and there the bucket walk dominates the distance arithmetic: 2mm
        # cells make it visit a hundred times more empty buckets than 6mm do.
        self._fields: dict[str, LayerField] = {
            TOP: LayerField(cell_mm=6.0), BOTTOM: LayerField(cell_mm=6.0)
        }
        self._planes: dict[str, list] = {TOP: [], BOTTOM: []}

        for pad in problem.pads:
            if pad.net not in grounds:
                continue
            capsule = pad_capsule(pad)
            for layer in pad.layers:
                if layer in self._fields:
                    self._fields[layer].add(capsule, ("pad", pad.id))
        for source in (problem.existing_traces, solution.traces):
            for trace in source:
                if trace.net not in grounds or trace.layer not in self._fields:
                    continue
                for index, (a, b) in enumerate(trace.segments):
                    self._fields[trace.layer].add(
                        segment_capsule(a.x, a.y, b.x, b.y, trace.width_mm),
                        ("trace", f"{trace.id}#{index}"),
                    )
        for source in (problem.existing_vias, solution.vias):
            for via in source:
                if via.net not in grounds:
                    continue
                capsule = disc_capsule(via.center.x, via.center.y, via.pad_mm)
                for layer in (TOP, BOTTOM):
                    self._fields[layer].add(capsule, ("via", via.id))
        for plane, outline, holes in _plane_indexes(problem.planes):
            if plane.net in grounds and plane.layer in self._planes:
                self._planes[plane.layer].append((outline, holes))

        self._has_ground = bool(grounds) and (
            len(self._fields[TOP]) + len(self._fields[BOTTOM]) > 0
            or any(self._planes.values())
        )

    @property
    def present(self) -> bool:
        """False when the board has no ground copper at all — then every number
        this field produces is *not applicable*, which is a different answer
        from *bad* and must be reported as one."""
        return self._has_ground

    def has_plane(self, layer: str) -> bool:
        return bool(self._planes.get(layer))

    def in_plane(self, x: float, y: float, layer: str) -> bool:
        for outline, holes in self._planes.get(layer, ()):
            if outline.contains(x, y) and not any(h.contains(x, y) for h in holes):
                return True
        return False

    def lateral(self, x: float, y: float, layer: str) -> float:
        """Nearest ground copper on ``layer``, ignoring the other side."""
        if self.in_plane(x, y, layer):
            # A pour on the trace's own layer surrounds it at the design
            # clearance; the pour outline in the model is not carved by the
            # trace, so the true gap is the rule rather than zero.
            return self.clearance_mm
        return self._fields[layer].nearest(x, y)[0] if layer in self._fields else math.inf

    def separation(self, x: float, y: float, layer: str) -> tuple[float, str]:
        """``(distance to the return, which return it was)``.

        ``inf`` with kind ``"none"`` when there is no ground copper anywhere.
        """
        best = math.inf
        kind = "none"
        same = self.lateral(x, y, layer)
        if same < best:
            best, kind = same, "same-layer"
        far = other_layer(layer)
        if self.in_plane(x, y, far):
            if self.thickness_mm < best:
                best, kind = self.thickness_mm, "plane"
        elif far in self._fields:
            lateral = self._fields[far].nearest(x, y)[0]
            if math.isfinite(lateral):
                across = math.hypot(lateral, self.thickness_mm)
                if across < best:
                    best, kind = across, "opposite-layer"
        return (best, kind)

    def referenced(self, x: float, y: float, layer: str) -> bool:
        """Is there a return path directly under this point?

        The reference-plane question, not the loop-area one: ground copper on
        the *opposite* layer within :data:`REFERENCE_MM`, or a pour covering it.
        """
        far = other_layer(layer)
        if self.in_plane(x, y, far):
            return True
        if far not in self._fields:
            return False
        return self._fields[far].nearest(x, y)[0] <= REFERENCE_MM


def signal_traces(
    problem: RoutingProblem, solution: RoutingSolution, *, include_power: bool = True
) -> list[Trace]:
    """The solution's traces that carry a signal, in a deterministic order.

    Ground is excluded: it *is* the return, so asking how far its return is
    means nothing. Power is in by default because a rail's return loop is as
    real as a signal's, and the report keeps them separable by net class.
    """
    grounds = ground_net_ids(problem)
    classes = {n.id: n.net_class for n in problem.nets}
    keep = []
    for trace in solution.traces:
        if trace.net in grounds:
            continue
        if not include_power and classes.get(trace.net) == "power":
            continue
        keep.append(trace)
    keep.sort(key=lambda t: (t.net, t.layer, t.id))
    return keep


def traces_by_net(traces: Sequence[Trace]) -> dict[str, list[Trace]]:
    out: dict[str, list[Trace]] = {}
    for trace in traces:
        out.setdefault(trace.net, []).append(trace)
    return out


__all__ = [
    "COPPER_THICKNESS_MM",
    "GroundField",
    "LayerField",
    "REFERENCE_MM",
    "RHO_CU_OHM_MM",
    "SAMPLE_STEP_MM",
    "SHEET_R_OHM_PER_SQ",
    "Step",
    "VIA_PLATING_MM",
    "ground_net_ids",
    "other_layer",
    "signal_traces",
    "trace_ohms",
    "traces_by_net",
    "via_barrel_ohms",
    "walk",
]
