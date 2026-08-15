"""Legality. The synthetic cases state exactly what should be found; the
instance cases assert the thing that matters most about the whole benchmark —
**the empty solution on every shipped instance is DRC-clean**. If it is not,
the extractor is inventing defects and every score is measured against noise.
"""

from __future__ import annotations

import unittest

import routerfix
from routerlib import drc
from routerlib.bench import instance_paths, load_instance
from routerlib.model import (
    BOTTOM,
    TOP,
    DesignRules,
    Drill,
    Keepout,
    Net,
    Point,
    RoutingSolution,
    Trace,
    Via,
    empty_solution,
)


class Shorts(unittest.TestCase):
    def board(self):
        return routerfix.two_pad_board(
            extra_pads=(routerfix.pad("p3", "N2", 0.0, 0.0, w=1.0, h=1.0),),
            extra_nets=(Net("N2", "OTHER", "signal", ("p3", "p4"), 0.2),),
        )

    def test_a_trace_across_another_net_is_a_short(self):
        problem = self.board()
        solution = routerfix.straight_trace(problem)
        found = drc.copper_clearance_findings(problem, solution)
        self.assertTrue(any(v.kind == "short" for v in found), found)

    def test_a_trace_that_misses_is_clean(self):
        problem = self.board()
        solution = RoutingSolution(
            router="t",
            traces=(Trace("t0", "N1", TOP, (Point(-5, 3), Point(5, 3)), 0.2),),
        )
        self.assertEqual(drc.copper_clearance_findings(problem, solution), [])

    def test_clearance_band_is_the_pipelines(self):
        """0.09mm is the gate (0.10 floor minus 0.01 tolerance), so 0.085 is an
        error and 0.095 is a warning. Both are under the fab floor; only one is
        blocked, exactly as kicad_normalize configures KiCad."""
        rules = DesignRules.jlcpcb()
        for offset, severity in ((0.085, "error"), (0.095, "warning")):
            gap_y = 0.5 + 0.1 + offset  # pad half-height + trace half-width + gap
            problem = routerfix.two_pad_board(
                rules=rules,
                extra_pads=(routerfix.pad("p3", "N2", 0.0, gap_y, w=1.0, h=1.0),),
                extra_nets=(Net("N2", "OTHER", "signal", ("p3", "p4"), 0.2),),
            )
            solution = RoutingSolution(
                router="t",
                traces=(Trace("t0", "N1", TOP, (Point(-5, 0), Point(5, 0)), 0.2),),
            )
            found = [
                v for v in drc.copper_clearance_findings(problem, solution)
                if v.kind == "clearance"
            ]
            self.assertEqual(len(found), 1, f"{offset}: {found}")
            self.assertEqual(found[0].severity, severity, found[0].detail)


class Vias(unittest.TestCase):
    def test_via_inside_an_smd_pad_is_an_error(self):
        problem = routerfix.two_pad_board()
        solution = RoutingSolution(
            router="t",
            vias=(Via("v0", "N1", Point(-5.0, 0.0)),),
        )
        found = drc.via_in_pad_findings(problem, solution)
        self.assertEqual([v.kind for v in found], ["via_in_pad"])

    def test_via_beside_the_pad_is_fine(self):
        problem = routerfix.two_pad_board()
        solution = RoutingSolution(router="t", vias=(Via("v0", "N1", Point(-3.0, 0)),))
        self.assertEqual(drc.via_in_pad_findings(problem, solution), [])


class Keepouts(unittest.TestCase):
    def test_copper_in_a_keepout(self):
        problem = routerfix.two_pad_board(
            keepouts=(Keepout("k1", Point(0, 0), 2.0, 2.0),)
        )
        solution = routerfix.straight_trace(problem)
        found = drc.keepout_findings(problem, solution)
        self.assertEqual([v.kind for v in found], ["keepout"])


class Edges(unittest.TestCase):
    def test_copper_off_the_board(self):
        problem = routerfix.two_pad_board()
        solution = RoutingSolution(
            router="t",
            traces=(Trace("t0", "N1", TOP, (Point(-30, 0), Point(-25, 0)), 0.2),),
        )
        found = drc.edge_findings(problem, solution)
        self.assertTrue(found)
        self.assertIn(found[0].kind, ("off_board", "trace_edge"))

    def test_copper_hard_against_the_edge(self):
        problem = routerfix.two_pad_board()
        solution = RoutingSolution(
            router="t",
            traces=(Trace("t0", "N1", TOP, (Point(-9.95, -5), Point(-9.95, 5)), 0.2),),
        )
        found = drc.edge_findings(problem, solution)
        self.assertEqual([v.kind for v in found], ["trace_edge"])


class Holes(unittest.TestCase):
    def test_a_track_too_near_a_mounting_hole_comes_from_the_pipeline(self):
        """This is the defect that blocked all three example boards. It must be
        found by circuitpy.checks, not by a second opinion of our own."""
        rules = DesignRules.jlcpcb()
        problem = routerfix.two_pad_board(
            rules=rules,
            drills=(Drill("mh1", Point(0.0, 0.4), 2.0, 2.0, plated=False),),
        )
        solution = routerfix.straight_trace(problem)
        route, _ = drc.pipeline_findings(problem, solution)
        hole = [v for v in route if v.kind == "dfm_hole_clearance"]
        self.assertTrue(hole, route)
        self.assertEqual(hole[0].source, "circuitpy.checks")
        self.assertEqual(hole[0].severity, "error")


class Coverage(unittest.TestCase):
    def test_gaps_are_reported_with_every_result(self):
        problem = routerfix.two_pad_board()
        result = drc.check(problem, empty_solution(), use_pipeline=False)
        self.assertTrue(result.coverage_gaps)
        self.assertTrue(any("islanding" in gap for gap in result.coverage_gaps))


class Instances(unittest.TestCase):
    def test_every_shipped_instance_starts_clean(self):
        """The benchmark's own honesty check. An instance whose *empty*
        solution already violates a rule would charge a router for a defect it
        did not cause."""
        paths = instance_paths()
        self.assertGreaterEqual(len(paths), 3, "no instances committed")
        for path in paths:
            with self.subTest(instance=path.stem):
                problem = load_instance(path)
                result = drc.check(problem, empty_solution())
                self.assertEqual(
                    result.error_counts(), {},
                    f"{path.stem} baseline is not clean: "
                    + "; ".join(v.detail for v in result.errors[:3]),
                )

    def test_recorded_baselines_still_match(self):
        """The committed instance file records the baseline it was extracted
        with. If the check set changes, this fails — which is the point: a
        score is only comparable to another taken with the same ruler."""
        import json

        for path in instance_paths():
            with self.subTest(instance=path.stem):
                data = json.loads(path.read_text(encoding="utf-8"))
                recorded = data.get("baseline")
                if recorded is None:
                    self.skipTest("instance predates baseline recording")
                problem = load_instance(path)
                result = drc.check(problem, empty_solution())
                self.assertEqual(recorded["errors"], len(result.errors))
                self.assertEqual(recorded["byKind"], result.by_kind())


if __name__ == "__main__":
    unittest.main()
