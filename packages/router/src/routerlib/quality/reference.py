"""Reference continuity: does the return path ever disappear under the trace?

A trace with a solid return directly beneath it has a defined impedance and a
tight loop. A trace that runs off the edge of its reference — over a void in the
pour, across a slot another trace cut in it, or over bare laminate — has no
return underneath at all, and the current comes back the long way round.

**This is invisible in DRC by construction.** Every clearance is legal on both
sides of a plane split. The defect is that the *plane* is not there, and no rule
in any fab profile is about absence.

Two numbers, and the second is the one that matters:

``referenced_fraction``
    how much of the net's copper has ground on the other layer within
    :data:`~routerlib.quality.common.REFERENCE_MM`.
``gap_crossings``
    how many times, walking along a trace, that answer flips from yes to no.
    A trace 90% referenced in one piece is fine; a trace 90% referenced with
    eleven crossings is eleven discontinuities.

**Two modes, reported explicitly.** With a poured ground plane on the reference
layer the measurement is the textbook one — the crossings are plane splits, and
``plane_split_crossings`` counts the ones where foreign copper on the reference
layer is what carved the slot. With no plane (which is every board we ship
today) the "reference" is routed ground copper, ``mode`` says ``trace``, and the
honest reading is that a 2-layer board without a pour has almost no reference at
all. That is a finding about the board template, not about the router, and the
report keeps them apart by naming the mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from routerlib.geometry import GridIndex, disc_capsule, pad_capsule, point_shape_distance, segment_capsule
from routerlib.model import BOTTOM, TOP, RoutingProblem, RoutingSolution

from routerlib.quality.common import (
    REFERENCE_MM,
    SAMPLE_STEP_MM,
    GroundField,
    ground_net_ids,
    other_layer,
    signal_traces,
    walk,
)


@dataclass(frozen=True)
class NetReference:
    net: str
    length_mm: float
    referenced_fraction: float
    unreferenced_mm: float
    gap_crossings: int
    longest_unreferenced_mm: float

    def as_dict(self) -> dict:
        return {
            "net": self.net,
            "lengthMm": round(self.length_mm, 4),
            "referencedFraction": round(self.referenced_fraction, 4),
            "unreferencedMm": round(self.unreferenced_mm, 4),
            "gapCrossings": self.gap_crossings,
            "longestUnreferencedMm": round(self.longest_unreferenced_mm, 4),
        }


@dataclass(frozen=True)
class ReferenceResult:
    #: ``"plane"`` when a ground pour covers a reference layer, ``"trace"``
    #: when the only return is routed copper, ``"none"`` when neither exists.
    mode: str
    referenced_fraction: float | None
    unreferenced_mm: float
    gap_crossings: int
    #: Crossings where foreign copper on the reference layer is what interrupts
    #: the return — a real plane split rather than the edge of the pour. Only
    #: meaningful in ``plane`` mode.
    plane_split_crossings: int | None
    measured_length_mm: float
    nets: tuple[NetReference, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "referencedFraction": (
                None if self.referenced_fraction is None
                else round(self.referenced_fraction, 4)
            ),
            "unreferencedMm": round(self.unreferenced_mm, 3),
            "gapCrossings": self.gap_crossings,
            "planeSplitCrossings": self.plane_split_crossings,
            "measuredLengthMm": round(self.measured_length_mm, 3),
            "nets": [n.as_dict() for n in self.nets],
        }


def _foreign_fields(
    problem: RoutingProblem, solution: RoutingSolution
) -> dict[str, GridIndex]:
    """Copper on each layer that is *not* ground — what carves a plane."""
    grounds = ground_net_ids(problem)
    fields: dict[str, GridIndex] = {TOP: GridIndex(2.0), BOTTOM: GridIndex(2.0)}
    for pad in problem.pads:
        if pad.net in grounds:
            continue
        capsule = pad_capsule(pad)
        for layer in pad.layers:
            if layer in fields:
                fields[layer].insert(capsule, pad.id)
    for source in (problem.existing_traces, solution.traces):
        for trace in source:
            if trace.net in grounds or trace.layer not in fields:
                continue
            for a, b in trace.segments:
                fields[trace.layer].insert(
                    segment_capsule(a.x, a.y, b.x, b.y, trace.width_mm), trace.id
                )
    for source in (problem.existing_vias, solution.vias):
        for via in source:
            if via.net in grounds:
                continue
            capsule = disc_capsule(via.center.x, via.center.y, via.pad_mm)
            for layer in (TOP, BOTTOM):
                fields[layer].insert(capsule, via.id)
    return fields


def _near(field_: GridIndex, x: float, y: float, within: float) -> bool:
    probe = (x, y, x, y, 0.0)
    for capsule, _ in field_.query(probe, margin=within):
        if point_shape_distance(x, y, capsule) <= within:
            return True
    return False


def measure(
    problem: RoutingProblem,
    solution: RoutingSolution,
    *,
    ground: GroundField | None = None,
    step_mm: float = SAMPLE_STEP_MM,
) -> ReferenceResult:
    ground = ground or GroundField(problem, solution)
    traces = signal_traces(problem, solution)

    has_plane = ground.has_plane(TOP) or ground.has_plane(BOTTOM)
    if not ground.present:
        mode = "none"
    elif has_plane:
        mode = "plane"
    else:
        mode = "trace"

    foreign = _foreign_fields(problem, solution) if mode == "plane" else {}

    per_net: dict[str, dict] = {}
    splits = 0
    for step in walk(traces, step_mm):
        row = per_net.setdefault(
            step.net,
            {"len": 0.0, "ref": 0.0, "cross": 0, "run": 0.0, "worst": 0.0,
             "prev": None, "trace": None},
        )
        if row["trace"] != step.trace_index:
            # A new trace starts a new run; a gap does not straddle two traces.
            row["trace"] = step.trace_index
            row["prev"] = None
            row["run"] = 0.0
        referenced = ground.referenced(step.x, step.y, step.layer) if mode != "none" else False
        row["len"] += step.length_mm
        if referenced:
            row["ref"] += step.length_mm
            if row["prev"] is False:
                row["worst"] = max(row["worst"], row["run"])
            row["run"] = 0.0
        else:
            if row["prev"] is True:
                row["cross"] += 1
                if mode == "plane":
                    far = other_layer(step.layer)
                    if _near(foreign.get(far, GridIndex(2.0)), step.x, step.y, REFERENCE_MM):
                        splits += 1
            row["run"] += step.length_mm
        row["prev"] = referenced
    for row in per_net.values():
        if row["prev"] is False:
            row["worst"] = max(row["worst"], row["run"])

    nets = tuple(
        NetReference(
            net=net_id,
            length_mm=row["len"],
            referenced_fraction=(row["ref"] / row["len"]) if row["len"] else 0.0,
            unreferenced_mm=row["len"] - row["ref"],
            gap_crossings=row["cross"],
            longest_unreferenced_mm=row["worst"],
        )
        for net_id, row in sorted(per_net.items())
        if row["len"] > 0
    )
    total = sum(n.length_mm for n in nets)
    referenced = sum(n.referenced_fraction * n.length_mm for n in nets)
    return ReferenceResult(
        mode=mode,
        referenced_fraction=(referenced / total) if total and mode != "none" else None,
        unreferenced_mm=total - referenced,
        gap_crossings=sum(n.gap_crossings for n in nets),
        plane_split_crossings=splits if mode == "plane" else None,
        measured_length_mm=total,
        nets=nets,
    )


__all__ = ["NetReference", "ReferenceResult", "measure"]
