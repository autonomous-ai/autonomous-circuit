"""The selector, and the two things it must never do quietly.

The rules themselves are fitted to one tournament, so a test that asserted
"harness-puck picks pathfinder" would only be asserting that the fit has not
been re-derived. What is worth pinning is the *shape*: that a rule always
fires, that the evidence travels with the pick, that the modes compose copper
rather than lose it, and — the one that matters — that a relay never returns
fewer connected nets than its lead router did on its own.
"""

from __future__ import annotations

import unittest

import routerfix
from routerlib import connectivity as conn
from routerlib import portfolio
from routerlib.bench import features_of, instance_paths, load_instance
from routerlib.baseline import PatternRouter
from routerlib.model import Budget, RoutingSolution
from routerlib.scoring import score

BUDGET = Budget(max_iterations=20_000, max_nodes=200_000, seed=0)


def registry() -> dict:
    """Only the baseline. The tournament families live outside the package and
    a unit test must not depend on loading nine modules by path."""
    return {PatternRouter.name: PatternRouter}


def _selection_over(candidates: tuple[str, ...]) -> portfolio.Selection:
    """A hand-made selection, so a mode can be tested on the routers a unit
    test actually has rather than on the ones the rules would name."""
    return portfolio.Selection(
        router=candidates[0],
        rule="test",
        why="hand-made for a test",
        fallback="baseline-pattern",
        candidates=candidates,
        mode="relay",
        expectation=portfolio.Expectation("routable", "likely", "test"),
    )


class Rules(unittest.TestCase):
    def test_a_rule_always_fires(self):
        for path in instance_paths():
            problem = load_instance(path)
            selection = portfolio.select(features_of(problem))
            self.assertIn(selection.rule, {r.name for r in portfolio.RULES})
            self.assertIn(selection.router, portfolio.FAMILIES)

    def test_every_rule_carries_its_measurement(self):
        for rule in portfolio.RULES:
            self.assertTrue(rule.why.strip(), f"{rule.name} has no evidence")
            self.assertTrue(
                any(ch.isdigit() for ch in rule.why),
                f"{rule.name}'s evidence quotes no number",
            )

    def test_rejected_rules_are_kept(self):
        # A tried-and-failed rule is worth more than an untried one: without
        # this list the next agent re-derives "regular matrix -> structured
        # router" from the same data that already refuted it.
        self.assertGreaterEqual(len(portfolio.REJECTED_RULES), 4)
        for name, why in portfolio.REJECTED_RULES:
            self.assertTrue(name.strip() and why.strip())

    def test_trivial_board_takes_the_cheap_router(self):
        problem = routerfix.two_pad_board()
        selection = portfolio.select(features_of(problem))
        self.assertEqual(selection.rule, "trivial-cheapest")
        self.assertEqual(selection.router, "baseline-pattern")

    def test_budget_class_sets_the_candidate_count(self):
        problem = routerfix.two_pad_board()
        f = features_of(problem)
        for name, width in portfolio.BUDGET_WIDTH.items():
            self.assertEqual(
                len(portfolio.select(f, budget_class=name).candidates), width
            )
        with self.assertRaises(ValueError):
            portfolio.select(f, budget_class="unlimited")

    def test_fallback_is_never_the_pick(self):
        for path in instance_paths():
            selection = portfolio.select(features_of(load_instance(path)))
            self.assertNotEqual(selection.fallback, selection.router)


class Difficulty(unittest.TestCase):
    def test_thresholds_are_ordered(self):
        self.assertLess(portfolio.HARD_NETS, portfolio.UNFINISHED_NETS)

    def test_forecast_never_promises(self):
        for path in instance_paths():
            problem = load_instance(path)
            expectation = portfolio.difficulty_of(features_of(problem))
            self.assertIn(expectation.difficulty,
                          {"routable", "hard", "beyond"})
            self.assertIn(expectation.completes, {"likely", "unlikely", "no"})
            self.assertIn(str(len(problem.routable_nets)), expectation.evidence)


class Modes(unittest.TestCase):
    def problem(self):
        return routerfix.two_pad_board()

    def test_single_runs_one_router(self):
        problem = self.problem()
        result = portfolio.route(problem, BUDGET, registry(),
                                 budget_class="cheap", mode="single")
        self.assertEqual(len(result.stages), 1)
        self.assertTrue(result.solution.router.startswith("portfolio["))
        self.assertGreaterEqual(score(problem, result.solution).completeness, 1.0)

    def test_relay_stops_when_everything_is_connected(self):
        problem = self.problem()
        reg = {PatternRouter.name: PatternRouter, "second": PatternRouter}
        result = portfolio._route_relay(
            problem, BUDGET, reg, _selection_over(("baseline-pattern", "second"))
        )
        # The lead finishes the board, so no follower is asked for anything.
        self.assertEqual(len(result.stages), 1)
        self.assertTrue(any("every net connected" in n for n in result.notes))

    def test_relay_never_loses_a_net(self):
        """The property the whole mode rests on: a follower adds copper on top
        of what is already down, so connectivity is monotone."""
        problem = load_instance(
            next(p for p in instance_paths() if "usb-c-power" in p.stem)
        )
        reg = {PatternRouter.name: PatternRouter, "second": PatternRouter}
        lead = PatternRouter().route(problem, BUDGET)
        lead_nets = len(conn.analyse(problem, lead).connected_nets)
        result = portfolio._route_relay(
            problem, BUDGET, reg,
            _selection_over(("baseline-pattern", "second")),
        )
        after = len(conn.analyse(problem, result.solution).connected_nets)
        self.assertGreaterEqual(after, lead_nets)
        for a, b in zip(result.stages, result.stages[1:]):
            self.assertGreaterEqual(b.completeness, a.completeness)

    def test_an_unregistered_lead_falls_back_rather_than_returning_nothing(self):
        problem = load_instance(
            next(p for p in instance_paths() if "usb-c-power" in p.stem)
        )
        # The real registry here has only the baseline, so every tournament
        # family the selector names is missing. The run must still produce
        # copper — an empty board reported as a result is the exact failure
        # this package exists to make impossible.
        result = portfolio.route(problem, BUDGET, registry(),
                                 budget_class="standard", mode="relay")
        self.assertTrue(result.solution.traces)
        self.assertTrue(any("not registered" in n for n in result.notes))

    def test_an_empty_registry_raises(self):
        with self.assertRaises(KeyError):
            portfolio.route(self.problem(), BUDGET, {}, mode="single")

    def test_best_of_n_warns_when_it_ranks_with_the_harness(self):
        problem = self.problem()
        result = portfolio.route(problem, BUDGET, registry(),
                                 budget_class="standard", mode="best-of-n")
        self.assertTrue(
            any("inscribed-stadium" in note for note in result.notes),
            "a best-of-N ranked by the biased key must say so",
        )

    def test_best_of_n_is_never_worse_than_its_best_member(self):
        problem = load_instance(
            next(p for p in instance_paths() if "status-led" in p.stem)
        )
        reg = registry()
        result = portfolio.route(problem, BUDGET, reg,
                                 budget_class="thorough", mode="best-of-n")
        winner = portfolio.harness_key(problem, result.solution)
        for name, key in result.ranked:
            self.assertLessEqual(winner, key, f"{name} scored better than the pick")

    def test_a_dead_family_costs_a_stage_not_the_run(self):
        class Exploding:
            name = "exploding"

            def route(self, problem, budget):
                raise RuntimeError("boom")

        problem = load_instance(
            next(p for p in instance_paths() if "usb-c-power" in p.stem)
        )
        reg = dict(registry())
        reg["exploding"] = Exploding
        result = portfolio._route_relay(
            problem, BUDGET, reg,
            _selection_over(("baseline-pattern", "exploding")),
        )
        self.assertTrue(any("raised RuntimeError" in n for n in result.notes))
        self.assertTrue(result.solution.traces)

    def test_unknown_mode_refuses(self):
        with self.assertRaises(ValueError):
            portfolio.route(self.problem(), BUDGET, registry(), mode="vibes")


class Determinism(unittest.TestCase):
    def test_relay_is_byte_identical_across_runs(self):
        problem = load_instance(
            next(p for p in instance_paths() if "usb-c-power" in p.stem)
        )
        reg = registry()
        prints = {
            portfolio.route(problem, BUDGET, reg, budget_class="thorough",
                            mode="relay").solution.fingerprint()
            for _ in range(2)
        }
        self.assertEqual(len(prints), 1, "relay is not deterministic")


class PipelineKey(unittest.TestCase):
    def test_legality_tier_comes_from_the_caller(self):
        problem = self.__class__.problem = routerfix.two_pad_board()
        solution = PatternRouter().route(problem, BUDGET)
        key = portfolio.pipeline_key_factory(lambda s: 7)
        self.assertEqual(key(problem, solution)[1], 7)
        # Completeness still leads: a net that is not connected is a dead board
        # and nothing below it compensates.
        empty = RoutingSolution(router="empty", traces=(), vias=(), complete=False)
        self.assertGreater(key(problem, empty)[0], key(problem, solution)[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
