"""Tests for the pathfinder-negotiated router.

Run from ``packages/router``::

    PYTHONPATH=src:../circuitpy/src python3.12 -m pytest algorithms -q

The module under test has a hyphen in its name because the tournament asked for
that filename, so it is loaded by path rather than imported. Everything else is
ordinary.

What these assert, in order of how much they matter:

1. **The router never emits a DRC error.** On every instance it is run against,
   scored by the same scorer the tournament uses. A router that scores badly is
   a bad router; a router that emits an illegal board is a scrapped board.
2. **The same input gives the same output**, twice, byte for byte.
3. It says what it did honestly: ``complete`` matches recomputed connectivity.
4. The pieces underneath do what the algorithm assumes: occupancy counts nets
   and not writes, the grid indexes what it says it indexes, a plane is a
   destination.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_PACKAGE = _HERE.parent
for _path in (_PACKAGE / "src", _PACKAGE.parent / "circuitpy" / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


def _load():
    spec = importlib.util.spec_from_file_location(
        "pathfinder_negotiated", _HERE / "pathfinder-negotiated.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["pathfinder_negotiated"] = module
    spec.loader.exec_module(module)
    return module


pfn = _load()

from routerlib.bench import INSTANCE_DIR, load_instance, with_ground_plane  # noqa: E402
from routerlib.drc import check as drc_check  # noqa: E402
from routerlib.model import (  # noqa: E402
    BOTTOM,
    TOP,
    Board,
    Budget,
    DesignRules,
    Drill,
    Net,
    Pad,
    Point,
    RoutingProblem,
)
from routerlib.scoring import determinism_check, score  # noqa: E402

#: Small enough to run in a test suite, big enough to be a real problem. Each is
#: named because a failure should say which board broke.
QUICK = (
    "matrix-status-led",
    "matrix-i2c-bus",
    "matrix-ldo-3v3__usb-c-power",
    "matrix-status-led__ws2812-chain",
)
MEDIUM = ("matrix-i2c-bus__ldo-3v3__usb-c-power", "matrix-ldo-3v3__usb-c-power__ws2812-chain")


def instance(name: str) -> RoutingProblem:
    return load_instance(INSTANCE_DIR / f"{name}.json")


def routed(name: str, **kwargs):
    problem = instance(name)
    solution = pfn.PathFinderNegotiatedRouter(**kwargs).route(problem, Budget())
    return problem, solution


# ---------------------------------------------------------------------------
# Geometry and bookkeeping
# ---------------------------------------------------------------------------


def test_grid_roundtrips_a_point_to_its_own_cell():
    grid = pfn.Grid((-10.0, -5.0, 10.0, 5.0), 0.25, guard=4)
    for x, y in ((0.0, 0.0), (3.3, -1.1), (-9.9, 4.9)):
        cell = grid.cell_of(x, y)
        cx, cy = grid.xy_of(cell)
        assert math.hypot(cx - x, cy - y) <= 0.25 * math.sqrt(2) / 2 + 1e-9


def test_grid_guard_keeps_the_widest_stamp_inside_the_array():
    grid = pfn.Grid((-10.0, -5.0, 10.0, 5.0), 0.25, guard=4)
    offsets = grid.offsets_within(1.0)  # 4 cells at this pitch
    for x, y in ((-10.0, -5.0), (10.0, 5.0)):
        cell = grid.cell_of(x, y)
        for off in offsets:
            assert 0 <= cell + off < grid.n


def test_offsets_within_is_a_disc_and_is_symmetric():
    grid = pfn.Grid((0.0, 0.0, 4.0, 4.0), 0.2, guard=6)
    offsets = grid.offsets_within(0.45)
    assert 0 in offsets
    assert set(offsets) == {-o for o in offsets}
    # 0.4 is inside a 0.45 radius; 0.6 is not.
    assert 2 in offsets and 3 not in offsets


def test_inside_mask_matches_a_known_square():
    grid = pfn.Grid((-5.0, -5.0, 5.0, 5.0), 0.5, guard=2)
    square = (
        Point(-2.0, -2.0),
        Point(2.0, -2.0),
        Point(2.0, 2.0),
        Point(-2.0, 2.0),
    )
    mask = pfn._inside_mask(grid, square)
    assert mask[grid.cell_of(0.0, 0.0)] == 1
    assert mask[grid.cell_of(1.7, 1.7)] == 1
    assert mask[grid.cell_of(3.0, 0.0)] == 0
    assert mask[grid.cell_of(0.0, -4.0)] == 0


def test_two_nets_claiming_one_cell_block_it_for_both():
    grid = pfn.Grid((-2.0, -2.0, 2.0, 2.0), 0.25, guard=3)
    arr = pfn._blank(grid.n)
    capsule = (0.0, 0.0, 0.0, 0.0, 0.1)
    pfn._mark_capsule(arr, grid, capsule, 0.2, 1)
    assert arr[grid.cell_of(0.0, 0.0)] == 1
    pfn._mark_capsule(arr, grid, capsule, 0.2, 2)
    assert arr[grid.cell_of(0.0, 0.0)] == pfn.BLOCKED
    # ... and a cell well outside the inflated capsule is untouched.
    assert arr[grid.cell_of(1.5, 1.5)] == pfn.FREE


def test_merge_collinear_keeps_corners_and_drops_the_rest():
    line = [Point(0, 0), Point(1, 0), Point(2, 0), Point(3, 0)]
    assert pfn._merge_collinear(line) == [Point(0, 0), Point(3, 0)]
    bend = [Point(0, 0), Point(1, 0), Point(2, 0), Point(2, 1)]
    assert pfn._merge_collinear(bend) == [Point(0, 0), Point(2, 0), Point(2, 1)]
    assert pfn._merge_collinear([Point(0, 0)]) == [Point(0, 0)]


def test_occupancy_counts_nets_not_writes():
    """The bug this guards: a net's own neighbouring cells have overlapping
    clearance discs, so counting raw writes makes a net collide with itself and
    every congestion reading afterwards is fiction."""
    problem = instance("matrix-status-led__ws2812-chain")
    router = pfn.PathFinderNegotiatedRouter()
    router.route(problem, Budget())
    grid = router._grid
    for qi, layers in enumerate(router._occ):
        for arr in layers:
            assert max(arr) <= len(problem.routable_nets), (
                "a cell is claimed more times than there are nets, so one net is "
                "being counted more than once"
            )
    # And every cell a net occupies is claimed by that net exactly once.
    for plan in router._plans_by_net.values():
        for (qi, layer), cells in plan.stamps.items():
            assert len(cells) == len(set(cells))


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", QUICK)
def test_never_emits_a_drc_error(name):
    problem, solution = routed(name)
    result = drc_check(problem, solution)
    assert not result.errors, [v.detail for v in result.errors]


@pytest.mark.parametrize("name", QUICK)
def test_says_honestly_whether_it_finished(name):
    problem, solution = routed(name)
    assert score(problem, solution).claim_honest


@pytest.mark.parametrize("name", QUICK[:3])
def test_same_input_same_output(name):
    problem = instance(name)
    result = determinism_check(
        pfn.PathFinderNegotiatedRouter(), problem, Budget(), runs=2
    )
    assert result.deterministic, result.detail


def test_two_pad_instances_are_fully_routed():
    for name in ("matrix-status-led", "matrix-i2c-bus"):
        problem, solution = routed(name)
        assert score(problem, solution).completeness == 1.0


def test_beats_the_pattern_baseline_on_completeness():
    """The one number a real router has to win. Compared on the same instance,
    with the same scorer, against the floor the tournament ships."""
    from routerlib.baseline import PatternRouter

    problem = instance("matrix-ldo-3v3__usb-c-power")
    mine = score(problem, pfn.PathFinderNegotiatedRouter().route(problem, Budget()))
    floor = score(problem, PatternRouter().route(problem, Budget()))
    assert mine.completeness > floor.completeness
    assert mine.errors == 0


def test_no_via_lands_in_an_smd_pad():
    for name in MEDIUM:
        problem, solution = routed(name)
        assert solution.vias, f"{name} routed without a single via — check the test"
        result = drc_check(problem, solution)
        assert not [v for v in result.violations if v.kind == "via_in_pad"]


def test_power_nets_are_routed_at_power_width_unless_rescued():
    """A rail keeps its width, except where the router says out loud that it did
    not: a necked stub or a rescued net is recorded in ``notes``."""
    problem, solution = routed("matrix-ldo-3v3__usb-c-power")
    rails = {
        n.id for n in problem.nets if n.net_class in ("power", "ground")
    }
    rail_traces = [t for t in solution.traces if t.net in rails]
    assert rail_traces
    thin = [t for t in rail_traces if t.width_mm < problem.rules.power_trace_mm - 1e-9]
    if thin:
        assert any("neck" in n or "signal width" in n for n in solution.notes)


# ---------------------------------------------------------------------------
# Planes
# ---------------------------------------------------------------------------


def test_a_plane_is_a_destination_not_an_obstacle():
    """The failure this is named after: the shipped router produced
    byte-identical copper with and without a ground pour, because it saw the
    pour's 73 stitching vias as 73 obstacles. Adding a plane must change the
    ground net's copper, and must not make the board worse."""
    plain = instance("matrix-ldo-3v3__rp2040-core__usb-c-power")
    poured = with_ground_plane(plain)

    a = pfn.PathFinderNegotiatedRouter().route(plain, Budget())
    b = pfn.PathFinderNegotiatedRouter().route(poured, Budget())
    assert a.fingerprint() != b.fingerprint()

    ground = [n for n in poured.nets if n.net_class == "ground"][0]
    plane = poured.planes[0]
    on_ground = [t for t in b.traces if t.net == ground.id]
    # Ground copper on a poured board is short stubs to a via, not a spanning
    # tree: nothing on the ground net should be a long haul across the board.
    if on_ground:
        assert max(t.length_mm for t in on_ground) < plain.board.width_mm / 2

    result = drc_check(poured, b)
    assert not result.errors, [v.detail for v in result.errors]
    assert plane.layer in (TOP, BOTTOM)


def test_ground_net_connects_through_the_pour():
    poured = with_ground_plane(instance("matrix-ldo-3v3__usb-c-power"))
    solution = pfn.PathFinderNegotiatedRouter().route(poured, Budget())
    result = score(poured, solution)
    ground = [n for n in poured.nets if n.net_class == "ground"][0]
    assert ground.id not in result.unconnected


# ---------------------------------------------------------------------------
# Budget and degenerate input
# ---------------------------------------------------------------------------


def test_a_starved_budget_still_returns_legal_copper():
    problem = instance("matrix-ldo-3v3__usb-c-power__ws2812-chain")
    solution = pfn.PathFinderNegotiatedRouter().route(
        problem, Budget(max_iterations=1, max_nodes=500)
    )
    assert not drc_check(problem, solution).errors
    assert score(problem, solution).claim_honest


def test_a_board_with_nothing_to_route_is_complete():
    rules = DesignRules.jlcpcb()
    problem = RoutingProblem(
        id="empty",
        board=Board(width_mm=10.0, height_mm=10.0),
        rules=rules,
        pads=(),
        nets=(),
    )
    solution = pfn.PathFinderNegotiatedRouter().route(problem, Budget())
    assert solution.complete
    assert not solution.traces


def test_a_walled_in_pad_is_reported_not_hidden():
    """When a pad cannot be reached the router says the reason is the placement,
    and it does not invent copper to cover for it.

    ``p2`` is boxed in by four unnetted pads 0.55mm away on the axes. The
    placement itself is legal — 0.15mm between copper, above the 0.10mm floor —
    but the widest gap out is 0.378mm and a 0.2mm track needs 0.494mm, so there
    is no escape at any width the board allows.
    """
    rules = DesignRules.jlcpcb()
    boxed_in = [
        Pad(id=f"blk{i}", net=None, center=Point(3.0 + dx, dy), width_mm=0.4,
            height_mm=0.4, layers=(TOP,))
        for i, (dx, dy) in enumerate(((0.55, 0.0), (-0.55, 0.0), (0.0, 0.55), (0.0, -0.55)))
    ]
    pads = (
        Pad(id="p1", net="n1", center=Point(-3.0, 0.0), width_mm=0.6, height_mm=0.6,
            layers=(TOP,)),
        Pad(id="p2", net="n1", center=Point(3.0, 0.0), width_mm=0.4, height_mm=0.4,
            layers=(TOP,)),
        *boxed_in,
    )
    problem = RoutingProblem(
        id="walled-in",
        board=Board(width_mm=14.0, height_mm=8.0),
        rules=rules,
        pads=pads,
        nets=(
            Net(id="n1", name="N1", net_class="signal", pads=("p1", "p2"),
                min_width_mm=0.2),
        ),
    )
    # The placement is clean before anything is routed, so a finding afterwards
    # is the router's.
    from routerlib.model import empty_solution

    assert not drc_check(problem, empty_solution()).errors

    solution = pfn.PathFinderNegotiatedRouter().route(problem, Budget())
    assert not solution.complete
    assert "n1" in solution.unrouted_nets
    assert not drc_check(problem, solution).errors
    assert any("left open" in note for note in solution.notes)


def test_a_straight_two_pad_net_is_one_trace_and_no_via():
    rules = DesignRules.jlcpcb()
    pads = (
        Pad(id="p1", net="n1", center=Point(-3.0, 0.0), width_mm=0.8, height_mm=0.8,
            layers=(TOP,)),
        Pad(id="p2", net="n1", center=Point(3.0, 0.0), width_mm=0.8, height_mm=0.8,
            layers=(TOP,)),
    )
    problem = RoutingProblem(
        id="straight",
        board=Board(width_mm=14.0, height_mm=8.0),
        rules=rules,
        pads=pads,
        nets=(
            Net(id="n1", name="N1", net_class="signal", pads=("p1", "p2"),
                min_width_mm=0.2),
        ),
    )
    solution = pfn.PathFinderNegotiatedRouter().route(problem, Budget())
    assert solution.complete
    assert not solution.vias
    # 6mm apart, so a sane route is not much longer than 6mm.
    assert solution.copper_length_mm < 7.0


def test_reported_iterations_are_rounds_and_nodes_are_expansions():
    problem = instance("matrix-ldo-3v3__usb-c-power")
    solution = pfn.PathFinderNegotiatedRouter(max_rounds=3).route(problem, Budget())
    assert 1 <= solution.iterations <= 3
    assert solution.nodes_expanded > 0
