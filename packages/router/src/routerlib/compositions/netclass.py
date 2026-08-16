"""Net-class decomposition: route by class, each class to the expert for it.

Route the planed nets first (a pad drops a via into the pour and there is
nothing to route), then power at the width its current demands, then diff pairs
*together* as coupled pairs, then everything else to the strongest general
family. ``plane-and-classes`` already applies this idea once, monolithically,
with the order baked into a class constant; here the stages are pulled apart so
each one's router is a string in a plan and the order is data. A plan is a tuple
of :class:`ClassStage`; :data:`PLANS` holds the named ones.

Measured on all 16 benchmark instances, budget ``2M iterations / 20M nodes /
seed 0``, two runs each, ruler ``e1ee2a5623d0`` — and the answer is that
**three of the four stages are worth nothing and one of them is worth a lot**:

======================  ============  =====  =========  ===============================
plan                    mean routed   clean  coupling   what it changes
======================  ============  =====  =========  ===============================
``monolithic``          92.5%         7/16    3.1%      the control: one router, no
                                                        decomposition at all
``plane-only``          92.5%         7/16    2.6%      + a plane stage
``pairs`` *(default)*   92.4%         7/16   27.5%      + a diff-pair stage
``pairs-general``       92.0%         7/16    8.8%      …that stage to the general
                                                        router instead of the expert
``brief``               74.7%         3/16   27.3%      + a power stage
``brief-plane-last``    74.9%         3/16   30.2%      …with the plane stage last
``brief-pair-general``  75.6%         3/16    1.4%      both of the above
======================  ============  =====  =========  ===============================

Coupling is the mean over the nine instances that have a differential pair.
Every plan scored **0 harness errors and 16/16 deterministic**, so the only
columns that move are completeness and coupling.

**The diff-pair stage is the whole win.** It takes mean coupling from 3.1% to
27.5% — 0.8% → 53.0% on ``harness-puck``, 5.2% → 30.7% on
``matrix-rp2040-core__usb-c-data``, 4.5% → 37.7% on ``terminal-keyboard`` — and
costs one net out of 380. It pays only when the stage goes to the *pair
expert*: with the same three stages and only that router swapped, coupling
falls to 8.8% and completeness with it. This is the one thing the EE review
asked for on USB D+/D− and the one thing a general router structurally cannot
do, because a pair is two nets to it.

**The power stage is a 17.7-point loss and the single largest effect here.**
Same routers, same budget, one thing different — power goes first, alone. Nine
of sixteen instances lose nets and five of them lose forty points:
``matrix-ldo-3v3__usb-c-power`` goes from 100% to 60%. The cause is visible in
the stage table: asked for two rails and nothing else, the general router takes
the two shortest top-layer paths, spends no vias, and cuts the board in half;
the three nets that follow cannot cross. Routing every net makes the next one
harder, and a stage boundary makes that worse in both directions — the early
stage cannot see the nets it is about to strand, and the late stage cannot rip
up the copper stranding it.

**The plane stage deletes work that was already being deleted.** 92.5% against
92.5%, identical on all sixteen instances. The argument for it is sound — a
poured ground is not a routing problem, and it is ~30% of the pads — but the
general family we run, ``maze-astar``, already treats a pour as a net rather
than as obstacles. The stage would matter against a router that does not, which
is exactly the shipped autorouter's defect (byte-identical copper with and
without a plane) and exactly not ``maze-astar``'s. It is kept in the default
plan because it costs nothing and it is insurance against the general slot
being filled by a family that is plane-blind.

**Plane first or plane last is a non-question**: 74.7% against 74.9%.

For scale, the relay in ``routerlib.portfolio`` re-measured on this same ruler
is **90.6%**, below the single-router control. Its published 98.0% was measured
before the pad model was corrected, with ``pathfinder-negotiated`` as the lead —
the family that lost 12.8 points of completeness when its workspace stopped
lying to it.

**Two things make a composition different from four routers run in sequence**,
and both of them are the reason this is a module rather than a shell loop:

*Each stage sees the previous stages' copper as obstacles.* A stage is handed a
problem whose ``nets`` are only its own class and whose ``existing_traces`` /
``existing_vias`` are everything placed so far, so it plans against the real
board. The composition therefore cannot invent a clearance violation that no
single stage could see — the same guarantee the relay has, and the reason
neither is a merge of independent runs.

*Copper ids are namespaced per stage.* Two families that both mint ``v0`` are
common — ``plane-and-classes`` and ``maze-astar`` do exactly that — and the
scorer's union-find is keyed on ``(copper id, layer)``. Un-namespaced, two vias
called ``v0`` on two different nets become one node, the two nets' pads merge
into one component, and the composition reports completeness it did not
achieve. Every stage's copper is renamed ``<stage label>.<id>`` on the way out.

**What the numbers here are not.** Nothing in this module is a claim about
fab-readiness — no plan clears the bar of 100% routed and zero findings on more
than the seven instances the control already clears. A plan that does not help
is a result to report rather than a plan to bury, which is why three of the six
rows above are kept in :data:`PLANS` and refuted in :data:`REJECTED_STAGES`
rather than deleted. The stage table exists so the limiting stage is visible: a
composition whose signal stage leaves seven nets open is not fixed by swapping
the plane router.

Every row above is in
``benchmarks/compositions/netclass-2026-08-16.json``, per instance and per
stage. To re-run it::

    python3.12 packages/router/scripts/netclass_suite.py plans
    python3.12 packages/router/scripts/netclass_suite.py run \\
        --instance harness-puck --plan pairs
    python3.12 packages/router/scripts/netclass_suite.py suite --runs 2 \\
        --plan pairs,plane-only,monolithic,brief --out work/netclass/suite.json
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from routerlib import connectivity as conn
from routerlib.compositions.registry import Registry
from routerlib.model import (
    Budget,
    Net,
    RoutingProblem,
    RoutingSolution,
    Trace,
    Via,
)

#: Every net class the contract defines. A plan that names anything else is
#: rejected at construction: a silent typo in a class name is a stage that
#: claims nothing and a bucket that quietly falls through to the catch-all.
NET_CLASSES: frozenset[str] = frozenset(
    {"signal", "power", "ground", "diff_pair"}
)


# ---------------------------------------------------------------------------
# A stage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassStage:
    """One class of nets, and the router that gets them.

    ``label`` names the stage in the report and namespaces its copper ids, so
    it has to be unique within a plan and stable across runs — it is part of the
    serialised output.

    A stage claims a net when **every** filter it sets is satisfied:

    ``classes``
        the net's ``net_class`` is in this tuple. Empty means "any class".
    ``planed``
        ``True`` claims only nets that own a poured plane, ``False`` only nets
        that do not, ``None`` does not care. This is the distinction that makes
        the plane stage possible at all: "ground" and "ground with a pour" are
        different problems, and only the second one is free.
    ``rest``
        claims whatever the earlier stages did not. At most one per plan, and it
        must be last, because a catch-all in the middle makes every stage after
        it dead code.
    """

    label: str
    router: str
    classes: tuple[str, ...] = ()
    planed: bool | None = None
    rest: bool = False

    def __post_init__(self) -> None:
        unknown = sorted(set(self.classes) - NET_CLASSES)
        if unknown:
            raise ValueError(
                f"stage {self.label!r} names unknown net class(es) "
                f"{unknown} (have: {', '.join(sorted(NET_CLASSES))})"
            )
        if self.rest and (self.classes or self.planed is not None):
            raise ValueError(
                f"stage {self.label!r} is a catch-all and also filters; a "
                "catch-all takes what is left by definition"
            )

    def claims(self, net: Net, planed: bool) -> bool:
        if self.rest:
            return True
        if self.classes and net.net_class not in self.classes:
            return False
        if self.planed is not None and self.planed != planed:
            return False
        return True

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def validate_plan(plan: Sequence[ClassStage]) -> tuple[ClassStage, ...]:
    """A plan is data, so it is checked once rather than trusted everywhere."""
    if not plan:
        raise ValueError("a plan needs at least one stage")
    labels = [stage.label for stage in plan]
    duplicates = sorted({name for name in labels if labels.count(name) > 1})
    if duplicates:
        raise ValueError(
            f"stage labels must be unique — they namespace copper ids and two "
            f"stages sharing one merge in the scorer's union-find: {duplicates}"
        )
    for index, stage in enumerate(plan[:-1]):
        if stage.rest:
            raise ValueError(
                f"catch-all stage {stage.label!r} is at position {index} of "
                f"{len(plan)}; every stage after it would be dead"
            )
    return tuple(plan)


# ---------------------------------------------------------------------------
# The plans
# ---------------------------------------------------------------------------

#: The strongest general family on the corrected ruler. ``maze-astar`` pays
#: 0.2 points of completeness for a true pad model and goes from 1 clean
#: instance to 7; ``pathfinder-negotiated``, which led under the old ruler, pays
#: 12.8 points for the same zero errors. See ``docs/architecture/routing.md``,
#: "Told the truth, they route legally".
GENERAL = "maze-astar"

#: The plane expert, and the pair expert. Both jobs land on the same family for
#: the same reason: it is the only one that models a class rather than a wire.
PLANE_EXPERT = "plane-and-classes"
PAIR_EXPERT = "plane-and-classes"


def plan_for(
    *,
    general: str = GENERAL,
    plane: str = PLANE_EXPERT,
    pair: str = PAIR_EXPERT,
    power: str | None = None,
    plane_last: bool = False,
    ground_stage: bool = False,
    pair_stage: bool = True,
) -> tuple[ClassStage, ...]:
    """Build a plan. Every stage's router is an argument, which is the point.

    ``power`` defaults to ``None`` and that is a measurement, not a taste:
    a power stage costs 17.7 points of mean completeness on this benchmark
    (92.4% → 74.7%) with every other stage held fixed. ``power=GENERAL``
    reproduces the ``brief`` plan.

    ``plane_last`` moves the plane stage to the end. It is exposed because the
    intuition and one earlier measurement disagree — a stitching via is the
    cheapest object on a board, but there is one per ground pad (40 on
    hydrate-coaster) and placed first they are 40 obstacles — and because at
    stage granularity the question turns out not to matter: 74.7% first against
    74.9% last.

    ``ground_stage`` gives *unplaned* ground its own stage before the catch-all.
    Off, and unmeasured: it is the same shape as the power stage, which is the
    one thing here that is known to hurt.
    """
    stages: list[ClassStage] = []
    plane_stage = ClassStage("plane", plane, planed=True)
    if not plane_last:
        stages.append(plane_stage)
    if power:
        stages.append(ClassStage("power", power, classes=("power",), planed=False))
    if pair_stage:
        stages.append(
            ClassStage("pair", pair, classes=("diff_pair",), planed=False)
        )
    if ground_stage:
        stages.append(ClassStage("ground", general, classes=("ground",), planed=False))
    if plane_last:
        stages.append(plane_stage)
    stages.append(ClassStage("rest", general, rest=True))
    return validate_plan(stages)


#: Planes, then pairs to the pair expert, then everything else to the general
#: family. Three stages rather than the four the brief asks for, because the
#: fourth was measured and it costs 17.7 points — see :data:`REJECTED_STAGES`.
DEFAULT_PLAN: tuple[ClassStage, ...] = plan_for()

#: Named plans. Every one of these has a row in :data:`MEASURED`; a plan with
#: no measurement behind it is a guess wearing a plan's clothes, and this
#: benchmark is small enough that the two look identical.
PLANS: Mapping[str, tuple[ClassStage, ...]] = {
    "pairs": DEFAULT_PLAN,
    #: Only the stage that was supposed to delete work.
    "plane-only": plan_for(pair_stage=False),
    #: The control. One stage, one router, no decomposition — so a plan that
    #: does not beat this one has bought nothing, and the suite says so against
    #: the same ruler and the same budget rather than against a number copied
    #: out of another run.
    "monolithic": (ClassStage("all", GENERAL, rest=True),),
    #: What the brief asks for, kept because it is the thing that was measured.
    "brief": plan_for(power=GENERAL),
    "brief-plane-last": plan_for(power=GENERAL, plane_last=True),
    "brief-pair-general": plan_for(power=GENERAL, pair=GENERAL),
    "pairs-general": plan_for(pair=GENERAL),
}

#: What each plan scored. 16 instances, ``2M iterations / 20M nodes / seed 0``,
#: two runs per cell, ruler ``e1ee2a5623d0``, ``packages/router`` at ``a0a12c1``.
#: ``coupling`` is the mean differential-pair coupling over the nine instances
#: that have a pair. Every plan scored 0 harness errors and 16/16 deterministic.
#:
#: This is here rather than in a report because the next agent will otherwise
#: re-derive "power needs its own stage" from the same intuition that produced
#: it the first time. A tried-and-failed stage is worth more than an untried one.
MEASURED: Mapping[str, dict] = {
    "monolithic": {"mean": 0.925, "clean": 7, "coupling": 0.031},
    "plane-only": {"mean": 0.925, "clean": 7, "coupling": 0.026},
    "pairs": {"mean": 0.924, "clean": 7, "coupling": 0.275},
    "pairs-general": {"mean": 0.920, "clean": 7, "coupling": 0.088},
    "brief": {"mean": 0.747, "clean": 3, "coupling": 0.273},
    "brief-plane-last": {"mean": 0.749, "clean": 3, "coupling": 0.302},
    "brief-pair-general": {"mean": 0.756, "clean": 3, "coupling": 0.014},
}

#: The ruler every number in :data:`MEASURED` was taken against. Two scores are
#: comparable only when their hashes match; a run against a different check set
#: is a new baseline, not an improvement.
MEASURED_RULER = "e1ee2a5623d0"

#: Stages that were built, measured and are false. Kept, not deleted, and
#: pinned by a test — the same discipline as ``portfolio.REJECTED_RULES``.
REJECTED_STAGES: tuple[tuple[str, str], ...] = (
    (
        "power gets its own stage, before signals",
        "92.4% -> 74.7% mean completeness over 16 instances with every other "
        "stage held fixed, 7/16 clean -> 3/16. Five instances drop 40 points "
        "(matrix-ldo-3v3__usb-c-power: 100% -> 60%). Asked for two rails and "
        "nothing else, the general router takes the two shortest top-layer "
        "paths, spends no vias, and cuts the board; the nets that follow "
        "cannot cross and no later stage may rip up",
    ),
    (
        "the plane stage removes ~30% of the problem",
        "true of the problem and worth nothing here: 92.5% with the stage and "
        "92.5% without, identical on all 16 instances. maze-astar, the general "
        "family, already models a pour as a net. The stage is insurance "
        "against a plane-blind general router, not a source of completeness",
    ),
    (
        "plane vias belong first, before the board fills up",
        "74.7% first against 74.9% last, 16 instances. At stage granularity "
        "the ordering does not move the number; the earlier 80.1%-vs-85.2% "
        "measurement was inside plane-and-classes, where the plane job "
        "competes with that family's own net ordering",
    ),
    (
        "any router can have the diff-pair stage",
        "same three stages, only the pair stage's router swapped: coupling "
        "27.5% -> 8.8% and completeness 92.4% -> 92.0%. Inside the four-stage "
        "brief plan the same swap reads 27.3% -> 1.4%. A pair is two nets to a "
        "general router; the stage is worth having only because the expert "
        "routes the second half into a corridor beside the first",
    ),
)


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Partition:
    """Which nets each stage was given, plus the ones nobody claimed."""

    buckets: tuple[tuple[str, tuple[str, ...]], ...]
    unclaimed: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "buckets": {label: list(ids) for label, ids in self.buckets},
            "unclaimed": list(self.unclaimed),
        }


def partition(problem: RoutingProblem, plan: Sequence[ClassStage]) -> Partition:
    """Every routable net to the first stage that claims it.

    First-match rather than best-match, because a net routed twice is copper
    placed twice, and two stages both believing they own a net is the single
    way this composition could produce a short with itself.

    Only *routable* nets are partitioned. A one-pad net is already connected;
    handing it to a stage would inflate that stage's completeness for free,
    which is the same reason the scorer leaves it out of the denominator.
    """
    planed_nets = {plane.net for plane in problem.planes}
    buckets: dict[str, list[str]] = {stage.label: [] for stage in plan}
    unclaimed: list[str] = []
    for net in problem.routable_nets:
        for stage in plan:
            if stage.claims(net, net.id in planed_nets):
                buckets[stage.label].append(net.id)
                break
        else:
            unclaimed.append(net.id)
    return Partition(
        buckets=tuple((stage.label, tuple(buckets[stage.label])) for stage in plan),
        unclaimed=tuple(unclaimed),
    )


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageReport:
    """What one stage was asked for and what it delivered.

    ``connected`` is measured **at the end of the run**, not at the end of the
    stage. The two are the same number today — the buckets are disjoint and no
    later stage can join an earlier stage's pads — and measuring it at the end
    anyway is cheap insurance against that stopping being true silently.
    """

    label: str
    router: str
    asked: int
    connected: int
    completeness: float
    vias: int
    copper_mm: float
    seconds: float
    #: ``ran`` / ``empty`` (no nets in this class) / ``missing`` (router not in
    #: the registry) / ``raised`` (the family died and cost its stage).
    status: str = "ran"
    detail: str = ""

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)

    def line(self) -> str:
        pct = "  n/a" if not self.asked else f"{self.completeness * 100:5.1f}%"
        return (
            f"    {self.label:<8} {self.router:<22} asked={self.asked:>3} "
            f"connected={self.connected:>3} {pct}  {self.vias:>4} vias "
            f"{self.copper_mm:>8.1f}mm  ({self.seconds:5.1f}s) {self.status}"
            + (f" — {self.detail}" if self.detail else "")
        )


@dataclass(frozen=True)
class CompositionResult:
    solution: RoutingSolution
    plan_name: str
    stages: tuple[StageReport, ...]
    partition: Partition
    completeness: float
    notes: tuple[str, ...] = ()

    def limiting_stage(self) -> StageReport | None:
        """The stage that left the most nets open. What to fix first.

        Ties break on plan order, so the answer is stable and the earlier stage
        wins — the earlier a stage is, the more of the board its copper shapes.
        """
        ran = [s for s in self.stages if s.asked]
        if not ran:
            return None
        return min(ran, key=lambda s: (s.connected - s.asked, self.stages.index(s)))

    def table(self) -> str:
        rows = [s.line() for s in self.stages]
        limit = self.limiting_stage()
        if limit is not None and limit.connected < limit.asked:
            rows.append(
                f"    limiting stage: {limit.label} "
                f"({limit.asked - limit.connected} net(s) open)"
            )
        for note in self.notes:
            rows.append(f"    note: {note}")
        return "\n".join(rows)

    def as_dict(self) -> dict:
        limit = self.limiting_stage()
        return {
            "plan": self.plan_name,
            "stages": [s.as_dict() for s in self.stages],
            "partition": self.partition.as_dict(),
            "completeness": round(self.completeness, 6),
            "limitingStage": limit.label if limit else None,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


def _namespaced(
    solution: RoutingSolution, label: str
) -> tuple[tuple[Trace, ...], tuple[Via, ...]]:
    """The stage's copper with its ids prefixed by the stage label.

    Not cosmetic. ``routerlib.connectivity`` unions ``(copper id, layer)``
    nodes and ``routerlib.drc`` skips a pair when ``other.id == item.id``, so
    two stages that both mint ``v0`` produce one union-find node carrying two
    nets — a false connection *and* a short that is never checked. Both
    ``plane-and-classes`` and ``maze-astar`` mint ``v0``.
    """
    traces = tuple(replace(t, id=f"{label}.{t.id}") for t in solution.traces)
    vias = tuple(replace(v, id=f"{label}.{v.id}") for v in solution.vias)
    return traces, vias


def compose(
    problem: RoutingProblem,
    budget: Budget,
    registry: Registry,
    *,
    plan: Sequence[ClassStage] | str = DEFAULT_PLAN,
    plan_name: str | None = None,
) -> CompositionResult:
    """Run one plan over one board and report every stage separately.

    Each stage gets the **whole** budget, exactly as the relay does, so the two
    compositions are comparable. That is deliberately generous and it is the
    right way round under the north star's exchange rate: a fab round trip is
    two weeks and about $85, a stage is seconds.

    Deterministic: the plan is a fixed tuple, the buckets come from
    ``problem.routable_nets`` (already sorted by the problem), every family is
    itself deterministic under a counted budget, and the id prefixes are the
    stage labels.
    """
    if isinstance(plan, str):
        if plan not in PLANS:
            raise KeyError(
                f"unknown plan {plan!r} (have: {', '.join(sorted(PLANS))})"
            )
        plan_name, plan = plan, PLANS[plan]
    stages_plan = validate_plan(plan)
    plan_name = plan_name or "custom"

    split = partition(problem, stages_plan)
    buckets = dict(split.buckets)

    placed_traces: list[Trace] = []
    placed_vias: list[Via] = []
    reports: list[StageReport] = []
    notes: list[str] = []
    iterations = 0
    nodes = 0
    wall = 0.0

    if split.unclaimed:
        notes.append(
            f"{len(split.unclaimed)} net(s) matched no stage and were never "
            f"routed: {', '.join(split.unclaimed[:4])}"
            + ("…" if len(split.unclaimed) > 4 else "")
        )

    for stage in stages_plan:
        asked = buckets[stage.label]
        if not asked:
            reports.append(
                StageReport(stage.label, stage.router, 0, 0, 1.0, 0, 0.0, 0.0,
                            status="empty",
                            detail="no net of this class on this board")
            )
            continue
        if stage.router not in registry:
            reports.append(
                StageReport(stage.label, stage.router, len(asked), 0, 0.0, 0, 0.0,
                            0.0, status="missing",
                            detail=f"not in the registry ({len(registry)} families)")
            )
            notes.append(
                f"stage {stage.label!r} wanted {stage.router!r}, which is not "
                "registered — its nets were left unrouted rather than handed "
                "to a substitute nobody asked for"
            )
            continue

        wanted = set(asked)
        stage_problem = replace(
            problem,
            nets=tuple(n for n in problem.nets if n.id in wanted),
            existing_traces=tuple(problem.existing_traces) + tuple(placed_traces),
            existing_vias=tuple(problem.existing_vias) + tuple(placed_vias),
        )
        started = time.perf_counter()
        try:
            solution = registry[stage.router]().route(stage_problem, budget)
        except Exception as exc:  # noqa: BLE001 — a family that dies costs a stage
            seconds = time.perf_counter() - started
            reports.append(
                StageReport(stage.label, stage.router, len(asked), 0, 0.0, 0, 0.0,
                            round(seconds, 3), status="raised",
                            detail=f"{type(exc).__name__}: {exc}")
            )
            notes.append(f"{stage.router} raised {type(exc).__name__}: {exc}")
            continue
        seconds = time.perf_counter() - started

        traces, vias = _namespaced(solution, stage.label)
        placed_traces.extend(traces)
        placed_vias.extend(vias)
        iterations += solution.iterations
        nodes += solution.nodes_expanded
        wall += seconds
        reports.append(
            StageReport(
                label=stage.label,
                router=stage.router,
                asked=len(asked),
                connected=0,
                completeness=0.0,
                vias=len(vias),
                copper_mm=round(sum(t.length_mm for t in traces), 4),
                seconds=round(seconds, 3),
            )
        )

    merged = RoutingSolution(
        router=f"netclass[{plan_name}]",
        traces=tuple(placed_traces),
        vias=tuple(placed_vias),
        iterations=iterations,
        nodes_expanded=nodes,
        wall_clock_s=wall,
    )
    linked = conn.analyse(problem, merged)
    connected = set(linked.connected_nets)

    # Per-class completeness, measured against the finished board. This is the
    # column the whole module exists to produce: which stage is limiting.
    finished: list[StageReport] = []
    for report in reports:
        asked_ids = buckets[report.label]
        hit = sum(1 for net_id in asked_ids if net_id in connected)
        finished.append(
            replace(
                report,
                connected=hit,
                completeness=(hit / len(asked_ids)) if asked_ids else 1.0,
            )
        )

    solution = replace(
        merged,
        complete=linked.completeness >= 1.0,
        unrouted_nets=linked.unconnected_nets,
        notes=tuple(notes),
    )
    return CompositionResult(
        solution=solution,
        plan_name=plan_name,
        stages=tuple(finished),
        partition=split,
        completeness=linked.completeness,
        notes=tuple(notes),
    )


class NetClassRouter:
    """The composition as a plain :class:`~routerlib.model.Router`.

    So it can enter the tournament, the determinism check and the portfolio on
    the same terms as any single family — a composition that cannot be scored
    beside the things it composes is a composition nobody can check.
    """

    def __init__(
        self,
        registry: Registry,
        plan: Sequence[ClassStage] | str = DEFAULT_PLAN,
        *,
        name: str | None = None,
    ) -> None:
        self.registry = registry
        if isinstance(plan, str):
            self.plan_name = plan
            self.plan = validate_plan(PLANS[plan])
        else:
            self.plan_name = name or "custom"
            self.plan = validate_plan(plan)
        self.name = name or f"netclass[{self.plan_name}]"

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        return compose(
            problem, budget, self.registry,
            plan=self.plan, plan_name=self.plan_name,
        ).solution


__all__ = [
    "DEFAULT_PLAN",
    "GENERAL",
    "NET_CLASSES",
    "PAIR_EXPERT",
    "PLANE_EXPERT",
    "PLANS",
    "ClassStage",
    "CompositionResult",
    "NetClassRouter",
    "Partition",
    "Registry",
    "StageReport",
    "compose",
    "partition",
    "plan_for",
    "validate_plan",
]
