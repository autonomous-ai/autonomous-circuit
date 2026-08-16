"""The handoff: what the router asks a human for when it cannot finish.

Every test here is about a claim reaching a person. The module is allowed to
say "I do not know"; it is not allowed to say something a measurement does not
support, so most of these assert the *absence* of a claim as hard as they
assert its presence.
"""

from __future__ import annotations

import pytest

from routerlib import diagnose as dg
from routerlib.connectivity import analyse, pad_components
from routerlib.model import (
    BOTTOM,
    TOP,
    Board,
    Keepout,
    Net,
    Point,
    RoutingProblem,
    RoutingSolution,
    Trace,
)
from routerfix import RULES, pad, straight_trace, two_pad_board


def _empty(problem: RoutingProblem) -> RoutingSolution:
    return RoutingSolution(router="none")


# ---------------------------------------------------------------------------
# The floor: nothing to say
# ---------------------------------------------------------------------------


def test_a_finished_board_asks_for_nothing():
    problem = two_pad_board()
    result = dg.diagnose(problem, straight_trace(problem))
    assert result.complete
    assert result.asks == ()
    assert result.nets == ()
    assert result.as_dict()["unroutedNets"] == 0


def test_an_open_board_is_a_router_limit_not_a_placement_problem():
    """Two pads, 10mm of empty board between them, no copper. Nothing is in the
    way, so the only honest answer is that the router did not do it."""
    problem = two_pad_board()
    result = dg.diagnose(problem, _empty(problem))
    assert [n.verdict for n in result.nets] == ["router_limit"]
    assert [a.kind for a in result.asks] == ["router_limit"]
    assert result.asks[0].move is None
    assert "nothing on the board has to move" in " ".join(result.asks[0].evidence)


# ---------------------------------------------------------------------------
# A wall between the pads
# ---------------------------------------------------------------------------


def _walled_board(gap_mm: float, *, component_b: str = "C9") -> RoutingProblem:
    """``p1`` and ``p2`` 10mm apart with a wall of foreign copper between them,
    pierced by one channel of exactly ``gap_mm`` of copper-to-copper space.

    The wall is made of plated holes so it blocks **both** layers. Built out of
    surface pads the first time, it blocked nothing: the search hopped to the
    back of the board and walked round, which is the correct answer to the
    wrong question.
    """
    half = gap_mm / 2.0 + 0.5  # pads are 1.0mm tall, so half-height is 0.5
    def wall(pad_id, net, y, component):
        return pad(pad_id, net, 0.0, y, w=0.6, h=1.0, kind="plated_hole",
                   component=component)

    blockers = (wall("b1", "N2", half, "R4"), wall("b2", "N3", -half, component_b))
    fence = tuple(
        wall(f"f{i}", "N2", half + 1.0 + i * 1.0, "R4") for i in range(9)
    ) + tuple(
        wall(f"g{i}", "N3", -half - 1.0 - i * 1.0, component_b) for i in range(9)
    )
    return two_pad_board(
        extra_pads=blockers + fence,
        extra_nets=(
            Net(id="N2", name="GND", net_class="ground",
                pads=("b1",) + tuple(f"f{i}" for i in range(9)), min_width_mm=0.5),
            Net(id="N3", name="VBUS", net_class="power",
                pads=("b2",) + tuple(f"g{i}" for i in range(9)), min_width_mm=0.5),
        ),
    )


def test_a_channel_too_narrow_names_both_walls_and_measures_the_gap():
    # 0.30mm of copper-to-copper space: 0.10mm clearance each side leaves
    # 0.10mm for copper, and the net is 0.20mm wide.
    problem = _walled_board(0.30)
    result = dg.diagnose(problem, _empty(problem))
    (net,) = result.nets
    assert net.verdict == "pinched"
    assert net.proven is True

    ask = result.asks[0]
    assert ask.pinch is not None and ask.pinch.second is not None
    assert {ask.pinch.first.label, ask.pinch.second.label} == {"R4", "C9"}
    assert ask.pinch.gap_mm == pytest.approx(0.30, abs=0.03)
    assert ask.pinch.usable_mm == pytest.approx(0.10, abs=0.03)
    assert ask.needed_mm == pytest.approx(0.20)
    # The headline number and the pinch number are the same number.
    assert f"{ask.pinch.usable_mm:.2f}" in ask.headline


def test_a_channel_wide_enough_is_never_reported_as_a_blockage():
    problem = _walled_board(0.60)  # 0.40mm usable against a 0.20mm net
    result = dg.diagnose(problem, _empty(problem))
    (net,) = result.nets
    assert net.verdict == "router_limit"
    assert all(ask.kind == "router_limit" for ask in result.asks)


def test_the_measured_move_is_performed_and_re_measured():
    problem = _walled_board(0.30)
    result = dg.diagnose(problem, _empty(problem))
    ask = result.asks[0]
    assert ask.kind == "move_part", ask.evidence
    move = ask.move
    assert move is not None
    assert move.part in ("R4", "C9")
    # It opens the channel past what the net needs, and says so with numbers.
    assert move.after_usable_mm >= ask.needed_mm
    assert move.distance_mm > 0
    assert move.heading in ("north", "south")
    assert "re-measured after the move" in move.caveat
    assert "not measured" in move.caveat  # what it does *not* claim


def test_the_move_is_withheld_when_the_part_cannot_travel():
    """Box the mover in: a hard wall 0.02mm behind each blocker means no
    distance is legal, and the report must fall back to the numbers."""
    problem = _walled_board(0.30)
    caged = tuple(problem.pads) + (
        pad("cage1", "N4", 0.0, 1.72, w=4.0, h=0.4, component="SHIELD"),
        pad("cage2", "N4", 0.0, -1.72, w=4.0, h=0.4, component="SHIELD"),
    )
    problem = RoutingProblem(
        id=problem.id,
        board=problem.board,
        rules=problem.rules,
        pads=caged,
        nets=problem.nets
        + (Net(id="N4", name="SHLD", net_class="signal",
               pads=("cage1", "cage2"), min_width_mm=0.2),),
    )
    result = dg.diagnose(problem, _empty(problem))
    blocked = [a for a in result.asks if a.nets == ("SIG",)]
    assert blocked, [a.nets for a in result.asks]
    assert blocked[0].move is None
    assert blocked[0].kind == "tight_gap"
    assert any("ran into something else" in line for line in blocked[0].evidence)


# ---------------------------------------------------------------------------
# The honesty rules
# ---------------------------------------------------------------------------


def test_a_pad_and_its_own_trace_are_one_wall_not_a_channel():
    """The two are touching, so the "gap" between them is a negative number
    about a connection. Naming it produced a -0.37mm pinch on a real board."""
    problem = _walled_board(0.30)
    solution = RoutingSolution(
        router="test",
        traces=(
            Trace(id="t0", net="N2", layer=TOP,
                  points=(Point(0.0, 0.65), Point(4.0, 0.65)), width_mm=0.3),
        ),
    )
    result = dg.diagnose(problem, solution)
    for ask in result.asks:
        if ask.pinch and ask.pinch.second:
            assert ask.pinch.first.wall != ask.pinch.second.wall
            assert ask.pinch.gap_mm > 0


def test_an_unattributable_failure_says_so_rather_than_guessing():
    diagnosis = dg.NetDiagnosis(
        net="n", name="MYSTERY", net_class="signal", width_mm=0.2, pads=2,
        fragments=2, verdict="unattributed", channel_mm=0.01, tolerance_mm=0.05,
        needed_mm=0.1, pinch=None, proven=False,
        reason="the narrow point could not be tied to two separate things",
    )
    asks = dg._build_asks(
        two_pad_board(), [], {}, [diagnosis], dg.DEFAULT_PROBE, [], {}
    )
    assert [a.kind for a in asks] == ["unattributed"]
    assert asks[0].move is None
    assert "could not be measured" in asks[0].headline


def test_a_keepout_a_part_already_sits_in_is_read_as_that_part_s_own():
    """circuit.json records no owner for a keepout, so the only evidence is
    geometry: a part whose pads are inside one brought it with it."""
    keepout = Keepout(
        id="k0", center=Point(0.0, 0.0), width_mm=3.0, height_mm=1.0,
        layers=(TOP, BOTTOM),
    )
    problem = two_pad_board(
        extra_pads=(pad("j1", "N5", 0.0, 0.0, w=1.0, h=0.4, component="J1"),),
        extra_nets=(Net(id="N5", name="SHIELD", net_class="signal",
                        pads=("j1",), min_width_mm=0.2),),
        keepouts=(keepout,),
    )
    obstacles = dg.build_obstacles(problem, _empty(problem))
    grids = dg._obstacle_index(obstacles)
    assert dg.owned_keepouts(obstacles, grids, "J1", problem.rules) == {"keepout:k0"}
    assert dg.owned_keepouts(obstacles, grids, "U1", problem.rules) == set()


def test_a_wide_rail_is_told_the_width_that_would_fit():
    problem = _walled_board(0.55)  # 0.35mm usable
    power = Net(id="N1", name="V3_3", net_class="power",
                pads=("p1", "p2"), min_width_mm=0.5, source_net_id="source_net_1")
    problem = RoutingProblem(
        id=problem.id, board=problem.board, rules=problem.rules,
        pads=problem.pads, keepouts=problem.keepouts,
        nets=(power,) + tuple(n for n in problem.nets if n.id != "N1"),
    )
    result = dg.diagnose(problem, _empty(problem))
    ask = next(a for a in result.asks if "V3_3" in a.nets)
    assert any("would fit through here" in line for line in ask.evidence)


# ---------------------------------------------------------------------------
# The machinery underneath
# ---------------------------------------------------------------------------


def test_pad_components_names_the_islands_analyse_counts():
    problem = _walled_board(0.30)
    solution = _empty(problem)
    counted = analyse(problem, solution)
    named = pad_components(problem, solution)
    for net_id, groups in named.items():
        assert len(groups) == counted.fragments[net_id]
        flat = [pad_id for group in groups for pad_id in group]
        assert sorted(flat) == sorted(set(flat))


def test_room_is_measured_against_the_clearance_each_thing_demands():
    """A plated hole wants 0.28mm, an unplated one 0.20mm, copper 0.10mm. One
    number for all three is the defect this whole package exists to fix."""
    from routerlib.model import Drill

    problem = two_pad_board(
        drills=(
            # A plated hole that belongs to a pad wants 0.28mm; the same hole
            # with no pad is a via and wants 0.20mm. Three numbers, not one.
            Drill(id="d_pth", center=Point(3.0, 3.0), width_mm=0.8,
                  height_mm=0.8, plated=True, component="J1", pad_id="p1"),
            Drill(id="d_npth", center=Point(-3.0, 3.0), width_mm=2.2,
                  height_mm=2.2, plated=False, component="H1"),
        ),
    )
    obstacles = {o.id: o for o in dg.build_obstacles(problem, _empty(problem))}
    assert obstacles["pad:p1"].clearance_mm == pytest.approx(RULES.min_clearance_mm)
    assert obstacles["drill:d_pth"].clearance_mm == pytest.approx(
        RULES.min_pth_to_copper_mm
    )
    assert obstacles["drill:d_npth"].clearance_mm == pytest.approx(
        RULES.min_npth_to_copper_mm
    )


def test_the_board_edge_is_an_obstacle_with_its_own_clearance():
    problem = two_pad_board()
    outlined = RoutingProblem(
        id=problem.id,
        board=Board(
            width_mm=20.0,
            height_mm=20.0,
            outline=(Point(-10, -10), Point(10, -10), Point(10, 10), Point(-10, 10)),
        ),
        rules=problem.rules,
        pads=problem.pads,
        nets=problem.nets,
    )
    edges = [o for o in dg.build_obstacles(outlined, _empty(outlined))
             if o.kind == "edge"]
    assert len(edges) == 4
    assert edges[0].clearance_mm == pytest.approx(RULES.min_edge_clearance_mm)
    assert edges[0].label == "the board edge"


def test_a_board_with_no_copper_gets_the_free_answer_not_the_slow_one():
    """The copper step produced nothing. Measuring every channel to discover
    that costs 43 seconds on a 36-connection board and says the same thing."""
    problem = _walled_board(0.30)
    bare = dg.bare_board_diagnosis(problem, _empty(problem))
    assert bare is not None
    assert [a.kind for a in bare.asks] == ["router_limit"]
    assert "not one track or hole" in bare.asks[0].evidence[0]
    assert bare.notes == ("no copper on the board; the geometry was not measured",)
    # It is the caller's shortcut, never diagnose()'s: an empty board still has
    # geometry worth measuring, and this must not swallow it.
    assert dg.diagnose(problem, _empty(problem)).asks[0].kind != "router_limit"


def test_a_board_that_is_finished_has_no_bare_answer_either():
    problem = two_pad_board()
    assert dg.bare_board_diagnosis(problem, straight_trace(problem)) is None
    assert dg.bare_board_diagnosis(problem, _empty(problem)) is not None


def test_connections_with_room_are_one_request_however_many_there_are():
    """Twelve "there was room and it was not used" rows are one decision, and
    twelve cards would bury the one that is not."""
    rows = [
        dg.NetDiagnosis(
            net=f"n{i}", name=f"SIG{i}", net_class="signal", width_mm=0.2, pads=2,
            fragments=2, verdict="router_limit", channel_mm=0.4, tolerance_mm=0.03,
            needed_mm=0.1, pinch=None, reason="room",
        )
        for i in range(12)
    ]
    asks = dg._build_asks(
        two_pad_board(), [], {}, rows, dg.DEFAULT_PROBE, [], {}
    )
    assert len(asks) == 1
    assert len(asks[0].nets) == 12


def test_the_diagnosis_is_deterministic():
    problem = _walled_board(0.30)
    first = dg.diagnose(problem, _empty(problem)).as_dict()
    second = dg.diagnose(problem, _empty(problem)).as_dict()
    first.pop("seconds")
    second.pop("seconds")
    assert first == second


def test_the_dict_form_carries_every_number_a_screen_needs():
    problem = _walled_board(0.30)
    payload = dg.diagnose(problem, _empty(problem)).as_dict()
    assert payload["schema"] == "routing-help@1"
    assert payload["unroutedNets"] == 1
    ask = payload["asks"][0]
    assert set(ask) >= {
        "kind", "nets", "headline", "at", "layer", "pinch", "neededMm",
        "proven", "move", "congestion", "evidence",
    }
    assert ask["pinch"]["gapMm"] > 0
    assert isinstance(ask["evidence"], list) and ask["evidence"]
