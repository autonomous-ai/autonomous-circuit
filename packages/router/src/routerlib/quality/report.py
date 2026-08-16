"""One report, one ruler, six metrics.

:func:`measure` runs every metric over one routed board and returns a single
serialisable object. It builds the :class:`~routerlib.quality.common.GroundField`
once and hands it to the three metrics that need it, which is most of why the
whole benchmark scores in seconds.

**The ruler.** Every number here depends on a sampling step, a reference window,
a coupling window and a sheet resistance. Change any of them and the numbers
move without the boards moving — the exact failure the north star calls
target-anchoring. So :class:`QualityRuler` carries them all, hashes them, and
:meth:`QualityReport.ruler_line` prints the hash beside the score. Two quality
scores are comparable only when their ruler hashes match.

**These are scores, not gates.** Nothing in this package returns a severity, a
finding, or a boolean pass. ``fab.ready`` stays what it is: zero error-severity
findings plus independently verified gerbers. A soft metric wired into a hard
gate would be the worst available outcome — it would make the gate arguable, and
the only thing that makes the gate worth having is that it is not.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from routerlib.model import RoutingProblem, RoutingSolution

from routerlib.quality import detour as detour_mod
from routerlib.quality import diffpair as diffpair_mod
from routerlib.quality import loop_area as loop_mod
from routerlib.quality import power as power_mod
from routerlib.quality import reference as reference_mod
from routerlib.quality import vias as vias_mod
from routerlib.quality.common import (
    REFERENCE_MM,
    SAMPLE_STEP_MM,
    SHEET_R_OHM_PER_SQ,
    VIA_PLATING_MM,
    GroundField,
)

#: Bumped whenever a metric's definition changes. It is in the ruler hash, so
#: an old number and a new one can never be silently compared.
QUALITY_VERSION = "1"

#: What these metrics knowingly do not see. Shipped with every report, for the
#: same reason ``routerlib.drc.COVERAGE_GAPS`` is: a good score must never read
#: as "everything was looked at".
COVERAGE_GAPS: tuple[str, ...] = (
    "return current is modelled as flowing in the nearest ground copper at "
    "every point, not along the path back to the source — optimistic wherever "
    "ground is routed rather than poured",
    "a poured plane is one equipotential node in the power model, so plane "
    "resistance reads as zero",
    "no dielectric constant, no impedance: loop area is geometry, not a "
    "field solve",
    "crossings are counted between routed segments only — a crossing resolved "
    "by going around a pad is invisible",
    "voltage drop needs currents the routing problem does not carry; without "
    "them only resistance is reported",
    "copper pours are not carved by the traces that cross them, so a trace on "
    "the pour layer reads as referenced by its own pour",
)


@dataclass(frozen=True)
class QualityRuler:
    version: str
    sample_step_mm: float
    reference_mm: float
    coupling_window_factor: float
    diff_pair_gap_mm: float
    skew_budget_mm: float
    sheet_r_ohm_per_sq: float
    via_plating_mm: float
    board_thickness_mm: float
    coverage_gaps: tuple[str, ...] = COVERAGE_GAPS

    @property
    def hash(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "v": self.version,
                    "step": self.sample_step_mm,
                    "ref": self.reference_mm,
                    "couple": self.coupling_window_factor,
                    "gap": self.diff_pair_gap_mm,
                    "skew": self.skew_budget_mm,
                    "sheet": round(self.sheet_r_ohm_per_sq, 12),
                    "plating": self.via_plating_mm,
                    "thickness": self.board_thickness_mm,
                    "gaps": list(self.coverage_gaps),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:12]

    def as_dict(self) -> dict:
        return {
            "qualityRulerHash": self.hash,
            "version": self.version,
            "sampleStepMm": self.sample_step_mm,
            "referenceMm": self.reference_mm,
            "couplingWindowFactor": self.coupling_window_factor,
            "diffPairGapMm": self.diff_pair_gap_mm,
            "skewBudgetMm": self.skew_budget_mm,
            "sheetROhmPerSq": round(self.sheet_r_ohm_per_sq, 9),
            "viaPlatingMm": self.via_plating_mm,
            "boardThicknessMm": self.board_thickness_mm,
            "coverageGaps": list(self.coverage_gaps),
        }


@dataclass(frozen=True)
class QualityReport:
    instance: str
    router: str
    ruler: QualityRuler
    loop: loop_mod.LoopAreaResult
    reference: reference_mod.ReferenceResult
    diff_pair: diffpair_mod.DiffPairResult
    power: power_mod.PowerResult
    detour: detour_mod.DetourResult
    vias: vias_mod.ViaResult
    notes: tuple[str, ...] = field(default_factory=tuple)

    def ruler_line(self) -> str:
        return (
            f"quality ruler : {self.ruler.hash}, v{self.ruler.version}, "
            f"{self.ruler.sample_step_mm:g}mm step, "
            f"{self.ruler.reference_mm:g}mm reference window "
            f"— compare only against a run with the same hash"
        )

    def summary(self) -> dict:
        """The one-row-per-board view the calibration table is built from."""
        return {
            "instance": self.instance,
            "router": self.router,
            "loopAreaMm2": _r(self.loop.total_loop_area_mm2, 1),
            "meanReturnMm": _r(self.loop.mean_return_mm, 3),
            "worstNetLoopMm2": _r(self.loop.worst_net_loop_area_mm2, 1),
            "referenceMode": self.reference.mode,
            "referencedFraction": _r(self.reference.referenced_fraction, 4),
            "gapCrossings": self.reference.gap_crossings,
            "pairCoupled": _r(self.diff_pair.coupled_fraction, 4),
            "pairGapCv": _r(self.diff_pair.worst_gap_cv, 4),
            "pairReferenced": _r(self.diff_pair.referenced_fraction, 4),
            "pairSkewMm": _r(self.diff_pair.worst_skew_mm, 3),
            "powerWorstMohm": _r(self.power.worst_path_mohm, 2),
            #: ``None`` unless the caller supplied currents — resistance is a
            #: property of the copper, volts need to know what is attached.
            "powerWorstDropMv": _r(self.power.worst_drop_mv, 2),
            "powerMaxDaisy": self.power.max_daisy_depth,
            "powerChainedFraction": _r(self.power.chained_pad_fraction, 4),
            "detourRatio": _r(self.detour.detour_ratio, 4),
            "hpwlRatio": _r(self.detour.hpwl_ratio, 4),
            "worstDetourRatio": _r(self.detour.worst_detour_ratio, 3),
            "crossings": self.detour.crossings,
            "selfCrossings": self.detour.self_crossings,
            "bends": self.detour.bends,
            "vias": self.vias.count,
            "viasOnHighSpeed": self.vias.on_high_speed,
            "danglingVias": self.vias.dangling,
            "scoredNets": self.detour.scored_nets,
            "skippedUnconnectedNets": self.detour.skipped_unconnected_nets,
        }

    def as_dict(self) -> dict:
        return {
            "instance": self.instance,
            "router": self.router,
            "measuredAgainst": self.ruler.as_dict(),
            "summary": self.summary(),
            "loopArea": self.loop.as_dict(),
            "reference": self.reference.as_dict(),
            "diffPair": self.diff_pair.as_dict(),
            "power": self.power.as_dict(),
            "detour": self.detour.as_dict(),
            "vias": self.vias.as_dict(),
            "notes": list(self.notes),
        }


def _r(value, digits: int):
    return None if value is None else round(value, digits)


def ruler_for(problem: RoutingProblem, step_mm: float = SAMPLE_STEP_MM) -> QualityRuler:
    return QualityRuler(
        version=QUALITY_VERSION,
        sample_step_mm=step_mm,
        reference_mm=REFERENCE_MM,
        coupling_window_factor=diffpair_mod.COUPLING_WINDOW_FACTOR,
        diff_pair_gap_mm=problem.rules.diff_pair_gap_mm,
        skew_budget_mm=diffpair_mod.USB_SKEW_BUDGET_MM,
        sheet_r_ohm_per_sq=SHEET_R_OHM_PER_SQ,
        via_plating_mm=VIA_PLATING_MM,
        board_thickness_mm=float(problem.board.thickness_mm),
    )


def measure(
    problem: RoutingProblem,
    solution: RoutingSolution,
    *,
    currents_ma: dict[str, float] | None = None,
    sources: dict[str, str] | None = None,
    step_mm: float = SAMPLE_STEP_MM,
) -> QualityReport:
    """Score one routed board on all six quality metrics.

    Pure: same problem and same copper give the same report, byte for byte.
    """
    ground = GroundField(problem, solution)
    notes: list[str] = []
    if not ground.present:
        notes.append(
            "no ground copper on this board — loop area, reference continuity "
            "and pair reference are not applicable rather than perfect"
        )
    if currents_ma is None:
        notes.append(
            "no per-net currents supplied, so power reports resistance only"
        )
    return QualityReport(
        instance=problem.id,
        router=solution.router,
        ruler=ruler_for(problem, step_mm),
        loop=loop_mod.measure(problem, solution, ground=ground, step_mm=step_mm),
        reference=reference_mod.measure(
            problem, solution, ground=ground, step_mm=step_mm
        ),
        diff_pair=diffpair_mod.measure(
            problem, solution, ground=ground, step_mm=step_mm
        ),
        power=power_mod.measure(
            problem, solution, currents_ma=currents_ma, sources=sources, ground=ground
        ),
        detour=detour_mod.measure(problem, solution),
        vias=vias_mod.measure(problem, solution),
        notes=tuple(notes),
    )


__all__ = [
    "COVERAGE_GAPS",
    "QUALITY_VERSION",
    "QualityReport",
    "QualityRuler",
    "measure",
    "ruler_for",
]
