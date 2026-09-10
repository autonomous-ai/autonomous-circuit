"""Repair mode's edit step: it moves copper and nothing else."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import repair  # noqa: E402


def _ir() -> list[dict]:
    wire = lambda x, y, layer="top", **k: {"route_type": "wire", "x": x, "y": y, "layer": layer, "width": 0.127, **k}
    return [
        {"type": "pcb_board", "width": 20, "height": 20},
        {"type": "pcb_via", "pcb_via_id": "via_1", "x": 2.0, "y": 7.0, "pcb_trace_id": "t_a",
         "outer_diameter": 0.6, "hole_diameter": 0.3, "layers": ["top", "bottom"]},
        {"type": "pcb_trace", "pcb_trace_id": "t_a", "route": [
            wire(0.0, 7.0, start_pcb_port_id="pp_1"),
            wire(1.0, 7.0),
            wire(2.0, 7.0),
            {"route_type": "via", "x": 2.0, "y": 7.0, "from_layer": "top", "to_layer": "bottom",
             "via_diameter": 0.6, "via_hole_diameter": 0.3},
            wire(2.0, 7.0, layer="bottom"),
            wire(2.0, 9.0, layer="bottom", end_pcb_port_id="pp_2"),
        ]},
        {"type": "pcb_trace", "pcb_trace_id": "t_b", "route": [
            wire(1.5, 6.9, start_pcb_port_id="pp_3"),
            wire(2.3, 6.9),
            wire(2.6, 7.3),
            wire(2.6, 9.0, end_pcb_port_id="pp_4"),
        ]},
    ]


class RepairTest(unittest.TestCase):

    def _run(self, edits, ir=None):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "main.circuit.json"
            before = ir or _ir()
            p.write_text(json.dumps(before))
            try:
                applied = repair.apply_edits(p, edits)
            except repair.RepairError:
                after = json.loads(p.read_text())
                self.assertEqual(after, before, "a refused edit list leaves the file untouched")
                raise
            return applied, json.loads(p.read_text())

    def _route(self, after, tid):
        return next(e for e in after if e.get("pcb_trace_id") == tid and e["type"] == "pcb_trace")["route"]

    def test_move_point_moves_one_wire_vertex(self):
        applied, after = self._run([{"op": "move_point", "trace": "t_b", "index": 1, "x": 2.3, "y": 6.6}])
        self.assertEqual(self._route(after, "t_b")[1]["y"], 6.6)
        self.assertEqual(applied[0]["from"], [2.3, 6.9])

    def test_move_point_refuses_a_via_and_a_pad_anchor(self):
        with self.assertRaisesRegex(repair.RepairError, "is a via"):
            self._run([{"op": "move_point", "trace": "t_a", "index": 3, "x": 0, "y": 0}])
        with self.assertRaisesRegex(repair.RepairError, "anchored to a pad"):
            self._run([{"op": "move_point", "trace": "t_a", "index": 0, "x": 0, "y": 0}])

    def test_move_point_refuses_the_wire_end_that_sits_on_a_via(self):
        """Moving that vertex alone is finding A by hand."""
        for i in (2, 4):
            with self.assertRaisesRegex(repair.RepairError, "sits on a via"):
                self._run([{"op": "move_point", "trace": "t_a", "index": i, "x": 2.2, "y": 7.0}])
        # the vertex before it, not on the via, still moves
        _, after = self._run([{"op": "move_point", "trace": "t_a", "index": 1, "x": 1.0, "y": 6.8}])
        self.assertEqual(self._route(after, "t_a")[1]["y"], 6.8)

    def test_move_via_carries_the_wires_that_meet_it(self):
        """The join the contiguity check measures stays a join
        (2026-09-10: `trace_clearance` breaking exactly this join was finding A)."""
        applied, after = self._run([{"op": "move_via", "via": "via_1", "x": 2.4, "y": 7.4}])
        via = next(e for e in after if e.get("type") == "pcb_via")
        self.assertEqual((via["x"], via["y"]), (2.4, 7.4))
        r = self._route(after, "t_a")
        self.assertEqual([(p["x"], p["y"]) for p in r[2:5]], [(2.4, 7.4)] * 3)
        self.assertEqual(applied[0]["carriedPoints"], 3)
        # the rest of the trace, and the other trace, did not move
        self.assertEqual((r[1]["x"], r[1]["y"]), (1.0, 7.0))
        self.assertEqual(self._route(after, "t_b")[1]["x"], 2.3)

    def test_move_via_refuses_when_no_trace_joins_it(self):
        ir = _ir()
        ir[1]["x"] = 5.0  # a via nothing sits on
        with self.assertRaisesRegex(repair.RepairError, "refusing to guess"):
            self._run([{"op": "move_via", "via": "via_1", "x": 1, "y": 1}], ir)

    def test_insert_and_delete_keep_layers_and_anchors(self):
        applied, after = self._run([
            {"op": "insert_point", "trace": "t_a", "after": 3, "x": 2.0, "y": 8.0},
            {"op": "delete_points", "trace": "t_b", "indices": [1, 2]},
        ])
        r = self._route(after, "t_a")
        self.assertEqual(r[4]["layer"], "bottom", "after a via, the new vertex is on the layer the via lands on")
        self.assertEqual(r[4]["width"], 0.127)
        self.assertEqual(len(self._route(after, "t_b")), 2)
        with self.assertRaisesRegex(repair.RepairError, "is a via"):
            self._run([{"op": "delete_points", "trace": "t_a", "indices": [3]}])
        with self.assertRaisesRegex(repair.RepairError, "anchored to a pad"):
            self._run([{"op": "delete_points", "trace": "t_b", "indices": [1, 2, 3]}])
        bare = _ir()
        bare[3]["route"] = [{"route_type": "wire", "x": x, "y": 1.0, "layer": "top", "width": 0.127} for x in (0.0, 1.0, 2.0)]
        with self.assertRaisesRegex(repair.RepairError, "fewer than two"):
            self._run([{"op": "delete_points", "trace": "t_b", "indices": [0, 1]}], bare)

    def test_all_or_nothing(self):
        with self.assertRaisesRegex(repair.RepairError, "unknown op"):
            self._run([
                {"op": "move_point", "trace": "t_b", "index": 1, "x": 9, "y": 9},
                {"op": "teleport"},
            ])

    def test_edits_file_accepts_a_bare_list_or_an_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "main.circuit.json"; p.write_text(json.dumps(_ir()))
            e = Path(tmp) / "edits.json"
            e.write_text(json.dumps({"edits": [{"op": "move_point", "trace": "t_b", "index": 1, "x": 2.3, "y": 6.5}]}))
            self.assertEqual(len(repair.apply_edits_file(p, e)), 1)


if __name__ == "__main__":
    unittest.main()
