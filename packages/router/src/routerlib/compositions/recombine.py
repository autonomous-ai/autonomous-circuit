"""Solution recombination: build one board out of N boards, net by net.

Every other composition in this package moves *work* between families. This one
moves *copper*. Take N finished solutions from N families, cut each one apart
along net boundaries, and reassemble a board from the best piece of each — then
repair what the merge breaks. It is crossover across algorithms rather than
within one, and it is the only composition here that can beat every input,
because it is the only one that can hold two families' copper at once.

It is also the one that already failed once. In the portfolio phase, handing a
whole board to a second family and unioning the copper on
``matrix-ldo-3v3__rp2040-core__usb-c-power`` added **575mm of copper and 39 vias
for zero extra nets**: each family re-solved the nets that were already solved,
and nothing resolved the geometry where the two answers overlapped. That failure
is the specification for this module, and this module reproduced a second one
before it worked — see *Free merging destroys coherence* below.

The measurement that says there is something here
-------------------------------------------------

Split the copper already on disk by net and ask, of each net's routing *in
isolation*, whether it connects the net and whether it is legal against the
fixed board. Over the 16 benchmark instances, with the three families re-run
against the corrected pad model:

===============================  ==========  =======
                                 nets        of 380
===============================  ==========  =======
best single family per instance  342         90.0%
union over the three families    367         96.6%
of those, legal in isolation     367         96.6%
===============================  ==========  =======

Twenty-five nets are routed by *somebody* and not by the per-instance winner,
and not one of them is lost to its own illegality — every one of those routings
is clean against the pads, drills, keepouts and board edge. So the whole
question is co-existence, and 96.6% is the ceiling: no merge connects a net
nobody routed.

Free merging destroys coherence — measured, not assumed
--------------------------------------------------------

The obvious algorithm is *rank every net's candidates, greedily take the best
non-conflicting one, re-route the rest*. It is :data:`FREE` here and it is a
**loss**: over three instances it connected 48 of 62 nets where the best single
input connected 54, and on ``matrix-rp2040-core__usb-c-data`` it fell to 57.1%
against ``maze-astar``'s 85.7%. Eleven nets had a viable routing that no
ordering could fit.

The reason is not the ordering and not the ranking — all three rankings scored
identically, and a single-family merge reproduces that family net for net with
nothing lost. It is that **a family's solution is internally coherent and a
cherry-pick is not.** Every net in one family's board was routed knowing what
that family had already committed. Take net A from one family and net B from
another and each was routed against a board the other does not live on; the
copper that A displaces is exactly the room B was counting on.

So the merge is anchored. :data:`ANCHORED` takes one family's solution **whole**
— the coherence comes free with it — and then transplants, from the other
families, only the nets the base failed. That direction is monotone: a
transplant is offered space the base was not using, and a transplant that does
not fit is dropped, so the result is never worse than its base. Every family is
tried as the base and the best result kept, which costs nothing because a
transplant runs no router.

Anchoring alone is still not enough — and that is the second measurement
-------------------------------------------------------------------------

Anchored, with no repair, transplants **zero** nets on every instance measured.
Not one. The reason is one line long and it is the same line as before: the
nets a base fails are the nets its own copper blocks, so the other families'
routings for exactly those nets land on exactly the occupied space. Measured
blocking sets, three families: 1, 1, 2, 2, 6 nets on ``harness-puck``, 9 on
``matrix-rp2040-core__usb-c-data``, up to 7 on ``hydrate-coaster``.

That is why :func:`recombine` has a repair, and why the repair is allowed to
call a router. **Evict** the nets standing in a transplant's way, put the
transplant in, and re-route the evicted ones around it. This is rip-up and
reroute across a stage boundary — the one thing a relay structurally cannot do,
because a relay never takes the lead's copper back out. Swapping an evicted net
for another family's routing of the same net is tried first because it is free,
and it has never once worked, for the same reason the transplant did not.

What is left after that goes to a residual router with the accepted copper as
obstacles. That part *is* relay, deliberately, because relay works — but seeded
with a board that already carries other families' answers.

Two honest possibilities, and both are results
----------------------------------------------

If the assembly ends up taking every net from one family, this is relay with
extra steps and :attr:`Recombination.single_source` says so. If it mixes sources
and still does not beat relay, that is a negative result about crossover and it
belongs in the document beside the other negative results this benchmark has
produced. Neither outcome is a reason to report the other.
"""

from __future__ import annotations

import dataclasses
import math
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from routerlib import connectivity as conn
from routerlib.geometry import GridIndex, capsule_gap, disc_capsule, segment_capsule
from routerlib.model import (
    BOTTOM,
    TOP,
    Budget,
    RoutingProblem,
    RoutingSolution,
    Trace,
    Via,
)
from routerlib.workspace import Workspace

#: Take one family's board whole, then transplant into it only the nets it
#: failed. Monotone against its base, and the default.
ANCHORED = "anchored"

#: Cherry-pick every net independently. Measured as a loss and kept because a
#: tried-and-failed composition is worth more than an untried one — deleting it
#: is how the next agent re-derives it from the same data that refuted it.
FREE = "free"

MODES: tuple[str, ...] = (ANCHORED, FREE)

#: Ranking keys a caller can ask for, and what each one optimises.
#:
#: ``obstruction``
#:     Least space denied to the nets that follow: swept copper area widened by
#:     the clearance on each side, plus a via's exclusion disc. The default,
#:     because space is the resource the assembly is allocating.
#: ``scorer``
#:     The harness quality tier — via count, then copper length. Ranks each net
#:     the way the board will be ranked.
#: ``source``
#:     Candidate-list order, so the first family named wins every tie. The
#:     control: when this scores the same as the other two, the ranking is not
#:     what is doing the work, and on the first measured run it did.
RANKINGS: tuple[str, ...] = ("obstruction", "scorer", "source")


# ---------------------------------------------------------------------------
# One net, lifted out of one solution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NetRouting:
    """One net's copper, cut out of one family's solution, measured alone.

    ``connects`` and ``legal`` are the two admission questions and they are
    asked separately because they fail for different reasons. A routing that
    does not connect is a partial net — copper that looks like an answer and is
    not, which is the failure mode the whole package exists to eliminate. A
    routing that connects but is illegal against the *fixed* board was never a
    candidate on any board; that is not the merge's fault and no merge repairs
    it.

    ``blocked_mm2`` is what this routing costs everybody else: the copper's own
    area grown by the clearance on each side, because that is the region no
    other net may enter. It is the ranking currency, and the one number here
    that is about the board rather than about the net.
    """

    net: str
    source: str
    traces: tuple[Trace, ...]
    vias: tuple[Via, ...]
    connects: bool
    legal: bool
    refusal: str
    length_mm: float
    via_count: int
    blocked_mm2: float

    @property
    def viable(self) -> bool:
        return self.connects and self.legal

    @property
    def empty(self) -> bool:
        """No copper at all — the net's pads already touch, or a plane joins
        them. Nothing to choose between and nothing to conflict with."""
        return not self.traces and not self.vias

    def as_dict(self) -> dict:
        return {
            "net": self.net,
            "source": self.source,
            "connects": self.connects,
            "legal": self.legal,
            "refusal": self.refusal,
            "lengthMm": round(self.length_mm, 4),
            "vias": self.via_count,
            "blockedMm2": round(self.blocked_mm2, 4),
            "traces": len(self.traces),
        }


def _blocked_area(traces: Sequence[Trace], vias: Sequence[Via],
                  clearance: float) -> float:
    """Board area this copper denies to every other net.

    A segment of length L and width W denies a stadium of half-width
    ``W/2 + c``; a via of pad diameter D denies a disc of radius ``D/2 + c``.
    Overlap between a net's own segments is not subtracted — this is a ranking
    currency, not a measurement, and double-counting a corner changes no order.
    """
    total = 0.0
    for trace in traces:
        half = trace.width_mm / 2.0 + clearance
        for a, b in trace.segments:
            total += 2.0 * half * a.distance_to(b) + math.pi * half * half
    for via in vias:
        radius = via.pad_mm / 2.0 + clearance
        total += math.pi * radius * radius
    return total


def _slice(solution: RoutingSolution, net: str) -> tuple[tuple[Trace, ...], tuple[Via, ...]]:
    return (
        tuple(t for t in solution.traces if t.net == net),
        tuple(v for v in solution.vias if v.net == net),
    )


def _fits(workspace: Workspace, traces: Sequence[Trace], vias: Sequence[Via],
          net: str) -> str:
    """``""`` if this copper may be placed, else why not.

    Asked of :class:`~routerlib.workspace.Workspace` rather than of a private
    check, for the reason the workspace exists: it answers with the same
    geometry the scorer grades with, so a merge that passes here and fails the
    score is a bug in the harness and not a surprise about the board. Vias go
    first because a via is refused for reasons a trace never is — via-in-pad and
    hole-to-hole — and finding that out before measuring twenty segments is
    free.
    """
    for via in vias:
        verdict = workspace.via_ok(via.center, net, drill=via.drill_mm, pad=via.pad_mm)
        if verdict is not True:
            return f"via {via.id}: {verdict.reason} {verdict.detail}".strip()
    for trace in traces:
        for a, b in trace.segments:
            if a == b:
                continue
            verdict = workspace.segment_ok(trace.layer, a, b, trace.width_mm, net)
            if verdict is not True:
                return f"{trace.id}: {verdict.reason} {verdict.detail}".strip()
    return ""


def decompose(
    problem: RoutingProblem,
    solutions: Mapping[str, RoutingSolution],
    *,
    clearance_mm: float | None = None,
) -> dict[str, tuple[NetRouting, ...]]:
    """Cut every solution apart by net and measure each piece on its own.

    ``clearance_mm`` is the bar a piece must clear against the *fixed* board and
    it defaults to the fab floor (0.10mm), not to the gate (0.09mm). The gate
    exists because two geometry engines disagree by a few microns, and copper
    admitted at the gate is copper that scores a warning and lands on the fab's
    tolerance. A merge has a free choice here in a way a router under pressure
    does not, so it takes the stricter number.

    Returns net id → candidates in input order, including the ones that failed:
    a caller that only sees survivors cannot tell an empty net from a hard one.
    """
    rules = problem.rules
    clearance = rules.min_clearance_mm if clearance_mm is None else clearance_mm
    routable = sorted(n.id for n in problem.routable_nets)
    out: dict[str, list[NetRouting]] = {nid: [] for nid in routable}

    for source in sorted(solutions):
        solution = solutions[source]
        connected = set(conn.analyse(problem, solution).connected_nets)
        # One workspace per source. Nothing is committed to it, so it stays the
        # fixed board for every net it judges.
        board = Workspace(problem, clearance=clearance)
        for net in routable:
            traces, vias = _slice(solution, net)
            joins = net in connected
            refusal = "" if joins else "does not connect the net"
            legal = True
            if joins and (traces or vias):
                refusal = _fits(board, traces, vias, net)
                legal = not refusal
            out[net].append(
                NetRouting(
                    net=net,
                    source=source,
                    traces=traces,
                    vias=vias,
                    connects=joins,
                    legal=legal,
                    refusal=refusal,
                    length_mm=sum(t.length_mm for t in traces),
                    via_count=len(vias),
                    blocked_mm2=_blocked_area(traces, vias, clearance),
                )
            )
    return {net: tuple(rows) for net, rows in out.items()}


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _rank_key(ranking: str, order: Sequence[str]) -> Callable[[NetRouting], tuple]:
    index = {name: i for i, name in enumerate(order)}

    if ranking == "obstruction":
        return lambda r: (r.via_count, round(r.blocked_mm2, 6),
                          round(r.length_mm, 6), index.get(r.source, len(index)))
    if ranking == "scorer":
        return lambda r: (r.via_count, round(r.length_mm, 6),
                          round(r.blocked_mm2, 6), index.get(r.source, len(index)))
    if ranking == "source":
        return lambda r: (index.get(r.source, len(index)),)
    raise ValueError(f"unknown ranking {ranking!r} (have: {', '.join(RANKINGS)})")


# ---------------------------------------------------------------------------
# The result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Assignment:
    """Which family's copper this net ended up with, and what it beat."""

    net: str
    source: str
    stage: str
    viable_candidates: int
    rejected_by_conflict: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "net": self.net,
            "source": self.source,
            "stage": self.stage,
            "viableCandidates": self.viable_candidates,
            "rejectedByConflict": list(self.rejected_by_conflict),
        }


@dataclass(frozen=True)
class Recombination:
    """The merged board and every number needed to argue about it."""

    solution: RoutingSolution
    assignments: tuple[Assignment, ...]
    #: The family whose board was taken whole. Empty in :data:`FREE`.
    base: str
    mode: str
    #: Nets still open after the merge and before the residual router.
    unassigned: tuple[str, ...]
    #: Nets a viable routing existed for that no ranking could fit.
    lost_to_conflict: tuple[str, ...]
    completeness: float
    #: Nets at least one input routed, legally, in isolation. No merge can pass
    #: this without a router running.
    ceiling: float
    #: Best completeness of any single input solution.
    best_input: float
    ranking: str
    clearance_mm: float
    seconds: float
    #: Placed nets the repair took back out to make room for another.
    evictions: int = 0
    #: Nets won by evicting and re-homing rather than by finding a free lane.
    repairs: int = 0
    #: Every base tried, with the completeness it reached. Empty in FREE.
    base_scores: tuple[tuple[str, float], ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def sources(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.assignments:
            counts[row.source] = counts.get(row.source, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def transplanted(self) -> int:
        """Nets whose copper came from a family other than the base.

        Zero is the number that matters: it means the base's board plus a
        residual stage, which is a relay, and the result has to be reported as
        one whatever it scores.
        """
        return sum(
            1 for a in self.assignments
            if a.stage in ("transplant", "repair", "rehomed")
            and a.source != self.base
        )

    @property
    def single_source(self) -> bool:
        """Every merged net came from one family."""
        merged = {a.source for a in self.assignments if a.stage != "residual"}
        return len(merged) <= 1

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "base": self.base,
            "baseScores": [[n, round(c, 6)] for n, c in self.base_scores],
            "completeness": round(self.completeness, 6),
            "ceiling": round(self.ceiling, 6),
            "bestInput": round(self.best_input, 6),
            "ranking": self.ranking,
            "clearanceMm": self.clearance_mm,
            "sources": self.sources,
            "transplanted": self.transplanted,
            "evictions": self.evictions,
            "repairs": self.repairs,
            "singleSource": self.single_source,
            "unassigned": list(self.unassigned),
            "lostToConflict": list(self.lost_to_conflict),
            "assignments": [a.as_dict() for a in self.assignments],
            "seconds": round(self.seconds, 3),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# The residual stage
# ---------------------------------------------------------------------------

#: Called with the problem reduced to the still-open nets and the accepted
#: copper installed as ``existing_traces`` / ``existing_vias``. Returns only the
#: extra copper it found.
Residual = Callable[[RoutingProblem, Budget], RoutingSolution]


def relay_residual(
    registry: Mapping[str, Callable[[], object]],
    chain: Sequence[str],
) -> Residual:
    """A residual stage that relays the leftovers through ``chain``.

    Each family in turn is asked only for the nets still open, with everything
    accepted so far as obstacles — the composition that is already measured to
    work. A family missing from the registry is skipped and a family that
    raises costs its stage and nothing else, because a crashed follower must not
    turn a merged board into no board.
    """

    def run(problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        base_traces = tuple(problem.existing_traces)
        base_vias = tuple(problem.existing_vias)
        merged = RoutingSolution(router="residual", traces=base_traces, vias=base_vias)
        for name in chain:
            factory = registry.get(name)
            if factory is None:
                continue
            open_nets = set(conn.analyse(problem, merged).unconnected_nets)
            if not open_nets:
                break
            stage_problem = dataclasses.replace(
                problem,
                nets=tuple(n for n in problem.nets if n.id in open_nets),
                existing_traces=tuple(merged.traces),
                existing_vias=tuple(merged.vias),
            )
            try:
                stage = factory().route(stage_problem, budget)
            except Exception:  # noqa: BLE001 — a family that dies costs a stage
                continue
            merged = dataclasses.replace(
                merged,
                traces=tuple(merged.traces) + tuple(stage.traces),
                vias=tuple(merged.vias) + tuple(stage.vias),
            )
        return RoutingSolution(
            router="residual",
            traces=tuple(merged.traces)[len(base_traces):],
            vias=tuple(merged.vias)[len(base_vias):],
        )

    return run


# ---------------------------------------------------------------------------
# The board being assembled: who is placed, and who is in whose way
# ---------------------------------------------------------------------------


class _Board:
    """Placed net routings, and the one question the assembly asks of them:
    *which already-placed nets are in the way of this one?*

    Not a :class:`~routerlib.workspace.Workspace`. A workspace answers
    ``may I?`` with the first reason it finds and cannot un-commit, and both of
    those are wrong here. The assembly needs the **whole** blocking set, because
    a transplant that collides with one net is a candidate for repair and one
    that collides with five is not; and it needs to take copper back out again,
    because eviction is the only repair that exists. Legality against the fixed
    board is not re-asked: :func:`decompose` settled it once per candidate, with
    the same geometry, before anything was placed.
    """

    __slots__ = ("problem", "clearance", "placed", "_grids", "_drills", "_dirty")

    def __init__(self, problem: RoutingProblem, clearance: float) -> None:
        self.problem = problem
        self.clearance = clearance
        self.placed: dict[str, NetRouting] = {}
        self._grids: dict[str, GridIndex] = {}
        self._drills = GridIndex(2.0)
        self._dirty = True

    def copy(self) -> "_Board":
        clone = _Board(self.problem, self.clearance)
        clone.placed = dict(self.placed)
        return clone

    # -- membership ------------------------------------------------------

    def add(self, routing: NetRouting) -> None:
        self.placed[routing.net] = routing
        self._dirty = True

    def drop(self, nets) -> None:
        for net in nets:
            self.placed.pop(net, None)
        self._dirty = True

    @property
    def traces(self) -> tuple[Trace, ...]:
        return tuple(t for net in sorted(self.placed) for t in self.placed[net].traces)

    @property
    def vias(self) -> tuple[Via, ...]:
        return tuple(v for net in sorted(self.placed) for v in self.placed[net].vias)

    def solution(self, router: str = "recombine") -> RoutingSolution:
        return RoutingSolution(router=router, traces=self.traces, vias=self.vias)

    # -- the query -------------------------------------------------------

    def _reindex(self) -> None:
        self._grids = {TOP: GridIndex(2.0), BOTTOM: GridIndex(2.0)}
        self._drills = GridIndex(2.0)
        for net in sorted(self.placed):
            routing = self.placed[net]
            for trace in routing.traces:
                for a, b in trace.segments:
                    self._grids.setdefault(trace.layer, GridIndex(2.0)).insert(
                        segment_capsule(a.x, a.y, b.x, b.y, trace.width_mm), net
                    )
            for via in routing.vias:
                pad = disc_capsule(via.center.x, via.center.y, via.pad_mm)
                for layer in (TOP, BOTTOM):
                    self._grids.setdefault(layer, GridIndex(2.0)).insert(pad, net)
                self._drills.insert(
                    disc_capsule(via.center.x, via.center.y, via.drill_mm), net
                )
        self._dirty = False

    def blockers(self, routing: NetRouting) -> tuple[str, ...]:
        """Every placed net whose copper this routing may not share a board with.

        Clearance on the same layer for traces and via pads, plus hole-to-hole
        between drills on **either** layer and regardless of net — two barrels
        too close break out into each other whatever they carry, which is the
        rule ``Workspace.via_ok`` applies and the one a merge is most likely to
        get wrong, because each family's vias were legal before they met.
        """
        if self._dirty:
            self._reindex()
        hit: set[str] = set()
        net = routing.net
        margin = self.clearance + 0.05
        hole_margin = self.problem.rules.min_hole_to_hole_mm + 0.05

        def sweep(capsule, layers) -> None:
            for layer in layers:
                grid = self._grids.get(layer)
                if grid is None:
                    continue
                for other, owner in grid.query(capsule, margin):
                    if owner == net or owner in hit:
                        continue
                    if capsule_gap(capsule, other) < self.clearance:
                        hit.add(owner)

        for trace in routing.traces:
            for a, b in trace.segments:
                if a == b:
                    continue
                sweep(
                    segment_capsule(a.x, a.y, b.x, b.y, trace.width_mm),
                    (trace.layer,),
                )
        for via in routing.vias:
            sweep(disc_capsule(via.center.x, via.center.y, via.pad_mm), (TOP, BOTTOM))
            drill = disc_capsule(via.center.x, via.center.y, via.drill_mm)
            for other, owner in self._drills.query(drill, hole_margin):
                if owner in hit:
                    continue
                if capsule_gap(drill, other) < self.problem.rules.min_hole_to_hole_mm:
                    hit.add(owner)
        return tuple(sorted(hit))


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


@dataclass
class _Assembly:
    board: _Board
    assignments: list[Assignment]
    lost: list[str]
    evictions: int = 0
    repairs: int = 0


def _scarcity(viable: Mapping[str, Sequence[NetRouting]]):
    """Most-constrained-variable order: fewest viable routings first.

    A net two families can route is offered the board before a net eight can,
    because the one with two choices is the one that runs out of them. Among
    equally scarce nets the one that needs the most copper goes first — it has
    the least room to move. Ties break on the net id so the order is a property
    of the board and not of a dictionary.
    """

    def key(net: str) -> tuple:
        rows = viable.get(net) or ()
        longest = max((r.length_mm for r in rows), default=0.0)
        return (len(rows) if rows else 1 << 30, -round(longest, 6), net)

    return key


def _place(assembly: _Assembly, net: str, rows: Sequence[NetRouting],
           stage: str) -> bool:
    """First candidate with nothing in its way. No repair, no eviction."""
    rejected: list[str] = []
    for row in rows:
        blocking = assembly.board.blockers(row)
        if blocking:
            rejected.append(f"{row.source}: blocked by {len(blocking)} net(s)")
            continue
        assembly.board.add(row)
        assembly.assignments.append(
            Assignment(net=net, source=row.source, stage=stage,
                       viable_candidates=len(rows),
                       rejected_by_conflict=tuple(rejected))
        )
        return True
    if rows:
        assembly.lost.append(net)
    return False


def _assemble_free(problem, viable, clearance) -> _Assembly:
    assembly = _Assembly(_Board(problem, clearance), [], [])
    for net in sorted(viable, key=_scarcity(viable)):
        _place(assembly, net, viable[net], "merge")
    return assembly


def _assemble_anchored(problem, viable, clearance, base: str) -> _Assembly:
    """One family's board whole, then transplants into the space it left.

    The base goes in first and in scarcity order, which for a single family is
    only a tie-break: its nets co-existed on its own board, so they co-exist
    here. Anything it did not route is then offered to the other families,
    hardest net first, against a board that already holds the base.
    """
    assembly = _Assembly(_Board(problem, clearance), [], [])
    order = sorted(viable, key=_scarcity(viable))
    covered: set[str] = set()
    for net in order:
        rows = [r for r in viable[net] if r.source == base]
        if rows and _place(assembly, net, rows, "base"):
            covered.add(net)
    # A base net that did not fit is not lost — another family may still have
    # it, and it goes back into the pool rather than counting against the merge.
    assembly.lost.clear()
    for net in order:
        if net in covered:
            continue
        _place(assembly, net, [r for r in viable[net] if r.source != base],
               "transplant")
    return assembly


# ---------------------------------------------------------------------------
# The repair: rip up what blocks a transplant, and re-route it
# ---------------------------------------------------------------------------


def _repair(
    problem: RoutingProblem,
    assembly: _Assembly,
    viable: Mapping[str, Sequence[NetRouting]],
    *,
    max_evictions: int,
    reroute: Residual | None,
    budget: Budget,
) -> None:
    """Take out what stands in a transplant's way, then put it back elsewhere.

    This is the conflict repair, and it is the only part of recombination that
    can win a net no relay wins. A relay never removes the lead's copper, so a
    net the lead's copper blocks is a net the relay cannot have; every one of
    the transplants measured on this benchmark is refused for exactly that
    reason, at one to nine blocking nets each.

    Two ways to put a blocker back, tried in that order because of what they
    cost:

    1. **Swap it for another family's routing of the same net.** Free — the
       copper is already on disk. Measured on the three-family set: it never
       once worked. A blocker's alternatives were routed on boards that do not
       contain the transplant either, so they collide with it in the same place.
    2. **Re-route it.** ``reroute`` is a router given the evicted nets and the
       trial board — including the transplant — as obstacles. This is rip-up and
       reroute across a stage boundary, which none of the nine families does,
       and it is the mechanism the whole composition rests on.

    **The trade must be strictly positive.** One net gained for one net lost is
    a different board, not a better one, and a merge that accepts an even trade
    will wander. Every evicted net must come back or the attempt is rolled back
    — free, because the trial board is a copy.
    """
    if max_evictions <= 0:
        return
    order = _scarcity(viable)
    # A successful repair changes the board, so a net that did not fit before
    # it may fit after. Loop until a pass wins nothing; the cap is only there so
    # a bug in the progress accounting cannot spin.
    for _round in range(_REPAIR_ROUNDS):
        before = len(assembly.lost)
        _repair_pass(problem, assembly, viable, order,
                     max_evictions=max_evictions, reroute=reroute, budget=budget)
        if len(assembly.lost) >= before:
            return


#: How many times the repair may sweep the still-open nets. The loop already
#: stops the moment a pass wins nothing, so this only bounds the damage if the
#: progress accounting is ever wrong: a wasted pass rather than a hung suite.
_REPAIR_ROUNDS = 4


def _repair_pass(
    problem: RoutingProblem,
    assembly: _Assembly,
    viable: Mapping[str, Sequence[NetRouting]],
    order,
    *,
    max_evictions: int,
    reroute: Residual | None,
    budget: Budget,
) -> None:
    for net in list(assembly.lost):
        # The board may have moved under us since this net was given up on.
        free = [r for r in viable.get(net, ()) if not assembly.board.blockers(r)]
        if free:
            assembly.board.add(free[0])
            assembly.lost.remove(net)
            assembly.assignments.append(
                Assignment(net=net, source=free[0].source, stage="transplant",
                           viable_candidates=len(viable.get(net, ())))
            )
            continue
        rows = viable.get(net, ())
        for row in rows:
            blocking = assembly.board.blockers(row)
            if not blocking or len(blocking) > max_evictions:
                continue
            trial = assembly.board.copy()
            trial.drop(blocking)
            if trial.blockers(row):
                continue  # something un-evictable is also in the way
            trial.add(row)
            open_again = []
            for evicted in sorted(blocking, key=order):
                for other in viable.get(evicted, ()):
                    if not trial.blockers(other):
                        trial.add(other)
                        break
                else:
                    open_again.append(evicted)
            if open_again:
                if reroute is None:
                    continue
                stage = dataclasses.replace(
                    problem,
                    nets=tuple(n for n in problem.nets if n.id in set(open_again)),
                    existing_traces=trial.traces,
                    existing_vias=trial.vias,
                )
                rerouted = reroute(stage, budget)
                probe = RoutingSolution(
                    router="probe",
                    traces=trial.traces + tuple(rerouted.traces),
                    vias=trial.vias + tuple(rerouted.vias),
                )
                if set(conn.analyse(problem, probe).unconnected_nets) & set(open_again):
                    continue
                # The new copper *is* those nets' routing now, so it goes on the
                # board's index like any other candidate. Leaving it beside the
                # index would make it invisible to the next blocker query, and a
                # merge that cannot see its own copper is the naive merge again.
                for evicted in open_again:
                    traces, vias = _slice(rerouted, evicted)
                    trial.add(
                        NetRouting(
                            net=evicted, source="rerouted",
                            traces=traces, vias=vias,
                            connects=True, legal=True, refusal="",
                            length_mm=sum(t.length_mm for t in traces),
                            via_count=len(vias),
                            blocked_mm2=_blocked_area(
                                traces, vias, assembly.board.clearance
                            ),
                        )
                    )
            assembly.board = trial
            assembly.evictions += len(blocking)
            assembly.repairs += 1
            assembly.lost.remove(net)
            for previous in [a for a in assembly.assignments if a.net in blocking]:
                assembly.assignments.remove(previous)
            assembly.assignments.append(
                Assignment(
                    net=net, source=row.source, stage="repair",
                    viable_candidates=len(rows),
                    rejected_by_conflict=tuple(f"evicted {b}" for b in blocking),
                )
            )
            for evicted in sorted(blocking, key=order):
                placed = assembly.board.placed.get(evicted)
                source = placed.source if placed else "lost"
                assembly.assignments.append(
                    Assignment(
                        net=evicted,
                        source=source,
                        # "rehomed" is another family's copper for the same net;
                        # "rerouted" is copper that did not exist until the
                        # repair asked for it. Only the second one costs a
                        # router run, and only the second one is new geometry.
                        stage="rerouted" if source == "rerouted" else "rehomed",
                        viable_candidates=len(viable.get(evicted, ())),
                    )
                )
            break


# ---------------------------------------------------------------------------
# The merge
# ---------------------------------------------------------------------------


def recombine(
    problem: RoutingProblem,
    solutions: Mapping[str, RoutingSolution],
    *,
    mode: str = ANCHORED,
    base: str | None = None,
    ranking: str = "obstruction",
    clearance_mm: float | None = None,
    max_evictions: int = 2,
    reroute: Residual | None = None,
    residual: Residual | None = None,
    budget: Budget | None = None,
    order: Sequence[str] | None = None,
) -> Recombination:
    """Build one board out of ``solutions``, net by net.

    ``base`` names the family to anchor on; ``None`` tries every family and
    keeps the best result, which is affordable because a transplant runs no
    router. ``max_evictions`` is how many placed nets the repair may take out to
    make room for one more — ``0`` disables the repair, which is the control
    that says how much of the result is the repair and how much is the merge.
    ``order`` breaks ties between equally good candidates and defaults to the
    sorted family names, so the same inputs give the same board whatever order
    the caller happened to build the mapping in. Determinism is not a nicety
    here: two runs that merge differently cannot be compared to each other, let
    alone to a relay.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r} (have: {', '.join(MODES)})")
    started = time.perf_counter()
    clearance = (
        problem.rules.min_clearance_mm if clearance_mm is None else clearance_mm
    )
    names = tuple(order) if order else tuple(sorted(solutions))
    key = _rank_key(ranking, names)

    candidates = decompose(problem, solutions, clearance_mm=clearance)

    # Nets already joined with no copper — pads that touch, or a plane. Not the
    # merge's work, and counting them as its work would inflate it.
    already = set(conn.analyse(problem, RoutingSolution(router="empty")).connected_nets)

    viable: dict[str, list[NetRouting]] = {}
    for net, rows in candidates.items():
        if net in already:
            continue
        viable[net] = sorted((r for r in rows if r.viable and not r.empty), key=key)

    routable = len(problem.routable_nets) or 1
    ceiling = (sum(1 for rows in viable.values() if rows) + len(already)) / routable

    if mode == FREE:
        assembly = _assemble_free(problem, viable, clearance)
        chosen_base = ""
        base_scores: tuple[tuple[str, float], ...] = ()
    else:
        bases = [base] if base else [
            n for n in names
            if any(r.source == n for rows in viable.values() for r in rows)
        ]
        if not bases:
            bases = list(names[:1])
        tried: list[tuple[str, float, _Assembly]] = []
        for name in bases:
            attempt = _assemble_anchored(problem, viable, clearance, name)
            reached = conn.analyse(problem, attempt.board.solution()).completeness
            tried.append((name, reached, attempt))
        # Most nets wins; ties go to the fewest vias, then the least copper,
        # then the first family named — the harness quality tier, applied to a
        # choice the harness never sees.
        tried.sort(
            key=lambda row: (
                -row[1],
                len(row[2].board.vias),
                round(sum(t.length_mm for t in row[2].board.traces), 6),
                names.index(row[0]) if row[0] in names else len(names),
            )
        )
        chosen_base, _, assembly = tried[0]
        base_scores = tuple((n, round(c, 6)) for n, c, _a in tried)

    # The repair runs once, on the winner. Running it inside every base's
    # assembly would call a router N times to answer a question decided by
    # geometry, and on a shared machine that is the difference between a
    # composition and a nuisance.
    _repair(
        problem, assembly, viable,
        max_evictions=max_evictions,
        reroute=reroute,
        budget=budget or Budget(),
    )

    merged = assembly.board.solution()
    assignments = list(assembly.assignments)

    linked = conn.analyse(problem, merged)
    unassigned = tuple(linked.unconnected_nets)

    if residual is not None and unassigned:
        stage_problem = dataclasses.replace(
            problem,
            nets=tuple(n for n in problem.nets if n.id in set(unassigned)),
            existing_traces=tuple(merged.traces),
            existing_vias=tuple(merged.vias),
        )
        extra = residual(stage_problem, budget or Budget())
        merged = dataclasses.replace(
            merged,
            traces=tuple(merged.traces) + tuple(extra.traces),
            vias=tuple(merged.vias) + tuple(extra.vias),
        )
        after = conn.analyse(problem, merged)
        for net in sorted(set(after.connected_nets) - set(linked.connected_nets)):
            assignments.append(
                Assignment(
                    net=net,
                    source="residual",
                    stage="residual",
                    viable_candidates=len(viable.get(net, ())),
                )
            )
        linked = after

    merged = dataclasses.replace(
        merged,
        complete=linked.completeness >= 1.0,
        unrouted_nets=linked.unconnected_nets,
        wall_clock_s=time.perf_counter() - started,
    )

    best_input = max(
        (conn.analyse(problem, s).completeness for s in solutions.values()),
        default=0.0,
    )
    lost = tuple(sorted(set(assembly.lost) & set(linked.unconnected_nets)))

    notes: list[str] = []
    result = Recombination(
        solution=merged,
        assignments=tuple(assignments),
        base=chosen_base,
        mode=mode,
        unassigned=unassigned,
        lost_to_conflict=lost,
        completeness=linked.completeness,
        ceiling=ceiling,
        best_input=best_input,
        ranking=ranking,
        clearance_mm=clearance,
        seconds=time.perf_counter() - started,
        evictions=assembly.evictions,
        repairs=assembly.repairs,
        base_scores=base_scores,
    )
    if mode == ANCHORED and result.transplanted == 0:
        notes.append(
            "no net was transplanted — this is the base family plus a residual "
            "stage, which is a relay, and must be reported as one"
        )
    if lost:
        notes.append(
            f"{len(lost)} net(s) had a viable routing that no ranking could fit: "
            "the merge lost them to geometry, not to the routers"
        )
    return dataclasses.replace(result, notes=tuple(notes))


# ---------------------------------------------------------------------------
# As a router
# ---------------------------------------------------------------------------


class RecombineRouter:
    """:func:`recombine` behind the ``Router`` protocol.

    Runs each input family on the whole board first, which is the expensive way
    to get the inputs and the only way that works when nothing is on disk. Every
    input run is independent, so the wall clock of this router is the slowest
    family and not the sum — provided the caller parallelises, which the suite
    runner deliberately does not, because the machine is shared.
    """

    name = "recombine"

    def __init__(
        self,
        registry: Mapping[str, Callable[[], object]],
        inputs: Sequence[str],
        *,
        residual_chain: Sequence[str] = (),
        mode: str = ANCHORED,
        ranking: str = "obstruction",
        clearance_mm: float | None = None,
    ) -> None:
        self.registry = registry
        self.inputs = tuple(inputs)
        self.residual_chain = tuple(residual_chain)
        self.mode = mode
        self.ranking = ranking
        self.clearance_mm = clearance_mm

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        solutions: dict[str, RoutingSolution] = {}
        notes: list[str] = []
        for name in self.inputs:
            factory = self.registry.get(name)
            if factory is None:
                notes.append(f"{name} is not registered; dropped as an input")
                continue
            try:
                solutions[name] = factory().route(problem, budget)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{name} raised {type(exc).__name__}: {exc}; dropped")
        if not solutions:
            raise RuntimeError(
                f"no input family produced a solution (asked: {', '.join(self.inputs)})"
            )
        residual = (
            relay_residual(self.registry, self.residual_chain)
            if self.residual_chain
            else None
        )
        result = recombine(
            problem,
            solutions,
            mode=self.mode,
            ranking=self.ranking,
            clearance_mm=self.clearance_mm,
            residual=residual,
            budget=budget,
            order=self.inputs,
        )
        return dataclasses.replace(
            result.solution,
            router=(
                f"recombine[{result.base}"
                f"+{result.transplanted}]" if result.base else "recombine[free]"
            ),
            notes=tuple(notes) + result.notes,
        )


__all__ = [
    "ANCHORED",
    "Assignment",
    "FREE",
    "MODES",
    "NetRouting",
    "RANKINGS",
    "Recombination",
    "RecombineRouter",
    "Residual",
    "decompose",
    "recombine",
    "relay_residual",
]
