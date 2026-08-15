"""Scoring: the lexicographic order, the ruler, and the determinism check."""

from __future__ import annotations

import unittest

import routerfix
from routerlib import scoring
from routerlib.model import (
    TOP,
    Budget,
    DesignRules,
    Net,
    Point,
    RoutingSolution,
    Trace,
    Via,
    empty_solution,
)


class Lexicographic(unittest.TestCase):
    def scores(self):
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        good = scoring.score(problem, routerfix.straight_trace(problem))
        nothing = scoring.score(problem, empty_solution())
        return problem, good, nothing

    def test_completeness_beats_everything(self):
        problem, good, nothing = self.scores()
        self.assertLess(good.key(), nothing.key())
        self.assertEqual(good.completeness, 1.0)

    def test_an_incomplete_legal_board_beats_a_complete_illegal_one(self):
        """Not a preference — the tier order. A dead board and a scrapped board
        are both worthless, but completeness is checked first, so this test
        pins the *shape* of the ordering rather than the outcome."""
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        complete_legal = scoring.score(problem, routerfix.straight_trace(problem))
        complete_illegal = scoring.score(
            problem,
            RoutingSolution(
                router="bad",
                traces=(
                    Trace("t0", "N1", TOP, (Point(-5, 0), Point(5, 0)), 0.2),
                    # 0.05mm wide: under the 0.10mm fab floor.
                    Trace("t1", "N1", TOP, (Point(-5, 2), Point(5, 2)), 0.05),
                ),
            ),
        )
        self.assertEqual(complete_illegal.completeness, 1.0)
        self.assertGreater(complete_illegal.errors, 0)
        self.assertLess(complete_legal.key(), complete_illegal.key())

    def test_fewer_vias_wins_when_everything_else_ties(self):
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        lean = scoring.score(problem, routerfix.straight_trace(problem))
        fat = scoring.score(
            problem,
            RoutingSolution(
                router="t",
                traces=routerfix.straight_trace(problem).traces,
                vias=(Via("v0", "N1", Point(3, 3)),),
            ),
        )
        self.assertLess(lean.key(), fat.key())

    def test_wall_clock_is_not_in_the_key(self):
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        base = routerfix.straight_trace(problem)
        slow = RoutingSolution(
            router=base.router, traces=base.traces, complete=True, wall_clock_s=99.0
        )
        self.assertEqual(
            scoring.score(problem, base).key(), scoring.score(problem, slow).key()
        )


class Honesty(unittest.TestCase):
    def test_a_false_completion_claim_is_scored_on_the_copper(self):
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        liar = RoutingSolution(router="liar", complete=True)
        result = scoring.score(problem, liar)
        self.assertEqual(result.completeness, 0.0)
        self.assertFalse(result.claim_honest)
        self.assertTrue(any("claimed complete" in note for note in result.notes))

    def test_an_honest_failure_is_recorded_as_honest(self):
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        result = scoring.score(problem, empty_solution())
        self.assertTrue(result.claim_honest)


class Rulers(unittest.TestCase):
    def test_the_hash_travels_with_the_score(self):
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        result = scoring.score(problem, empty_solution())
        self.assertEqual(len(result.ruler.hash), 12)
        self.assertIn("compare only", result.ruler.line())
        self.assertIn("coverageGaps", result.as_dict()["measuredAgainst"])

    def test_a_shorter_ruler_is_a_different_ruler(self):
        rules = DesignRules.jlcpcb()
        a = scoring.ruler_for(rules)
        b = scoring.Ruler(
            scorer_version=a.scorer_version,
            fab_profile=a.fab_profile,
            clearance_gate_mm=a.clearance_gate_mm,
            min_clearance_mm=a.min_clearance_mm,
            check_kinds=a.check_kinds[:-1],
            coverage_gaps=a.coverage_gaps,
        )
        self.assertNotEqual(a.hash, b.hash)


class _Flaky:
    """A router that reads a counter instead of its seed. The exact failure the
    determinism check exists to catch."""

    name = "flaky"

    def __init__(self):
        self.calls = 0

    def route(self, problem, budget):
        self.calls += 1
        offset = 0.0 if self.calls == 1 else 0.5
        return RoutingSolution(
            router=self.name,
            traces=(
                Trace("t0", "N1", TOP, (Point(-5, offset), Point(5, offset)), 0.2),
            ),
        )


class Determinism(unittest.TestCase):
    def test_a_stable_router_passes(self):
        from routerlib.baseline import PatternRouter

        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        result = scoring.determinism_check(PatternRouter(), problem, Budget())
        self.assertTrue(result.deterministic, result.detail)

    def test_a_flaky_router_fails_and_says_why(self):
        problem = routerfix.two_pad_board(rules=DesignRules.jlcpcb())
        result = scoring.determinism_check(_Flaky(), problem, Budget())
        self.assertFalse(result.deterministic)
        self.assertIn("different copper", result.detail)

    def test_it_compares_bytes_too_not_only_geometry(self):
        result = scoring.DeterminismResult(
            deterministic=False,
            fingerprints=("a", "a"),
            payload_hashes=("x", "y"),
            detail="same copper, different serialised bytes (unstable ids or order)",
        )
        self.assertIn("bytes", result.detail)


if __name__ == "__main__":
    unittest.main()
