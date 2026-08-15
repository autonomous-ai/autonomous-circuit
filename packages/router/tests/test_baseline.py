"""The baseline router, and the properties every entrant in the tournament has
to have. These are the tests a new algorithm should be added to."""

from __future__ import annotations

import unittest

import routerfix
from routerlib import bench, scoring
from routerlib.baseline import PatternRouter, _mst_edges
from routerlib.model import BOTTOM, TOP, Budget, DesignRules, Router

#: Kept small on purpose: the machine is shared and the point of these tests is
#: the property, not the size.
SMALL = 3


def _small_problems():
    problems = sorted(bench.load_all(), key=lambda p: len(p.pads))
    return problems[:SMALL]


class Contract(unittest.TestCase):
    def test_it_is_a_router(self):
        self.assertIsInstance(PatternRouter(), Router)

    def test_it_returns_a_solution_for_an_empty_problem(self):
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        solution = PatternRouter().route(problem, Budget())
        self.assertEqual(solution.router, "baseline-pattern")
        self.assertGreaterEqual(solution.iterations, 1)

    def test_it_routes_the_easy_case(self):
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        result = scoring.score(problem, PatternRouter().route(problem, Budget()))
        self.assertEqual(result.completeness, 1.0)
        self.assertEqual(result.errors, 0)


class Legality(unittest.TestCase):
    """The property that makes this a usable floor: it never emits copper it
    cannot defend. A pattern router with no rip-up *will* fail to finish dense
    boards; the honest output of that is an incomplete board, never a complete
    illegal one."""

    def test_no_drc_errors_on_any_small_instance(self):
        for problem in _small_problems():
            with self.subTest(instance=problem.id):
                solution = PatternRouter().route(problem, Budget())
                result = scoring.score(problem, solution)
                self.assertEqual(
                    result.errors, 0,
                    f"{problem.id}: {result.error_kinds}",
                )

    def test_it_never_claims_more_than_it_did(self):
        for problem in _small_problems():
            with self.subTest(instance=problem.id):
                solution = PatternRouter().route(problem, Budget())
                result = scoring.score(problem, solution)
                self.assertTrue(
                    result.claim_honest,
                    f"{problem.id} claimed {solution.complete} but routed "
                    f"{result.completeness:.0%}",
                )


class Determinism(unittest.TestCase):
    def test_byte_identical_across_runs(self):
        for problem in _small_problems():
            with self.subTest(instance=problem.id):
                result = scoring.determinism_check(
                    PatternRouter(), problem, Budget(), runs=2
                )
                self.assertTrue(result.deterministic, result.detail)


class Budgets(unittest.TestCase):
    def test_a_tiny_budget_stops_early_and_says_so(self):
        problems = _small_problems()
        if not problems:
            self.skipTest("no instances")
        problem = problems[-1]
        stingy = PatternRouter().route(problem, Budget(max_iterations=5))
        generous = PatternRouter().route(problem, Budget())
        self.assertLessEqual(stingy.iterations, 20)
        self.assertLess(len(stingy.traces), len(generous.traces) + 1)
        self.assertFalse(stingy.complete)


class Planes(unittest.TestCase):
    def test_a_plane_variant_is_not_harder_than_the_bare_one(self):
        """A ground plane should make routing easier, not add 73 obstacles.
        The router we ship produced byte-identical copper with and without a
        pour; this asserts the opposite behaviour."""
        problems = {p.id: p for p in bench.load_all()}
        bare = problems.get("hydrate-coaster")
        planed = problems.get("hydrate-coaster-plane")
        if bare is None or planed is None:
            self.skipTest("plane variant not present")
        a = scoring.score(bare, PatternRouter().route(bare, Budget()))
        b = scoring.score(planed, PatternRouter().route(planed, Budget()))
        self.assertGreaterEqual(b.completeness, a.completeness)
        self.assertEqual(b.errors, 0)


class SpanningTree(unittest.TestCase):
    def test_prim_is_deterministic_and_spans(self):
        pads = tuple(
            routerfix.pad(f"p{i}", "N1", float(i), float(i % 3)) for i in range(6)
        )
        first = _mst_edges(pads)
        second = _mst_edges(pads)
        self.assertEqual(
            [(a.id, b.id) for a, b in first], [(a.id, b.id) for a, b in second]
        )
        self.assertEqual(len(first), len(pads) - 1)


if __name__ == "__main__":
    unittest.main()
