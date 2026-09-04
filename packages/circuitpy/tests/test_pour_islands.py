"""Dropping dead pour copper: does it take the islands, and only the islands?

The whole risk of this pass is in one direction. Removing a region that
something on the board is actually joined to turns a working plane into an
open circuit, and no later gate would necessarily catch it — `unconnected_items`
is computed on pads and tracks, not on pour membership. So most of what is
tested here is restraint: the plane itself is never a candidate, anything with
a via, a pad, a hole or a track in it stays, and a pad counts by its copper
rather than by its centre.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import pour_islands  # noqa: E402


def _board() -> list[dict]:
    return [{"type": "pcb_board", "width": 40.0, "height": 40.0,
             "center": {"x": 0, "y": 0}, "num_layers": 2, "thickness": 1.6}]


def _net(net_id: str, key: str) -> dict:
    return {"type": "source_net", "source_net_id": net_id,
            "subcircuit_connectivity_map_key": key}


def _source_port(source_port_id: str, key: str) -> dict:
    return {"type": "source_port", "source_port_id": source_port_id,
            "subcircuit_connectivity_map_key": key}


def _port(port_id: str, source_port_id: str) -> dict:
    return {"type": "pcb_port", "pcb_port_id": port_id,
            "source_port_id": source_port_id}


def _pour(pour_id: str, net_id: str, x: float, y: float, half: float,
          layer: str = "bottom") -> dict:
    return {
        "type": "pcb_copper_pour", "pcb_copper_pour_id": pour_id,
        "layer": layer, "source_net_id": net_id, "shape": "brep",
        "brep_shape": {"outer_ring": {"vertices": [
            {"x": x - half, "y": y - half}, {"x": x + half, "y": y - half},
            {"x": x + half, "y": y + half}, {"x": x - half, "y": y + half},
        ]}, "inner_rings": []},
    }


def _run(elements: list[dict]):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "main.circuit.json"
        path.write_text(json.dumps(elements), encoding="utf-8")
        result = pour_islands.drop_dead_pour_islands(path)
        after = json.loads(path.read_text(encoding="utf-8"))
        raw = path.read_text(encoding="utf-8")
    return result, after, raw


def _pour_ids(elements: list[dict]) -> set[str]:
    return {e["pcb_copper_pour_id"] for e in elements
            if e.get("type") == "pcb_copper_pour"}


#: The plane, plus one island far away from anything.
def _plane_and_island() -> list[dict]:
    return _board() + [
        _net("gnd", "KEY_GND"),
        _pour("plane", "gnd", 0.0, 0.0, 15.0),
        _pour("island", "gnd", -12.0, -12.0, 1.0),
    ]


class PourIslandTest(unittest.TestCase):

    def test_drops_a_region_nothing_reaches(self):
        result, after, _ = _run(_plane_and_island())
        self.assertTrue(result.ran)
        self.assertTrue(result.changed)
        self.assertEqual(_pour_ids(after), {"plane"})
        self.assertEqual([d.pour_id for d in result.dropped], ["island"])

    def test_never_drops_the_largest_region_of_a_net(self):
        """A pass that can delete the plane is one nobody should run."""
        # Nothing anchors either region; only the smaller may go.
        result, after, _ = _run(_plane_and_island())
        self.assertIn("plane", _pour_ids(after))

    def test_a_lone_pour_with_no_anchor_survives(self):
        """One region is the plane by definition, however unanchored."""
        elements = _board() + [_net("gnd", "KEY_GND"),
                               _pour("plane", "gnd", 0.0, 0.0, 15.0)]
        result, after, raw = _run(elements)
        self.assertFalse(result.changed)
        self.assertEqual(_pour_ids(after), {"plane"})
        self.assertEqual(raw, json.dumps(elements),
                         "a board with nothing dead must not be rewritten")

    def test_a_via_of_the_net_keeps_its_region(self):
        elements = _plane_and_island() + [
            {"type": "pcb_trace", "pcb_trace_id": "t_gnd",
             "connectsTo": ["pp_g"],
             "route": [{"route_type": "wire", "x": 8.0, "y": 8.0,
                        "layer": "bottom", "width": 0.2},
                       {"route_type": "wire", "x": 9.0, "y": 9.0,
                        "layer": "bottom", "width": 0.2}]},
            _source_port("sp_g", "KEY_GND"), _port("pp_g", "sp_g"),
            {"type": "pcb_via", "pcb_via_id": "v1", "pcb_trace_id": "t_gnd",
             "x": -12.0, "y": -12.0, "outer_diameter": 0.6,
             "hole_diameter": 0.3, "layers": ["top", "bottom"]},
        ]
        result, after, _ = _run(elements)
        self.assertFalse(result.changed)
        self.assertEqual(_pour_ids(after), {"plane", "island"})

    def test_a_track_of_the_net_keeps_its_region(self):
        """Same layer, same net: it is one conductor.

        Two of rc-servo-driver-4ch's regions are joined by nothing else, and
        without this clause the pass called them dead where KiCad does not.
        """
        elements = _plane_and_island() + [
            _source_port("sp_g", "KEY_GND"), _port("pp_g", "sp_g"),
            {"type": "pcb_trace", "pcb_trace_id": "t_gnd",
             "connectsTo": ["pp_g"],
             "route": [{"route_type": "wire", "x": -12.5, "y": -12.0,
                        "layer": "bottom", "width": 0.2},
                       {"route_type": "wire", "x": -11.5, "y": -12.0,
                        "layer": "bottom", "width": 0.2}]},
        ]
        result, after, _ = _run(elements)
        self.assertFalse(result.changed)
        self.assertEqual(_pour_ids(after), {"plane", "island"})

    def test_a_track_on_the_other_layer_does_not(self):
        """Copper on a different layer is a different conductor."""
        elements = _plane_and_island() + [
            _source_port("sp_g", "KEY_GND"), _port("pp_g", "sp_g"),
            {"type": "pcb_trace", "pcb_trace_id": "t_gnd",
             "connectsTo": ["pp_g"],
             "route": [{"route_type": "wire", "x": -12.5, "y": -12.0,
                        "layer": "top", "width": 0.2},
                       {"route_type": "wire", "x": -11.5, "y": -12.0,
                        "layer": "top", "width": 0.2}]},
        ]
        result, _, _ = _run(elements)
        self.assertTrue(result.changed)

    def test_a_pad_counts_by_its_copper_not_its_centre(self):
        """Centre containment called five of wb-32's regions dead that KiCad
        joins to the net."""
        # Island spans x,y in [-13, -11]. A 2x2 pad centred at (-10.5, -12)
        # has its centre outside and its copper across the edge.
        elements = _plane_and_island() + [
            _source_port("sp_g", "KEY_GND"), _port("pp_g", "sp_g"),
            {"type": "pcb_smtpad", "pcb_smtpad_id": "pad_g", "layer": "bottom",
             "shape": "rect", "x": -10.5, "y": -12.0,
             "width": 2.0, "height": 2.0, "pcb_port_id": "pp_g"},
        ]
        result, after, _ = _run(elements)
        self.assertFalse(result.changed)
        self.assertEqual(_pour_ids(after), {"plane", "island"})

    def test_another_nets_copper_does_not_keep_a_region(self):
        """Only the pour's own net joins it. Foreign copper inside a region is
        a clearance problem, not a connection."""
        elements = _plane_and_island() + [
            _net("v5", "KEY_V5"),
            _source_port("sp_v", "KEY_V5"), _port("pp_v", "sp_v"),
            {"type": "pcb_smtpad", "pcb_smtpad_id": "pad_v", "layer": "bottom",
             "shape": "rect", "x": -12.0, "y": -12.0,
             "width": 0.5, "height": 0.5, "pcb_port_id": "pp_v"},
        ]
        result, _, _ = _run(elements)
        self.assertTrue(result.changed)
        self.assertEqual([d.pour_id for d in result.dropped], ["island"])

    def test_layers_are_scored_separately(self):
        """A top region is not kept alive by the bottom plane."""
        elements = _board() + [
            _net("gnd", "KEY_GND"),
            _pour("bottom_plane", "gnd", 0.0, 0.0, 15.0, layer="bottom"),
            _pour("top_plane", "gnd", 0.0, 0.0, 14.0, layer="top"),
            _pour("top_island", "gnd", -12.0, -12.0, 1.0, layer="top"),
        ]
        result, after, _ = _run(elements)
        self.assertEqual(_pour_ids(after), {"bottom_plane", "top_plane"})
        self.assertEqual([d.pour_id for d in result.dropped], ["top_island"])

    def test_reports_area_and_says_where(self):
        result, _, _ = _run(_plane_and_island())
        # The number is checked numerically; the string is only asked to name
        # the layer. Asserting a formatted figure against a geometry constant
        # two steps away tests the format, not the measurement.
        self.assertAlmostEqual(result.area_mm2, 4.0, places=6)
        finding = result.findings()[0]
        self.assertIn("bottom", finding["detail"])
        self.assertEqual(finding["severity"], "info")

    def test_running_it_twice_changes_nothing_the_second_time(self):
        """Membership does not move once the dead regions are gone, so unlike
        the trace sweep this pass is a fixed point. Verified on
        weather-badge-32, macropad-12-oled and desk-cube-55 as well."""
        elements = _plane_and_island()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.circuit.json"
            path.write_text(json.dumps(elements), encoding="utf-8")
            first = pour_islands.drop_dead_pour_islands(path)
            once = path.read_text(encoding="utf-8")
            second = pour_islands.drop_dead_pour_islands(path)
            twice = path.read_text(encoding="utf-8")
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual(once, twice)

    def test_a_board_with_no_pour_does_nothing(self):
        result, _, _ = _run(_board() + [_net("gnd", "KEY_GND")])
        self.assertFalse(result.ran)
        self.assertEqual(result.findings(), [])

    def test_unreadable_input_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = pour_islands.drop_dead_pour_islands(
                Path(tmp) / "missing.circuit.json")
        self.assertFalse(result.ran)
        self.assertIsNotNone(result.note)
        self.assertEqual(result.findings(), [])


if __name__ == "__main__":
    unittest.main()
