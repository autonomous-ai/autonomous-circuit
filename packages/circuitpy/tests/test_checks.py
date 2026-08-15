"""checks.py — harvesters over real built circuit.json fixtures + synthetic
geometry. Every harvester must be never-raise."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import circuitproj  # noqa: E402
from circuitproj import EnvGuard, FIXTURES, load_fixture  # noqa: E402

from circuitpy import checks  # noqa: E402
from circuitpy.fab import get_profile  # noqa: E402
from circuitpy.spec import ResolvedProduct  # noqa: E402

PROFILE = get_profile("jlcpcb")


def _product(envelope: tuple[float, float] | None = (60.0, 40.0)) -> ResolvedProduct:
    return ResolvedProduct(
        name="t",
        description="",
        power="usb-c-5v",
        envelope_mm=envelope,
        layers=2,
        fab="jlcpcb",
        assembly=True,
        path=Path("product.json"),
    )


class HarvestCircuitJson(unittest.TestCase):
    def test_bad_overlap_errors_harvested_verbatim(self) -> None:
        warnings = checks.harvest_circuit_json(load_fixture("bad-overlap.circuit.json"))
        kinds = {w["kind"] for w in warnings}
        self.assertIn("pcb_footprint_overlap_error", kinds)
        self.assertIn("pcb_pad_pad_clearance_error", kinds)
        self.assertIn("pcb_autorouting_error", kinds)
        for warning in warnings:
            if warning["kind"].endswith("_error"):
                self.assertEqual(warning["severity"], "error")
            elif warning["kind"].endswith("_warning"):
                self.assertEqual(warning["severity"], "warning")

    def test_part_localization_uses_refdes(self) -> None:
        warnings = checks.harvest_circuit_json(load_fixture("bad-overlap.circuit.json"))
        overlap = [w for w in warnings if w["kind"] == "pcb_courtyard_overlap_error"]
        self.assertTrue(overlap)
        self.assertIn("R1", overlap[0]["part"])

    def test_bad_port_error_present(self) -> None:
        warnings = checks.harvest_circuit_json(load_fixture("bad-port.circuit.json"))
        kinds = {w["kind"] for w in warnings}
        self.assertIn("source_trace_not_connected_error", kinds)

    def test_good_board_has_no_errors(self) -> None:
        warnings = checks.harvest_circuit_json(load_fixture("good.circuit.json"))
        self.assertFalse([w for w in warnings if w["severity"] == "error"])

    def test_supplier_mismatch_left_to_iou_bander(self) -> None:
        warnings = checks.harvest_circuit_json(
            load_fixture("parts-engine.circuit.json")
        )
        kinds = {w["kind"] for w in warnings}
        self.assertNotIn("supplier_footprint_mismatch_warning", kinds)

    def test_never_raises_on_garbage(self) -> None:
        warnings = checks.harvest_circuit_json([{"type": None}, "junk", 42])  # type: ignore[list-item]
        self.assertIsInstance(warnings, list)


def _switch_schematic(
    *,
    ftype: str = "simple_push_button",
    same_component: bool = True,
    loop: bool = True,
) -> list[dict]:
    """Minimal circuit.json slice: one (or two) components with a schematic
    wire between two ports. Mirrors the real element shapes measured on
    terminal-keyboard (a schematic_trace whose source_trace_id names the two
    ports it bridges)."""
    owner_b = "schematic_component_1" if same_component else "schematic_component_2"
    elements: list[dict] = [
        {"type": "source_component", "source_component_id": "source_component_1",
         "name": "SW1", "ftype": ftype},
        {"type": "source_component", "source_component_id": "source_component_2",
         "name": "J1", "ftype": "simple_chip"},
        {"type": "schematic_component", "schematic_component_id": "schematic_component_1",
         "source_component_id": "source_component_1"},
        {"type": "schematic_component", "schematic_component_id": "schematic_component_2",
         "source_component_id": "source_component_2"},
        {"type": "schematic_port", "schematic_port_id": "schematic_port_1",
         "schematic_component_id": "schematic_component_1",
         "source_port_id": "source_port_1"},
        {"type": "schematic_port", "schematic_port_id": "schematic_port_2",
         "schematic_component_id": owner_b, "source_port_id": "source_port_2"},
    ]
    if loop:
        elements.append(
            {"type": "schematic_trace", "schematic_trace_id": "schematic_trace_1",
             "source_trace_id": "schematic_port_2-schematic_port_1", "edges": []}
        )
    return elements


class SchematicTruth(unittest.TestCase):
    """Ledger #29: a wire looping across a switch symbol is a drawn short."""

    def test_switch_self_loop_fires(self) -> None:
        warnings = checks.schematic_truth_warnings(_switch_schematic())
        self.assertEqual([w["kind"] for w in warnings], ["schematic_symbol_short"])
        self.assertEqual(warnings[0]["part"], "SW1")
        self.assertEqual(warnings[0]["severity"], "warning")

    def test_wire_between_two_components_is_fine(self) -> None:
        warnings = checks.schematic_truth_warnings(
            _switch_schematic(same_component=False)
        )
        self.assertEqual(warnings, [])

    def test_connector_tie_is_not_flagged(self) -> None:
        # USB-C's DP1/DP2 tie is real copper and must keep drawing as a wire.
        warnings = checks.schematic_truth_warnings(
            _switch_schematic(ftype="simple_chip")
        )
        self.assertEqual(warnings, [])

    def test_no_loop_no_warning(self) -> None:
        warnings = checks.schematic_truth_warnings(_switch_schematic(loop=False))
        self.assertEqual(warnings, [])

    def test_fires_on_terminal_keyboard_class_fixture(self) -> None:
        # The real defect: 50 keys, each drawn shorted. One warning per key,
        # not one per redundant tie.
        elements = _switch_schematic()
        elements.append(
            {"type": "schematic_trace", "schematic_trace_id": "schematic_trace_2",
             "source_trace_id": "schematic_port_1-schematic_port_2", "edges": []}
        )
        warnings = checks.schematic_truth_warnings(elements)
        self.assertEqual(len(warnings), 1)

    def test_never_raises_on_garbage(self) -> None:
        warnings = checks.schematic_truth_warnings([{"type": None}, "junk", 42])  # type: ignore[list-item]
        self.assertIsInstance(warnings, list)


class IoUBands(unittest.TestCase):
    def _element(self, iou: float) -> dict:
        return {
            "type": "supplier_footprint_mismatch_warning",
            "message": f"footprint mismatch (copper IoU {iou}).",
            "footprint_copper_intersection_over_union": iou,
        }

    def test_bands(self) -> None:
        for iou, expected in ((0.45, "error"), (0.6, "warning"), (0.8, "info")):
            warnings = checks.iou_warnings([self._element(iou)], PROFILE)
            self.assertEqual(len(warnings), 1, iou)
            self.assertEqual(warnings[0]["severity"], expected, iou)
            self.assertEqual(warnings[0]["kind"], "supplier_footprint_mismatch_warning")

    def test_high_iou_dropped(self) -> None:
        self.assertEqual(checks.iou_warnings([self._element(0.9)], PROFILE), [])

    def test_real_fixture_bands_as_info(self) -> None:
        warnings = checks.iou_warnings(
            load_fixture("parts-engine.circuit.json"), PROFILE
        )
        self.assertTrue(warnings)  # 0.77-ish IoU on correct 0402 parts
        self.assertTrue(all(w["severity"] == "info" for w in warnings))

    def test_iou_parsed_from_message_when_field_missing(self) -> None:
        element = {
            "type": "supplier_footprint_mismatch_warning",
            "message": "mismatch (copper IoU 0.42).",
        }
        warnings = checks.iou_warnings([element], PROFILE)
        self.assertEqual(warnings[0]["severity"], "error")


class RunTscircuitChecks(EnvGuard, unittest.TestCase):
    def test_findings_on_bad_overlap(self) -> None:
        warnings = checks.run_tscircuit_checks(FIXTURES / "bad-overlap.circuit.json")
        self.assertTrue(warnings)
        self.assertTrue(all(w["severity"] == "error" for w in warnings))
        self.assertNotIn("check_failed", {w["kind"] for w in warnings})

    def test_clean_on_good(self) -> None:
        warnings = checks.run_tscircuit_checks(FIXTURES / "good.circuit.json")
        self.assertEqual([w for w in warnings if w["kind"] != "check_failed"], [])

    def test_missing_file_becomes_check_failed(self) -> None:
        warnings = checks.run_tscircuit_checks(FIXTURES / "does-not-exist.json")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["kind"], "check_failed")
        self.assertEqual(warnings[0]["severity"], "warning")


class KicadReportParsing(unittest.TestCase):
    def test_drc_report_shape(self) -> None:
        report = {
            "violations": [
                {
                    "type": "clearance",
                    "severity": "error",
                    "description": "Clearance violation (netclass clearance 0.2mm; actual 0.1mm)",
                    "items": [{"description": "Track [GND] on F.Cu near R1"}],
                }
            ],
            "unconnected_items": [
                {
                    "type": "unconnected_items",
                    "severity": "warning",
                    "description": "Missing connection between items",
                    "items": [{"description": "Pad U3.pin7"}],
                }
            ],
        }
        warnings = checks.parse_kicad_report(report, kind="drc_violation")
        self.assertEqual(len(warnings), 2)
        self.assertEqual(warnings[0]["kind"], "drc_violation")
        self.assertEqual(warnings[0]["severity"], "error")
        self.assertIn("R1", warnings[0]["part"])
        self.assertEqual(warnings[1]["severity"], "warning")
        self.assertIn("U3.pin7", warnings[1]["part"])

    def test_erc_sheets_shape(self) -> None:
        report = {
            "sheets": [
                {
                    "violations": [
                        {
                            "type": "pin_not_driven",
                            "severity": "error",
                            "description": "Input pin not driven",
                            "items": [{"description": "Symbol U1 Pin 3"}],
                        }
                    ]
                }
            ]
        }
        warnings = checks.parse_kicad_report(report, kind="erc_violation")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["kind"], "erc_violation")

    def test_garbage_becomes_check_failed(self) -> None:
        warnings = checks.parse_kicad_report("{not json", kind="drc_violation")
        self.assertEqual(warnings[0]["kind"], "check_failed")

    def test_unknown_severity_maps_to_info(self) -> None:
        report = {"violations": [{"severity": "exclusion", "description": "x"}]}
        warnings = checks.parse_kicad_report(report, kind="drc_violation")
        self.assertEqual(warnings[0]["severity"], "info")


class DfmGate(unittest.TestCase):
    def _board(self, width: float = 20, height: float = 20, thickness: float = 1.6) -> dict:
        return {
            "type": "pcb_board",
            "width": width,
            "height": height,
            "thickness": thickness,
            "num_layers": 2,
            "center": {"x": 0, "y": 0},
        }

    def test_clean_geometry_passes(self) -> None:
        cj = [
            self._board(),
            {
                "type": "pcb_trace",
                "route": [{"route_type": "wire", "width": 0.2, "x": 0, "y": 0}],
            },
        ]
        self.assertEqual(checks.dfm_warnings(cj, _product(), PROFILE), [])

    def test_thin_trace_blocks(self) -> None:
        cj = [
            self._board(),
            {
                "type": "pcb_trace",
                "route": [{"route_type": "wire", "width": 0.08, "x": 0, "y": 0}],
            },
        ]
        warnings = checks.dfm_warnings(cj, _product(), PROFILE)
        self.assertEqual(warnings[0]["kind"], "dfm_trace_width")
        self.assertEqual(warnings[0]["severity"], "error")

    def test_narrow_trace_warns(self) -> None:
        cj = [
            self._board(),
            {
                "type": "pcb_trace",
                "route": [{"route_type": "wire", "width": 0.13, "x": 0, "y": 0}],
            },
        ]
        warnings = checks.dfm_warnings(cj, _product(), PROFILE)
        self.assertEqual(warnings[0]["severity"], "warning")

    def test_small_drill_blocks(self) -> None:
        cj = [self._board(), {"type": "pcb_via", "hole_diameter": 0.2, "outer_diameter": 0.6, "x": 0, "y": 0}]
        kinds = {w["kind"] for w in checks.dfm_warnings(cj, _product(), PROFILE)}
        self.assertIn("dfm_drill_size", kinds)

    def test_small_via_diameter_blocks(self) -> None:
        cj = [self._board(), {"type": "pcb_via", "hole_diameter": 0.1, "outer_diameter": 0.2, "x": 0, "y": 0}]
        kinds = {w["kind"] for w in checks.dfm_warnings(cj, _product(), PROFILE)}
        self.assertIn("dfm_via_diameter", kinds)

    def test_legal_but_thin_via_warns_not_blocks(self) -> None:
        """A via at JLC's floor is legal. Blocking it would flag every routed
        board (the router's own vias are fine) and train everyone to ignore DFM."""
        cj = [self._board(), {"type": "pcb_via", "hole_diameter": 0.2, "outer_diameter": 0.35, "x": 0, "y": 0}]
        warnings = [w for w in checks.dfm_warnings(cj, _product(), PROFILE)
                    if w["kind"].startswith("dfm_")]
        self.assertTrue(warnings, "expected advisory DFM warnings")
        self.assertEqual({w["severity"] for w in warnings}, {"warning"})

    def test_via_not_judged_by_pth_annular_rule(self) -> None:
        """Regression: vias were checked against the PTH annular-ring spec
        (0.2mm), which no ordinary via meets."""
        cj = [self._board(), {"type": "pcb_via", "hole_diameter": 0.3, "outer_diameter": 0.6, "x": 0, "y": 0}]
        blocking = [w for w in checks.dfm_warnings(cj, _product(), PROFILE)
                    if w["severity"] == "error"]
        self.assertEqual(blocking, [])

    def test_thin_annular_ring_blocks(self) -> None:
        cj = [
            self._board(),
            {"type": "pcb_plated_hole", "hole_diameter": 0.6, "outer_diameter": 0.8, "x": 0, "y": 0},
        ]
        kinds = {w["kind"] for w in checks.dfm_warnings(cj, _product(), PROFILE)}
        self.assertIn("dfm_annular_ring", kinds)

    def test_tiny_board_blocks(self) -> None:
        cj = [self._board(width=2, height=2)]
        kinds = {w["kind"] for w in checks.dfm_warnings(cj, _product(), PROFILE)}
        self.assertIn("dfm_board_size", kinds)

    def test_envelope_exceeded_blocks(self) -> None:
        cj = [self._board(width=80, height=20)]
        warnings = checks.dfm_warnings(cj, _product(envelope=(60, 40)), PROFILE)
        kinds = {w["kind"] for w in warnings}
        self.assertIn("board_exceeds_envelope", kinds)
        self.assertTrue(
            all(w["severity"] == "error" for w in warnings if w["kind"] == "board_exceeds_envelope")
        )

    def test_nonstandard_thickness_warns(self) -> None:
        cj = [self._board(thickness=1.4)]
        warnings = checks.dfm_warnings(cj, _product(), PROFILE)
        self.assertEqual(warnings[0]["kind"], "dfm_thickness")
        self.assertEqual(warnings[0]["severity"], "warning")

    def test_edge_clearance_blocks(self) -> None:
        cj = [
            self._board(),
            {"type": "pcb_smtpad", "x": 9.9, "y": 0, "width": 0.5, "height": 0.5},
        ]
        kinds = {w["kind"] for w in checks.dfm_warnings(cj, _product(), PROFILE)}
        self.assertIn("dfm_edge_clearance", kinds)

    def test_real_good_fixture_only_thickness_warning(self) -> None:
        # The bench good board ships the toolchain default 1.4mm thickness.
        warnings = checks.dfm_warnings(
            load_fixture("good.circuit.json"), _product(), PROFILE
        )
        self.assertEqual({w["kind"] for w in warnings}, {"dfm_thickness"})

    def test_never_raises_on_garbage(self) -> None:
        warnings = checks.dfm_warnings(
            [{"type": "pcb_board", "width": "x"}], _product(), PROFILE  # type: ignore[list-item]
        )
        self.assertIsInstance(warnings, list)


class BomGate(unittest.TestCase):
    def test_missing_lcsc_blocks_assembly(self) -> None:
        rows = [{"designator": "R1", "lcsc": ""}]
        warnings = checks.bom_gate(rows, assembly=True)
        self.assertEqual(warnings[0]["kind"], "part_not_orderable")
        self.assertEqual(warnings[0]["severity"], "error")
        self.assertEqual(warnings[0]["part"], "R1")

    def test_missing_lcsc_advises_bare_pcb(self) -> None:
        warnings = checks.bom_gate([{"designator": "R1", "lcsc": ""}], assembly=False)
        self.assertEqual(warnings[0]["severity"], "info")

    def test_copper_features_are_not_unorderable_parts(self) -> None:
        """Test points, fiducials and mounting holes are copper, not parts.

        Regression: blocking on them made test points impossible to add, while
        the review panel's testability lens asks for one on every rail.
        """
        rows = [
            {"designator": "TP1", "comment": "testpoint", "lcsc": ""},
            {"designator": "H1", "footprint": "mounting hole", "lcsc": ""},
            {"designator": "FID1", "comment": "fiducial", "lcsc": ""},
        ]
        self.assertEqual(checks.bom_gate(rows, assembly=True), [])

    def test_a_real_part_still_blocks(self) -> None:
        rows = [
            {"designator": "TP1", "comment": "testpoint", "lcsc": ""},
            {"designator": "R5", "comment": "4.7k", "lcsc": ""},
        ]
        warnings = checks.bom_gate(rows, assembly=True)
        self.assertEqual([w["part"] for w in warnings], ["R5"])

    def test_part_drift_against_lock(self) -> None:
        rows = [
            {"designator": "R1", "lcsc": "C111", "lock": {"lcsc": "C222", "basic": True}}
        ]
        warnings = checks.bom_gate(rows, assembly=True)
        self.assertEqual(warnings[0]["kind"], "part_drift")
        self.assertIn("C111", warnings[0]["detail"])
        self.assertIn("C222", warnings[0]["detail"])

    def test_extended_part_advises(self) -> None:
        rows = [
            {"designator": "U1", "lcsc": "C333", "lock": {"lcsc": "C333", "basic": False}}
        ]
        warnings = checks.bom_gate(rows, assembly=True)
        self.assertEqual(warnings[0]["kind"], "extended_part")
        self.assertEqual(warnings[0]["severity"], "info")

    def test_clean_locked_rows_pass(self) -> None:
        rows = [
            {"designator": "R1", "lcsc": "C111", "lock": {"lcsc": "C111", "basic": True}}
        ]
        self.assertEqual(checks.bom_gate(rows, assembly=True), [])


class Dedupe(unittest.TestCase):
    def test_exact_duplicates_dropped_order_kept(self) -> None:
        a = {"part": "R1", "kind": "k", "detail": "d", "severity": "error"}
        b = {"part": "R2", "kind": "k", "detail": "d", "severity": "error"}
        out = checks.dedupe([a, dict(a), b])
        self.assertEqual(out, [a, b])


if __name__ == "__main__":
    unittest.main()
