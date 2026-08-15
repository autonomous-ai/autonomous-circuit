"""The contract types: quantisation, ordering, fingerprints, and a budget that
counts rather than times."""

from __future__ import annotations

import unittest

import routerfix
from routerlib.model import (
    BOTTOM,
    TOP,
    Budget,
    DesignRules,
    Drill,
    Net,
    Point,
    Router,
    RoutingSolution,
    Trace,
    Via,
    empty_solution,
)


class Quantisation(unittest.TestCase):
    def test_points_snap_to_a_nanometre(self):
        self.assertEqual(Point(1.00000000004, 2.0).x, 1.0)
        self.assertEqual(Point(-0.0, 0.0).x, 0.0)

    def test_trace_length(self):
        trace = Trace("t", "N1", TOP, (Point(0, 0), Point(3, 4)), 0.2)
        self.assertAlmostEqual(trace.length_mm, 5.0)


class Fingerprints(unittest.TestCase):
    def solution(self, order_flipped=False):
        traces = [
            Trace("a", "N1", TOP, (Point(0, 0), Point(1, 0)), 0.2),
            Trace("b", "N2", BOTTOM, (Point(0, 1), Point(1, 1)), 0.2),
        ]
        if order_flipped:
            traces.reverse()
        return RoutingSolution(router="t", traces=tuple(traces))

    def test_same_copper_different_order_is_the_same_fingerprint(self):
        self.assertEqual(
            self.solution().fingerprint(), self.solution(True).fingerprint()
        )

    def test_different_copper_is_a_different_fingerprint(self):
        other = RoutingSolution(
            router="t",
            traces=(Trace("a", "N1", TOP, (Point(0, 0), Point(1.001, 0)), 0.2),),
        )
        self.assertNotEqual(self.solution().fingerprint(), other.fingerprint())

    def test_ids_and_timing_are_not_in_the_fingerprint(self):
        renamed = RoutingSolution(
            router="other",
            traces=(
                Trace("zzz", "N1", TOP, (Point(0, 0), Point(1, 0)), 0.2),
                Trace("yyy", "N2", BOTTOM, (Point(0, 1), Point(1, 1)), 0.2),
            ),
            wall_clock_s=99.0,
        )
        self.assertEqual(self.solution().fingerprint(), renamed.fingerprint())


class Budgets(unittest.TestCase):
    def test_iterations_exhaust(self):
        meter = Budget(max_iterations=3).meter()
        self.assertTrue(meter.tick())
        self.assertTrue(meter.tick())
        self.assertFalse(meter.tick())
        self.assertEqual(meter.stop_reason, "iterations")

    def test_nodes_exhaust(self):
        meter = Budget(max_nodes=10).meter()
        meter.expand(10)
        self.assertTrue(meter.exhausted)
        self.assertEqual(meter.stop_reason, "nodes")

    def test_wall_clock_is_only_a_safety_valve(self):
        """It exists, it is generous, and hitting it invalidates the run —
        it is never the thing that decides how much search happens."""
        self.assertGreaterEqual(Budget().wall_clock_cap_s, 60.0)
        meter = Budget(wall_clock_cap_s=0.0).meter()
        self.assertTrue(meter.exhausted)
        self.assertEqual(meter.stop_reason, "wall_clock")


class Rules(unittest.TestCase):
    def test_from_profile_reads_the_pipeline(self):
        from circuitpy.fab import get_profile

        profile = get_profile("jlcpcb")
        rules = DesignRules.from_profile(profile)
        self.assertEqual(rules.min_clearance_mm, profile.min_clearance_mm)
        self.assertAlmostEqual(
            rules.clearance_gate_mm,
            profile.min_clearance_mm - profile.drc_tolerance_mm,
        )
        self.assertEqual(rules.min_pth_to_copper_mm, profile.min_pth_to_copper_mm)

    def test_the_target_sits_above_the_gate(self):
        """The whole diagnosis of the router we ship: it aims at the floor and
        lands under it."""
        rules = DesignRules.jlcpcb()
        self.assertGreater(rules.target_clearance_mm, rules.min_clearance_mm)
        self.assertGreater(rules.min_clearance_mm, rules.clearance_gate_mm)

    def test_three_hole_clearance_numbers_not_one(self):
        rules = DesignRules.jlcpcb()
        via = Drill("v", Point(0, 0), 0.3, 0.3, plated=True)
        pth = Drill("h", Point(0, 0), 0.9, 0.9, plated=True, pad_id="pad")
        npth = Drill("m", Point(0, 0), 2.0, 2.0, plated=False)
        self.assertEqual(rules.hole_clearance(via), 0.20)
        self.assertEqual(rules.hole_clearance(pth), 0.28)
        self.assertEqual(rules.hole_clearance(npth), 0.20)


class Problems(unittest.TestCase):
    def test_pads_and_nets_are_sorted(self):
        problem = routerfix.two_pad_board()
        self.assertEqual([p.id for p in problem.pads], ["p1", "p2"])

    def test_one_pad_nets_are_not_routable(self):
        problem = routerfix.two_pad_board(
            extra_nets=(
                Net("N2", "LONE", "signal", ("p3",), 0.2),
            ),
            extra_pads=(routerfix.pad("p3", "N2", 5, 5),),
        )
        self.assertEqual(len(problem.nets), 2)
        self.assertEqual([n.id for n in problem.routable_nets], ["N1"])


class Protocol(unittest.TestCase):
    def test_the_baseline_satisfies_the_protocol(self):
        from routerlib.baseline import PatternRouter

        self.assertIsInstance(PatternRouter(), Router)


if __name__ == "__main__":
    unittest.main()
