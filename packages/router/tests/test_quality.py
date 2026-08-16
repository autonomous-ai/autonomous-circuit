"""Quality metrics: the numbers a human EE grades and DRC cannot see.

Three kinds of test here, and the third is the one that matters most.

*Arithmetic* — a synthetic board whose answer is known by hand. A trace 1mm
from its return over a 10mm run encloses 10mm^2; there is no judgment in that.

*Agreement* — the coupled fraction must equal ``routerlib.scoring``'s to the
last digit at the same step, and the reference window and skew budget must equal
``verifylib.netclass``'s. Two packages that report "62% coupled" have to mean
the same thing, and the only way to keep that true is a test that fails when
they drift.

*Not a gate* — nothing in this package may emit a severity, a finding, or a
pass mark. ``fab.ready`` is zero error-severity findings plus verified gerbers,
and a soft metric wired into a hard gate would make the gate arguable.
"""

from __future__ import annotations

import json
import math
import time
import unittest
from pathlib import Path

import routerfix
from routerlib import quality, scoring
from routerlib.model import (
    BOTTOM,
    TOP,
    Board,
    DesignRules,
    Net,
    Plane,
    Point,
    RoutingProblem,
    RoutingSolution,
    Trace,
    Via,
    empty_solution,
)
from routerlib.quality import common, detour, diffpair, loop_area, power, reference, vias

REPO = Path(__file__).resolve().parents[3]
INSTANCES = REPO / "packages" / "router" / "benchmarks" / "instances"
COPPER = REPO / "work" / "tournament" / "copper"


def _ground_net(pads: tuple[str, ...]) -> Net:
    return Net(
        id="GND", name="GND", net_class="ground", pads=pads,
        min_width_mm=0.5, source_net_id="source_net_gnd", priority=10,
    )


def board_with_ground_trace(offset_mm: float) -> tuple[RoutingProblem, RoutingSolution]:
    """A 10mm signal on top with a ground trace ``offset_mm`` beside it.

    The ground *pads* sit at x = +/-9, outside the signal's span, so the
    nearest return everywhere along the signal is the ground trace and the
    expected answer is arithmetic rather than a guess about which piece of
    copper wins.
    """
    problem = routerfix.two_pad_board(
        gap_mm=10.0,
        extra_pads=(
            routerfix.pad("g1", "GND", -9.0, offset_mm, component="U2"),
            routerfix.pad("g2", "GND", 9.0, offset_mm, component="U2"),
        ),
        extra_nets=(_ground_net(("g1", "g2")),),
    )
    solution = RoutingSolution(
        router="test",
        traces=(
            Trace(id="t0", net="N1", layer=TOP,
                  points=(Point(-5.0, 0.0), Point(5.0, 0.0)), width_mm=0.2),
            Trace(id="t1", net="GND", layer=TOP,
                  points=(Point(-9.0, offset_mm), Point(9.0, offset_mm)),
                  width_mm=0.2),
        ),
        complete=True,
    )
    return problem, solution


class LoopArea(unittest.TestCase):
    def test_a_return_one_millimetre_away_encloses_one_square_per_millimetre(self):
        """10mm of trace, 1mm from its return, is 10mm^2 of loop. The 0.2mm
        traces are 0.1mm of copper wide each side, so the *edge* gap is
        0.9mm — the metric measures copper edge to copper edge, which is the
        conservative reading and is what the assertion allows for."""
        problem, solution = board_with_ground_trace(1.0)
        result = loop_area.measure(problem, solution)
        self.assertAlmostEqual(result.mean_return_mm, 0.9, places=6)
        self.assertAlmostEqual(result.total_loop_area_mm2, 9.0, places=4)
        self.assertEqual(result.worst_net, "N1")

    def test_a_further_return_encloses_more(self):
        near = loop_area.measure(*board_with_ground_trace(1.0))
        far = loop_area.measure(*board_with_ground_trace(4.0))
        self.assertGreater(far.total_loop_area_mm2, near.total_loop_area_mm2 * 3)

    def test_a_plane_underneath_is_the_dielectric_thickness(self):
        """Over a pour on the other side the loop height is the board, not a
        lateral gap: 1.6mm of FR-4 and nothing else."""
        outline = (Point(-9, -9), Point(9, -9), Point(9, 9), Point(-9, 9))
        problem = routerfix.two_pad_board(
            gap_mm=10.0,
            extra_pads=(routerfix.pad("g1", "GND", 0.0, 8.0, component="U2"),
                        routerfix.pad("g2", "GND", 0.0, -8.0, component="U2")),
            extra_nets=(_ground_net(("g1", "g2")),),
            planes=(Plane(id="pour", net="GND", layer=BOTTOM, outline=outline),),
        )
        solution = RoutingSolution(
            router="test",
            traces=(Trace(id="t0", net="N1", layer=TOP,
                          points=(Point(-5.0, 0.0), Point(5.0, 0.0)),
                          width_mm=0.2),),
            complete=True,
        )
        result = loop_area.measure(problem, solution)
        self.assertAlmostEqual(result.mean_return_mm, 1.6, places=6)
        self.assertAlmostEqual(result.nets[0].over_return_fraction, 1.0, places=6)

    def test_no_ground_is_not_applicable_rather_than_zero(self):
        problem = routerfix.two_pad_board()
        result = loop_area.measure(problem, routerfix.straight_trace(problem))
        self.assertIsNone(result.total_loop_area_mm2)
        self.assertIsNone(result.mean_return_mm)


class Reference(unittest.TestCase):
    def _plane_board(self, holes=()):
        outline = (Point(-9, -9), Point(9, -9), Point(9, 9), Point(-9, 9))
        problem = routerfix.two_pad_board(
            gap_mm=16.0,
            extra_pads=(routerfix.pad("g1", "GND", 0.0, 8.0, component="U2"),
                        routerfix.pad("g2", "GND", 0.0, -8.0, component="U2")),
            extra_nets=(_ground_net(("g1", "g2")),),
            planes=(Plane(id="pour", net="GND", layer=BOTTOM,
                          outline=outline, holes=holes),),
        )
        solution = RoutingSolution(
            router="test",
            traces=(Trace(id="t0", net="N1", layer=TOP,
                          points=(Point(-8.0, 0.0), Point(8.0, 0.0)),
                          width_mm=0.2),),
            complete=True,
        )
        return problem, solution

    def test_a_solid_plane_references_everything(self):
        result = reference.measure(*self._plane_board())
        self.assertEqual(result.mode, "plane")
        self.assertAlmostEqual(result.referenced_fraction, 1.0, places=6)
        self.assertEqual(result.gap_crossings, 0)

    def test_a_slot_in_the_plane_is_a_crossing(self):
        """A 2mm void straddling the trace's path: the return disappears and
        comes back, which is one gap crossing and about 2mm unreferenced."""
        hole = (Point(-1, -3), Point(1, -3), Point(1, 3), Point(-1, 3))
        result = reference.measure(*self._plane_board(holes=(hole,)))
        self.assertEqual(result.gap_crossings, 1)
        self.assertGreater(result.unreferenced_mm, 1.5)
        self.assertLess(result.unreferenced_mm, 2.6)

    def test_no_plane_says_so(self):
        result = reference.measure(*board_with_ground_trace(1.0))
        self.assertEqual(result.mode, "trace")
        self.assertIsNone(result.plane_split_crossings)

    def test_window_matches_verifylib(self):
        """Both packages ask "is there ground within X on the other side". If
        the two Xs ever differ, one of them is quietly reporting a different
        board."""
        netclass = _verifylib_netclass()
        if netclass is None:
            self.skipTest("verifylib not importable")
        self.assertEqual(common.REFERENCE_MM, netclass.DIFF_PAIR_REFERENCE_MM)


class DiffPair(unittest.TestCase):
    def test_skew_budget_matches_verifylib(self):
        netclass = _verifylib_netclass()
        if netclass is None:
            self.skipTest("verifylib not importable")
        self.assertEqual(diffpair.USB_SKEW_BUDGET_MM, netclass.USB_SKEW_BUDGET_MM)

    def test_coupling_window_is_the_scorers_own(self):
        self.assertIs(diffpair.COUPLING_WINDOW_FACTOR, scoring.COUPLING_WINDOW_FACTOR)

    def test_coupled_fraction_matches_scoring_on_a_real_board(self):
        """Extending a number is only extending it if it still equals the
        original. At the scorer's own step the two must agree exactly."""
        problem, solution = _real_cell("hydrate-coaster", "pathfinder-negotiated")
        if problem is None:
            self.skipTest("tournament copper not on disk")
        theirs = scoring._pair_coupling(problem, solution)  # noqa: SLF001
        ours = diffpair.measure(
            problem, solution, step_mm=scoring.COUPLING_STEP_MM
        ).coupled_fraction
        self.assertIsNotNone(theirs)
        self.assertAlmostEqual(ours, theirs, places=9)

    def test_a_parallel_pair_is_coupled_at_a_constant_gap(self):
        problem, solution = _pair_board(offsets=(0.0, 0.2))
        result = diffpair.measure(problem, solution)
        self.assertEqual(result.pair_count, 1)
        self.assertAlmostEqual(result.coupled_fraction, 1.0, places=6)
        self.assertAlmostEqual(result.worst_gap_cv, 0.0, places=9)
        self.assertEqual(result.pairs[0].via_asymmetry, 0)

    def test_a_diverging_pair_has_a_varying_gap(self):
        """Same coupled fraction, completely different geometry — which is
        exactly why the coupled fraction alone is not enough."""
        problem, solution = _pair_board(offsets=(0.0, 0.2), fan_to=0.55)
        result = diffpair.measure(problem, solution)
        self.assertAlmostEqual(result.coupled_fraction, 1.0, places=6)
        self.assertGreater(result.worst_gap_cv, 0.15)

    def test_no_pairs_is_none_not_perfect(self):
        problem = routerfix.two_pad_board()
        result = diffpair.measure(problem, routerfix.straight_trace(problem))
        self.assertEqual(result.pair_count, 0)
        self.assertIsNone(result.coupled_fraction)


class Power(unittest.TestCase):
    def test_resistance_is_sheet_resistance_times_squares(self):
        """10mm of 0.5mm copper is 20 squares at 0.495 milliohm each."""
        problem, solution = _power_board(chain=False)
        result = power.measure(problem, solution)
        row = next(n for n in result.nets if n.net == "V5")
        expected = common.SHEET_R_OHM_PER_SQ * (10.0 / 0.5) * 1000.0
        self.assertAlmostEqual(row.worst_path_mohm, expected, places=6)

    def test_a_star_has_no_daisy_depth_and_a_chain_does(self):
        star = power.measure(*_power_board(chain=False, loads=3))
        chain = power.measure(*_power_board(chain=True, loads=3))
        self.assertEqual(star.max_daisy_depth, 0)
        self.assertGreaterEqual(chain.max_daisy_depth, 1)
        self.assertGreater(chain.chained_pad_fraction, star.chained_pad_fraction)

    def test_current_turns_ohms_into_volts(self):
        problem, solution = _power_board(chain=False)
        result = power.measure(problem, solution, currents_ma={"V5": 500.0})
        row = next(n for n in result.nets if n.net == "V5")
        self.assertAlmostEqual(
            row.worst_drop_mv, row.worst_path_mohm * 0.5, places=9
        )

    def test_without_current_there_is_no_voltage(self):
        result = power.measure(*_power_board(chain=False))
        self.assertIsNone(result.worst_drop_mv)

    def test_a_via_adds_its_barrel(self):
        expected = common.via_barrel_ohms(0.3, 1.6)
        self.assertGreater(expected, 0.0005)
        self.assertLess(expected, 0.005)


class Detour(unittest.TestCase):
    def test_a_straight_two_pin_net_is_ratio_one(self):
        problem = routerfix.two_pad_board()
        result = detour.measure(problem, routerfix.straight_trace(problem))
        self.assertAlmostEqual(result.detour_ratio, 1.0, places=9)
        self.assertEqual(result.bends, 0)

    def test_a_dogleg_is_longer_than_the_straight_line(self):
        problem = routerfix.two_pad_board()
        solution = RoutingSolution(
            router="test",
            traces=(Trace(id="t0", net="N1", layer=TOP,
                          points=(Point(-5, 0), Point(-5, 4), Point(5, 4),
                                  Point(5, 0)),
                          width_mm=0.2),),
            complete=True,
        )
        result = detour.measure(problem, solution)
        self.assertAlmostEqual(result.detour_ratio, 1.8, places=9)
        self.assertEqual(result.bends, 2)

    def test_an_unconnected_net_is_skipped_not_scored_as_perfect(self):
        """A router that gives up must not score better for it."""
        problem = routerfix.two_pad_board()
        result = detour.measure(problem, empty_solution())
        self.assertEqual(result.scored_nets, 0)
        self.assertEqual(result.skipped_unconnected_nets, 1)
        self.assertIsNone(result.detour_ratio)

    def test_two_nets_crossing_on_opposite_layers_is_one_crossing(self):
        problem = routerfix.two_pad_board(
            extra_pads=(routerfix.pad("q1", "N2", 0.0, -5.0, component="U3"),
                        routerfix.pad("q2", "N2", 0.0, 5.0, component="U3")),
            extra_nets=(Net(id="N2", name="SIG2", net_class="signal",
                            pads=("q1", "q2"), min_width_mm=0.2,
                            source_net_id="source_net_2"),),
        )
        solution = RoutingSolution(
            router="test",
            traces=(
                Trace(id="t0", net="N1", layer=TOP,
                      points=(Point(-5, 0), Point(5, 0)), width_mm=0.2),
                Trace(id="t1", net="N2", layer=BOTTOM,
                      points=(Point(0, -5), Point(0, 5)), width_mm=0.2),
            ),
            complete=True,
        )
        result = detour.measure(problem, solution)
        self.assertEqual(result.crossings, 1)
        self.assertEqual(result.self_crossings, 0)


class Vias(unittest.TestCase):
    def test_a_via_with_copper_on_one_side_only_is_dangling(self):
        problem = routerfix.two_pad_board()
        solution = RoutingSolution(
            router="test",
            traces=(Trace(id="t0", net="N1", layer=TOP,
                          points=(Point(-5, 0), Point(5, 0)), width_mm=0.2),),
            vias=(Via(id="v0", net="N1", center=Point(0.0, 0.0)),),
            complete=True,
        )
        result = vias.measure(problem, solution)
        self.assertEqual(result.count, 1)
        self.assertEqual(result.dangling, 1)

    def test_a_via_between_two_layers_is_not_dangling(self):
        problem = routerfix.two_pad_board()
        solution = RoutingSolution(
            router="test",
            traces=(
                Trace(id="t0", net="N1", layer=TOP,
                      points=(Point(-5, 0), Point(0, 0)), width_mm=0.2),
                Trace(id="t1", net="N1", layer=BOTTOM,
                      points=(Point(0, 0), Point(5, 0)), width_mm=0.2),
            ),
            vias=(Via(id="v0", net="N1", center=Point(0.0, 0.0)),),
            complete=True,
        )
        self.assertEqual(vias.measure(problem, solution).dangling, 0)

    def test_two_layers_have_no_blind_stubs(self):
        problem = routerfix.two_pad_board()
        result = vias.measure(problem, routerfix.straight_trace(problem))
        self.assertIsNone(result.blind_stub_count)

    def test_high_speed_vias_are_counted_separately(self):
        problem, solution = _pair_board(offsets=(0.0, 0.2), with_via=True)
        result = vias.measure(problem, solution)
        self.assertEqual(result.count, 1)
        self.assertEqual(result.on_high_speed, 1)


class ReportContract(unittest.TestCase):
    def test_deterministic(self):
        problem, solution = board_with_ground_trace(1.0)
        a = quality.measure(problem, solution).as_dict()
        b = quality.measure(problem, solution).as_dict()
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_the_ruler_moves_when_the_step_moves(self):
        problem, solution = board_with_ground_trace(1.0)
        coarse = quality.measure(problem, solution, step_mm=1.0)
        fine = quality.measure(problem, solution, step_mm=0.1)
        self.assertNotEqual(coarse.ruler.hash, fine.ruler.hash)

    def test_an_empty_solution_scores_rather_than_raises(self):
        problem = routerfix.two_pad_board()
        report = quality.measure(problem, empty_solution())
        self.assertEqual(report.detour.scored_nets, 0)
        self.assertEqual(report.vias.count, 0)

    def test_nothing_here_is_a_gate(self):
        """No severity, no finding, no pass mark. ``fab.ready`` is defined by
        ``routerlib.drc`` and the pipeline, and this package must never be able
        to change that answer."""
        problem, solution = board_with_ground_trace(1.0)
        blob = json.dumps(quality.measure(problem, solution).as_dict()).lower()
        for banned in ("severity", "\"error\"", "finding", "fabready",
                       "blocking", "\"pass\"", "\"fail\""):
            self.assertNotIn(banned, blob, f"quality must not emit {banned}")

    def test_coverage_gaps_travel_with_the_score(self):
        problem, solution = board_with_ground_trace(1.0)
        report = quality.measure(problem, solution)
        self.assertTrue(report.ruler.coverage_gaps)
        self.assertIn("coverageGaps", report.as_dict()["measuredAgainst"])


class Speed(unittest.TestCase):
    def test_sixteen_instances_score_in_seconds(self):
        """The budget in the brief: all sixteen benchmark instances, in
        seconds. Measured on the heaviest copper on disk, not on empty
        solutions, because an empty board is not a measurement."""
        cells = []
        for path in sorted(INSTANCES.glob("*.json")):
            copper = COPPER / "pathfinder-negotiated" / path.name
            if copper.is_file():
                cells.append((path, copper))
        if len(cells) < 16:
            self.skipTest("tournament copper not on disk")
        from routerlib.adapters import solution_from_elements
        from routerlib.bench import load_instance

        started = time.perf_counter()
        for path, copper in cells:
            problem = load_instance(path)
            solution = solution_from_elements(
                problem, json.loads(copper.read_text(encoding="utf-8")),
                router="pathfinder-negotiated",
            )
            quality.measure(problem, solution)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 30.0, f"scored 16 instances in {elapsed:.1f}s")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _verifylib_netclass():
    import sys

    src = str(REPO / "packages" / "verify" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from verifylib import netclass
    except Exception:  # noqa: BLE001
        return None
    return netclass


def _real_cell(instance: str, router: str):
    path = INSTANCES / f"{instance}.json"
    copper = COPPER / router / f"{instance}.json"
    if not (path.is_file() and copper.is_file()):
        return (None, None)
    from routerlib.adapters import solution_from_elements
    from routerlib.bench import load_instance

    problem = load_instance(path)
    solution = solution_from_elements(
        problem, json.loads(copper.read_text(encoding="utf-8")), router=router
    )
    return problem, solution


def _pair_board(*, offsets, fan_to: float | None = None, with_via: bool = False):
    """Two 10mm legs called ``USB_DP``/``USB_DM``, optionally fanning apart."""
    a0, b0 = offsets
    pads = (
        routerfix.pad("p1", "DP", -5.0, a0),
        routerfix.pad("p2", "DP", 5.0, a0),
        routerfix.pad("n1", "DM", -5.0, b0, component="U2"),
        routerfix.pad("n2", "DM", 5.0, b0 if fan_to is None else fan_to,
                      component="U2"),
    )
    nets = (
        Net(id="DP", name="USB_DP", net_class="diff_pair", pads=("p1", "p2"),
            min_width_mm=0.2, source_net_id="source_net_dp", diff_partner="DM"),
        Net(id="DM", name="USB_DM", net_class="diff_pair", pads=("n1", "n2"),
            min_width_mm=0.2, source_net_id="source_net_dm", diff_partner="DP"),
    )
    problem = RoutingProblem(
        id="pair", board=Board(width_mm=20.0, height_mm=20.0),
        rules=routerfix.RULES, pads=pads, nets=nets,
    )
    solution = RoutingSolution(
        router="test",
        traces=(
            Trace(id="tp", net="DP", layer=TOP,
                  points=(Point(-5.0, a0), Point(5.0, a0)), width_mm=0.2),
            Trace(id="tn", net="DM", layer=TOP,
                  points=(Point(-5.0, b0),
                          Point(5.0, b0 if fan_to is None else fan_to)),
                  width_mm=0.2),
        ),
        vias=(Via(id="v0", net="DP", center=Point(0.0, a0)),) if with_via else (),
        complete=True,
    )
    return problem, solution


def _power_board(*, chain: bool, loads: int = 1):
    """A 5V rail from a source pad to ``loads`` load pads, star or chain.

    Star: every load hangs off the source on its own 10mm run.
    Chain: the loads are strung together, so load *k* sees *k* runs of copper.
    """
    pads = [routerfix.pad("src", "V5", 0.0, 0.0, w=0.6, h=0.6, component="U1")]
    traces = []
    for i in range(loads):
        x = 10.0 * (i + 1)
        pads.append(routerfix.pad(f"ld{i}", "V5", x, 0.0, w=0.6, h=0.6,
                                  component=f"L{i}"))
        start = Point(x - 10.0, 0.0) if chain else Point(0.0, 0.0)
        traces.append(
            Trace(id=f"t{i}", net="V5", layer=TOP,
                  points=(start, Point(x, 0.0)) if chain
                  else (Point(0.0, 0.0), Point(x, 0.0)),
                  width_mm=0.5)
        )
    net = Net(id="V5", name="V5", net_class="power",
              pads=tuple(p.id for p in pads), min_width_mm=0.5,
              source_net_id="source_net_v5", priority=20)
    problem = RoutingProblem(
        id="power", board=Board(width_mm=60.0, height_mm=20.0),
        rules=routerfix.RULES, pads=tuple(pads), nets=(net,),
    )
    return problem, RoutingSolution(router="test", traces=tuple(traces),
                                    complete=True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
