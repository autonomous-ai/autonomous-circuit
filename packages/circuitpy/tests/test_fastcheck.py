"""fastcheck.py + checks.trace_anchor_warnings — the sub-second edit gate.

Two things are asserted, because the feature is worthless without either:
the gate must *find* the geometry a drag names, and it must *notice* when the
drag breaks something. Synthetic geometry for the unit cases; the real
terminal-keyboard artifact for the end-to-end one, skipped when it is not on
disk (the same posture kicad-dependent tests take).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from circuitpy import checks, fastcheck  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = REPO_ROOT / "examples" / "terminal-keyboard"
EXAMPLE_JSON = EXAMPLE / "boards" / "main.circuit.json"


def _pad(pad_id: str, port: str, component: str, x: float, y: float) -> dict:
    return {
        "type": "pcb_smtpad",
        "pcb_smtpad_id": pad_id,
        "pcb_port_id": port,
        "pcb_component_id": component,
        "layer": "top",
        "shape": "rect",
        "width": 1.0,
        "height": 1.0,
        "x": x,
        "y": y,
    }


def _board(component_x: float = 0.0) -> list[dict]:
    """One resistor at (component_x, 0) with a trace anchored to its pins.

    The trace never moves — that is the point. `pcb_trace` is router-owned, so
    a drag leaves the copper behind and the anchor check is what notices.
    """
    return [
        {"type": "source_group", "source_group_id": "sg_root", "parent_source_group_id": None},
        {"type": "source_group", "source_group_id": "sg_r", "parent_source_group_id": "sg_root"},
        {
            "type": "source_component",
            "source_component_id": "sc_0",
            "source_group_id": "sg_r",
            "name": "R1",
        },
        {
            "type": "pcb_group",
            "pcb_group_id": "pg_0",
            "source_group_id": "sg_r",
            "anchor_position": {"x": component_x, "y": 0.0},
            "center": {"x": component_x, "y": 0.0},
        },
        {
            "type": "pcb_component",
            "pcb_component_id": "pc_0",
            "source_component_id": "sc_0",
            "center": {"x": component_x, "y": 0.0},
            "width": 2.0,
            "height": 1.0,
            "layer": "top",
            "rotation": 0,
        },
        _pad("pad_a", "port_a", "pc_0", component_x - 1.0, 0.0),
        _pad("pad_b", "port_b", "pc_0", component_x + 1.0, 0.0),
        {
            "type": "pcb_silkscreen_text",
            "pcb_silkscreen_text_id": "silk_0",
            "pcb_component_id": "pc_0",
            "anchor_position": {"x": component_x, "y": 0.8},
            "text": "R1",
            "layer": "top",
            "font_size": 0.5,
        },
        {
            "type": "pcb_trace",
            "pcb_trace_id": "trace_0",
            "route": [
                {"route_type": "wire", "x": -1.0, "y": 0.0, "width": 0.2,
                 "layer": "top", "start_pcb_port_id": "port_a"},
                {"route_type": "wire", "x": -5.0, "y": 0.0, "width": 0.2, "layer": "top"},
            ],
        },
    ]


class TraceAnchorTests(unittest.TestCase):
    def test_a_settled_board_reports_nothing(self) -> None:
        self.assertEqual(checks.trace_anchor_warnings(_board()), [])

    def test_a_part_dragged_off_its_copper_is_an_error(self) -> None:
        moved, applied = fastcheck.apply_moves(_board(), [fastcheck.Move(0.0, 0.0, 2.0, 0.0)])
        self.assertTrue(applied[0]["ok"], applied[0]["reason"])
        found = checks.trace_anchor_warnings(moved)
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0]["kind"], "trace_left_its_pad")
        self.assertEqual(found[0]["severity"], "error")
        self.assertEqual(found[0]["part"], "R1")
        # The pad is 1mm wide, so its edge starts 0.5mm from centre; a 2mm drag
        # leaves the route endpoint 1.5mm outside the copper.
        self.assertIn("1.500mm", found[0]["detail"])

    def test_it_never_raises_on_junk(self) -> None:
        for junk in ([], [None], [{"type": "pcb_trace", "route": "not a list"}],
                     [{"type": "pcb_trace", "route": [{"x": "left", "y": None}]}]):
            self.assertEqual(checks.trace_anchor_warnings(junk), [])


class ResolveMoveTests(unittest.TestCase):
    def test_a_group_anchor_drags_its_parts_and_their_drawings(self) -> None:
        resolved = fastcheck.resolve_move(_board(), fastcheck.Move(0.0, 0.0, 1.0, 0.0))
        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["kind"], "group")
        self.assertEqual(resolved["refdes"], ["R1"])
        # group + component + two pads + the silkscreen label; never the trace.
        self.assertEqual(len(resolved["element_ids"]), 5)

    def test_copper_is_never_dragged(self) -> None:
        moved, _ = fastcheck.apply_moves(_board(), [fastcheck.Move(0.0, 0.0, 2.0, 0.0)])
        trace = next(e for e in moved if e["type"] == "pcb_trace")
        self.assertEqual(trace["route"][0]["x"], -1.0)

    def test_a_move_at_no_anchor_is_reported_and_not_applied(self) -> None:
        before = _board()
        after, applied = fastcheck.apply_moves(before, [fastcheck.Move(99.0, 99.0, 1.0, 0.0)])
        self.assertFalse(applied[0]["ok"])
        self.assertIn("nothing on the built board", applied[0]["reason"])
        self.assertEqual(after, before)

    def test_a_lead_part_sitting_on_its_own_block_anchor_is_not_ambiguous(self) -> None:
        # The normal case for a small block: `pcb_group_53` and LED1 both sit at
        # (-45.8, -32) on terminal-keyboard. Only direct children of the board's
        # own group are candidates, so the group wins and the part inside it is
        # not a rival.
        elements = _board()
        for element in elements:
            if element["type"] == "pcb_component":
                element["center"] = {"x": 0.0, "y": 0.0}
        resolved = fastcheck.resolve_move(elements, fastcheck.Move(0.0, 0.0, 1.0, 0.0))
        self.assertTrue(resolved["ok"], resolved["reason"])
        self.assertEqual(resolved["kind"], "group")

    def test_the_source_list_is_never_mutated(self) -> None:
        before = _board()
        snapshot = json.dumps(before, sort_keys=True)
        fastcheck.apply_moves(before, [fastcheck.Move(0.0, 0.0, 3.0, -1.0)])
        self.assertEqual(json.dumps(before, sort_keys=True), snapshot)

    def test_parse_move_reads_the_cli_grammar(self) -> None:
        self.assertEqual(fastcheck.parse_move("-45.8,-32:2.8,0"),
                         fastcheck.Move(-45.8, -32.0, 2.8, 0.0))


@unittest.skipUnless(EXAMPLE_JSON.is_file(), "terminal-keyboard has not been built")
class RealBoardTests(unittest.TestCase):
    """The claim the whole feature rests on: a 2.8mm drag on the largest board
    we ship is caught without compiling anything."""

    def test_the_gate_finds_what_a_20s_compile_finds(self) -> None:
        elements = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))
        # <StatusLed> at (-45.8, -32) owns LED1 and R20.
        moved, applied = fastcheck.apply_moves(
            elements, [fastcheck.Move(-45.8, -32.0, 2.8, 0.0)]
        )
        self.assertTrue(applied[0]["ok"], applied[0]["reason"])
        self.assertEqual(applied[0]["refdes"], ["LED1", "R20"])
        self.assertEqual(applied[0]["components"], 2)

        anchors = checks.trace_anchor_warnings(moved)
        self.assertTrue(anchors, "the drag must break the copper it left behind")
        self.assertTrue(all(w["severity"] == "error" for w in anchors))
        # And the untouched board is clean on the same check, or the finding
        # above would just be noise.
        self.assertEqual(checks.trace_anchor_warnings(elements), [])

    def test_the_verdict_carries_its_own_blind_spots(self) -> None:
        result = fastcheck.fast_check(EXAMPLE_JSON, project_root=EXAMPLE, node=False)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["geometry"], "as_built")
        blind = json.dumps(result["not_checked"])
        self.assertIn("copper pour", blind)
        self.assertIn("fab packet", blind)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
