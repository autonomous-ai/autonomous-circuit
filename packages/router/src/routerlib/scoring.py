"""The score, in the order that matters to a board that gets built.

Lexicographic, four tiers, and the order is not negotiable because it is the
order in which a defect costs money:

1. **completeness** — the fraction of nets fully connected. An unrouted net is
   a dead board; nothing below this can compensate for it.
2. **legality** — DRC violations, errors before warnings. A violation is a
   scrapped board, two weeks and about $85.
3. **quality** — via count, total copper length, differential-pair coupling,
   power-net width. In that order, and only among boards that are complete and
   legal.
4. **cost** — iterations spent. Wall-clock is recorded and never scored: it is
   a property of the machine, and scoring it is how the current router became
   nondeterministic.

Two scores are comparable only when their rulers match, so every score carries
:class:`Ruler` — the check-set hash, the fab profile, the toolchain, the
package version. A rate can improve because the routes got better or because
the ruler got shorter, and from the number alone you cannot tell which.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Sequence

from routerlib import connectivity as conn
from routerlib import drc as drc_mod
from routerlib.geometry import GridIndex, point_segment_distance, segment_gap
from routerlib.model import (
    Budget,
    DesignRules,
    RoutingProblem,
    RoutingSolution,
    Trace,
)

#: Version of the scoring contract itself. Bump when a tier changes meaning.
SCORER_VERSION = "1"

#: How far apart two halves of a differential pair may run and still count as
#: coupled: three times the design gap. Wider than that and the pair is two
#: wires that happen to share a name.
COUPLING_WINDOW_FACTOR = 3.0

#: Step along a trace when measuring coupling. 0.1mm is finer than any feature
#: we route and keeps the measurement cheap.
COUPLING_STEP_MM = 0.1


@dataclass(frozen=True)
class Ruler:
    """What a score was measured against. Never report one without the other."""

    scorer_version: str
    fab_profile: str
    clearance_gate_mm: float
    min_clearance_mm: float
    check_kinds: tuple[str, ...]
    coverage_gaps: tuple[str, ...]
    toolchain: dict = field(default_factory=dict)

    @property
    def hash(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "scorer": self.scorer_version,
                    "fab": self.fab_profile,
                    "gate": self.clearance_gate_mm,
                    "floor": self.min_clearance_mm,
                    "kinds": list(self.check_kinds),
                    "gaps": list(self.coverage_gaps),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:12]

    def line(self) -> str:
        return (
            f"ruler : {self.hash}, {len(self.check_kinds)} check kinds, "
            f"{self.fab_profile} @ {self.clearance_gate_mm:g}mm gate "
            f"— compare only against a run with the same hash"
        )


#: Every violation kind the scorer can emit. Part of the ruler: a kind quietly
#: dropped between runs reads as progress.
CHECK_KINDS: tuple[str, ...] = (
    "clearance",
    "dfm_annular_ring",
    "dfm_drill_size",
    "dfm_edge_clearance",
    "dfm_hole_clearance",
    "dfm_power_trace_width",
    "dfm_trace_width",
    "dfm_via_diameter",
    "keepout",
    "off_board",
    "short",
    "trace_edge",
    "via_in_pad",
)


def ruler_for(rules: DesignRules) -> Ruler:
    toolchain: dict = {}
    try:  # never let a version probe fail a score
        from circuitpy import toolchain as tc

        toolchain = dict(tc.versions())
    except Exception:  # noqa: BLE001
        toolchain = {}
    return Ruler(
        scorer_version=SCORER_VERSION,
        fab_profile=rules.profile_id,
        clearance_gate_mm=rules.clearance_gate_mm,
        min_clearance_mm=rules.min_clearance_mm,
        check_kinds=CHECK_KINDS,
        coverage_gaps=drc_mod.COVERAGE_GAPS,
        toolchain=toolchain,
    )


# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Quality:
    via_count: int
    copper_mm: float
    #: Fraction of differential-pair copper running beside its partner.
    #: ``None`` when the board has no pairs — not 1.0, because "perfect" and
    #: "not applicable" are different answers.
    diff_pair_coupling: float | None
    #: Fraction of power/ground copper at or above the power width.
    power_width_ratio: float | None
    #: Fraction of copper on the top layer. 0.5 is balanced.
    top_layer_ratio: float | None
    longest_net_mm: float

    def as_dict(self) -> dict:
        return asdict(self)


def _pair_coupling(
    problem: RoutingProblem, solution: RoutingSolution
) -> float | None:
    pairs = [
        (n.id, n.diff_partner)
        for n in problem.nets
        if n.net_class == "diff_pair" and n.diff_partner and n.id < n.diff_partner
    ]
    if not pairs:
        return None
    window = problem.rules.diff_pair_gap_mm * COUPLING_WINDOW_FACTOR
    by_net: dict[str, list[Trace]] = {}
    for trace in solution.traces:
        by_net.setdefault(trace.net, []).append(trace)

    total = 0.0
    coupled = 0.0
    for a_id, b_id in pairs:
        partner_segs = [
            (t.layer, p0.x, p0.y, p1.x, p1.y)
            for t in by_net.get(b_id, ())
            for p0, p1 in t.segments
        ]
        for trace in by_net.get(a_id, ()):
            for p0, p1 in trace.segments:
                length = p0.distance_to(p1)
                if length <= 0:
                    continue
                steps = max(1, int(math.ceil(length / COUPLING_STEP_MM)))
                step_len = length / steps
                for i in range(steps):
                    t = (i + 0.5) / steps
                    x = p0.x + (p1.x - p0.x) * t
                    y = p0.y + (p1.y - p0.y) * t
                    total += step_len
                    best = min(
                        (
                            point_segment_distance(x, y, sx0, sy0, sx1, sy1)
                            for layer, sx0, sy0, sx1, sy1 in partner_segs
                            if layer == trace.layer
                        ),
                        default=math.inf,
                    )
                    if best <= window:
                        coupled += step_len
    if total <= 0:
        return 0.0
    return coupled / total


def _power_width_ratio(
    problem: RoutingProblem, solution: RoutingSolution
) -> float | None:
    rails = {
        n.id for n in problem.nets if n.net_class in ("power", "ground")
    }
    total = 0.0
    wide = 0.0
    for trace in solution.traces:
        if trace.net not in rails:
            continue
        total += trace.length_mm
        if trace.width_mm >= problem.rules.power_trace_mm - 1e-9:
            wide += trace.length_mm
    if total <= 0:
        return None
    return wide / total


def quality_of(problem: RoutingProblem, solution: RoutingSolution) -> Quality:
    copper = solution.copper_length_mm
    top = sum(t.length_mm for t in solution.traces if t.layer == "top")
    by_net: dict[str, float] = {}
    for trace in solution.traces:
        by_net[trace.net] = by_net.get(trace.net, 0.0) + trace.length_mm
    return Quality(
        via_count=len(solution.vias),
        copper_mm=round(copper, 4),
        diff_pair_coupling=_pair_coupling(problem, solution),
        power_width_ratio=_power_width_ratio(problem, solution),
        top_layer_ratio=(round(top / copper, 4) if copper > 0 else None),
        longest_net_mm=round(max(by_net.values(), default=0.0), 4),
    )


# ---------------------------------------------------------------------------
# The score
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Score:
    instance: str
    router: str
    #: Tier 1
    completeness: float
    connected_nets: int
    routable_nets: int
    unconnected: tuple[str, ...]
    #: Tier 2
    errors: int
    warnings: int
    violations_by_kind: dict
    error_kinds: dict
    board_findings: tuple[dict, ...]
    #: Tier 3
    quality: Quality
    #: Tier 4
    iterations: int
    nodes_expanded: int
    wall_clock_s: float
    #: Bookkeeping
    ruler: Ruler
    fingerprint: str
    claimed_complete: bool
    orphan_copper: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def legal(self) -> bool:
        return self.errors == 0

    @property
    def clean(self) -> bool:
        """The bar: everything connected and nothing illegal."""
        return self.completeness >= 1.0 and self.errors == 0

    @property
    def claim_honest(self) -> bool:
        """Did the router's own ``complete`` flag tell the truth?"""
        return self.claimed_complete == (self.completeness >= 1.0)

    def key(self) -> tuple:
        """Lexicographic, lower is better. Wall-clock is deliberately absent."""
        q = self.quality
        return (
            round(1.0 - self.completeness, 9),
            self.errors,
            self.warnings,
            q.via_count,
            round(q.copper_mm, 3),
            round(1.0 - (q.diff_pair_coupling if q.diff_pair_coupling is not None else 1.0), 6),
            round(1.0 - (q.power_width_ratio if q.power_width_ratio is not None else 1.0), 6),
            self.iterations,
        )

    def line(self) -> str:
        q = self.quality
        pair = (
            "n/a" if q.diff_pair_coupling is None
            else f"{q.diff_pair_coupling * 100:.0f}%"
        )
        return (
            f"{self.instance:<28} {self.router:<18} "
            f"{self.completeness * 100:5.1f}% routed  "
            f"{self.errors:>3} err {self.warnings:>3} warn  "
            f"{q.via_count:>4} vias  {q.copper_mm:>8.1f}mm  "
            f"pair {pair:>4}  {self.iterations:>7} it  "
            f"({self.wall_clock_s:.1f}s)"
        )

    def as_dict(self) -> dict:
        return {
            "instance": self.instance,
            "router": self.router,
            "completeness": round(self.completeness, 6),
            "connectedNets": self.connected_nets,
            "routableNets": self.routable_nets,
            "unconnected": list(self.unconnected),
            "errors": self.errors,
            "warnings": self.warnings,
            "violationsByKind": self.violations_by_kind,
            "errorKinds": self.error_kinds,
            "boardFindings": [dict(f) for f in self.board_findings],
            "quality": self.quality.as_dict(),
            "iterations": self.iterations,
            "nodesExpanded": self.nodes_expanded,
            "wallClockS": round(self.wall_clock_s, 3),
            "fingerprint": self.fingerprint,
            "claimedComplete": self.claimed_complete,
            "claimHonest": self.claim_honest,
            "orphanCopper": list(self.orphan_copper),
            "clean": self.clean,
            "notes": list(self.notes),
            "measuredAgainst": {
                "rulerHash": self.ruler.hash,
                "scorerVersion": self.ruler.scorer_version,
                "fabProfile": self.ruler.fab_profile,
                "clearanceGateMm": self.ruler.clearance_gate_mm,
                "checkKinds": list(self.ruler.check_kinds),
                "coverageGaps": list(self.ruler.coverage_gaps),
                "toolchain": self.ruler.toolchain,
            },
        }


def score(
    problem: RoutingProblem,
    solution: RoutingSolution,
    *,
    use_pipeline: bool = True,
) -> Score:
    """Score one solution against one problem. Never raises on bad copper —
    a router that emits nonsense gets a bad score, not a traceback."""
    result = drc_mod.check(problem, solution, use_pipeline=use_pipeline)
    linked = conn.analyse(problem, solution)
    notes: list[str] = []
    if solution.complete and linked.completeness < 1.0:
        notes.append(
            f"router claimed complete but {len(linked.unconnected_nets)} net(s) "
            "are not connected — scored on the recomputed number"
        )
    if linked.orphan_copper:
        notes.append(
            f"{len(linked.orphan_copper)} trace(s) touch no pad of their own net"
        )
    return Score(
        instance=problem.id,
        router=solution.router,
        completeness=linked.completeness,
        connected_nets=len(linked.connected_nets),
        routable_nets=linked.routable_nets,
        unconnected=linked.unconnected_nets,
        errors=len(result.errors),
        warnings=len(result.warnings),
        violations_by_kind=result.by_kind(),
        error_kinds=result.error_counts(),
        board_findings=tuple(v.as_dict() for v in result.board_violations),
        quality=quality_of(problem, solution),
        iterations=solution.iterations,
        nodes_expanded=solution.nodes_expanded,
        wall_clock_s=solution.wall_clock_s,
        ruler=ruler_for(problem.rules),
        fingerprint=solution.fingerprint(),
        claimed_complete=solution.complete,
        orphan_copper=linked.orphan_copper,
        notes=tuple(notes),
    )


def better(a: Score, b: Score) -> Score:
    """The winner under the lexicographic order."""
    return a if a.key() <= b.key() else b


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeterminismResult:
    deterministic: bool
    fingerprints: tuple[str, ...]
    payload_hashes: tuple[str, ...]
    detail: str = ""


def determinism_check(
    router,
    problem: RoutingProblem,
    budget: Budget,
    *,
    runs: int = 2,
) -> DeterminismResult:
    """Run the router ``runs`` times and require byte-identical output.

    Two things are compared, because they fail differently:

    * the **fingerprint** — the copper, ids and timings stripped. A router that
      finds the same copper in a different order still passes.
    * the **serialised circuit.json bytes** — the actual artifact. A router that
      finds the same copper but mints a different id fails here, and it should:
      the pipeline hashes what it writes.

    Determinism is a scored property, not a nicety. A router that fails this
    cannot be compared to anything, including itself yesterday.
    """
    from routerlib.adapters import solution_to_elements

    fingerprints: list[str] = []
    payloads: list[str] = []
    for _ in range(runs):
        solution = router.route(problem, budget)
        fingerprints.append(solution.fingerprint())
        payloads.append(
            hashlib.sha256(
                json.dumps(
                    solution_to_elements(problem, solution),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
    same_copper = len(set(fingerprints)) == 1
    same_bytes = len(set(payloads)) == 1
    detail = ""
    if not same_copper:
        detail = "different copper between runs"
    elif not same_bytes:
        detail = "same copper, different serialised bytes (unstable ids or order)"
    return DeterminismResult(
        deterministic=same_copper and same_bytes,
        fingerprints=tuple(fingerprints),
        payload_hashes=tuple(payloads),
        detail=detail,
    )


__all__ = [
    "CHECK_KINDS",
    "DeterminismResult",
    "Quality",
    "Ruler",
    "SCORER_VERSION",
    "Score",
    "better",
    "determinism_check",
    "quality_of",
    "ruler_for",
    "score",
]
