"""Unit tests for the ``ripup-reroute`` family.

Two kinds of test, and the split matters. The synthetic boards state an answer
that can be checked by hand — this exact trace, this many vias, this refusal.
The instance-backed ones assert the properties that make a score worth quoting:
zero DRC errors, byte-identical reruns, and a real gain over the same router
with rip-up switched off.

Nothing here asserts a completeness number. Those live in the benchmark report,
where they travel with the ruler they were measured against.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from routerlib import drc as drc_mod
from routerlib.bench import INSTANCE_DIR, load_instance
from routerlib.model import (
    BOTTOM,
    TOP,
    Board,
    Budget,
    DesignRules,
    Keepout,
    Net,
    Plane,
    Point,
    RoutingProblem,
)
from routerlib.scoring import determinism_check, score

import routerfix

ALGORITHMS = Path(__file__).resolve().parents[1] / "algorithms"


def _load(name: str = "ripup-reroute"):
    spec = importlib.util.spec_from_file_location(
        f"_algo_{name.replace('-', '_')}", ALGORITHMS / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rr = _load()

#: Small counted budget: these tests are about behaviour, not endurance.
SMALL = Budget(max_iterations=50_000, max_nodes=300_000, seed=0)


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------


def test_pitch_is_a_function_of_area_only():
    """Same area, same grid — and a bigger board never gets a finer one."""
    assert rr._pitch_for(212.0) == rr.MIN_PITCH_MM
    assert rr._pitch_for(10_080.0) == 0.20
    coarse = [rr._pitch_for(a) for a in (500, 2_000, 10_000, 40_000, 200_000)]
    assert coarse == sorted(coarse)
    assert max(coarse) <= rr.MAX_PITCH_MM


def test_a_grid_free_cell_is_a_cell_workspace_agrees_with():
    """The property the whole router rests on.

    ``avail`` is measured from the cell **square**, so a step between two free
    cell centres is copper the exact geometry also accepts. If that ever stops
    being true, the router is planning against a board the scorer does not
    recognise — so the test walks real cells and asks ``Workspace``, which is
    the same oracle the scorer uses.
    """
    from routerlib.workspace import Workspace

    crowd = tuple(
        routerfix.pad(f"x{i}", f"O{i}", x, y, w=1.6, h=0.8)
        for i, (x, y) in enumerate(
            ((-3.0, 1.0), (3.0, -1.0), (0.0, 2.5), (0.0, -2.5))
        )
    )
    nets = tuple(
        Net(id=f"O{i}", name=f"o{i}", net_class="signal", pads=(f"x{i}",),
            min_width_mm=0.2)
        for i in range(len(crowd))
    )
    problem = routerfix.two_pad_board(
        gap_mm=12.0, extra_pads=crowd, extra_nets=nets
    )
    grid = rr._Grid(problem, pitch=0.25)
    ws = Workspace(problem)
    checked = 0
    for cell in range(0, grid.ncells, 7):
        if grid.avail[0][cell] < 0.1:
            continue
        right = cell + 1
        if right % grid.nx == 0 or grid.avail[0][right] < 0.1:
            continue
        verdict = ws.segment_ok(
            TOP, grid.center(cell), grid.center(right), 0.2, "__probe__"
        )
        assert verdict is True, (
            f"grid says free, Workspace says {verdict} at {grid.center(cell)}"
        )
        checked += 1
    assert checked > 50, "the sample was too small to mean anything"


def test_cells_outside_the_outline_are_blocked():
    outline = tuple(
        Point(x, y) for x, y in ((-5, -5), (5, -5), (5, 5), (-5, 5))
    )
    problem = RoutingProblem(
        id="outline",
        board=Board(width_mm=20.0, height_mm=20.0, outline=outline),
        rules=routerfix.RULES,
        pads=(routerfix.pad("p1", "N1", -2, 0), routerfix.pad("p2", "N1", 2, 0)),
        nets=(Net(id="N1", name="SIG", net_class="signal",
                  pads=("p1", "p2"), min_width_mm=0.2),),
    )
    grid = rr._Grid(problem, pitch=0.25)
    for cell in range(grid.ncells):
        point = grid.center(cell)
        if abs(point.x) > 5.0 or abs(point.y) > 5.0:
            assert grid.avail[0][cell] < 0, f"{point} is off the board"


# ---------------------------------------------------------------------------
# Occupancy and rip-up
# ---------------------------------------------------------------------------


def test_removing_a_net_restores_exactly_what_it_took():
    """Rip-up has to be exact, or the second attempt is routing a board that no
    longer describes reality."""
    problem = routerfix.two_pad_board(gap_mm=8.0)
    grid = rr._Grid(problem, pitch=0.25)
    occ = rr._Occupancy(grid)
    before = ({k: dict(v) for k, v in occ.block[0].items()},
              {k: dict(v) for k, v in occ.block[1].items()})
    occ.add_trace("N1", TOP, (Point(-4, 0), Point(4, 0)), 0.2, 0.147)
    occ.add_via("N1", Point(0.0, 2.0), 0.6, 0.147)
    assert occ.block[0], "a committed trace must occupy something"
    occ.remove_net("N1")
    assert occ.block[0] == before[0]
    assert occ.block[1] == before[1]


def test_occupancy_never_blocks_the_net_that_owns_it():
    problem = routerfix.two_pad_board(gap_mm=8.0)
    grid = rr._Grid(problem, pitch=0.25)
    occ = rr._Occupancy(grid)
    occ.add_trace("N1", TOP, (Point(-4, 0), Point(4, 0)), 0.2, 0.147)
    # The cell the trace leaves least room in — right on top of the copper.
    hit = min(sorted(occ.block[0]), key=lambda cell: occ.block[0][cell]["N1"])
    assert occ.nets_blocking(0, hit, 0.1, "N1") == []
    assert occ.nets_blocking(0, hit, 0.1, "N2") == ["N1"]


def test_two_contributions_to_one_cell_survive_removing_one():
    problem = routerfix.two_pad_board(gap_mm=8.0)
    grid = rr._Grid(problem, pitch=0.25)
    occ = rr._Occupancy(grid)
    occ.add_trace("N1", TOP, (Point(-4, 0), Point(4, 0)), 0.2, 0.147)
    occ.add_trace("N2", TOP, (Point(-4, 0.3), Point(4, 0.3)), 0.2, 0.147)
    shared = sorted(set(occ.block[0]))
    assert shared
    occ.remove_net("N1")
    assert any("N2" in occ.block[0][cell] for cell in occ.block[0])
    assert all("N1" not in entry for entry in occ.block[0].values())


# ---------------------------------------------------------------------------
# Routing behaviour on boards where the answer is known
# ---------------------------------------------------------------------------


def test_two_pads_on_an_empty_board_route_straight_with_no_via():
    problem = routerfix.two_pad_board(gap_mm=8.0)
    solution = rr.RipUpRerouteRouter().route(problem, SMALL)
    result = score(problem, solution)
    assert result.completeness == 1.0
    assert result.errors == 0
    assert solution.vias == (), "a straight shot needs no layer change"
    # Copper close to the straight-line distance: 8mm between pad centres.
    assert 7.5 <= solution.copper_length_mm <= 9.5


def test_a_wall_forces_a_detour_not_a_violation():
    """A keepout across the direct path. The router must go round it, and the
    result must still be legal — never a shortcut it cannot defend."""
    wall = Keepout(id="k1", center=Point(0.0, 0.0), width_mm=1.0, height_mm=6.0)
    problem = routerfix.two_pad_board(gap_mm=10.0, keepouts=(wall,))
    solution = rr.RipUpRerouteRouter().route(problem, SMALL)
    result = score(problem, solution)
    assert result.completeness == 1.0
    assert result.errors == 0
    assert solution.copper_length_mm > 10.0, "it went through the wall"


def test_a_sealed_pad_is_reported_unrouted_not_shorted():
    """Boxed in on every side. The honest answer is an unconnected net; the
    dishonest one is copper through the box."""
    box = tuple(
        Keepout(id=f"k{i}", center=Point(x, y), width_mm=w, height_mm=h)
        for i, (x, y, w, h) in enumerate((
            (-5.0, 1.6, 3.2, 0.4), (-5.0, -1.6, 3.2, 0.4),
            (-6.6, 0.0, 0.4, 3.6), (-3.4, 0.0, 0.4, 3.6),
        ))
    )
    problem = routerfix.two_pad_board(gap_mm=10.0, keepouts=box)
    solution = rr.RipUpRerouteRouter().route(problem, SMALL)
    result = score(problem, solution)
    assert result.completeness == 0.0
    assert result.errors == 0
    assert solution.complete is False
    assert result.claim_honest


def test_a_pad_inside_its_own_pour_needs_no_copper_at_all():
    """The fact the shipped router cannot represent: a pour is the net."""
    poured = tuple(Point(x, y) for x, y in
                   ((-9, -9), (9, -9), (9, 9), (-9, 9)))
    plane = Plane(id="pl", net="N1", layer=BOTTOM, outline=poured)
    extra = routerfix.pad("p3", "N1", 0.0, 4.0, layer=BOTTOM)
    problem = routerfix.two_pad_board(
        gap_mm=8.0,
        extra_pads=(extra,),
        planes=(plane,),
    )
    problem = RoutingProblem(
        id=problem.id, board=problem.board, rules=problem.rules,
        pads=problem.pads, drills=problem.drills, keepouts=problem.keepouts,
        planes=problem.planes,
        nets=(Net(id="N1", name="GND", net_class="ground",
                  pads=("p1", "p2", "p3"), min_width_mm=0.5),),
    )
    solution = rr.RipUpRerouteRouter().route(problem, SMALL)
    result = score(problem, solution)
    assert result.completeness == 1.0
    assert result.errors == 0
    # p3 is already in the pour; p1 and p2 are on top and each need one via.
    assert len(solution.vias) == 2


def test_a_rail_necks_down_rather_than_leaving_the_net_open():
    """A 0.6mm gate across the only route. 0.5mm copper does not fit through it
    with clearance either side; 0.2mm does. ``dfm_power_trace_width`` is a
    warning and an open net is a dead board, so the router takes the warning —
    and says so in its notes rather than quietly."""
    gate = (
        Keepout(id="g1", center=Point(0.0, 5.15), width_mm=0.6, height_mm=9.7),
        Keepout(id="g2", center=Point(0.0, -5.15), width_mm=0.6, height_mm=9.7),
    )
    a = routerfix.pad("p1", "N1", -4.0, 0.0)
    b = routerfix.pad("p2", "N1", 4.0, 0.0)
    problem = RoutingProblem(
        id="neck",
        board=Board(width_mm=20.0, height_mm=20.0),
        rules=routerfix.RULES,
        pads=(a, b),
        keepouts=gate,
        nets=(Net(id="N1", name="V5", net_class="power",
                  pads=("p1", "p2"), min_width_mm=0.5),),
    )
    solution = rr.RipUpRerouteRouter().route(problem, SMALL)
    assert score(problem, solution).completeness == 1.0
    assert any("below their class width" in note for note in solution.notes)


def test_it_never_drills_a_via_into_an_smd_pad():
    """Not a preference: via-in-pad needs filled-and-capped plating we do not
    order, and the scorer calls it an error."""
    crowd = tuple(
        routerfix.pad(f"x{i}", f"O{i}", x, y, w=1.6, h=1.6)
        for i, (x, y) in enumerate(((-2.0, 0.0), (2.0, 0.0), (0.0, 2.0)))
    )
    nets = tuple(
        Net(id=f"O{i}", name=f"o{i}", net_class="signal", pads=(f"x{i}",),
            min_width_mm=0.2)
        for i in range(3)
    )
    problem = routerfix.two_pad_board(
        gap_mm=12.0, extra_pads=crowd, extra_nets=nets
    )
    solution = rr.RipUpRerouteRouter().route(problem, SMALL)
    result = drc_mod.check(problem, solution, use_pipeline=False)
    assert [v for v in result.errors if v.kind == "via_in_pad"] == []


def test_the_width_ladder_has_two_rungs_and_never_goes_under_the_warn_width():
    rules = DesignRules()
    assert rr._width_ladder(0.2, rules) == [0.2, 0.15]
    assert rr._width_ladder(0.5, rules) == [0.5, 0.2]
    assert min(rr._width_ladder(0.5, rules)) >= rules.warn_trace_mm


def test_the_spanning_tree_is_stable_under_pad_order():
    pads = [routerfix.pad(f"p{i}", "N1", x, y)
            for i, (x, y) in enumerate(
                ((0.0, 0.0), (3.1, 0.2), (3.4, 3.7), (0.5, 3.2), (6.7, 1.3)))]
    forward = rr._mst_edges(pads)
    backward = rr._mst_edges(list(reversed(pads)))
    as_ids = lambda edges: sorted(tuple(sorted((a.id, b.id))) for a, b in edges)
    assert as_ids(forward) == as_ids(backward)


# ---------------------------------------------------------------------------
# The properties a score depends on
# ---------------------------------------------------------------------------


def _instance(name: str) -> RoutingProblem:
    path = INSTANCE_DIR / f"{name}.json"
    if not path.exists():  # pragma: no cover - fixtures are committed
        pytest.skip(f"instance {name} is not on disk")
    return load_instance(path)


@pytest.mark.parametrize(
    "name",
    ["matrix-status-led__ws2812-chain", "matrix-ldo-3v3__usb-c-power"],
)
def test_zero_drc_errors_on_real_instances(name):
    """The rule the family is built around: never place copper it cannot
    defend. A completeness number is an opinion; this is not."""
    problem = _instance(name)
    router = rr.RipUpRerouteRouter(node_cap=250_000)
    solution = router.route(problem, Budget(seed=0))
    result = score(problem, solution)
    assert result.errors == 0, result.error_kinds
    assert result.claim_honest


@pytest.mark.parametrize("name", ["matrix-status-led__ws2812-chain"])
def test_same_input_same_bytes(name):
    """Determinism is a scored property. Two runs must agree on the copper *and*
    on the serialised circuit.json, because the pipeline hashes what it writes."""
    problem = _instance(name)
    budget = Budget(seed=0)
    again = determinism_check(
        rr.RipUpRerouteRouter(node_cap=250_000), problem, budget, runs=2
    )
    assert again.deterministic, again.detail


def test_a_different_seed_is_allowed_to_differ_but_must_be_stable():
    """Rip-up perturbs its reroute order with a seeded RNG. Two runs at seed 7
    must match each other, whatever they do relative to seed 0."""
    problem = _instance("matrix-ldo-3v3__usb-c-power")
    router = rr.RipUpRerouteRouter(node_cap=200_000)
    first = router.route(problem, Budget(seed=7))
    second = rr.RipUpRerouteRouter(node_cap=200_000).route(problem, Budget(seed=7))
    assert first.fingerprint() == second.fingerprint()


def test_rip_up_is_never_worse_than_the_greedy_control_and_sometimes_better():
    """The claim the family is making, and the invariant behind it.

    The control is the *same* maze router with rip-up switched off, so a
    difference is rip-up and not the grid, the A*, or the pad escape. And
    because the loop keeps the best board it has seen — which starts as the
    greedy result — rip-up can never return *fewer* connected nets than the
    control. That is a structural guarantee, so it is asserted on every
    instance here, not just on average.
    """
    names = [
        "matrix-ldo-3v3__rp2040-core__usb-c-power",
        "matrix-ldo-3v3__rp2040-core__usb-c-power-plane",
    ]
    wins = 0
    for name in names:
        problem = _instance(name)
        budget = Budget(seed=0)
        greedy = score(problem, rr.GreedyControlRouter().route(problem, budget))
        ripped = score(problem, rr.RipUpRerouteRouter().route(problem, budget))
        assert greedy.errors == 0 and ripped.errors == 0
        assert ripped.completeness >= greedy.completeness - 1e-9, name
        if ripped.completeness > greedy.completeness + 1e-9:
            wins += 1
    assert wins >= 1, "rip-up bought nothing anywhere — the family has no claim"


def test_no_two_vias_are_drilled_too_close_together():
    """Hole-to-hole is the one rule that does not care whose net it is.

    Two barrels 0.45mm apart break out into each other whatever they carry.
    The grid learned this the expensive way: without it a five-net board logged
    fifty-two vias proposed and thrown away by ``Workspace``, each one a
    connection lost.
    """
    problem = _instance("matrix-ldo-3v3__usb-c-power")
    solution = rr.RipUpRerouteRouter().route(problem, Budget(seed=0))
    floor = problem.rules.via_drill_mm + problem.rules.min_hole_to_hole_mm
    vias = list(solution.vias)
    assert len(vias) > 4, "not enough vias for this test to mean anything"
    for i, a in enumerate(vias):
        for b in vias[i + 1:]:
            gap = a.center.distance_to(b.center)
            assert gap >= floor - 1e-6, f"{a.id} and {b.id} are {gap:.3f}mm apart"


def test_the_control_really_does_no_rip_up():
    assert rr.GreedyControlRouter().rip_up_passes == 0
    assert rr.GreedyControlRouter(node_cap=10).node_cap == 10


def test_budget_is_counted_and_reported():
    problem = _instance("matrix-status-led__ws2812-chain")
    solution = rr.RipUpRerouteRouter().route(problem, Budget(max_nodes=5_000, seed=0))
    assert solution.nodes_expanded <= 5_000 + rr.DEFAULT_SEARCH_NODE_CAP
    assert solution.iterations > 0
    assert any("nodes expanded" in note for note in solution.notes)


def test_the_registry_exposes_both_entrants():
    assert set(rr.ROUTERS) == {"ripup-reroute", "ripup-greedy-control"}
    for name, factory in rr.ROUTERS.items():
        assert factory.name == name
        assert hasattr(factory(), "route")
