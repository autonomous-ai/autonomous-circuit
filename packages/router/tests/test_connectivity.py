"""Completeness is recomputed from copper. These tests are the proof that a
router cannot talk its way to a good score."""

from __future__ import annotations

import unittest

import routerfix
from routerlib import connectivity
from routerlib.model import (
    BOTTOM,
    TOP,
    Net,
    Plane,
    Point,
    RoutingSolution,
    Trace,
    Via,
    empty_solution,
)


class Basics(unittest.TestCase):
    def test_no_copper_no_connection(self):
        problem = routerfix.two_pad_board()
        result = connectivity.analyse(problem, empty_solution())
        self.assertEqual(result.completeness, 0.0)
        self.assertEqual(result.unconnected_nets, ("N1",))

    def test_a_trace_that_lands_on_both_pads_connects(self):
        problem = routerfix.two_pad_board()
        result = connectivity.analyse(problem, routerfix.straight_trace(problem))
        self.assertEqual(result.completeness, 1.0)
        self.assertEqual(result.fragments["N1"], 1)

    def test_a_trace_that_stops_short_does_not(self):
        problem = routerfix.two_pad_board(gap_mm=10.0)
        solution = RoutingSolution(
            router="t",
            traces=(Trace("t0", "N1", TOP, (Point(-5, 0), Point(1, 0)), 0.2),),
            complete=True,
        )
        result = connectivity.analyse(problem, solution)
        self.assertEqual(result.completeness, 0.0)

    def test_copper_on_two_layers_does_not_connect_without_a_via(self):
        problem = routerfix.two_pad_board()
        solution = RoutingSolution(
            router="t",
            traces=(
                Trace("t0", "N1", TOP, (Point(-5, 0), Point(0, 0)), 0.2),
                Trace("t1", "N1", BOTTOM, (Point(0, 0), Point(5, 0)), 0.2),
            ),
        )
        self.assertEqual(connectivity.analyse(problem, solution).completeness, 0.0)

    def test_vias_carry_a_net_down_and_back_up(self):
        """Both pads are top-only SMD, so a bottom-layer detour only connects
        them if a via lands at each end of it."""
        problem = routerfix.two_pad_board()
        solution = RoutingSolution(
            router="t",
            traces=(
                Trace("t0", "N1", TOP, (Point(-5, 0), Point(-3, 0)), 0.2),
                Trace("t1", "N1", BOTTOM, (Point(-3, 0), Point(3, 0)), 0.2),
                Trace("t2", "N1", TOP, (Point(3, 0), Point(5, 0)), 0.2),
            ),
            vias=(Via("v0", "N1", Point(-3, 0)), Via("v1", "N1", Point(3, 0))),
        )
        self.assertEqual(connectivity.analyse(problem, solution).completeness, 1.0)

    def test_one_via_short_of_the_far_pad_is_still_open(self):
        problem = routerfix.two_pad_board()
        solution = RoutingSolution(
            router="t",
            traces=(
                Trace("t0", "N1", TOP, (Point(-5, 0), Point(-3, 0)), 0.2),
                Trace("t1", "N1", BOTTOM, (Point(-3, 0), Point(5, 0)), 0.2),
            ),
            vias=(Via("v0", "N1", Point(-3, 0)),),
        )
        self.assertEqual(connectivity.analyse(problem, solution).completeness, 0.0)


class Planes(unittest.TestCase):
    def plane_board(self):
        outline = (
            Point(-9, -9), Point(9, -9), Point(9, 9), Point(-9, 9),
        )
        problem = routerfix.two_pad_board(
            planes=(Plane("pl", "N1", BOTTOM, outline),),
        )
        return problem

    def test_two_vias_into_a_plane_connect_two_pads_with_no_trace_between(self):
        """The whole reason a plane exists. Vias sit beside each pad, a short
        top stub reaches them, and the plane does the rest."""
        problem = self.plane_board()
        solution = RoutingSolution(
            router="t",
            traces=(
                Trace("s0", "N1", TOP, (Point(-5, 0), Point(-4, 0)), 0.2),
                Trace("s1", "N1", TOP, (Point(5, 0), Point(4, 0)), 0.2),
            ),
            vias=(Via("v0", "N1", Point(-4, 0)), Via("v1", "N1", Point(4, 0))),
        )
        self.assertEqual(connectivity.analyse(problem, solution).completeness, 1.0)

    def test_a_plane_does_not_connect_another_net(self):
        problem = self.plane_board()
        solution = RoutingSolution(
            router="t",
            vias=(Via("v0", "N2", Point(-4, 0)), Via("v1", "N2", Point(4, 0))),
        )
        self.assertEqual(connectivity.analyse(problem, solution).completeness, 0.0)


class Orphans(unittest.TestCase):
    def test_copper_touching_nothing_is_reported(self):
        problem = routerfix.two_pad_board()
        solution = RoutingSolution(
            router="t",
            traces=(Trace("t0", "N1", TOP, (Point(0, 8), Point(3, 8)), 0.2),),
        )
        result = connectivity.analyse(problem, solution)
        self.assertEqual(result.orphan_copper, ("t0",))


if __name__ == "__main__":
    unittest.main()
