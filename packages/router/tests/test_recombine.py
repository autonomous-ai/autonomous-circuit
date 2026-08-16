"""Recombination: does the merge do what it says, and does it stop when it
should?

Every board here is built by hand so the right answer is a statement and not an
opinion. The centrepiece is :func:`one_channel_board` — two nets, one channel
wide enough for exactly one of them, and two families that each take the
channel for their own net. It is the smallest board on which recombination can
be *right*: neither input connects both nets, no merge that only fills free
lanes can either, and the only way to two nets is to rip one out and re-route
it. If the repair ever stops working, this board says so in one line.
"""

from __future__ import annotations

import dataclasses

import pytest

from routerlib import connectivity as conn
from routerlib.compositions.recombine import (
    ANCHORED,
    FREE,
    NetRouting,
    RecombineRouter,
    decompose,
    recombine,
    relay_residual,
)
from routerlib.model import (
    BOTTOM,
    TOP,
    Board,
    Budget,
    DesignRules,
    Keepout,
    Net,
    Pad,
    Point,
    RoutingProblem,
    RoutingSolution,
    Trace,
    Via,
)

RULES = DesignRules()  # plain defaults: no circuitpy needed to build a problem


def _pad(pad_id: str, net: str, x: float, y: float, *, component="U1") -> Pad:
    return Pad(
        id=pad_id, net=net, center=Point(x, y), width_mm=1.0, height_mm=1.0,
        layers=(TOP,), kind="smd", component=component, port_id=f"port_{pad_id}",
    )


def _trace(trace_id: str, net: str, points, *, width=0.2, layer=TOP) -> Trace:
    return Trace(
        id=trace_id, net=net, layer=layer,
        points=tuple(Point(x, y) for x, y in points), width_mm=width,
    )


def _net(net_id: str, pads: tuple[str, ...]) -> Net:
    return Net(
        id=net_id, name=net_id, net_class="signal", pads=pads,
        min_width_mm=0.2, source_net_id=f"source_{net_id}",
    )


# ---------------------------------------------------------------------------
# The one-channel board
# ---------------------------------------------------------------------------
#
#   y=8   +--------- A's detour ---------+        (open board, nobody uses it)
#   y=6   .......###.......                       wall, upper half
#   y=4        B o     o B                        net B's pads
#   y=1.5 ------- the channel -------              1mm gap, fits one net
#   y=1   .......###.......                       wall, lower half
#   y=0   A o             o A                     net A's pads
#
# ``alpha`` takes the channel for A and gives up on B; ``beta`` takes it for B
# and gives up on A. Both are honest 50% solutions. Two nets is only reachable
# by evicting whoever is in the channel and sending them over the top.


def one_channel_board() -> RoutingProblem:
    return RoutingProblem(
        id="one-channel",
        board=Board(width_mm=24.0, height_mm=24.0),
        rules=RULES,
        pads=(
            _pad("a1", "A", -6.0, 0.0),
            _pad("a2", "A", 6.0, 0.0),
            _pad("b1", "B", -4.0, 4.0, component="U2"),
            _pad("b2", "B", 4.0, 4.0, component="U2"),
        ),
        keepouts=(
            Keepout(id="wall_low", center=Point(0.0, -5.0),
                    width_mm=1.0, height_mm=12.0, layers=(TOP, BOTTOM)),
            Keepout(id="wall_high", center=Point(0.0, 4.0),
                    width_mm=1.0, height_mm=4.0, layers=(TOP, BOTTOM)),
        ),
        nets=(_net("A", ("a1", "a2")), _net("B", ("b1", "b2"))),
    )


def alpha_solution() -> RoutingSolution:
    """A through the channel; B not routed."""
    return RoutingSolution(
        router="alpha",
        traces=(_trace("al_a", "A", ((-6, 0), (-3, 1.5), (3, 1.5), (6, 0))),),
    )


def beta_solution() -> RoutingSolution:
    """B through the channel; A not routed."""
    return RoutingSolution(
        router="beta",
        traces=(_trace("be_b", "B", ((-4, 4), (-3, 1.5), (3, 1.5), (4, 4))),),
    )


def over_the_top(net: str = "A"):
    """A stub re-router: sends ``net`` around the wall, the long way.

    A stub rather than a family, on purpose. What is under test is whether the
    repair asks for a re-route, installs the answer and rolls back when the
    answer does not arrive — not whether ``maze-astar`` can find this path.
    """

    def run(problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        if net not in {n.id for n in problem.nets}:
            return RoutingSolution(router="stub")
        return RoutingSolution(
            router="stub",
            traces=(_trace("detour", net, ((-6, 0), (-6, 8), (6, 8), (6, 0))),),
        )

    return run


def dead_router():
    """A re-router that finds nothing. The repair must roll back, not accept."""

    def run(problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        return RoutingSolution(router="stub")

    return run


# ---------------------------------------------------------------------------
# decompose
# ---------------------------------------------------------------------------


def test_decompose_splits_by_net_and_measures_each_alone():
    problem = one_channel_board()
    candidates = decompose(problem, {"alpha": alpha_solution()})

    a = candidates["A"][0]
    assert a.source == "alpha"
    assert a.connects and a.legal and a.viable
    assert len(a.traces) == 1 and a.via_count == 0
    assert a.length_mm == pytest.approx(
        3.354101966 + 6.0 + 3.354101966, abs=1e-6
    )
    # Blocked area is the ranking currency: swept copper widened by clearance.
    assert a.blocked_mm2 > a.length_mm * (0.2 + 2 * RULES.min_clearance_mm) * 0.9

    b = candidates["B"][0]
    assert not b.connects
    assert not b.viable
    assert b.refusal == "does not connect the net"


def test_decompose_refuses_a_routing_that_is_illegal_on_its_own():
    """Copper through a keepout was never a candidate on any board, and saying
    so with the reason is the difference between a dropped net and a mystery."""
    problem = one_channel_board()
    through_the_wall = RoutingSolution(
        router="reckless",
        traces=(_trace("bad", "A", ((-6, 0), (6, 0))),),  # straight through wall_low
    )
    row = decompose(problem, {"reckless": through_the_wall})["A"][0]
    assert row.connects
    assert not row.legal
    assert "keepout" in row.refusal
    assert "wall_low" in row.refusal


def test_decompose_keeps_the_failures_so_a_caller_can_tell_them_apart():
    problem = one_channel_board()
    candidates = decompose(
        problem, {"alpha": alpha_solution(), "beta": beta_solution()}
    )
    assert {r.source for r in candidates["A"]} == {"alpha", "beta"}
    assert [r.viable for r in sorted(candidates["A"], key=lambda r: r.source)] == [
        True, False
    ]


# ---------------------------------------------------------------------------
# The control: one input in, the same board out
# ---------------------------------------------------------------------------


def test_one_input_reproduces_that_input_exactly():
    """The control that makes every other number in this module mean something.

    If merging a single solution with itself loses a net, then every comparison
    against a single family is measuring the merge's own damage and not the
    composition.
    """
    problem = one_channel_board()
    alpha = alpha_solution()
    result = recombine(problem, {"alpha": alpha})

    assert result.completeness == conn.analyse(problem, alpha).completeness
    assert result.base == "alpha"
    assert result.transplanted == 0
    assert result.single_source
    assert any("relay" in note for note in result.notes)
    assert {t.id for t in result.solution.traces} == {t.id for t in alpha.traces}


def test_a_net_already_joined_without_copper_is_not_the_merges_work():
    """Two pads that touch are connected before anyone routes. Counting them as
    a merge's achievement inflates it for free."""
    problem = RoutingProblem(
        id="touching",
        board=Board(width_mm=20.0, height_mm=20.0),
        rules=RULES,
        pads=(_pad("p1", "N", 0.0, 0.0), _pad("p2", "N", 0.6, 0.0)),
        nets=(_net("N", ("p1", "p2")),),
    )
    result = recombine(problem, {"alpha": RoutingSolution(router="alpha")})
    assert result.completeness == 1.0
    assert result.assignments == ()
    assert result.ceiling == 1.0


# ---------------------------------------------------------------------------
# Free merging is a loss, and it is pinned here
# ---------------------------------------------------------------------------


def test_free_merging_can_lose_a_net_that_anchoring_keeps():
    """The measured negative result, on the smallest board that shows it.

    ``FREE`` cherry-picks each net independently and both families' channel
    routings are equally good in isolation, so it takes one and then cannot
    place the other. ``ANCHORED`` takes a whole family and is never worse than
    that family. The two are the same here — one net — and the point of the
    test is that ``FREE`` is *not better*, and on real boards it is much worse:
    48 of 62 nets against the best input's 54.
    """
    problem = one_channel_board()
    inputs = {"alpha": alpha_solution(), "beta": beta_solution()}
    free = recombine(problem, inputs, mode=FREE, max_evictions=0)
    anchored = recombine(problem, inputs, mode=ANCHORED, max_evictions=0)

    assert free.completeness <= anchored.completeness
    assert anchored.completeness == 0.5
    assert anchored.base in ("alpha", "beta")


def test_anchored_is_never_worse_than_the_base_it_chose():
    problem = one_channel_board()
    inputs = {"alpha": alpha_solution(), "beta": beta_solution()}
    result = recombine(problem, inputs, max_evictions=0)
    assert result.completeness >= result.best_input


def test_a_transplant_the_base_blocks_is_reported_not_hidden():
    problem = one_channel_board()
    inputs = {"alpha": alpha_solution(), "beta": beta_solution()}
    result = recombine(problem, inputs, max_evictions=0)
    assert result.transplanted == 0
    assert result.lost_to_conflict == ("B",) if result.base == "alpha" else ("A",)
    assert any("no ranking could fit" in note for note in result.notes)


# ---------------------------------------------------------------------------
# The repair
# ---------------------------------------------------------------------------


def test_the_repair_wins_a_net_neither_input_and_no_relay_can():
    """The whole point of the module, in one assertion.

    Both inputs connect one net of two. A relay cannot reach two: the lead's
    copper owns the channel and a follower may route around it but never
    through it. Evicting the channel's occupant, giving the channel to the
    other net and sending the evicted one over the top reaches both.
    """
    problem = one_channel_board()
    inputs = {"alpha": alpha_solution(), "beta": beta_solution()}
    result = recombine(
        problem, inputs, max_evictions=1, reroute=over_the_top("A"), base="alpha"
    )

    assert result.best_input == 0.5
    assert result.completeness == 1.0
    assert result.repairs == 1
    assert result.evictions == 1
    assert result.solution.complete
    stages = {a.net: a.stage for a in result.assignments}
    assert stages["B"] == "repair"
    assert stages["A"] == "rerouted"


def test_the_repair_rolls_back_when_the_evicted_net_cannot_come_back():
    """An even trade is a different board, not a better one. Without a route
    for the evicted net the whole attempt is discarded, and the result is the
    one the merge would have reached without a repair at all."""
    problem = one_channel_board()
    inputs = {"alpha": alpha_solution(), "beta": beta_solution()}
    without = recombine(problem, inputs, max_evictions=0, base="alpha")
    failed = recombine(
        problem, inputs, max_evictions=1, reroute=dead_router(), base="alpha"
    )

    assert failed.completeness == without.completeness == 0.5
    assert failed.repairs == 0
    assert failed.evictions == 0
    assert failed.solution.fingerprint() == without.solution.fingerprint()


def test_the_repair_is_off_at_zero_evictions():
    problem = one_channel_board()
    inputs = {"alpha": alpha_solution(), "beta": beta_solution()}
    result = recombine(
        problem, inputs, max_evictions=0, reroute=over_the_top("A"), base="alpha"
    )
    assert result.repairs == 0
    assert result.completeness == 0.5


def test_the_repair_will_not_evict_more_than_it_is_allowed_to():
    """A cap of zero and a cap of one are different answers on this board, so
    the cap is doing what it says rather than being decorative."""
    problem = one_channel_board()
    inputs = {"alpha": alpha_solution(), "beta": beta_solution()}
    capped = recombine(
        problem, inputs, max_evictions=1, reroute=over_the_top("A"), base="alpha"
    )
    assert capped.evictions <= 1
    assert capped.completeness == 1.0


def test_rerouted_copper_is_visible_to_the_next_blocker_query():
    """Copper the repair produced has to join the board's index, or the merge
    stops being able to see its own copper — which is the naive merge again."""
    problem = one_channel_board()
    inputs = {"alpha": alpha_solution(), "beta": beta_solution()}
    result = recombine(
        problem, inputs, max_evictions=1, reroute=over_the_top("A"), base="alpha"
    )
    ids = {t.id for t in result.solution.traces}
    assert "detour" in ids
    assert "al_a" not in ids  # the evicted routing is gone, not shipped as well
    assert conn.analyse(problem, result.solution).completeness == 1.0


# ---------------------------------------------------------------------------
# Conflicts the merge must see
# ---------------------------------------------------------------------------


def test_two_families_vias_at_the_same_point_are_a_conflict():
    """Each family's vias were legal before they met. A merge that cannot see
    a via landing on another family's via ships two drills in one hole."""

    def through_hole(pad_id: str, net: str, x: float, y: float) -> Pad:
        return Pad(
            id=pad_id, net=net, center=Point(x, y), width_mm=1.0, height_mm=1.0,
            layers=(TOP, BOTTOM), kind="plated_hole", component="J1",
            port_id=f"port_{pad_id}",
        )

    problem = RoutingProblem(
        id="via-clash",
        board=Board(width_mm=20.0, height_mm=20.0),
        rules=RULES,
        pads=(
            _pad("a1", "A", -5.0, 0.0), through_hole("a2", "A", 5.0, 0.0),
            _pad("b1", "B", -5.0, 5.0, component="U2"),
            through_hole("b2", "B", 5.0, 5.0),
        ),
        nets=(_net("A", ("a1", "a2")), _net("B", ("b1", "b2"))),
    )

    # Both families change layer at exactly the same place.
    def hop(net: str, y: float, tag: str) -> RoutingSolution:
        return RoutingSolution(
            router=tag,
            traces=(
                _trace(f"{tag}_1", net, ((-5, y), (0, 0)), layer=TOP),
                _trace(f"{tag}_2", net, ((0, 0), (5, y)), layer=BOTTOM),
            ),
            vias=(Via(id=f"{tag}_v", net=net, center=Point(0.0, 0.0)),),
        )

    inputs = {"alpha": hop("A", 0.0, "alpha"), "beta": hop("B", 5.0, "beta")}
    assert all(
        conn.analyse(problem, s).completeness == 0.5 for s in inputs.values()
    ), "each input must connect its own net, or this tests nothing"

    result = recombine(problem, inputs, max_evictions=0)
    # One of them gets the spot; the other is refused rather than merged in.
    assert len(result.solution.vias) == 1
    assert result.completeness == 0.5
    assert len(result.lost_to_conflict) == 1


# ---------------------------------------------------------------------------
# Determinism and bookkeeping
# ---------------------------------------------------------------------------


def test_the_same_inputs_give_byte_identical_copper():
    problem = one_channel_board()
    inputs = {"alpha": alpha_solution(), "beta": beta_solution()}
    first = recombine(problem, inputs, max_evictions=1, reroute=over_the_top("A"))
    second = recombine(problem, inputs, max_evictions=1, reroute=over_the_top("A"))
    assert first.solution.fingerprint() == second.solution.fingerprint()


def test_the_order_the_caller_built_the_mapping_in_does_not_change_the_board():
    problem = one_channel_board()
    forward = recombine(
        problem, {"alpha": alpha_solution(), "beta": beta_solution()},
        max_evictions=0,
    )
    backward = recombine(
        problem, {"beta": beta_solution(), "alpha": alpha_solution()},
        max_evictions=0,
    )
    assert forward.solution.fingerprint() == backward.solution.fingerprint()
    assert forward.base == backward.base


def test_the_ceiling_is_what_the_inputs_can_reach_and_never_exceeded():
    problem = one_channel_board()
    inputs = {"alpha": alpha_solution(), "beta": beta_solution()}
    result = recombine(problem, inputs, max_evictions=0)
    assert result.ceiling == 1.0  # somebody routes each net, in isolation
    assert result.completeness <= result.ceiling


def test_an_unknown_mode_is_refused_rather_than_silently_defaulted():
    with pytest.raises(ValueError, match="unknown mode"):
        recombine(one_channel_board(), {"alpha": alpha_solution()}, mode="best")


def test_an_unknown_ranking_is_refused():
    with pytest.raises(ValueError, match="unknown ranking"):
        recombine(one_channel_board(), {"alpha": alpha_solution()}, ranking="length")


def test_as_dict_carries_the_numbers_a_report_needs():
    problem = one_channel_board()
    result = recombine(
        problem, {"alpha": alpha_solution(), "beta": beta_solution()},
        max_evictions=1, reroute=over_the_top("A"), base="alpha",
    )
    payload = result.as_dict()
    assert payload["mode"] == ANCHORED
    assert payload["base"] == "alpha"
    assert payload["repairs"] == 1
    assert payload["evictions"] == 1
    assert payload["completeness"] == 1.0
    assert payload["bestInput"] == 0.5
    assert payload["sources"]  # who supplied what, by family


# ---------------------------------------------------------------------------
# The residual stage
# ---------------------------------------------------------------------------


class _StubRouter:
    """Routes exactly one named net, straight, and only if it is asked for."""

    def __init__(self, net: str, points) -> None:
        self.net = net
        self.points = points

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        if self.net not in {n.id for n in problem.nets}:
            return RoutingSolution(router="stub")
        return RoutingSolution(
            router="stub", traces=(_trace(f"r_{self.net}", self.net, self.points),)
        )


def test_relay_residual_returns_only_what_it_added():
    """A residual that returns the copper it was handed would double every
    trace on the board the moment the caller merges it back in."""
    problem = one_channel_board()
    seeded = dataclasses.replace(
        problem, existing_traces=alpha_solution().traces
    )
    residual = relay_residual(
        {"stub": lambda: _StubRouter("B", ((-4, 4), (-4, 8), (4, 8), (4, 4)))},
        ("stub",),
    )
    out = residual(seeded, Budget())
    assert {t.id for t in out.traces} == {"r_B"}


def test_relay_residual_survives_a_family_that_is_missing_or_raises():
    class _Explodes:
        def route(self, problem, budget):
            raise RuntimeError("boom")

    problem = one_channel_board()
    residual = relay_residual(
        {
            "explodes": _Explodes,
            "stub": lambda: _StubRouter("B", ((-4, 4), (-4, 8), (4, 8), (4, 4))),
        },
        ("absent", "explodes", "stub"),
    )
    out = residual(problem, Budget())
    assert {t.id for t in out.traces} == {"r_B"}


def test_the_residual_stage_picks_up_what_the_merge_left_open():
    problem = one_channel_board()
    result = recombine(
        problem,
        {"alpha": alpha_solution(), "beta": beta_solution()},
        max_evictions=0,
        base="alpha",
        residual=relay_residual(
            {"stub": lambda: _StubRouter("B", ((-4, 4), (-4, 8), (4, 8), (4, 4)))},
            ("stub",),
        ),
    )
    assert result.completeness == 1.0
    assert [a.stage for a in result.assignments if a.net == "B"] == ["residual"]


# ---------------------------------------------------------------------------
# As a router
# ---------------------------------------------------------------------------


def test_the_router_wrapper_runs_its_inputs_and_merges_them():
    problem = one_channel_board()
    registry = {
        "alpha": lambda: _Replay(alpha_solution()),
        "beta": lambda: _Replay(beta_solution()),
    }
    router = RecombineRouter(registry, ("alpha", "beta"))
    solution = router.route(problem, Budget())
    assert conn.analyse(problem, solution).completeness == 0.5
    assert solution.router.startswith("recombine[")


def test_the_router_wrapper_says_which_input_was_missing():
    problem = one_channel_board()
    router = RecombineRouter(
        {"alpha": lambda: _Replay(alpha_solution())}, ("alpha", "ghost")
    )
    solution = router.route(problem, Budget())
    assert any("ghost" in note for note in solution.notes)


def test_the_router_wrapper_refuses_to_return_an_empty_board():
    """No input means a configuration problem, and a configuration problem that
    looks like a routing result is the failure this package exists to stop."""
    router = RecombineRouter({}, ("alpha",))
    with pytest.raises(RuntimeError, match="no input family"):
        router.route(one_channel_board(), Budget())


class _Replay:
    def __init__(self, solution: RoutingSolution) -> None:
        self.solution = solution

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        return self.solution
