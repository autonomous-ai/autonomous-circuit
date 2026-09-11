"""The craft score: a ruler for what a reviewer sees first, advisory only."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import craft  # noqa: E402


def _wire(x, y, layer="top"):
    return {"route_type": "wire", "x": x, "y": y, "layer": layer, "width": 0.127}


class CraftScoreTest(unittest.TestCase):

    def test_counts_vias_copper_jogs_offgrid_and_detours(self):
        elements = [
            {"type": "pcb_via", "pcb_via_id": "v1", "x": 5, "y": 0},
            # a straight 10mm run on the grid
            {"type": "pcb_trace", "pcb_trace_id": "straight", "route": [_wire(0, 0), _wire(10, 0)]},
            # a 10mm hop routed as a 20mm detour with a 0.1mm jog and one 30° stub
            {"type": "pcb_trace", "pcb_trace_id": "wandering", "route": [
                _wire(0, 10), _wire(0, 15), _wire(10, 15), _wire(10, 10.1), _wire(10, 10.0),
                _wire(10.866, 10.5),  # 30° — off the 45° grid, 1mm long so it is not a jog
            ]},
            # a via changes nothing planar
            {"type": "pcb_trace", "pcb_trace_id": "layered", "route": [
                _wire(0, 20), _wire(5, 20), {"route_type": "via", "x": 5, "y": 20}, _wire(5, 20, "bottom"), _wire(8, 20, "bottom"),
            ]},
        ]
        c = craft.score(elements)
        self.assertEqual(c["vias"], 1)
        self.assertAlmostEqual(c["routedCopperMm"], 10 + 20.1 + 1.0 + 8, places=0)
        self.assertEqual(c["jogs"], 1, "the 0.1mm step")
        self.assertEqual(c["offGridSegments"], 1, "the 30° stub")
        self.assertEqual(c["worstDetours"][0]["trace"], "wandering")
        self.assertGreater(c["worstDetours"][0]["ratio"], 1.9)
        straight = next(w for w in c["worstDetours"] if w["trace"] == "straight")
        self.assertEqual(straight["ratio"], 1.0)

    def test_short_hops_do_not_count_as_detours(self):
        # a 0.6mm pad-to-cap hop routed at 1.4mm is a 2.3x ratio and means nothing
        elements = [{"type": "pcb_trace", "pcb_trace_id": "hop", "route": [_wire(0, 0), _wire(0, 0.7), _wire(0.6, 0.7), _wire(0.6, 0)]}]
        self.assertEqual(craft.score(elements)["worstDetours"], [])

    def test_summary_is_one_info_finding(self):
        f = craft.summary_finding(craft.score([{"type": "pcb_trace", "pcb_trace_id": "t", "route": [_wire(0, 0), _wire(3, 0)]}]))
        self.assertEqual((f["kind"], f["severity"]), ("craft_summary", "info"))
        self.assertIn("0 vias", f["detail"])


if __name__ == "__main__":
    unittest.main()
