"""Differential pairs, past skew: coupled, constant-gap, and over a reference.

The EE review of 2026-08-15 named this one directly — *"USB D+/D- must run as a
differential pair: parallel, constant gap, over a ground reference."* Three
clauses. Two packages already measure parts of it and neither measures all
three as one score:

======================================  ===============================
``verifylib.netclass._diff_pair_skew``  length match (USB 2.0, 3.8mm)
``verifylib.netclass._diff_pair_coupling``  coupled fraction, 4x trace width
``verifylib.netclass._diff_pair_reference``  ground on the far layer, 0.5mm
``routerlib.scoring._pair_coupling``    coupled fraction, 3x design gap
======================================  ===============================

**What this adds rather than repeats.** The coupled fraction here is the same
definition ``routerlib.scoring`` already ranks routers on — window at
``COUPLING_WINDOW_FACTOR`` times ``rules.diff_pair_gap_mm`` — and
``tests/test_quality.py::test_coupled_fraction_matches_scoring`` fails if the
two ever disagree, so this is an extension of that number and not a second
opinion about it. Reference coverage reuses
:class:`~routerlib.quality.common.GroundField` at the same 0.5mm window
``verifylib`` uses. The skew budget is ``verifylib``'s 3.8mm.

Three things are new, and each is something a router can be told to optimise:

``gap_cv``
    the *constant* in "constant gap". Standard deviation of the coupled gap
    over its mean. A pair routed as a pair sits near zero; two wires that
    happen to converge and diverge sit high. Coupled fraction cannot see the
    difference — a pair that swings between 0.2mm and 0.6mm is 100% coupled.
``via_asymmetry``
    ``|vias on P - vias on N|``. Vias are the impedance discontinuities on the
    pair, and an unmatched one converts differential mode to common mode, which
    is the part that radiates.
``referenced_fraction``
    per pair rather than per board, so a pair that is fine over the connector
    and naked across the middle is not averaged into looking acceptable.

**No pass mark is invented.** USB 2.0's 3.8mm intra-pair skew is a real budget
and is cited. ``gap_cv`` and ``via_asymmetry`` have no standard, so they are
reported raw and boards are ranked against each other.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from routerlib.geometry import point_segment_distance
from routerlib.model import RoutingProblem, RoutingSolution

from routerlib.quality.common import SAMPLE_STEP_MM, GroundField, walk

#: How far apart the two legs may run and still count as coupled, as a multiple
#: of the design gap. Copied from ``routerlib.scoring.COUPLING_WINDOW_FACTOR``
#: by import below, never by transcription.
from routerlib.scoring import COUPLING_WINDOW_FACTOR  # noqa: E402

#: USB 2.0 intra-pair skew budget: 150ps at ~6.7ps/mm on FR-4 microstrip. The
#: same constant as ``verifylib.netclass.USB_SKEW_BUDGET_MM``; the test pins
#: them equal when ``verifylib`` is importable.
USB_SKEW_BUDGET_MM = 3.8


@dataclass(frozen=True)
class PairQuality:
    pair: tuple[str, str]
    #: Fraction of the P leg running within the coupling window of N.
    coupled_fraction: float
    gap_mean_mm: float | None
    gap_stdev_mm: float | None
    #: stdev / mean over the coupled samples. The "constant gap" clause.
    gap_cv: float | None
    referenced_fraction: float | None
    length_p_mm: float
    length_n_mm: float
    skew_mm: float
    skew_budget_mm: float
    vias_p: int
    vias_n: int

    @property
    def via_asymmetry(self) -> int:
        return abs(self.vias_p - self.vias_n)

    def as_dict(self) -> dict:
        return {
            "pair": list(self.pair),
            "coupledFraction": round(self.coupled_fraction, 4),
            "gapMeanMm": _r(self.gap_mean_mm, 4),
            "gapStdevMm": _r(self.gap_stdev_mm, 4),
            "gapCv": _r(self.gap_cv, 4),
            "referencedFraction": _r(self.referenced_fraction, 4),
            "lengthPMm": round(self.length_p_mm, 3),
            "lengthNMm": round(self.length_n_mm, 3),
            "skewMm": round(self.skew_mm, 3),
            "skewBudgetMm": self.skew_budget_mm,
            "skewOverBudget": self.skew_mm > self.skew_budget_mm,
            "viasP": self.vias_p,
            "viasN": self.vias_n,
            "viaAsymmetry": self.via_asymmetry,
        }


@dataclass(frozen=True)
class DiffPairResult:
    #: ``None`` everywhere when the board has no pairs. Not 1.0 — "perfect" and
    #: "not applicable" are different answers, the same rule
    #: ``routerlib.scoring.Quality`` already follows.
    pair_count: int
    coupled_fraction: float | None
    worst_coupled_fraction: float | None
    worst_gap_cv: float | None
    referenced_fraction: float | None
    worst_skew_mm: float | None
    pairs_over_skew_budget: int
    total_via_asymmetry: int
    pairs: tuple[PairQuality, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "pairCount": self.pair_count,
            "coupledFraction": _r(self.coupled_fraction, 4),
            "worstCoupledFraction": _r(self.worst_coupled_fraction, 4),
            "worstGapCv": _r(self.worst_gap_cv, 4),
            "referencedFraction": _r(self.referenced_fraction, 4),
            "worstSkewMm": _r(self.worst_skew_mm, 3),
            "pairsOverSkewBudget": self.pairs_over_skew_budget,
            "totalViaAsymmetry": self.total_via_asymmetry,
            "pairs": [p.as_dict() for p in self.pairs],
        }


def _r(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def pairs_of(problem: RoutingProblem) -> list[tuple[str, str]]:
    """Confirmed differential pairs, each listed once, in a stable order.

    ``routerlib.adapters.classify_nets`` establishes a pair by the polarity
    swap test and keeps only symmetric agreement, so this only has to
    de-duplicate.
    """
    return sorted(
        {
            tuple(sorted((n.id, n.diff_partner)))
            for n in problem.nets
            if n.net_class == "diff_pair" and n.diff_partner
        }
    )


def measure(
    problem: RoutingProblem,
    solution: RoutingSolution,
    *,
    ground: GroundField | None = None,
    step_mm: float = SAMPLE_STEP_MM,
) -> DiffPairResult:
    pairs = pairs_of(problem)
    if not pairs:
        return DiffPairResult(
            pair_count=0,
            coupled_fraction=None,
            worst_coupled_fraction=None,
            worst_gap_cv=None,
            referenced_fraction=None,
            worst_skew_mm=None,
            pairs_over_skew_budget=0,
            total_via_asymmetry=0,
        )

    ground = ground or GroundField(problem, solution)
    window = problem.rules.diff_pair_gap_mm * COUPLING_WINDOW_FACTOR

    by_net: dict[str, list] = {}
    for trace in solution.traces:
        by_net.setdefault(trace.net, []).append(trace)
    for net in by_net:
        by_net[net].sort(key=lambda t: (t.layer, t.id))
    vias_by_net: dict[str, int] = {}
    for via in solution.vias:
        vias_by_net[via.net] = vias_by_net.get(via.net, 0) + 1

    out: list[PairQuality] = []
    for a_id, b_id in pairs:
        partner_segs = [
            (t.layer, p0.x, p0.y, p1.x, p1.y)
            for t in by_net.get(b_id, ())
            for p0, p1 in t.segments
        ]
        total = coupled = referenced = 0.0
        gaps: list[tuple[float, float]] = []  # (gap, weight)
        for step in walk(by_net.get(a_id, ()), step_mm):
            total += step.length_mm
            best = min(
                (
                    point_segment_distance(step.x, step.y, sx0, sy0, sx1, sy1)
                    for layer, sx0, sy0, sx1, sy1 in partner_segs
                    if layer == step.layer
                ),
                default=math.inf,
            )
            if best <= window:
                coupled += step.length_mm
                gaps.append((best, step.length_mm))
            if ground.present and ground.referenced(step.x, step.y, step.layer):
                referenced += step.length_mm

        length_p = sum(t.length_mm for t in by_net.get(a_id, ()))
        length_n = sum(t.length_mm for t in by_net.get(b_id, ()))
        mean = stdev = cv = None
        if gaps:
            weight = sum(w for _, w in gaps)
            mean = sum(g * w for g, w in gaps) / weight
            var = sum(w * (g - mean) ** 2 for g, w in gaps) / weight
            stdev = math.sqrt(max(var, 0.0))
            cv = (stdev / mean) if mean > 0 else None
        out.append(
            PairQuality(
                pair=(a_id, b_id),
                coupled_fraction=(coupled / total) if total > 0 else 0.0,
                gap_mean_mm=mean,
                gap_stdev_mm=stdev,
                gap_cv=cv,
                referenced_fraction=(
                    (referenced / total) if (total > 0 and ground.present) else None
                ),
                length_p_mm=length_p,
                length_n_mm=length_n,
                skew_mm=abs(length_p - length_n),
                skew_budget_mm=USB_SKEW_BUDGET_MM,
                vias_p=vias_by_net.get(a_id, 0),
                vias_n=vias_by_net.get(b_id, 0),
            )
        )

    routed = [p for p in out if p.length_p_mm > 0]
    weight = sum(p.length_p_mm for p in routed)
    referenced_pairs = [p for p in routed if p.referenced_fraction is not None]
    return DiffPairResult(
        pair_count=len(out),
        coupled_fraction=(
            sum(p.coupled_fraction * p.length_p_mm for p in routed) / weight
            if weight else None
        ),
        worst_coupled_fraction=min((p.coupled_fraction for p in routed), default=None),
        worst_gap_cv=max((p.gap_cv for p in routed if p.gap_cv is not None), default=None),
        referenced_fraction=(
            sum(p.referenced_fraction * p.length_p_mm for p in referenced_pairs)
            / sum(p.length_p_mm for p in referenced_pairs)
            if referenced_pairs else None
        ),
        worst_skew_mm=max((p.skew_mm for p in routed), default=None),
        pairs_over_skew_budget=sum(
            1 for p in routed if p.skew_mm > p.skew_budget_mm
        ),
        total_via_asymmetry=sum(p.via_asymmetry for p in out),
        pairs=tuple(out),
    )


__all__ = [
    "COUPLING_WINDOW_FACTOR",
    "DiffPairResult",
    "PairQuality",
    "USB_SKEW_BUDGET_MM",
    "measure",
    "pairs_of",
]
