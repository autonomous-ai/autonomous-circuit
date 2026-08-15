"""Tests for the maze-astar family.

Two kinds, and the split matters:

* **Unit tests on the grid.** Almost everything that decides whether this router
  is correct lives in the rasteriser, not in A*. So the tests state the rules as
  numbers — 0.28mm to a component plated hole, 0.20mm to a via hole, no via
  inside an SMD pad whatever net it carries — and assert the grid encodes them.
  A search test cannot catch a wrong clearance; a stamp test can.
* **Property tests on real instances.** The properties every entrant owes the
  tournament: byte-identical output across runs, no DRC error it could have
  avoided, a ``complete`` flag that tells the truth, and — the one specific to
  this design — **never emit copper the Workspace would refuse**, because the
  grid is an approximation and only that gate keeps the approximation honest.

Kept to the three smallest instances on purpose. The machine is shared and the
point of these is the property, not the size.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import routerfix
from routerlib import bench, scoring
from routerlib.geometry import disc_capsule, segment_capsule
from routerlib.model import (
    BOTTOM,
    TOP,
    Board,
    Budget,
    DesignRules,
    Drill,
    Net,
    Pad,
    Plane,
    Point,
    Router,
    RoutingProblem,
)
from routerlib.workspace import Workspace


def _load():
    """Import ``algorithms/maze-astar.py``.

    The filename has a hyphen in it because that is the family's name in the
    tournament, so it cannot be imported normally. Registering it in
    ``sys.modules`` before executing it is not optional: ``dataclass`` looks its
    own module up by name while it processes the class.
    """
    path = Path(__file__).resolve().parents[1] / "algorithms" / "maze-astar.py"
    spec = importlib.util.spec_from_file_location("maze_astar", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["maze_astar"] = module
    spec.loader.exec_module(module)
    return module


mz = _load()

#: The three smallest instances. Enough to prove a property, cheap enough to run
#: on a machine several other agents are using.
SMALL = 3


def _small_problems():
    return sorted(bench.load_all(), key=lambda p: len(p.pads))[:SMALL]


def _router(**kwargs):
    return mz.MazeAStarRouter(**kwargs)


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class Contract(unittest.TestCase):
    def test_it_is_a_router(self):
        self.assertIsInstance(_router(), Router)

    def test_registry_entries_build_routers_with_distinct_names(self):
        names = {name: factory().name for name, factory in mz.ROUTERS.items()}
        self.assertEqual(set(names), set(names.values()))
        self.assertEqual(len(set(names.values())), len(names))

    def test_it_routes_the_easy_case(self):
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        result = scoring.score(problem, _router().route(problem, Budget()))
        self.assertEqual(result.completeness, 1.0)
        self.assertEqual(result.errors, 0)

    def test_the_easy_case_needs_no_via(self):
        """Two pads on one layer with nothing between them is one straight run.

        A via here would mean the cost model is not charging for layer changes,
        and the score ranks vias above copper length.
        """
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        solution = _router().route(problem, Budget())
        self.assertEqual(len(solution.vias), 0)
        self.assertEqual(len(solution.traces), 1)

    def test_it_reports_its_grid(self):
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        solution = _router().route(problem, Budget())
        self.assertTrue(any("grid " in note for note in solution.notes), solution.notes)


# ---------------------------------------------------------------------------
# The grid: where correctness actually lives
# ---------------------------------------------------------------------------


class GridResolution(unittest.TestCase):
    def test_pitch_is_the_finest_that_fits(self):
        problem = routerfix.two_pad_board()
        pitch = mz.choose_pitch(problem)
        spec = mz.make_spec(problem, pitch)
        self.assertLessEqual(spec.ncells, mz.MAX_CELLS)
        self.assertIn(pitch, mz.PITCH_LADDER)
        finer = [p for p in mz.PITCH_LADDER if p < pitch]
        for candidate in finer:
            self.assertGreater(mz.make_spec(problem, candidate).ncells, mz.MAX_CELLS)

    def test_pitch_depends_only_on_the_board(self):
        problem = routerfix.two_pad_board()
        self.assertEqual(mz.choose_pitch(problem), mz.choose_pitch(problem))

    def test_a_bigger_board_gets_a_coarser_or_equal_pitch(self):
        small = routerfix.two_pad_board()
        big = RoutingProblem(
            id="big", board=Board(width_mm=200.0, height_mm=200.0),
            rules=small.rules, pads=small.pads, nets=small.nets,
        )
        self.assertGreaterEqual(mz.choose_pitch(big), mz.choose_pitch(small))

    def test_cell_and_centre_round_trip(self):
        spec = mz.GridSpec(x0=-10.0, y0=-10.0, pitch=0.2, nx=100, ny=100)
        for cell in (0, 1, 55, 4321, spec.ncells - 1):
            x, y = spec.centre(cell)
            self.assertEqual(spec.cell_at(x, y), cell)


class Stamping(unittest.TestCase):
    """Owner semantics: free takes the value, a different owner blocks, the same
    owner is left alone. Every clearance rule in this router is that one rule
    applied at a radius, so if this is wrong nothing else can be right."""

    def setUp(self):
        self.spec = mz.GridSpec(x0=0.0, y0=0.0, pitch=0.1, nx=40, ny=40)

    def test_free_takes_the_value(self):
        grid = [mz.FREE] * self.spec.ncells
        mz._stamp(grid, self.spec, disc_capsule(2.0, 2.0, 0.4), 0.0, 7)
        self.assertEqual(grid[self.spec.cell_at(2.0, 2.0)], 7)

    def test_the_same_owner_is_left_alone(self):
        grid = [mz.FREE] * self.spec.ncells
        mz._stamp(grid, self.spec, disc_capsule(2.0, 2.0, 0.4), 0.0, 7)
        mz._stamp(grid, self.spec, disc_capsule(2.0, 2.0, 0.4), 0.0, 7)
        self.assertEqual(grid[self.spec.cell_at(2.0, 2.0)], 7)

    def test_a_second_owner_blocks_the_cell_for_everyone(self):
        grid = [mz.FREE] * self.spec.ncells
        mz._stamp(grid, self.spec, disc_capsule(2.0, 2.0, 0.4), 0.0, 7)
        mz._stamp(grid, self.spec, disc_capsule(2.0, 2.0, 0.4), 0.0, 9)
        self.assertEqual(grid[self.spec.cell_at(2.0, 2.0)], mz.BLOCKED)

    def test_the_radius_is_the_capsule_plus_the_extra(self):
        grid = [mz.FREE] * self.spec.ncells
        mz._stamp(grid, self.spec, disc_capsule(2.0, 2.0, 0.4), 0.3, 5)
        # disc radius 0.2 + extra 0.3 = 0.5mm reach
        self.assertEqual(grid[self.spec.cell_at(2.45, 2.0)], 5)
        self.assertEqual(grid[self.spec.cell_at(2.65, 2.0)], mz.FREE)

    def test_a_segment_capsule_is_swept_not_bounded(self):
        grid = [mz.FREE] * self.spec.ncells
        mz._stamp(grid, self.spec, segment_capsule(1.0, 1.0, 3.0, 1.0, 0.2), 0.0, 3)
        self.assertEqual(grid[self.spec.cell_at(2.0, 1.0)], 3)
        self.assertEqual(grid[self.spec.cell_at(2.0, 1.5)], mz.FREE)


class HoleClearance(unittest.TestCase):
    """JLC publishes three copper-to-hole numbers and the router uses all three.

    This is the defect ``packages/router`` was built because of: the shipped
    autorouter ran copper 0.07mm from a hole that needs 0.28mm. Here it is a
    property of the grid, so the test is a distance measurement.
    """

    def _board_with_drill(self, plated: bool, is_via: bool, net: str | None):
        rules = DesignRules.jlcpcb()
        drill = Drill(
            id="d1", center=Point(0.0, 0.0), width_mm=0.6, height_mm=0.6,
            plated=plated, net=net, pad_id=None if is_via else "p9",
        )
        problem = routerfix.two_pad_board(rules=rules, drills=(drill,))
        return problem, mz.GridBoard(
            problem, clearance=rules.target_clearance_mm, widths=(0.2,), pitch=0.1
        ), rules

    def test_a_component_plated_hole_holds_the_pth_number(self):
        problem, board, rules = self._board_with_drill(True, False, None)
        self.assertAlmostEqual(rules.min_pth_to_copper_mm, 0.28)
        grid = board.occ[(TOP, mz._width_key(0.2))]
        # copper edge at 0.24mm from the 0.6mm hole: centre 0.3 + 0.24 + 0.1
        self.assertEqual(grid[board.spec.cell_at(0.64, 0.0)], mz.BLOCKED)
        # copper edge at 0.34mm: legal
        self.assertEqual(grid[board.spec.cell_at(0.75, 0.0)], mz.FREE)

    def test_a_via_hole_holds_the_smaller_via_number(self):
        _, board, rules = self._board_with_drill(True, True, None)
        self.assertAlmostEqual(rules.min_via_to_copper_mm, 0.20)
        grid = board.occ[(TOP, mz._width_key(0.2))]
        # 0.24mm of copper-to-hole clears a via hole and would not clear a PTH
        self.assertEqual(grid[board.spec.cell_at(0.66, 0.0)], mz.FREE)

    def test_the_holes_own_net_may_reach_it(self):
        problem, board, _ = self._board_with_drill(True, False, "N1")
        index = board.net_index["N1"]
        grid = board.occ[(TOP, mz._width_key(0.2))]
        self.assertEqual(grid[board.spec.cell_at(0.64, 0.0)], index)

    def test_an_unplated_hole_blocks_its_own_net_too(self):
        """0.20mm to a non-plated hole, and no exemption for the net named on
        it: nothing is soldered to a hole that is not plated, so there is no
        connection for an exemption to serve."""
        problem, board, rules = self._board_with_drill(False, False, "N1")
        self.assertAlmostEqual(rules.min_npth_to_copper_mm, 0.20)
        grid = board.occ[(TOP, mz._width_key(0.2))]
        self.assertEqual(grid[board.spec.cell_at(0.55, 0.0)], mz.BLOCKED)
        self.assertEqual(grid[board.spec.cell_at(0.66, 0.0)], mz.FREE)


class WidthClasses(unittest.TestCase):
    def test_a_wide_net_may_go_strictly_fewer_places(self):
        """The cells a 0.5mm rail can occupy are a subset of the cells a 0.2mm
        signal can. One grid for both would be wrong in one direction or the
        other, and this is the assertion that says which."""
        rules = DesignRules.jlcpcb()
        problem = routerfix.two_pad_board(rules=rules)
        board = mz.GridBoard(
            problem, clearance=rules.target_clearance_mm,
            widths=(0.2, 0.5), pitch=0.1,
        )
        thin = board.occ[(TOP, mz._width_key(0.2))]
        fat = board.occ[(TOP, mz._width_key(0.5))]
        thin_free = {i for i, v in enumerate(fat) if v == mz.FREE}
        fat_free = {i for i, v in enumerate(thin) if v == mz.FREE}
        self.assertTrue(thin_free < fat_free, "the wide grid is not stricter")


class ViaRules(unittest.TestCase):
    def setUp(self):
        self.rules = DesignRules.jlcpcb()
        self.problem = routerfix.two_pad_board(rules=self.rules)
        self.board = mz.GridBoard(
            self.problem, clearance=self.rules.target_clearance_mm,
            widths=(0.2,), pitch=0.1,
        )

    def test_no_via_inside_an_smd_pad_even_its_own_nets(self):
        pad = self.problem.pads_by_id["p1"]
        cell = self.board.spec.cell_at(pad.center.x, pad.center.y)
        self.assertEqual(self.board.via_occ[cell], mz.BLOCKED)

    def test_the_via_grid_answers_the_same_as_the_workspace(self):
        """Sample the via grid against ``Workspace.via_ok`` over a lattice.

        The grid may be *stricter* — it is stamped with a sub-cell margin — but
        it must never say yes where the Workspace says no. That direction is the
        whole safety property of this router.
        """
        margin = 0.72 * 0.1  # a provably sound grid, whatever the shipped default is
        board = mz.GridBoard(
            self.problem, clearance=self.rules.target_clearance_mm + margin,
            widths=(0.2,), pitch=0.1,
        )
        ws = Workspace(self.problem, clearance=self.rules.target_clearance_mm)
        disagreements = []
        for j in range(4, board.spec.ny - 4, 7):
            for i in range(4, board.spec.nx - 4, 7):
                cell = j * board.spec.nx + i
                x, y = board.spec.centre(cell)
                grid_ok = board.via_occ[cell] in (mz.FREE, board.net_index["N1"])
                ws_ok = ws.via_ok(Point(x, y), "N1") is True
                if grid_ok and not ws_ok:
                    disagreements.append((round(x, 3), round(y, 3)))
        self.assertEqual(disagreements, [], "grid allowed a via the Workspace refuses")


class BoardEdge(unittest.TestCase):
    def test_copper_may_not_sit_within_the_edge_clearance(self):
        rules = DesignRules.jlcpcb()
        problem = routerfix.two_pad_board(rules=rules)
        board = mz.GridBoard(
            problem, clearance=rules.target_clearance_mm, widths=(0.2,), pitch=0.1
        )
        grid = board.occ[(TOP, mz._width_key(0.2))]
        # board is 20 x 20 centred on the origin; edge clearance 0.2 + half width
        self.assertEqual(grid[board.spec.cell_at(9.95, 0.0)], mz.BLOCKED)
        self.assertEqual(grid[board.spec.cell_at(9.5, 0.0)], mz.FREE)

    def test_off_board_cells_are_blocked(self):
        rules = DesignRules.jlcpcb()
        problem = routerfix.two_pad_board(rules=rules)
        board = mz.GridBoard(
            problem, clearance=rules.target_clearance_mm, widths=(0.2,), pitch=0.1
        )
        grid = board.occ[(TOP, mz._width_key(0.2))]
        self.assertEqual(grid[board.spec.cell_at(10.5, 0.0)], mz.BLOCKED)


class Polygons(unittest.TestCase):
    def test_spans_cover_a_square_and_nothing_else(self):
        spec = mz.GridSpec(x0=-2.0, y0=-2.0, pitch=0.5, nx=12, ny=12)
        square = (
            Point(-1.0, -1.0), Point(1.0, -1.0), Point(1.0, 1.0), Point(-1.0, 1.0),
        )
        rows = mz.polygon_spans(square, spec)
        covered = {
            j * spec.nx + i
            for j, row in enumerate(rows)
            for lo, hi in row
            for i in range(lo, hi + 1)
        }
        for cell in range(spec.ncells):
            x, y = spec.centre(cell)
            self.assertEqual(
                cell in covered, -1.0 < x < 1.0 and -1.0 < y < 1.0, (x, y)
            )


class Collinear(unittest.TestCase):
    def test_merging_collinear_points_changes_no_geometry(self):
        points = [Point(0.0, 0.0), Point(1.0, 0.0), Point(2.0, 0.0), Point(2.0, 1.0)]
        self.assertEqual(
            mz._merge_collinear(points),
            [Point(0.0, 0.0), Point(2.0, 0.0), Point(2.0, 1.0)],
        )

    def test_a_staircase_keeps_every_corner(self):
        points = [Point(0.0, 0.0), Point(1.0, 0.0), Point(1.0, 1.0), Point(2.0, 1.0)]
        self.assertEqual(mz._merge_collinear(points), points)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class Search(unittest.TestCase):
    def setUp(self):
        self.rules = DesignRules.jlcpcb()
        self.problem = routerfix.two_pad_board(rules=self.rules)
        self.board = mz.GridBoard(
            self.problem, clearance=self.rules.target_clearance_mm,
            widths=(0.2,), pitch=0.2,
        )
        self.maze = mz.Maze(self.board)

    def test_it_finds_a_straight_line_across_open_board(self):
        spec = self.board.spec
        start = spec.cell_at(-4.0, 0.0)
        goal = spec.cell_at(4.0, 0.0)
        result = self.maze.search(
            sources=[(0, start)], goals={goal}, width=0.2,
            net_index=self.board.net_index["N1"], target_cell=goal,
            target_layers=(0,),
        )
        self.assertTrue(result.path)
        self.assertEqual(result.path[0], (0, start))
        self.assertEqual(result.path[-1], (0, goal))
        # no layer change on an empty board
        self.assertEqual({layer for layer, _ in result.path}, {0})

    def test_it_refuses_when_there_is_no_path(self):
        spec = self.board.spec
        grid = self.board.occ[(TOP, mz._width_key(0.2))]
        other = self.board.occ[(BOTTOM, mz._width_key(0.2))]
        for j in range(spec.ny):  # a wall through both layers
            cell = j * spec.nx + spec.cell_at(0.0, 0.0) % spec.nx
            grid[cell] = mz.BLOCKED
            other[cell] = mz.BLOCKED
            self.board.via_occ[cell] = mz.BLOCKED
        goal = spec.cell_at(4.0, 0.0)
        result = self.maze.search(
            sources=[(0, spec.cell_at(-4.0, 0.0))], goals={goal}, width=0.2,
            net_index=self.board.net_index["N1"], target_cell=goal,
            target_layers=(0,),
        )
        self.assertFalse(result.path)
        self.assertEqual(result.reason, "no path")

    def test_a_wall_on_one_layer_is_crossed_by_via(self):
        spec = self.board.spec
        grid = self.board.occ[(TOP, mz._width_key(0.2))]
        column = spec.cell_at(0.0, 0.0) % spec.nx
        for j in range(spec.ny):
            grid[j * spec.nx + column] = mz.BLOCKED
        goal = spec.cell_at(4.0, 0.0)
        result = self.maze.search(
            sources=[(0, spec.cell_at(-4.0, 0.0))], goals={goal}, width=0.2,
            net_index=self.board.net_index["N1"], target_cell=goal,
            target_layers=(0,),
        )
        self.assertTrue(result.path)
        self.assertEqual({layer for layer, _ in result.path}, {0, 1})

    def test_the_node_cap_is_honoured_and_named(self):
        spec = self.board.spec
        goal = spec.cell_at(4.0, 0.0)
        result = self.maze.search(
            sources=[(0, spec.cell_at(-4.0, 0.0))], goals={goal}, width=0.2,
            net_index=self.board.net_index["N1"], target_cell=None,
            node_cap=5,
        )
        self.assertFalse(result.path)
        self.assertEqual(result.reason, "node cap")
        self.assertLessEqual(result.nodes, 5)


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


class Ordering(unittest.TestCase):
    def setUp(self):
        self.problem = _small_problems()[-1]
        self.stats = mz.net_stats(self.problem)

    def test_every_ordering_is_a_permutation_of_every_routable_net(self):
        expected = {n.id for n in self.problem.routable_nets if len(n.pads) >= 2}
        for ordering in mz.ORDERINGS:
            with self.subTest(ordering=ordering):
                got = [s.net.id for s in mz.order_nets(self.stats, ordering)]
                self.assertEqual(len(got), len(set(got)))
                self.assertEqual(set(got), expected)

    def test_each_ordering_is_stable(self):
        for ordering in mz.ORDERINGS:
            with self.subTest(ordering=ordering):
                a = [s.net.id for s in mz.order_nets(self.stats, ordering)]
                b = [s.net.id for s in mz.order_nets(list(reversed(self.stats)), ordering)]
                self.assertEqual(a, b)

    def test_shortest_and_longest_run_the_lengths_opposite_ways(self):
        """Not exact reversals — ties break on net id both ways, deliberately,
        because a stable order matters more than a pretty symmetry."""
        by_id = {s.net.id: round(s.mst_mm, 6) for s in self.stats}
        short = [by_id[s.net.id] for s in mz.order_nets(self.stats, "shortest-first")]
        long = [by_id[s.net.id] for s in mz.order_nets(self.stats, "longest-first")]
        self.assertEqual(short, sorted(short))
        self.assertEqual(long, sorted(long, reverse=True))

    def test_an_unknown_ordering_is_refused_by_name(self):
        with self.assertRaises(ValueError):
            mz.order_nets(self.stats, "vibes")
        with self.assertRaises(ValueError):
            mz.MazeAStarRouter(ordering="vibes")


# ---------------------------------------------------------------------------
# The properties every entrant owes the tournament
# ---------------------------------------------------------------------------


class Legality(unittest.TestCase):
    def test_no_drc_errors_on_any_small_instance(self):
        for problem in _small_problems():
            with self.subTest(instance=problem.id):
                result = scoring.score(problem, _router().route(problem, Budget()))
                self.assertEqual(result.errors, 0, f"{problem.id}: {result.error_kinds}")

    def test_every_emitted_trace_survives_the_workspace(self):
        """The gate this router lives or dies by.

        The grid is an approximation of the rules. If anything it approves ever
        reaches the solution without the Workspace agreeing, the whole design is
        unsound — so this checks the *output*, with a Workspace built fresh from
        the problem, not the one the router was holding.
        """
        for problem in _small_problems():
            with self.subTest(instance=problem.id):
                solution = _router().route(problem, Budget())
                ws = Workspace(problem, clearance=problem.rules.target_clearance_mm)
                for trace in solution.traces:
                    verdict = ws.path_ok(
                        trace.layer, trace.points, trace.width_mm, trace.net
                    )
                    self.assertIs(
                        verdict, True,
                        f"{problem.id} {trace.id}: {getattr(verdict, 'reason', '')} "
                        f"{getattr(verdict, 'detail', '')}",
                    )
                    ws.commit_trace(trace)
                for via in solution.vias:
                    verdict = ws.via_ok(via.center, via.net)
                    self.assertIs(
                        verdict, True,
                        f"{problem.id} {via.id}: {getattr(verdict, 'reason', '')}",
                    )
                    ws.commit_via(via)

    def test_it_never_claims_more_than_it_did(self):
        for problem in _small_problems():
            with self.subTest(instance=problem.id):
                solution = _router().route(problem, Budget())
                result = scoring.score(problem, solution)
                self.assertTrue(
                    result.claim_honest,
                    f"{problem.id} claimed {solution.complete} but routed "
                    f"{result.completeness:.0%}",
                )

    def test_unrouted_nets_are_named_not_just_counted(self):
        problem = _small_problems()[-1]
        solution = _router().route(problem, Budget(max_iterations=3))
        if solution.complete:
            self.skipTest("this instance finished inside three iterations")
        self.assertTrue(solution.unrouted_nets)
        known = {n.id for n in problem.nets}
        self.assertTrue(set(solution.unrouted_nets) <= known)


class Determinism(unittest.TestCase):
    def test_byte_identical_across_runs(self):
        for problem in _small_problems():
            with self.subTest(instance=problem.id):
                result = scoring.determinism_check(
                    _router(), problem, Budget(), runs=2
                )
                self.assertTrue(result.deterministic, result.detail)

    def test_a_reused_router_object_gives_the_same_answer(self):
        """State lives in ``_State``, not on the router, so a second call on the
        same object cannot inherit the first one's board."""
        problem = _small_problems()[-1]
        router = _router()
        first = router.route(problem, Budget())
        second = router.route(problem, Budget())
        self.assertEqual(first.fingerprint(), second.fingerprint())

    def test_the_seed_does_not_change_the_answer(self):
        """There is no randomness in this family. If a seed ever moves the
        output, something is reading the system RNG."""
        problem = _small_problems()[-1]
        a = _router().route(problem, Budget(seed=0))
        b = _router().route(problem, Budget(seed=99))
        self.assertEqual(a.fingerprint(), b.fingerprint())


class Budgets(unittest.TestCase):
    def test_a_tiny_iteration_budget_stops_early_and_says_so(self):
        problem = _small_problems()[-1]
        stingy = _router().route(problem, Budget(max_iterations=2))
        generous = _router().route(problem, Budget())
        self.assertLessEqual(len(stingy.traces), len(generous.traces))
        self.assertTrue(
            any("budget exhausted" in note for note in stingy.notes), stingy.notes
        )

    def test_a_tiny_node_budget_does_not_produce_illegal_copper(self):
        problem = _small_problems()[-1]
        solution = _router().route(problem, Budget(max_nodes=50))
        result = scoring.score(problem, solution)
        self.assertEqual(result.errors, 0, result.error_kinds)

    def test_nodes_are_counted(self):
        problem = _small_problems()[-1]
        solution = _router().route(problem, Budget())
        self.assertGreater(solution.nodes_expanded, 0)


class Planes(unittest.TestCase):
    def test_a_pour_is_a_net_and_not_a_field_of_obstacles(self):
        """The shipped router produced byte-identical copper with and without a
        ground pour: it counted 73 ground vias as 73 obstacles. This asserts the
        opposite — the pour makes the board *easier*."""
        problems = {p.id: p for p in bench.load_all()}
        bare = problems.get("hydrate-coaster")
        planed = problems.get("hydrate-coaster-plane")
        if bare is None or planed is None:
            self.skipTest("plane variant not present")
        budget = Budget()
        a = scoring.score(bare, _router().route(bare, budget))
        b = scoring.score(planed, _router().route(planed, budget))
        self.assertGreaterEqual(b.completeness, a.completeness)
        self.assertEqual(b.errors, 0, b.error_kinds)

    def test_a_pad_inside_the_pour_costs_no_copper(self):
        rules = DesignRules.jlcpcb()
        ring = (
            Point(-9.0, -9.0), Point(9.0, -9.0), Point(9.0, 9.0), Point(-9.0, 9.0),
        )
        hole = Pad(
            id="g1", net="GND", center=Point(3.0, 3.0), width_mm=1.2, height_mm=1.2,
            layers=(TOP, BOTTOM), kind="plated_hole", component="J1",
        )
        other = Pad(
            id="g2", net="GND", center=Point(-3.0, -3.0), width_mm=1.2,
            height_mm=1.2, layers=(TOP, BOTTOM), kind="plated_hole", component="J1",
        )
        net = Net(
            id="GND", name="GND", net_class="ground", pads=("g1", "g2"),
            min_width_mm=0.5,
        )
        problem = routerfix.two_pad_board(
            rules=rules,
            extra_pads=(hole, other),
            extra_nets=(net,),
            planes=(Plane(id="pl", net="GND", layer=BOTTOM, outline=ring),),
        )
        solution = _router().route(problem, Budget())
        ground_copper = [t for t in solution.traces if t.net == "GND"]
        self.assertEqual(ground_copper, [], "poured pads should need no traces")
        result = scoring.score(problem, solution)
        self.assertEqual(result.completeness, 1.0)


class Necking(unittest.TestCase):
    def test_the_neck_is_the_narrowest_width_that_is_not_a_finding(self):
        rules = DesignRules.jlcpcb()
        self.assertEqual(_router().resolved_neck(rules), rules.warn_trace_mm)
        self.assertGreaterEqual(_router().resolved_neck(rules), rules.min_trace_mm)

    def test_a_neck_below_the_fab_minimum_is_clamped_not_honoured(self):
        rules = DesignRules.jlcpcb()
        self.assertEqual(_router(neck_width_mm=0.05).resolved_neck(rules),
                         rules.min_trace_mm)

    def test_a_power_net_falls_back_to_the_neck_and_a_signal_net_can_too(self):
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        state = type("S", (), {"problem": problem})()
        rail = Net(id="P", name="V3_3", net_class="power", pads=("a", "b"),
                   min_width_mm=0.5)
        self.assertEqual(_router()._widths_for(state, rail), [0.5, 0.15])
        self.assertEqual(_router()._widths_for(state, problem.nets[0]), [0.2, 0.15])

    def test_necking_can_be_turned_off(self):
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        state = type("S", (), {"problem": problem})()
        net = problem.nets[0]
        self.assertEqual(
            _router(neck_width_mm=0)._widths_for(state, net), [net.min_width_mm]
        )


class RipUp(unittest.TestCase):
    def test_rip_up_never_makes_an_instance_worse(self):
        """A pass that connects no more nets than before is rolled back, so the
        rip-up variant can only be better than the plain one or equal to it."""
        for problem in _small_problems():
            with self.subTest(instance=problem.id):
                budget = Budget()
                plain = scoring.score(problem, _router().route(problem, budget))
                ripped = scoring.score(
                    problem, _router(rip_up_passes=2).route(problem, budget)
                )
                self.assertGreaterEqual(ripped.completeness, plain.completeness)
                self.assertEqual(ripped.errors, 0, ripped.error_kinds)


if __name__ == "__main__":
    unittest.main()
