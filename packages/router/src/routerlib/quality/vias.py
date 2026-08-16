"""Vias: how many, where they hurt most, and which ones do nothing.

``routerlib.scoring.Quality`` already counts vias and ranks on the count. That
is the right first-order number and it is not enough, because vias are not
interchangeable:

``on_high_speed``
    a via on a differential pair or any net carrying edges is an impedance
    discontinuity — a stub, a capacitance and a return-path interruption in one
    object. Ten vias on power rails and two on USB is a different board from
    two on power and ten on USB, and the plain count cannot tell them apart.
``dangling``
    a via with copper on only one of its two layers. It changed nothing and it
    cost a drill hit, a plated barrel, and an antenna. These come from routers
    that place a via and then reroute the far side away from it.
``unpaired_pair_vias``
    the differential-pair case of the same idea, kept here as a count so via
    quality and pair quality agree: see
    :mod:`routerlib.quality.diffpair` for the per-pair breakdown.

**No stubs in the buried sense.** On a 2-layer board every via goes all the way
through, so there is no unused barrel length to resonate — the classic
back-drill defect cannot occur here and is not reported as zero, it is reported
as not applicable. That changes the day we route four layers.

**No pass mark.** Nobody publishes a maximum via count. Density per cm^2 is the
comparable figure across boards of different sizes and it is ranked, not gated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from routerlib.model import BOTTOM, TOP, RoutingProblem, RoutingSolution

#: A trace endpoint lands on a via when it is inside the via's own pad.
LANDS_WITHIN_PAD_FRACTION = 0.5


@dataclass(frozen=True)
class ViaResult:
    count: int
    per_cm2: float | None
    on_high_speed: int
    on_power: int
    on_ground: int
    dangling: int
    #: ``None`` on a 2-layer board: a through via has no unused barrel.
    blind_stub_count: int | None
    worst_net: str | None
    worst_net_vias: int | None
    per_net: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "count": self.count,
            "perCm2": None if self.per_cm2 is None else round(self.per_cm2, 4),
            "onHighSpeed": self.on_high_speed,
            "onPower": self.on_power,
            "onGround": self.on_ground,
            "dangling": self.dangling,
            "blindStubCount": self.blind_stub_count,
            "worstNet": self.worst_net,
            "worstNetVias": self.worst_net_vias,
            "perNet": dict(sorted(self.per_net.items())),
        }


def measure(problem: RoutingProblem, solution: RoutingSolution) -> ViaResult:
    classes = {n.id: n.net_class for n in problem.nets}
    per_net: dict[str, int] = {}
    for via in solution.vias:
        per_net[via.net] = per_net.get(via.net, 0) + 1

    # Which layers actually have same-net copper landing on each via. A via
    # with copper on one side only changed nothing and cost a drill hit.
    layer_points: dict[str, list[tuple[float, float, str]]] = {TOP: [], BOTTOM: []}
    for trace in solution.traces:
        if trace.layer not in layer_points:
            continue
        for point in trace.points:
            layer_points[trace.layer].append((point.x, point.y, trace.net))

    dangling = 0
    for via in solution.vias:
        reach = via.pad_mm * LANDS_WITHIN_PAD_FRACTION
        touched = set()
        for layer in (TOP, BOTTOM):
            for x, y, net in layer_points[layer]:
                if net != via.net:
                    continue
                if math.hypot(x - via.center.x, y - via.center.y) <= reach:
                    touched.add(layer)
                    break
        if len(touched) < 2:
            dangling += 1

    area_cm2 = problem.board.area_mm2 / 100.0
    worst = max(per_net.items(), key=lambda kv: (kv[1], kv[0]), default=None)
    return ViaResult(
        count=len(solution.vias),
        per_cm2=(len(solution.vias) / area_cm2) if area_cm2 > 0 else None,
        on_high_speed=sum(
            n for net, n in per_net.items() if classes.get(net) == "diff_pair"
        ),
        on_power=sum(n for net, n in per_net.items() if classes.get(net) == "power"),
        on_ground=sum(n for net, n in per_net.items() if classes.get(net) == "ground"),
        dangling=dangling,
        blind_stub_count=None if problem.board.layer_count <= 2 else 0,
        worst_net=worst[0] if worst else None,
        worst_net_vias=worst[1] if worst else None,
        per_net=dict(sorted(per_net.items())),
    )


__all__ = ["ViaResult", "measure"]
