"""netwidth.py — how wide a net can be, measured from the placement.

The whole feature exists because the honest answer to "make the rails wider" is
a number the engineer has to be given *before* they choose. Two of the three
tests here are arithmetic with a closed form, so a regression cannot hide behind
"well, geometry is complicated": a track leaving a pad in a row of pads can be
`2 x (pitch - pad_half_width - clearance)` wide, and that is what the measured
escape must come back as.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from circuitpy import fab, netwidth  # noqa: E402

PROFILE = fab.get_profile("jlcpcb")
REPO_ROOT = Path(__file__).resolve().parents[3]
KEYBOARD = REPO_ROOT / "examples" / "terminal-keyboard" / "boards" / "main.circuit.json"


def _board(pads: list[dict], net_name: str = "V5") -> list[dict]:
    """A board with one component whose pads all sit on one net."""
    elements: list[dict] = [
        {"type": "pcb_board", "width": 40, "height": 40, "center": {"x": 0, "y": 0}},
        {"type": "source_net", "source_net_id": "net_1", "name": net_name,
         "subcircuit_connectivity_map_key": "key_1"},
        {"type": "source_component", "source_component_id": "sc_1", "name": "U1"},
        {"type": "pcb_component", "pcb_component_id": "pc_1", "source_component_id": "sc_1",
         "center": {"x": 0, "y": 0}, "width": 4, "height": 4, "layer": "top", "rotation": 0},
    ]
    for i, pad in enumerate(pads):
        port = f"port_{i}"
        # The connectivity key lives on the **source** port; a `pcb_port`
        # carries only the join to it (`diffpair._Board._port_net_map`). A
        # fixture that puts it on the pcb_port builds a board with no nets at
        # all, which is a silent empty report rather than a failure.
        elements.append({
            "type": "source_port", "source_port_id": f"sp_{i}", "name": f"PIN{i}",
            "source_component_id": "sc_1",
            "subcircuit_connectivity_map_key": pad.pop("net_key", "key_1"),
        })
        elements.append({
            "type": "pcb_port", "pcb_port_id": port, "source_port_id": f"sp_{i}",
            "pcb_component_id": "pc_1",
        })
        elements.append({
            "type": "pcb_smtpad", "pcb_smtpad_id": f"pad_{i}", "pcb_port_id": port,
            "pcb_component_id": "pc_1", "layer": "top", "shape": "rect", **pad,
        })
    return elements


class EscapeArithmetic(unittest.TestCase):
    def test_a_pad_alone_on_the_board_escapes_as_wide_as_it_is_asked(self) -> None:
        rows = netwidth.net_width_report(_board([{"x": 0, "y": 0, "width": 1.0, "height": 1.0}]), PROFILE)
        self.assertEqual(len(rows), 1, rows)
        # Nothing to run into, so the answer is the cap and it says so — "at
        # least this wide", never "exactly this".
        self.assertEqual(rows[0]["ceiling_mm"], netwidth.CAP_MM)
        self.assertTrue(rows[0]["ceiling_capped"])

    def test_the_ceiling_between_two_pads_is_the_pitch_arithmetic(self) -> None:
        """`2 x (pitch - pad_half_width - clearance)`, which is the sentence
        the docs make about the RP2040 and the reason V3_3 can never be 0.5mm
        on any board carrying one."""
        pitch, pad_w = 0.8, 0.4
        clearance = float(PROFILE.min_clearance_mm)
        # Two neighbours, one either side, on a *different* net — same-net
        # copper is exempt from its own clearance rule (ledger #24) and would
        # measure a ceiling that does not exist.
        rows = netwidth.net_width_report(
            _board([
                {"x": 0, "y": 0, "width": pad_w, "height": pad_w},
                {"x": pitch, "y": 0, "width": pad_w, "height": pad_w, "net_key": "key_other"},
                {"x": -pitch, "y": 0, "width": pad_w, "height": pad_w, "net_key": "key_other"},
            ]),
            PROFILE,
        )
        expected = round(2 * (pitch - pad_w / 2 - clearance), 4)
        measured = rows[0]["ceiling_mm"]
        # The escape fans 180 ways and the best of them runs straight up the
        # channel, so this is exact rather than approximate.
        self.assertAlmostEqual(measured, expected, places=3, msg=f"{measured} vs {expected}")
        self.assertFalse(rows[0]["ceiling_capped"])
        self.assertEqual(rows[0]["pads"], 1)

    def test_a_polygon_pad_is_counted_not_guessed(self) -> None:
        # A polygon pad carries `points` and no centre — the USB-C shell pads
        # are the only ones we ship, and a report that silently dropped them
        # would overstate a net's ceiling by leaving out its widest obstacle.
        elements = _board([{"x": 0, "y": 0, "width": 1.0, "height": 1.0}])
        elements.append({
            "type": "pcb_port", "pcb_port_id": "port_poly", "source_port_id": "sp_0",
            "pcb_component_id": "pc_1",
        })
        elements.append({
            "type": "pcb_smtpad", "pcb_smtpad_id": "pad_poly", "pcb_port_id": "port_poly",
            "pcb_component_id": "pc_1", "layer": "top", "shape": "polygon",
            "points": [{"x": 5, "y": 5}, {"x": 6, "y": 5}, {"x": 6, "y": 6}],
        })
        rows = netwidth.net_width_report(elements, PROFILE)
        self.assertEqual(rows[0]["pads"], 1)
        self.assertEqual(rows[0]["pads_skipped"], 1)


class DeclaredWidths(unittest.TestCase):
    def test_the_largest_declaration_on_a_net_is_the_one_that_counts(self) -> None:
        # `nominalTraceWidth` is a `max` over every trace joined to the net, so
        # one declaration anywhere sets the whole net and a partial marking is
        # identical to a full one.
        elements = [
            {"type": "source_net", "source_net_id": "net_1", "name": "V5"},
            {"type": "source_trace", "source_trace_id": "t1",
             "connected_source_net_ids": ["net_1"], "min_trace_thickness": 0.3},
            {"type": "source_trace", "source_trace_id": "t2",
             "connected_source_net_ids": ["net_1"], "min_trace_thickness": 0.5},
            {"type": "source_trace", "source_trace_id": "t3",
             "connected_source_net_ids": ["net_1"]},
        ]
        self.assertEqual(netwidth.declared_widths(elements), {"V5": 0.5})

    def test_a_board_that_declares_nothing_reports_nothing(self) -> None:
        self.assertEqual(netwidth.declared_widths([{"type": "source_net", "name": "V5"}]), {})


@unittest.skipUnless(KEYBOARD.is_file(), "terminal-keyboard has not been built")
class RealBoard(unittest.TestCase):
    def test_the_shipped_board_reports_the_ceilings_the_docs_were_written_from(self) -> None:
        """The numbers in `docs/architecture/rail-width.md` and the reason the
        EE's finding 4 cannot simply be done: V3_3 is capped by the RP2040's
        own pitch, and V5 is not capped by anything."""
        rows = {
            row["net"]: row
            for row in netwidth.net_width_report(
                json.loads(KEYBOARD.read_text(encoding="utf-8")), PROFILE, rails_only=True
            )
        }
        self.assertIn("V3_3", rows)
        self.assertIn("V5", rows)
        self.assertAlmostEqual(rows["V3_3"]["ceiling_mm"], 0.4, places=3)
        self.assertIn("U3.", rows["V3_3"]["ceiling_at"])
        self.assertAlmostEqual(rows["V5"]["ceiling_mm"], 1.1, places=3)
        # And what the board is routed at today, which is the other half of the
        # sentence an engineer needs: 0.2mm against a 0.5mm floor.
        self.assertAlmostEqual(rows["V3_3"]["narrowest_mm"], 0.2, places=3)
        self.assertIsNone(rows["V3_3"]["declared_mm"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
