"""Both directions of the circuit.json bridge, exercised on a real built board
rather than a hand-written stub. A translation bug here would show up as a
routing result and nobody would know."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import routerfix
from routerlib.adapters import (
    ROUTE_ELEMENT_TYPES,
    apply_solution,
    circuit_json_for_scoring,
    classify_nets,
    problem_from_circuit_json,
    solution_to_elements,
)
from routerlib.model import TOP, DesignRules, Point, RoutingSolution, Trace, Via

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "examples" / "hydrate-coaster" / "boards" / "main.circuit.json"


def _example():
    if not EXAMPLE.is_file():  # pragma: no cover
        raise unittest.SkipTest(f"no built example at {EXAMPLE}")
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


class Reading(unittest.TestCase):
    def setUp(self):
        self.elements = _example()
        self.problem = problem_from_circuit_json(
            self.elements, problem_id="hydrate-coaster"
        )

    def test_board_and_obstacles_survive(self):
        board = self.problem.board
        self.assertEqual(board.layer_count, 2)
        self.assertGreater(len(board.outline), 100)
        self.assertGreater(len(self.problem.pads), 100)
        self.assertTrue(self.problem.drills)
        self.assertTrue(self.problem.keepouts)

    def test_polygon_pads_are_kept_not_dropped(self):
        """A USB-C shell tab is a polygon pad with no x/y. Dropping it hands
        the router a piece of copper as free space."""
        raw = sum(
            1 for e in self.elements
            if e.get("type") == "pcb_smtpad" and e.get("shape") == "polygon"
        )
        kept = sum(1 for p in self.problem.pads if p.shape == "polygon")
        self.assertGreater(raw, 0)
        self.assertEqual(kept, raw)

    def test_rotation_is_read(self):
        turned = [p for p in self.problem.pads if p.rotation_deg]
        self.assertTrue(turned, "no rotated pads read from a board that has eight")

    def test_routes_are_stripped_by_default(self):
        self.assertEqual(self.problem.existing_traces, ())
        self.assertEqual(self.problem.existing_vias, ())
        kept = problem_from_circuit_json(
            self.elements, problem_id="x", strip_routes=False
        )
        self.assertTrue(kept.existing_traces)

    def test_planes_are_stripped_by_default(self):
        """The pours on our built boards were generated after routing, so their
        outlines are carved around the old traces. Inheriting one leaks the
        previous solution into the problem."""
        self.assertEqual(self.problem.planes, ())
        with_pours = problem_from_circuit_json(
            self.elements, problem_id="x", strip_planes=False
        )
        self.assertTrue(with_pours.planes)

    def test_net_classes_come_from_the_compiler_not_a_guess(self):
        by_name = {n.name: n for n in self.problem.nets}
        self.assertEqual(by_name["GND"].net_class, "ground")
        self.assertEqual(by_name["V5"].net_class, "power")
        self.assertEqual(by_name["V3_3"].net_class, "power")

    def test_usb_pairs_are_found_and_are_symmetric(self):
        by_name = {n.name: n for n in self.problem.nets}
        dp, dm = by_name["USB_DP"], by_name["USB_DM"]
        self.assertEqual(dp.net_class, "diff_pair")
        self.assertEqual(dp.diff_partner, dm.id)
        self.assertEqual(dm.diff_partner, dp.id)

    def test_power_nets_carry_the_power_width(self):
        rules = self.problem.rules
        gnd = next(n for n in self.problem.nets if n.name == "GND")
        sig = next(n for n in self.problem.nets if n.net_class == "signal")
        self.assertEqual(gnd.min_width_mm, rules.power_trace_mm)
        self.assertEqual(sig.min_width_mm, rules.signal_trace_mm)


class NetClassification(unittest.TestCase):
    def test_a_swap_only_pairs_when_the_partner_exists(self):
        names = {"a": "USB_DP", "b": "USB_DM", "c": "LEDPWR"}
        classes, partners = classify_nets(names, {})
        self.assertEqual(partners, {"a": "b", "b": "a"})
        self.assertEqual(classes["c"], "signal")

    def test_a_suffixed_pair_still_pairs(self):
        names = {"a": "USB_DP_CONN", "b": "USB_DM_CONN"}
        _, partners = classify_nets(names, {})
        self.assertEqual(partners, {"a": "b", "b": "a"})

    def test_a_rail_is_never_half_a_pair(self):
        names = {"a": "V5", "b": "GND"}
        classes, partners = classify_nets(
            names, {"a": {"is_power": True}, "b": {"is_ground": True}}
        )
        self.assertEqual(partners, {})
        self.assertEqual(classes, {"a": "power", "b": "ground"})


class WidthSurvivesTheRoundTrip(unittest.TestCase):
    """A route that changes width mid-way must not come back all one width.

    This is not a nicety. ``ab_incumbent`` re-emits the shipped autorouter's own
    copper through this reader to score it, so a reader that widens a trace
    charges the incumbent for clearance violations that are not on the board.
    It did: hydrate-coaster's copper scored 0 harness errors while the board was
    uniformly 0.15mm and 130 — 42 of them shorts — the day the boards gained
    per-net rail widths, with the copper byte-identical across both.
    """

    def _one_trace(self, widths):
        elements = [
            {
                "type": "pcb_board",
                "pcb_board_id": "b",
                "center": {"x": 5.0, "y": 0.0},
                "width": 20.0,
                "height": 20.0,
                "num_layers": 2,
            },
            {
                "type": "pcb_trace",
                "pcb_trace_id": "t1",
                "route": [
                    {"route_type": "wire", "layer": "top", "x": float(i),
                     "y": 0.0, "width": w}
                    for i, w in enumerate(widths)
                ],
            },
        ]
        problem = problem_from_circuit_json(
            elements, problem_id="w", strip_routes=False
        )
        return problem.existing_traces

    def test_a_constant_width_route_is_one_trace(self):
        traces = self._one_trace([0.15, 0.15, 0.15])
        self.assertEqual(len(traces), 1)
        self.assertAlmostEqual(traces[0].width_mm, 0.15)

    def test_a_widening_route_is_split_not_flattened(self):
        traces = self._one_trace([0.15, 0.15, 0.5, 0.5])
        self.assertEqual([round(t.width_mm, 3) for t in traces], [0.15, 0.5])
        # The polyline stays continuous: the second run starts where the first
        # ended, so no copper is dropped at the seam.
        self.assertEqual(traces[0].points[-1], traces[1].points[0])

    def test_no_segment_is_reported_narrower_than_it_is(self):
        """The joining segment takes the wider of the two, never the narrower."""
        for widths in ([0.15, 0.5], [0.5, 0.15], [0.2, 0.4, 0.2]):
            traces = self._one_trace(widths)
            self.assertTrue(traces, widths)
            for trace in traces:
                self.assertLessEqual(trace.width_mm, max(widths))
            self.assertAlmostEqual(
                max(t.width_mm for t in traces), max(widths), places=6
            )

    def test_the_real_board_round_trips_at_its_own_widths(self):
        elements = _example()
        problem = problem_from_circuit_json(
            elements, problem_id="h", strip_routes=False, strip_planes=True
        )
        on_disk = {
            round(float(p.get("width") or 0), 3)
            for element in elements
            if element.get("type") == "pcb_trace"
            for p in element.get("route") or []
            if p.get("route_type") != "via" and p.get("width")
        }
        read_back = {round(t.width_mm, 3) for t in problem.existing_traces}
        self.assertTrue(on_disk, "the fixture board has no trace widths")
        self.assertTrue(
            read_back <= on_disk,
            f"reader invented widths the board does not have: {read_back - on_disk}",
        )


class Writing(unittest.TestCase):
    def setUp(self):
        self.elements = _example()
        self.problem = problem_from_circuit_json(self.elements, problem_id="h")
        net = self.problem.routable_nets[0]
        pads = self.problem.pads_of(net.id)
        self.solution = RoutingSolution(
            router="test",
            traces=(
                Trace("t0", net.id, TOP, (pads[0].center, pads[1].center), 0.2),
            ),
            vias=(Via("v0", net.id, Point(0.0, 0.0)),),
        )

    def test_elements_have_the_shape_the_pipeline_reads(self):
        out = solution_to_elements(self.problem, self.solution)
        trace = next(e for e in out if e["type"] == "pcb_trace")
        via = next(e for e in out if e["type"] == "pcb_via")
        self.assertTrue(trace["pcb_trace_id"])
        self.assertEqual(trace["route"][0]["route_type"], "wire")
        self.assertIn("layer", trace["route"][0])
        self.assertIn("width", trace["route"][0])
        self.assertTrue(trace["connectsTo"])
        self.assertEqual(via["hole_diameter"], 0.3)
        self.assertEqual(via["outer_diameter"], 0.6)
        self.assertIn("subcircuit_connectivity_map_key", via)

    def test_ids_are_derived_not_minted(self):
        first = solution_to_elements(self.problem, self.solution)
        second = solution_to_elements(self.problem, self.solution)
        self.assertEqual(json.dumps(first), json.dumps(second))

    def test_apply_replaces_only_the_copper(self):
        merged = apply_solution(self.elements, self.problem, self.solution)
        before = [e for e in self.elements if e.get("type") not in ROUTE_ELEMENT_TYPES]
        after = [e for e in merged if e.get("type") not in ROUTE_ELEMENT_TYPES]
        self.assertEqual(before, after)
        self.assertEqual(
            sum(1 for e in merged if e.get("type") == "pcb_trace"), 1
        )
        self.assertEqual(sum(1 for e in merged if e.get("type") == "pcb_via"), 1)

    def test_scoring_json_carries_every_obstacle(self):
        """The scorer must not be able to pass a board by forgetting a hole."""
        out = circuit_json_for_scoring(self.problem, self.solution)
        pads = sum(
            1 for e in out if e["type"] in ("pcb_smtpad", "pcb_plated_hole")
        )
        holes = sum(1 for e in out if e["type"] == "pcb_hole")
        self.assertEqual(pads, len(self.problem.pads))
        self.assertEqual(
            holes, sum(1 for d in self.problem.drills if d.pad_id is None)
        )
        self.assertEqual(
            sum(1 for e in out if e["type"] == "pcb_board"), 1
        )


if __name__ == "__main__":
    unittest.main()
