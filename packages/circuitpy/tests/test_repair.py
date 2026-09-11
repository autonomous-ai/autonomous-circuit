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


class LayerAndViaTest(unittest.TestCase):
    """A two-via hop back to one layer: the crystal net's only way under 10 mm."""

    def _run(self, edits, ir=None):
        return RepairTest._run(self, edits, ir)

    def _route(self, after, tid):
        return RepairTest._route(self, after, tid)

    def test_remove_via_refuses_while_the_sides_differ(self):
        with self.assertRaisesRegex(repair.RepairError, "set_layer one side first"):
            self._run([{"op": "remove_via", "via": "via_1"}])

    def test_set_layer_then_remove_via_gives_a_one_layer_trace(self):
        ir = _ir()
        ir[2]["route"][5].pop("end_pcb_port_id")  # the far end is a free vertex in this fixture
        applied, after = self._run([
            {"op": "set_layer", "trace": "t_a", "indices": [4, 5], "layer": "top"},
            {"op": "remove_via", "via": "via_1"},
        ], ir)
        r = self._route(after, "t_a")
        self.assertEqual([p.get("route_type") for p in r], ["wire"] * 4, "via point gone, coincident end merged")
        self.assertEqual({p["layer"] for p in r}, {"top"})
        self.assertEqual([(p["x"], p["y"]) for p in r], [(0.0, 7.0), (1.0, 7.0), (2.0, 7.0), (2.0, 9.0)])
        self.assertFalse(any(e.get("type") == "pcb_via" for e in after), "the pcb_via element is gone")
        self.assertEqual(applied[1]["op"], "remove_via")

    def test_set_layer_refuses_a_pad_anchor_and_a_via(self):
        with self.assertRaisesRegex(repair.RepairError, "anchored to a pad"):
            self._run([{"op": "set_layer", "trace": "t_a", "indices": [5], "layer": "top"}])
        with self.assertRaisesRegex(repair.RepairError, "is a via"):
            self._run([{"op": "set_layer", "trace": "t_a", "indices": [3], "layer": "top"}])
        with self.assertRaisesRegex(repair.RepairError, "layer must be one of"):
            self._run([{"op": "set_layer", "trace": "t_a", "indices": [1], "layer": "mid"}])

    def test_remove_via_refuses_a_layer_change_left_without_a_via(self):
        # re-layer only the near side: the far end (bottom, anchored) would
        # then follow a top wire with no via between
        with self.assertRaisesRegex(repair.RepairError, "with no via"):
            self._run([
                {"op": "set_layer", "trace": "t_a", "indices": [4], "layer": "top"},
                {"op": "remove_via", "via": "via_1"},
            ])


def _routed_board():
    """A 20x20 board: net A's trace runs straight through a foreign pad of net B."""
    wire = lambda x, y, layer="top", **k: {"route_type": "wire", "x": x, "y": y, "layer": layer, "width": 0.2, **k}
    return [
        {"type": "pcb_board", "width": 20, "height": 20, "center": {"x": 0, "y": 0}, "num_layers": 2, "thickness": 1.6},
        {"type": "source_net", "source_net_id": "net_a", "subcircuit_connectivity_map_key": "KEY_A"},
        {"type": "source_net", "source_net_id": "net_b", "subcircuit_connectivity_map_key": "KEY_B"},
        {"type": "source_port", "source_port_id": "sp_a1", "subcircuit_connectivity_map_key": "KEY_A"},
        {"type": "source_port", "source_port_id": "sp_a2", "subcircuit_connectivity_map_key": "KEY_A"},
        {"type": "source_port", "source_port_id": "sp_b", "subcircuit_connectivity_map_key": "KEY_B"},
        {"type": "pcb_port", "pcb_port_id": "pp_a1", "source_port_id": "sp_a1"},
        {"type": "pcb_port", "pcb_port_id": "pp_a2", "source_port_id": "sp_a2"},
        {"type": "pcb_port", "pcb_port_id": "pp_b", "source_port_id": "sp_b"},
        {"type": "pcb_smtpad", "pcb_smtpad_id": "pad_a1", "layer": "top", "shape": "rect", "x": -6, "y": 0, "width": 1, "height": 1, "pcb_port_id": "pp_a1"},
        {"type": "pcb_smtpad", "pcb_smtpad_id": "pad_a2", "layer": "top", "shape": "rect", "x": 6, "y": 0, "width": 1, "height": 1, "pcb_port_id": "pp_a2"},
        # the foreign pad squarely on the straight line
        {"type": "pcb_smtpad", "pcb_smtpad_id": "pad_b", "layer": "top", "shape": "rect", "x": 0, "y": 0, "width": 2, "height": 2, "pcb_port_id": "pp_b"},
        {"type": "pcb_trace", "pcb_trace_id": "t_a", "connectsTo": ["pp_a1", "pp_a2"], "route": [
            wire(-6, 0, start_pcb_port_id="pp_a1"), wire(-4, 0), wire(4, 0), wire(6, 0, end_pcb_port_id="pp_a2"),
        ]},
    ]


class RerouteTest(unittest.TestCase):
    """A* for one segment of one net, seeing every piece of foreign copper."""

    def _run(self, edits, ir):
        return RepairTest._run(self, edits, ir)

    def test_reroutes_around_a_foreign_pad(self):
        import math
        applied, after = self._run([{"op": "reroute", "trace": "t_a", "from_index": 1, "to_index": 2, "clearance": 0.15}], _routed_board())
        r = RepairTest._route(self, after, "t_a")
        # ends kept, interior replaced, every new vertex on the same layer
        self.assertEqual((r[0]["x"], r[0]["y"]), (-6, 0))
        self.assertEqual((r[-1]["x"], r[-1]["y"]), (6, 0))
        self.assertTrue(all(p["layer"] == "top" for p in r))
        # the new path clears pad_b (half-width 1.0 + clearance 0.15 + trace half 0.1)
        for p in r[1:-1]:
            self.assertTrue(abs(p["x"]) > 1.25 or abs(p["y"]) > 1.25, p)
        # …and every new segment stays clear of the pad's corner too
        def seg_clear(p, q):
            steps = 50
            for k in range(steps + 1):
                x = p["x"] + (q["x"] - p["x"]) * k / steps; y = p["y"] + (q["y"] - p["y"]) * k / steps
                dx = max(abs(x) - 1.0, 0); dy = max(abs(y) - 1.0, 0)
                if math.hypot(dx, dy) < 0.15 + 0.1 - 1e-6:
                    return False
            return True
        for p, q in zip(r, r[1:]):
            self.assertTrue(seg_clear(p, q), (p, q))
        self.assertEqual(applied[0]["op"], "reroute")
        self.assertGreater(applied[0]["lengthAfterMm"], applied[0]["lengthBeforeMm"], "going around costs length")

    def test_refuses_across_a_via_or_a_layer_change(self):
        ir = _routed_board()
        ir[-1]["route"][2] = {"route_type": "via", "x": 4, "y": 0, "from_layer": "top", "to_layer": "bottom"}
        ir[-1]["route"].insert(3, {"route_type": "wire", "x": 4, "y": 0, "layer": "bottom", "width": 0.2})
        ir[-1]["route"][-1]["layer"] = "bottom"
        with self.assertRaisesRegex(repair.RepairError, "via sits between|one layer per reroute|must be wire"):
            self._run([{"op": "reroute", "trace": "t_a", "from_index": 1, "to_index": 4}], ir)

    def test_refuses_when_the_corridor_is_closed(self):
        ir = _routed_board()
        # wall the whole window with a foreign pad taller than the search box
        ir.append({"type": "pcb_smtpad", "pcb_smtpad_id": "wall", "layer": "top", "shape": "rect", "x": 0, "y": 0, "width": 0.5, "height": 40, "pcb_port_id": "pp_b"})
        with self.assertRaisesRegex(repair.RepairError, "no path"):
            self._run([{"op": "reroute", "trace": "t_a", "from_index": 1, "to_index": 2}], ir)


class CheckpointTest(unittest.TestCase):

    def test_checkpoint_then_undo_restores_the_ir(self):
        with tempfile.TemporaryDirectory() as tmp:
            boards = Path(tmp) / "boards"; boards.mkdir()
            p = boards / "main.circuit.json"; p.write_text(json.dumps(_ir()))
            (boards / "main.board.json").write_text('{"fab":{"ready":true}}')
            self.assertIsNone(repair.undo_last_checkpoint(p), "nothing to undo yet")
            cp = repair.checkpoint(p)
            self.assertTrue(cp and (cp / "main.circuit.json").is_file() and (cp / "main.board.json").is_file())
            self.assertIn(".circuit", str(cp), "checkpoints live where the artifact watcher cannot see them")
            repair.apply_edits(p, [{"op": "move_point", "trace": "t_b", "index": 1, "x": 9, "y": 9}])
            self.assertEqual(RepairTest._route(self, json.loads(p.read_text()), "t_b")[1]["x"], 9)
            name = repair.undo_last_checkpoint(p)
            self.assertTrue(name)
            self.assertEqual(RepairTest._route(self, json.loads(p.read_text()), "t_b")[1]["x"], 2.3, "back to the checkpoint")
            self.assertIsNone(repair.undo_last_checkpoint(p), "a checkpoint is consumed once")

    def test_only_the_newest_ten_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            boards = Path(tmp) / "boards"; boards.mkdir()
            p = boards / "main.circuit.json"; p.write_text(json.dumps(_ir()))
            for _ in range(12):
                repair.checkpoint(p)
            root = Path(tmp) / ".circuit" / "repair-undo"
            self.assertEqual(len([d for d in root.iterdir() if d.is_dir()]), repair.KEEP_CHECKPOINTS)
