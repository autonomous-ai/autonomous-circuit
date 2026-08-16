"""Return path: how much area does each net's current loop enclose?

**The metric a professional watches and we do not measure at all.** Current
goes out along the trace and comes back along the nearest return, and the two
enclose a loop. That loop is an antenna in both directions: it radiates at
``E ~ f^2 * I * A`` and it picks up whatever is radiating at it. Halving the
loop halves the emission; nothing else on a 2-layer board buys as much.

No DRC rule mentions it. A board can be perfectly legal, perfectly connected,
and enclose ten times the loop area it needed to.

**What is measured.** March along every non-ground trace at
:data:`~routerlib.quality.common.SAMPLE_STEP_MM`; at each step ask
:class:`~routerlib.quality.common.GroundField` how far the nearest return is —
laterally on the same layer, across the dielectric to a plane, or diagonally to
ground copper on the other side. Multiply by the length that step stands for
and add it up. The result is in mm^2 and it is the area between the trace and
its return, which is the definition.

**What it is not, said plainly.** Return current follows the path back to the
source, not the nearest copper at every point. Over a pour those are the same
thing; over routed ground on a 2-layer board they are not, and there this reads
as *optimistic* — it finds a nearby ground trace that may be going somewhere
else entirely. It is a lower bound on the real loop, not an upper one.

**No threshold.** There is no IPC number for loop area, and inventing one would
be worse than reporting none. The report gives mm^2 per net, the worst net, and
``mean_return_mm`` — loop area divided by routed length, which is the average
distance to the return in millimetres and is comparable between a 6cm board and
a 10cm one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from routerlib.model import RoutingProblem, RoutingSolution

from routerlib.quality.common import (
    SAMPLE_STEP_MM,
    GroundField,
    signal_traces,
    walk,
)


@dataclass(frozen=True)
class NetLoop:
    net: str
    net_class: str
    length_mm: float
    loop_area_mm2: float
    #: Length-weighted mean distance to the return. Size-independent, so this
    #: is the column to rank boards on.
    mean_return_mm: float
    worst_return_mm: float
    #: Fraction of the net's copper whose return is on the other layer (a plane
    #: or a trace) rather than beside it. Higher is usually better on 2 layers.
    over_return_fraction: float

    def as_dict(self) -> dict:
        return {
            "net": self.net,
            "netClass": self.net_class,
            "lengthMm": round(self.length_mm, 4),
            "loopAreaMm2": round(self.loop_area_mm2, 4),
            "meanReturnMm": round(self.mean_return_mm, 4),
            "worstReturnMm": round(self.worst_return_mm, 4),
            "overReturnFraction": round(self.over_return_fraction, 4),
        }


@dataclass(frozen=True)
class LoopAreaResult:
    #: ``None`` when the board has no ground copper — not zero, because "no
    #: return anywhere" and "a perfect return" are opposite answers.
    total_loop_area_mm2: float | None
    mean_return_mm: float | None
    worst_net: str | None
    worst_net_loop_area_mm2: float | None
    worst_return_mm: float | None
    measured_length_mm: float
    nets: tuple[NetLoop, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "totalLoopAreaMm2": _round(self.total_loop_area_mm2, 3),
            "meanReturnMm": _round(self.mean_return_mm, 4),
            "worstNet": self.worst_net,
            "worstNetLoopAreaMm2": _round(self.worst_net_loop_area_mm2, 3),
            "worstReturnMm": _round(self.worst_return_mm, 4),
            "measuredLengthMm": round(self.measured_length_mm, 3),
            "nets": [n.as_dict() for n in self.nets],
        }


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def measure(
    problem: RoutingProblem,
    solution: RoutingSolution,
    *,
    ground: GroundField | None = None,
    step_mm: float = SAMPLE_STEP_MM,
) -> LoopAreaResult:
    ground = ground or GroundField(problem, solution)
    traces = signal_traces(problem, solution)
    classes = {n.id: n.net_class for n in problem.nets}

    per_net: dict[str, list[float]] = {}
    if ground.present:
        for step in walk(traces, step_mm):
            distance, kind = ground.separation(step.x, step.y, step.layer)
            if not math.isfinite(distance):
                continue
            row = per_net.setdefault(step.net, [0.0, 0.0, 0.0, 0.0])
            row[0] += step.length_mm
            row[1] += step.length_mm * distance
            row[2] = max(row[2], distance)
            if kind in ("plane", "opposite-layer"):
                row[3] += step.length_mm

    nets: list[NetLoop] = []
    for net_id in sorted(per_net):
        length, area, worst, over = per_net[net_id]
        if length <= 0:
            continue
        nets.append(
            NetLoop(
                net=net_id,
                net_class=classes.get(net_id, "signal"),
                length_mm=length,
                loop_area_mm2=area,
                mean_return_mm=area / length,
                worst_return_mm=worst,
                over_return_fraction=over / length,
            )
        )

    if not ground.present or not nets:
        return LoopAreaResult(
            total_loop_area_mm2=None,
            mean_return_mm=None,
            worst_net=None,
            worst_net_loop_area_mm2=None,
            worst_return_mm=None,
            measured_length_mm=round(sum(n.length_mm for n in nets), 4),
            nets=tuple(nets),
        )

    total_area = sum(n.loop_area_mm2 for n in nets)
    total_length = sum(n.length_mm for n in nets)
    worst = max(nets, key=lambda n: (n.loop_area_mm2, n.net))
    return LoopAreaResult(
        total_loop_area_mm2=total_area,
        mean_return_mm=total_area / total_length if total_length else None,
        worst_net=worst.net,
        worst_net_loop_area_mm2=worst.loop_area_mm2,
        worst_return_mm=max(n.worst_return_mm for n in nets),
        measured_length_mm=total_length,
        nets=tuple(nets),
    )


__all__ = ["LoopAreaResult", "NetLoop", "measure"]
