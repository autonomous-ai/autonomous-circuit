"""Given board Y, pick algorithm X — and when the budget allows, run several.

Algorithm selection is a real research area and it usually beats any single
algorithm, because the per-instance winner varies and the features that predict
it are cheap. On our benchmark it **does not**, and this module says so in code
rather than pretending otherwise. What is measured, on the 2026-08-16
tournament (16 instances, 9 families, ruler ``b3c77d55b171``):

* one family dominates — ``pathfinder-negotiated`` is at or tied with the best
  completeness on **12 of 16** instances, and no other family clears 11;
* a single-threshold feature rule, leave-one-out cross-validated over the 12
  instances that have a real board, leaves **17** nets unrouted against the
  constant "always pathfinder"'s **13**, at 202 real DRC errors against 80. The
  rule family loses to the constant. Selection is not where the win is;
* the oracle — the per-instance best of all nine, chosen with hindsight — is
  **11** unrouted against 13. Two nets out of 216. There is no headroom in
  picking;
* but the *union* of what the nine route is **378 of 380** nets. Every net but
  two is routed by somebody. All of the headroom is in composition.

So the portfolio here is three modes, and the interesting one is the third:

``single``
    Apply the rules, run one router. Cheapest, and within two nets of the
    oracle.

``best-of-n``
    Run the top candidates, keep the best-scoring result. Strictly no worse
    than its best member, and only possible because every family is
    deterministic and comparable. Measured gain over ``single``: one DRC error.
    It is here for the guarantee and for the risk it removes, not for the mean.

``relay``
    Run the lead router, then hand the **nets it failed** to the next family,
    with the copper already down as obstacles. Each stage sees a smaller, real
    problem. Measured on the three rp2040 cells: 88.2 → 94.1, 85.7 → **95.2**
    and 92.9 → **100.0** per cent, zero harness DRC errors at every step, for
    about 4% more copper. The middle one beats *every* single family and the
    oracle. This is what a portfolio is worth on this benchmark.

    The obvious variant — hand the next family the whole board and merge — was
    tried first and is a dead end: on ``matrix-ldo-3v3__rp2040-core__usb-c-power``
    three extra families added 575mm of copper and 39 vias for **zero** extra
    nets, because each one re-solved the nets that were already solved. Asking
    only for the residue is the whole trick.

Nothing in this module is a claim about fab-readiness. Under the north star —
100% routed *and* zero pipeline findings — the best single family, the oracle
and this portfolio all clear the same **3 of 12** real boards, and all three
are boards that seven of the nine families get right. See
``docs/architecture/routing.md``.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from routerlib import connectivity as conn
from routerlib.bench import Features, features_of
from routerlib.model import Budget, RoutingProblem, RoutingSolution
from routerlib.scoring import Score, score as score_solution

#: The tournament this module's rules were fitted to. Every number in a rule's
#: ``why`` comes from here. Change the tournament, re-derive the rules.
EVIDENCE = "tournament 2026-08-16, ruler b3c77d55b171, 16 instances x 9 families"

#: The families, best-completeness-first as measured. Ordering is fixed so a
#: candidate list is reproducible.
FAMILIES: tuple[str, ...] = (
    "pathfinder-negotiated",
    "maze-astar",
    "plane-and-classes",
    "ripup-reroute",
    "meta-anneal",
    "meta-genetic",
    "topological-graph",
    "exact-and-structured",
    "baseline-pattern",
)

#: Best-or-tied on completeness, out of 16 instances. Quoted by the rules.
DOMINANCE: Mapping[str, int] = {
    "pathfinder-negotiated": 12,
    "maze-astar": 11,
    "plane-and-classes": 9,
    "ripup-reroute": 6,
    "exact-and-structured": 4,
    "meta-genetic": 4,
    "meta-anneal": 4,
    "topological-graph": 3,
    "baseline-pattern": 2,
}

#: Real KiCad copper DRC errors per connected net, over the 12 instances with a
#: real board on disk, empty-solution control subtracted. This is the column
#: the harness scorer gets wrong, so it is recorded here from the pipeline.
ERRORS_PER_CONNECTED_NET: Mapping[str, float] = {
    "exact-and-structured": 0.27,
    "baseline-pattern": 0.53,
    "pathfinder-negotiated": 0.57,
    "topological-graph": 0.69,
    "ripup-reroute": 0.69,
    "maze-astar": 1.55,
    "meta-anneal": 2.11,
    "meta-genetic": 2.31,
    "plane-and-classes": 3.92,
}


# ---------------------------------------------------------------------------
# Difficulty — a property of the instance, not of any algorithm
# ---------------------------------------------------------------------------
#
# Net count separates the benchmark cleanly and monotonically, which almost
# nothing else does:
#
#   <= 14 nets   at least one family finishes the board          8 of 8
#   15..20 nets  at most one family finishes it                  2 of 2
#   >= 21 nets   no family has ever finished one                 6 of 6
#
# That is a statement about how hard the instance is, and it is the one thing
# the features do predict. It sets the default mode and it sets the honest
# forecast the caller gets back.

#: Above this, at most one family in the tournament finished a board.
HARD_NETS = 15
#: At or above this, no family has finished a board. Six of six.
UNFINISHED_NETS = 21


@dataclass(frozen=True)
class Expectation:
    """What the tournament says is likely to happen. Never a promise."""

    difficulty: str
    completes: str
    evidence: str

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def difficulty_of(features: Features) -> Expectation:
    n = features.routable_net_count
    if n >= UNFINISHED_NETS:
        return Expectation(
            "beyond",
            "no",
            f"{n} routable nets; no family finished any of the 6 benchmark "
            f"instances with >= {UNFINISHED_NETS} nets (best 85.7-95.5%)",
        )
    if n >= HARD_NETS:
        return Expectation(
            "hard",
            "unlikely",
            f"{n} routable nets; of the 2 benchmark instances in "
            f"{HARD_NETS}..{UNFINISHED_NETS - 1}, one was finished by exactly "
            f"one family and one by none",
        )
    return Expectation(
        "routable",
        "likely",
        f"{n} routable nets; all 8 benchmark instances with <= {HARD_NETS - 1} "
        f"nets were finished by at least one family",
    )


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """One rule: a name, a predicate, a pick, and the measurement behind it.

    ``why`` is not a comment. A rule that cannot name the instances it was
    derived from is a guess wearing a rule's clothes, and this benchmark is
    small enough that guesses look exactly like findings.
    """

    name: str
    pick: str
    why: str
    predicate: Callable[[Features], bool]

    def fires(self, features: Features) -> bool:
        return bool(self.predicate(features))


#: In order. The first that fires wins; the last always fires.
#:
#: There are two, and there are two because only two survive. Every rule the
#: brief suggested was tested against the tournament and every one of them is
#: false on our data — they are recorded in ``REJECTED_RULES`` below rather
#: than deleted, because a tried-and-failed rule is worth more than an
#: untried one.
RULES: tuple[Rule, ...] = (
    Rule(
        name="trivial-cheapest",
        pick="baseline-pattern",
        why=(
            "<= 2 routable nets: on both such instances all 9 families return "
            "100% routed with 0 errors and identical real-board DRC, so take "
            "the one that does no search (6s vs 202s over the suite)"
        ),
        predicate=lambda f: f.routable_net_count <= 2,
    ),
    Rule(
        name="dominant-default",
        pick="pathfinder-negotiated",
        why=(
            "at or tied with the best completeness on 12 of 16 instances "
            "(next best 11), the lowest unrouted total on the 12 real boards "
            "(13 of 216 nets), and 0.57 real DRC errors per connected net — "
            "on a par with the do-nothing baseline while connecting 2.3x as "
            "many nets"
        ),
        predicate=lambda f: True,
    ),
)

#: Rules that were tried and are false. Kept so nobody re-derives them.
REJECTED_RULES: tuple[tuple[str, str], ...] = (
    (
        "regular matrix -> structured router",
        "exact-and-structured ranks 7th or 8th of 9 on all four instances with "
        "grid_score >= 0.5, including terminal-keyboard (0.83), the most "
        "regular board we have",
    ),
    (
        "GND > 30% of pads -> plane-first",
        "on the 7 instances where ground is >= 25% of pads, plane-and-classes "
        "ranks 3rd to 8th of 9 and never first; on the highest-ground board "
        "(38%) it is 8th",
    ),
    (
        "a poured plane -> plane-first",
        "plane-and-classes wins 1 of the 3 plane variants and is 4th on the "
        "other two; it also carries the worst real-board legality of any "
        "family, 3.92 DRC errors per connected net",
    ),
    (
        "small and dense -> exact on windows",
        "exact-and-structured ranks 8th of 9 on all five instances with "
        "pad density >= 5 pads/cm2",
    ),
    (
        "any single-threshold feature rule",
        "leave-one-out over the 12 real boards: 17 nets unrouted and 202 real "
        "DRC errors, against 13 and 80 for the constant 'always pathfinder'. "
        "With 12 labelled instances the rule family overfits its own fold",
    ),
)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Selection:
    """Features in, algorithm out — with the rule that fired and a fallback."""

    router: str
    rule: str
    why: str
    fallback: str
    candidates: tuple[str, ...]
    mode: str
    expectation: Expectation
    evidence: str = EVIDENCE

    def line(self) -> str:
        return (
            f"{self.router}  [{self.rule}]  mode={self.mode} "
            f"candidates={'+'.join(self.candidates)}  "
            f"expect-complete={self.expectation.completes}"
        )

    def as_dict(self) -> dict:
        return {
            "router": self.router,
            "rule": self.rule,
            "why": self.why,
            "fallback": self.fallback,
            "candidates": list(self.candidates),
            "mode": self.mode,
            "expectation": self.expectation.as_dict(),
            "evidence": self.evidence,
        }


#: Candidate order for the multi-router modes, after the lead.
#:
#: Diversity first, then legality. ``maze-astar`` is the only other family that
#: reaches the top completeness often (11 of 16) and it fails on different nets
#: than pathfinder does; ``plane-and-classes`` is the only family that models a
#: poured plane as a net rather than as obstacles, and it is the only family
#: that ever finished ``matrix-rp2040-core__sw-tact``; ``exact-and-structured``
#: is last because it is the cleanest per connected net (0.27 real DRC errors)
#: and by then only a couple of nets are left, where clean matters more than
#: reach.
FOLLOWERS: tuple[str, ...] = (
    "maze-astar",
    "plane-and-classes",
    "exact-and-structured",
)

#: How many routers each budget class is allowed to run.
BUDGET_WIDTH: Mapping[str, int] = {"cheap": 1, "standard": 2, "thorough": 4}


def select(
    features: Features,
    *,
    budget_class: str = "standard",
    mode: str | None = None,
) -> Selection:
    """Pick a router, and a candidate list for the wider modes.

    ``budget_class`` is ``cheap`` (one router), ``standard`` (two) or
    ``thorough`` (four). The north star's exchange rate — a fab round trip is
    two weeks and about $85, a router run is minutes — says ``thorough`` should
    be the norm for anything that will actually be ordered; ``standard`` is the
    default here only because it is what an interactive build can absorb.
    """
    if budget_class not in BUDGET_WIDTH:
        raise ValueError(
            f"unknown budget class {budget_class!r} "
            f"(have: {', '.join(sorted(BUDGET_WIDTH))})"
        )
    rule = next(r for r in RULES if r.fires(features))
    expectation = difficulty_of(features)
    width = BUDGET_WIDTH[budget_class]

    candidates = [rule.pick]
    for name in FOLLOWERS:
        if len(candidates) >= width:
            break
        if name != rule.pick:
            candidates.append(name)

    if mode is None:
        # One router is enough when one router is expected to finish and the
        # budget is not there anyway; otherwise relay, because relay is the
        # only mode measured to beat the oracle.
        mode = "single" if width == 1 else "relay"

    # The last resort is the baseline, and not because it is good — it is the
    # worst family in the tournament. It is the only one that lives *inside*
    # the package. Every other family is a file in ``algorithms/`` loaded by
    # path, so every other family can be absent from a caller's registry, and
    # a fallback that can itself be missing is not a fallback.
    fallback = (
        "baseline-pattern" if rule.pick != "baseline-pattern"
        else "exact-and-structured"
    )
    return Selection(
        router=rule.pick,
        rule=rule.name,
        why=rule.why,
        fallback=fallback,
        candidates=tuple(candidates),
        mode=mode,
        expectation=expectation,
    )


def select_for(problem: RoutingProblem, **kwargs) -> Selection:
    """:func:`select`, measuring the features off the problem itself."""
    return select(features_of(problem), **kwargs)


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------

#: A registry maps a family name to a zero-argument factory returning
#: something with ``.route(problem, budget) -> RoutingSolution``.
Registry = Mapping[str, Callable[[], object]]

#: How a best-of-N run ranks its candidates. Lower is better.
ScoreKey = Callable[[RoutingProblem, RoutingSolution], tuple]


def harness_key(problem: RoutingProblem, solution: RoutingSolution) -> tuple:
    """The package's own lexicographic score.

    **It was known biased and the bias is gone.** Until 2026-08-16
    ``routerlib.geometry`` modelled a rectangular pad as its inscribed stadium,
    so a trace could sit 0.21mm inside a 1.0mm square pad and still measure
    0.09mm of clearance. Ranking all nine families by this key and taking the
    winner gave 220 real KiCad copper errors across the 12 real boards against
    144 for ranking by the pipeline's own answer, and the two disagreed on 7 of
    12. With pads and keepouts measured as their true shapes, this key's error
    count and KiCad's rank the families at Spearman +0.93.

    That is agreement, not equivalence. This key still cannot see a short
    through a plane island, and it is the same geometry the routers ask
    permission with, so a family can agree with itself. Prefer
    :func:`pipeline_key_factory` whenever a real board is available — an
    independent engine is worth more than a correlated one.
    """
    return score_solution(problem, solution).key()


def pipeline_key_factory(
    errors_of: Callable[[RoutingSolution], int]
) -> ScoreKey:
    """A key that asks an external engine for the legality tier.

    ``errors_of`` takes a solution and returns the number of real copper
    findings — in practice ``@tscircuit/checks`` plus ``kicad-cli pcb drc`` on
    the real board with an empty-solution control subtracted. Completeness
    still comes first, because a net that is not connected is a dead board and
    nothing below it compensates; only the legality tier changes hands.
    """

    def key(problem: RoutingProblem, solution: RoutingSolution) -> tuple:
        base = score_solution(problem, solution)
        q = base.quality
        return (
            round(1.0 - base.completeness, 9),
            errors_of(solution),
            base.warnings,
            q.via_count,
            round(q.copper_mm, 3),
            base.iterations,
        )

    return key


@dataclass(frozen=True)
class Stage:
    """One router's turn in a multi-router run."""

    router: str
    asked_nets: int
    added_nets: int
    completeness: float
    vias: int
    copper_mm: float
    seconds: float

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class PortfolioResult:
    solution: RoutingSolution
    selection: Selection
    stages: tuple[Stage, ...] = ()
    #: For ``best-of-n``: every candidate's score key, in candidate order.
    ranked: tuple[tuple[str, tuple], ...] = ()
    scored_with: str = "harness"
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "selection": self.selection.as_dict(),
            "stages": [s.as_dict() for s in self.stages],
            "ranked": [[name, list(key)] for name, key in self.ranked],
            "scoredWith": self.scored_with,
            "notes": list(self.notes),
        }


def _merge(base: RoutingSolution, extra: RoutingSolution) -> RoutingSolution:
    return dataclasses.replace(
        base,
        traces=tuple(base.traces) + tuple(extra.traces),
        vias=tuple(base.vias) + tuple(extra.vias),
        iterations=base.iterations + extra.iterations,
        nodes_expanded=base.nodes_expanded + extra.nodes_expanded,
        wall_clock_s=base.wall_clock_s + extra.wall_clock_s,
    )


def _run(registry: Registry, name: str, problem: RoutingProblem,
         budget: Budget) -> tuple[RoutingSolution, float]:
    if name not in registry:
        raise KeyError(
            f"router {name!r} is not registered "
            f"(have: {', '.join(sorted(registry))})"
        )
    t0 = time.perf_counter()
    solution = registry[name]().route(problem, budget)
    return solution, time.perf_counter() - t0


def _resolve(
    registry: Registry, selection: Selection
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Candidates that actually exist in this registry, plus what was dropped.

    A family missing from the registry is a **configuration** problem, not a
    routing one, and the two must not look alike. Dropped silently, a run with
    an unregistered lead returns a board with no copper on it and calls that a
    result — which is exactly the failure this package exists to stop being
    possible. So the fallback is substituted when the lead is gone, and a run
    with nothing left to call raises rather than returning an empty board.
    """
    kept = [name for name in selection.candidates if name in registry]
    missing = [name for name in selection.candidates if name not in registry]
    if not kept and selection.fallback in registry:
        kept = [selection.fallback]
    if not kept:
        raise KeyError(
            f"none of {selection.candidates} nor the fallback "
            f"{selection.fallback!r} is registered "
            f"(have: {', '.join(sorted(registry)) or 'nothing'})"
        )
    return tuple(kept), tuple(missing)


def route(
    problem: RoutingProblem,
    budget: Budget,
    registry: Registry,
    *,
    budget_class: str = "standard",
    mode: str | None = None,
    score_key: ScoreKey | None = None,
    scored_with: str = "harness",
) -> PortfolioResult:
    """Select, then run — in whichever mode the selection asked for.

    Deterministic in every mode: the candidate list is a fixed tuple, each
    family is itself deterministic under a counted budget, and the relay's
    residue is derived from connectivity rather than from iteration order.
    """
    selection = select(features_of(problem), budget_class=budget_class, mode=mode)
    if selection.mode == "single":
        return _route_single(problem, budget, registry, selection)
    if selection.mode == "relay":
        return _route_relay(problem, budget, registry, selection)
    if selection.mode == "best-of-n":
        return _route_best_of_n(
            problem, budget, registry, selection,
            score_key or harness_key, scored_with,
        )
    raise ValueError(f"unknown mode {selection.mode!r}")


def _stage_of(problem: RoutingProblem, name: str, asked: int, before: int,
              merged: RoutingSolution, seconds: float) -> Stage:
    linked = conn.analyse(problem, merged)
    return Stage(
        router=name,
        asked_nets=asked,
        added_nets=len(linked.connected_nets) - before,
        completeness=round(linked.completeness, 6),
        vias=len(merged.vias),
        copper_mm=round(merged.copper_length_mm, 4),
        seconds=round(seconds, 3),
    )


def _route_single(problem, budget, registry, selection) -> PortfolioResult:
    kept, missing = _resolve(registry, selection)
    name = kept[0]
    solution, seconds = _run(registry, name, problem, budget)
    solution = dataclasses.replace(solution, router=f"portfolio[{name}]")
    notes = (
        (f"not registered, skipped: {', '.join(missing)}",) if missing else ()
    )
    return PortfolioResult(
        solution=solution,
        selection=selection,
        stages=(_stage_of(problem, name,
                          len(problem.routable_nets), 0, solution, seconds),),
        notes=notes,
    )


def _route_relay(problem, budget, registry, selection) -> PortfolioResult:
    """Lead router first; each follower is asked only for the nets still open.

    The follower sees the copper already down as obstacles, so the composition
    cannot invent a clearance violation that neither stage could see — which is
    the whole reason this is a relay and not a merge of independent runs.
    """
    candidates, missing = _resolve(registry, selection)
    merged = RoutingSolution(
        router="portfolio", traces=problem.existing_traces,
        vias=problem.existing_vias, complete=False,
    )
    stages: list[Stage] = []
    notes: list[str] = []
    if missing:
        notes.append(f"not registered, skipped: {', '.join(missing)}")
    for index, name in enumerate(candidates):
        linked = conn.analyse(problem, merged)
        open_nets = set(linked.unconnected_nets)
        if index and not open_nets:
            notes.append(f"stopped after {index} router(s): every net connected")
            break
        stage_problem = (
            problem
            if index == 0
            else dataclasses.replace(
                problem,
                nets=tuple(n for n in problem.nets if n.id in open_nets),
                existing_traces=tuple(merged.traces),
                existing_vias=tuple(merged.vias),
            )
        )
        before = len(linked.connected_nets)
        try:
            solution, seconds = _run(registry, name, stage_problem, budget)
        except Exception as exc:  # noqa: BLE001 — a family that dies costs a stage
            notes.append(f"{name} raised {type(exc).__name__}: {exc}; stage skipped")
            continue
        merged = _merge(merged, solution)
        stages.append(
            _stage_of(problem, name, len(stage_problem.routable_nets),
                      before, merged, seconds)
        )
    linked = conn.analyse(problem, merged)
    chain = "+".join(s.router for s in stages)
    solution = dataclasses.replace(
        merged,
        router=f"portfolio-relay[{chain}]",
        complete=linked.completeness >= 1.0,
        unrouted_nets=linked.unconnected_nets,
        notes=tuple(notes),
    )
    return PortfolioResult(
        solution=solution, selection=selection, stages=tuple(stages),
        notes=tuple(notes),
    )


def _route_best_of_n(problem, budget, registry, selection, key,
                     scored_with) -> PortfolioResult:
    """Run every candidate on the whole board, keep the best-scoring one.

    Strictly no worse than its best member, and only meaningful because every
    family is deterministic: the same board gives the same candidates, so the
    same winner. Measured gain over ``single`` on this benchmark: one DRC error
    across 12 boards. It is here for the floor it guarantees, not for the mean
    it moves.
    """
    candidates, missing = _resolve(registry, selection)
    ranked: list[tuple[str, tuple, RoutingSolution, float]] = []
    notes: list[str] = []
    if missing:
        notes.append(f"not registered, skipped: {', '.join(missing)}")
    for name in candidates:
        try:
            solution, seconds = _run(registry, name, problem, budget)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{name} raised {type(exc).__name__}: {exc}; candidate dropped")
            continue
        ranked.append((name, key(problem, solution), solution, seconds))
    if not ranked:
        raise RuntimeError("every candidate failed; nothing to return")
    ranked.sort(key=lambda row: (row[1], candidates.index(row[0])))
    if scored_with == "harness":
        notes.append(
            "ranked with the harness key, which is biased by the inscribed-"
            "stadium pad model — it picks a different router than the pipeline "
            "does on 7 of 12 measured boards"
        )
    name, _, solution, seconds = ranked[0]
    winner = dataclasses.replace(solution, router=f"portfolio-best-of[{name}]")
    return PortfolioResult(
        solution=winner,
        selection=selection,
        stages=(_stage_of(problem, name, len(problem.routable_nets), 0,
                          winner, seconds),),
        ranked=tuple((n, k) for n, k, _s, _t in ranked),
        scored_with=scored_with,
        notes=tuple(notes),
    )


def score_of(problem: RoutingProblem, result: PortfolioResult) -> Score:
    return score_solution(problem, result.solution)


__all__ = [
    "BUDGET_WIDTH",
    "DOMINANCE",
    "ERRORS_PER_CONNECTED_NET",
    "EVIDENCE",
    "Expectation",
    "FAMILIES",
    "FOLLOWERS",
    "HARD_NETS",
    "PortfolioResult",
    "REJECTED_RULES",
    "RULES",
    "Rule",
    "Selection",
    "Stage",
    "UNFINISHED_NETS",
    "difficulty_of",
    "harness_key",
    "pipeline_key_factory",
    "route",
    "score_of",
    "select",
    "select_for",
]
