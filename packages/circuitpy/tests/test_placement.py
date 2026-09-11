"""The placement score: a ruler for routability, before any copper exists."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import placement  # noqa: E402


def _board(width=40, height=40):
    return {"type": "pcb_board", "pcb_board_id": "b", "center": {"x": 0, "y": 0}, "width": width, "height": height}


class _Fixture:
    """A tiny unrouted IR: chips, caps, a crystal, a connector, named nets."""

    def __init__(self):
        self.els: list[dict] = [_board()]
        self.n = 0

    def net(self, name, **flags):
        key = f"net_{name}"
        self.els.append({"type": "source_net", "source_net_id": key, "name": name,
                         "subcircuit_connectivity_map_key": key, **flags})
        return key

    def part(self, refdes, ftype, cx, cy, pins, w=2.0, h=2.0):
        """``pins`` = [(dx, dy, net_key), …] relative to the centre."""
        sc = f"sc_{refdes}"
        pc = f"pc_{refdes}"
        self.els.append({"type": "source_component", "source_component_id": sc, "name": refdes, "ftype": ftype})
        self.els.append({"type": "pcb_component", "pcb_component_id": pc, "source_component_id": sc,
                         "center": {"x": cx, "y": cy}, "width": w, "height": h, "layer": "top"})
        for dx, dy, net in pins:
            self.n += 1
            sp = f"sp_{self.n}"
            self.els.append({"type": "source_port", "source_port_id": sp, "source_component_id": sc,
                             "subcircuit_connectivity_map_key": net})
            self.els.append({"type": "pcb_port", "pcb_port_id": f"pp_{self.n}", "pcb_component_id": pc,
                             "source_port_id": sp, "x": cx + dx, "y": cy + dy})


class PlacementScoreTest(unittest.TestCase):

    def _fixture(self, cap_dx=1.5, crystal_dy=-4.0):
        f = _Fixture()
        gnd = f.net("GND", is_ground=True)
        v33 = f.net("V3_3", is_power=True)
        xin = f.net("XIN")
        xout = f.net("XOUT")
        sig = f.net("SIG")
        # a chip with four pins
        f.part("U1", "simple_chip", 0, 0, [(-1, 1, v33), (-1, -1, gnd), (1, 1, xin), (1, -1, xout)])
        # its decoupling cap, `cap_dx` from the chip's V3_3 pin
        f.part("C1", "simple_capacitor", -1 - cap_dx, 1, [(-0.4, 0, v33), (0.4, 0, gnd)], w=1, h=0.5)
        # a crystal below the chip
        f.part("Y1", "simple_crystal", 1, crystal_dy, [(-1, 0, xin), (1, 0, xout)], w=3, h=1.5)
        # a connector 2 mm from the top edge (board is 40 high: edge at y=20)
        f.part("J1", "simple_connector", 0, 16, [(0, 0, sig), (2, 0, gnd)], w=6, h=4)
        # a far chip on SIG so its ratsnest crosses the crystal's
        f.part("U2", "simple_chip", 6, -6, [(0, 0, sig), (1, 0, gnd)])
        return f

    def test_the_numbers_on_a_small_board(self):
        s = placement.score(self._fixture().els)
        self.assertEqual(s["pins"], 12)
        self.assertEqual(s["nets"], 5)  # GND, V3_3, XIN, XOUT, SIG all have ≥ 2 pins
        self.assertGreater(s["ratsnestMm"], 0)
        # one decoupling cap, 1.5 mm from the V3_3 pin, under the 3 mm limit
        self.assertEqual(s["decoupling"]["worst"][0]["cap"], "C1")
        self.assertAlmostEqual(s["decoupling"]["worst"][0]["mm"], 1.5 + 0.4, places=2)
        self.assertEqual(s["decoupling"]["overLimit"], 0)
        # the crystal is measured to the chip's XIN/XOUT pins
        self.assertEqual(s["crystals"][0]["crystal"], "Y1")
        self.assertEqual(s["crystals"][0]["chip"], "U1")
        # the connector's body sits 2 mm from the top edge
        self.assertEqual(s["connectorsToEdgeMm"][0]["connector"], "J1")
        self.assertAlmostEqual(s["connectorsToEdgeMm"][0]["mm"], 2.0, places=2)
        self.assertEqual(s["congestion"]["cellMm"], placement.CELL_MM)
        self.assertGreaterEqual(s["congestion"]["worst"], 3)

    def test_a_cap_moved_away_is_counted_over_the_limit(self):
        near = placement.score(self._fixture(cap_dx=1.5).els)
        far = placement.score(self._fixture(cap_dx=8.0).els)
        self.assertEqual(near["decoupling"]["overLimit"], 0)
        self.assertEqual(far["decoupling"]["overLimit"], 1)
        self.assertGreater(far["ratsnestMm"], near["ratsnestMm"])

    def test_crossings_count_edges_of_different_nets_only(self):
        # two nets whose ratsnest lines form an X, and one net crossing itself
        # is impossible (an MST has no cycles) — so exactly one crossing
        f = _Fixture()
        a = f.net("A")
        b = f.net("B")
        f.part("R1", "simple_resistor", -5, -5, [(0, 0, a)])
        f.part("R2", "simple_resistor", 5, 5, [(0, 0, a)])
        f.part("R3", "simple_resistor", -5, 5, [(0, 0, b)])
        f.part("R4", "simple_resistor", 5, -5, [(0, 0, b)])
        self.assertEqual(placement.score(f.els)["crossings"], 1)
        # rotate one net's pins so the lines are parallel: no crossing
        g = _Fixture()
        a = g.net("A")
        b = g.net("B")
        g.part("R1", "simple_resistor", -5, -5, [(0, 0, a)])
        g.part("R2", "simple_resistor", 5, -5, [(0, 0, a)])
        g.part("R3", "simple_resistor", -5, 5, [(0, 0, b)])
        g.part("R4", "simple_resistor", 5, 5, [(0, 0, b)])
        self.assertEqual(placement.score(g.els)["crossings"], 0)

    def test_ratsnest_is_the_minimum_spanning_tree(self):
        f = _Fixture()
        a = f.net("A")
        for i, (x, y) in enumerate([(0, 0), (10, 0), (10, 10), (0, 10)]):
            f.part(f"R{i}", "simple_resistor", x, y, [(0, 0, a)])
        s = placement.score(f.els)
        # a square's MST is three sides, never the diagonal
        self.assertAlmostEqual(s["ratsnestMm"], 30.0, places=1)
        self.assertEqual(len(placement.ratsnest_edges(f.els)), 3)

    def test_a_four_pin_crystal_is_measured_on_its_oscillator_nets_only(self):
        # Y1's two GND pins sit 1 mm from a foreign chip's GND pin; its XIN
        # pin is 6 mm from the MCU. The ruler must say 6, not 1.
        f = _Fixture()
        gnd = f.net("GND", is_ground=True)
        xin = f.net("XIN")
        xout = f.net("XOUT")
        f.part("U1", "simple_chip", 0, 0, [(1, 0, xin), (1, -1, xout), (-1, 0, gnd)])
        f.part("Y1", "simple_crystal", 7, 0, [(0, 0, xin), (1, 0, xout), (0, -1, gnd), (1, -1, gnd)])
        f.part("U9", "simple_chip", 7, -2, [(0, 0, gnd)])
        c = placement.score(f.els)["crystals"][0]
        self.assertEqual(c["chip"], "U1")
        self.assertAlmostEqual(c["mm"], 6.0, places=2)

    def test_a_part_on_the_back_does_not_crowd_the_front(self):
        # the same four pins in one 5 mm cell: on one side that cell holds 4,
        # split across sides the worst cell holds 2 and the sides are named
        f = _Fixture()
        a = f.net("A")
        f.part("U1", "simple_chip", 1, 1, [(0, 0, a), (0.5, 0, a)])
        f.part("U2", "simple_chip", 1, 2, [(0, 0, a), (0.5, 0, a)])
        one = placement.score(f.els)
        self.assertEqual(one["congestion"]["worst"], 4)
        self.assertEqual(one["pinsBySide"], {"top": 4})
        back = next(e for e in f.els if e.get("pcb_component_id") == "pc_U2")
        back["layer"] = "bottom"
        two = placement.score(f.els)
        self.assertEqual(two["congestion"]["worst"], 2)
        self.assertEqual(two["pinsBySide"], {"bottom": 2, "top": 2})
        self.assertEqual(set(two["congestion"]["bySide"]), {"top", "bottom"})
        self.assertIn("2 bottom", placement.summary_finding(two)["detail"])

    def test_a_board_with_no_pins_scores_zero_and_does_not_raise(self):
        s = placement.score([_board()])
        self.assertEqual((s["pins"], s["nets"], s["ratsnestMm"], s["crossings"]), (0, 0, 0.0, 0))
        self.assertEqual(s["congestion"]["worst"], 0)
        self.assertIn("placement_summary", placement.summary_finding(s)["kind"])

    def test_summary_is_one_info_finding_that_names_the_worst(self):
        s = placement.score(self._fixture(cap_dx=8.0).els)
        f = placement.summary_finding(s)
        self.assertEqual((f["kind"], f["severity"]), ("placement_summary", "info"))
        self.assertIn("C1", f["detail"])
        self.assertIn("Y1", f["detail"])
        self.assertIn("crossings", f["detail"])


if __name__ == "__main__":
    unittest.main()
