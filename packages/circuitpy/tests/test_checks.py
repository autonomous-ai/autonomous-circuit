"""checks.py — harvesters over real built circuit.json fixtures + synthetic
geometry. Every harvester must be never-raise."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

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

    def test_library_warning_does_not_become_a_blocker(self) -> None:
        output = (
            '[{"type":"pcb_trace_too_long_warning",'
            '"warning_type":"pcb_trace_too_long_warning",'
            '"message":"trace exceeds the preferred length"}]'
        )
        with mock.patch.object(checks.toolchain, "run_node", return_value=output):
            warnings = checks.run_tscircuit_checks(Path("board.circuit.json"))
        self.assertEqual(warnings[0]["kind"], "pcb_trace_too_long_warning")
        self.assertEqual(warnings[0]["severity"], "warning")

    def test_unknown_library_finding_remains_conservative(self) -> None:
        with mock.patch.object(
            checks.toolchain,
            "run_node",
            return_value='[{"type":"new_check","message":"unknown shape"}]',
        ):
            warnings = checks.run_tscircuit_checks(Path("board.circuit.json"))
        self.assertEqual(warnings[0]["severity"], "error")


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

    def test_same_net_track_may_leave_its_own_plated_hole(self) -> None:
        """A PTH annulus and its GND trace are one conductor, not a
        PTH-to-track clearance violation.

        Regression from all three example boards: after the first route point
        left a USB-C shell pad, the next segment was still inside the pad's
        copper and the net-blind check reported a 0.006mm gap.
        """
        key = "subcircuit_connectivity_net_gnd"
        cj = [
            self._board(),
            {
                "type": "source_net",
                "source_net_id": "source_net_gnd",
                "name": "GND",
                "subcircuit_connectivity_map_key": key,
            },
            {
                "type": "source_port",
                "source_port_id": "source_port_shell",
                "subcircuit_connectivity_map_key": key,
            },
            {
                "type": "pcb_port",
                "pcb_port_id": "pcb_port_shell",
                "source_port_id": "source_port_shell",
                "x": 0,
                "y": 0,
            },
            {
                "type": "pcb_plated_hole",
                "pcb_plated_hole_id": "shell_hole",
                "pcb_port_id": "pcb_port_shell",
                "x": 0,
                "y": 0,
                "hole_diameter": 0.8,
                "outer_diameter": 1.2,
            },
            {
                "type": "pcb_trace",
                "pcb_trace_id": "gnd_trace",
                "connection_name": "source_net_gnd",
                "connectsTo": ["pcb_port_shell"],
                "route": [
                    {"route_type": "wire", "x": 0, "y": 0, "width": 0.15, "layer": "top"},
                    {"route_type": "wire", "x": 0.55, "y": 0, "width": 0.15, "layer": "top"},
                    {"route_type": "wire", "x": 3, "y": 0, "width": 0.15, "layer": "top"},
                ],
            },
        ]
        warnings = checks.dfm_warnings(cj, _product(), PROFILE)
        self.assertNotIn("dfm_hole_clearance", {w["kind"] for w in warnings})

    def test_different_net_still_clears_a_plated_hole(self) -> None:
        key = "subcircuit_connectivity_net_gnd"
        cj = [
            self._board(),
            {
                "type": "source_port",
                "source_port_id": "source_port_shell",
                "subcircuit_connectivity_map_key": key,
            },
            {
                "type": "pcb_port",
                "pcb_port_id": "pcb_port_shell",
                "source_port_id": "source_port_shell",
                "x": 0,
                "y": 0,
            },
            {
                "type": "pcb_plated_hole",
                "pcb_plated_hole_id": "shell_hole",
                "pcb_port_id": "pcb_port_shell",
                "x": 0,
                "y": 0,
                "hole_diameter": 0.8,
                "outer_diameter": 1.2,
            },
            {
                "type": "pcb_trace",
                "pcb_trace_id": "signal_trace",
                "route": [
                    {"route_type": "wire", "x": -3, "y": 0.55, "width": 0.15, "layer": "top"},
                    {"route_type": "wire", "x": 3, "y": 0.55, "width": 0.15, "layer": "top"},
                ],
            },
        ]
        warnings = checks.dfm_warnings(cj, _product(), PROFILE)
        self.assertIn("dfm_hole_clearance", {w["kind"] for w in warnings})

    def test_trace_endpoint_must_reach_smd_pad_on_its_copper_layer(self) -> None:
        cj = [
            self._board(),
            {
                "type": "pcb_port",
                "pcb_port_id": "pcb_port_vbus",
                "layers": ["top"],
                "x": 2,
                "y": 1,
            },
            {
                "type": "pcb_trace",
                "pcb_trace_id": "vbus_trace",
                "route": [
                    {
                        "route_type": "wire",
                        "x": -2,
                        "y": 1,
                        "width": 0.15,
                        "layer": "bottom",
                    },
                    {
                        "route_type": "wire",
                        "x": 2,
                        "y": 1,
                        "width": 0.15,
                        "layer": "bottom",
                        "end_pcb_port_id": "pcb_port_vbus",
                    },
                ],
            },
        ]

        warnings = checks.dfm_warnings(cj, _product(), PROFILE)
        mismatch = [
            warning
            for warning in warnings
            if warning["kind"] == "pcb_trace_endpoint_layer_mismatch"
        ]
        self.assertEqual(len(mismatch), 1)
        self.assertEqual(mismatch[0]["severity"], "error")
        self.assertIn("bottom", mismatch[0]["detail"])
        self.assertIn("top", mismatch[0]["detail"])

    def test_trace_endpoint_on_matching_or_plated_layer_is_valid(self) -> None:
        for layers, trace_layer in ((["top"], "top"), (["top", "bottom"], "bottom")):
            with self.subTest(layers=layers, trace_layer=trace_layer):
                cj = [
                    self._board(),
                    {
                        "type": "pcb_port",
                        "pcb_port_id": "pcb_port_endpoint",
                        "layers": layers,
                        "x": 1,
                        "y": 1,
                    },
                    {
                        "type": "pcb_trace",
                        "pcb_trace_id": "matching_trace",
                        "route": [
                            {
                                "route_type": "wire",
                                "x": 0,
                                "y": 1,
                                "width": 0.15,
                                "layer": trace_layer,
                            },
                            {
                                "route_type": "wire",
                                "x": 1,
                                "y": 1,
                                "width": 0.15,
                                "layer": trace_layer,
                                "end_pcb_port_id": "pcb_port_endpoint",
                            },
                        ],
                    },
                ]
                warnings = checks.dfm_warnings(cj, _product(), PROFILE)
                self.assertNotIn(
                    "pcb_trace_endpoint_layer_mismatch",
                    {warning["kind"] for warning in warnings},
                )

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
            {
                "designator": "R1",
                "lcsc": "C111",
                "lock_id": "R1",
                "lock": {"lcsc": "C222", "basic": True},
            }
        ]
        warnings = checks.bom_gate(
            rows,
            assembly=True,
            parts_lock={"R1": {"lcsc": "C222", "basic": True}},
        )
        self.assertEqual(warnings[0]["kind"], "part_drift")
        self.assertEqual(warnings[0]["severity"], "error")
        self.assertIn("C111", warnings[0]["detail"])
        self.assertIn("C222", warnings[0]["detail"])
        self.assertNotIn("extended_part", {w["kind"] for w in warnings})

    def test_part_drift_only_advises_bare_pcb(self) -> None:
        rows = [
            {
                "designator": "R1",
                "lcsc": "C111",
                "lock_id": "R1",
                "lock": {"lcsc": "C222", "basic": True},
            }
        ]
        warnings = checks.bom_gate(rows, assembly=False)
        self.assertEqual(warnings[0]["kind"], "part_drift")
        self.assertEqual(warnings[0]["severity"], "warning")

    def test_extended_part_advises(self) -> None:
        rows = [
            {"designator": "U1", "lcsc": "C333", "lock": {"lcsc": "C333", "basic": False}}
        ]
        warnings = checks.bom_gate(rows, assembly=True)
        self.assertEqual(warnings[0]["kind"], "extended_part")
        self.assertEqual(warnings[0]["severity"], "info")

    def test_clean_locked_rows_pass(self) -> None:
        rows = [
            {
                "designator": "R1",
                "lcsc": "C111",
                "lock_id": "R1",
                "lock": {"lcsc": "C111", "basic": True},
            }
        ]
        self.assertEqual(
            checks.bom_gate(
                rows,
                assembly=True,
                parts_lock={"R1": {"lcsc": "C111", "basic": True}},
            ),
            [],
        )

    def test_missing_exact_lock_entry_blocks_assembly(self) -> None:
        """An exporter-supplied C-number is not a reproducible parts lock."""
        warnings = checks.bom_gate(
            [{"designator": "U1", "lcsc": "C333"}],
            assembly=True,
            parts_lock={},
        )
        self.assertEqual(
            [(w["part"], w["kind"], w["severity"]) for w in warnings],
            [("U1", "part_lock_missing", "error")],
        )

    def test_missing_exact_lock_entry_only_advises_bare_pcb(self) -> None:
        warnings = checks.bom_gate(
            [{"designator": "U1", "lcsc": "C333"}],
            assembly=False,
            parts_lock={},
        )
        self.assertEqual(warnings[0]["kind"], "part_lock_missing")
        self.assertEqual(warnings[0]["severity"], "info")

    def test_empty_lock_reports_every_populated_sourced_ref(self) -> None:
        warnings = checks.bom_gate(
            [
                {"designator": "R1", "lcsc": "C111"},
                {"designator": "U1", "lcsc": "C333"},
                {"designator": "TP1", "comment": "testpoint", "lcsc": ""},
            ],
            assembly=True,
            parts_lock={},
        )
        self.assertEqual(
            [(w["part"], w["kind"]) for w in warnings],
            [("R1", "part_lock_missing"), ("U1", "part_lock_missing")],
        )

    def test_stale_parts_lock_entry_blocks_assembly(self) -> None:
        rows = [
            {
                "designator": "R1",
                "lcsc": "C111",
                "lock_id": "R1",
                "lock": {"lcsc": "C111", "basic": True},
            }
        ]
        warnings = checks.bom_gate(
            rows,
            assembly=True,
            parts_lock={
                "R1": {"lcsc": "C111", "basic": True},
                "R99": {"lcsc": "C999", "basic": True},
            },
        )
        self.assertEqual(
            [(w["part"], w["kind"], w["severity"]) for w in warnings],
            [("R99", "part_lock_stale", "error")],
        )

    def test_stale_parts_lock_entry_only_advises_bare_pcb(self) -> None:
        warnings = checks.bom_gate(
            [
                {
                    "designator": "R1",
                    "lcsc": "C111",
                    "lock_id": "R1",
                    "lock": {"lcsc": "C111"},
                }
            ],
            assembly=False,
            parts_lock={
                "R1": {"lcsc": "C111"},
                "R99": {"lcsc": "C999"},
            },
        )
        self.assertEqual(warnings[0]["kind"], "part_lock_stale")
        self.assertEqual(warnings[0]["severity"], "info")

    def test_filtered_dnp_lock_entry_is_stale(self) -> None:
        rows = [
            {
                "designator": "R1",
                "lcsc": "C111",
                "lock_id": "R1",
                "lock": {"lcsc": "C111", "basic": True},
            }
        ]
        warnings = checks.bom_gate(
            rows,
            assembly=True,
            parts_lock={
                "R1": {"lcsc": "C111", "basic": True},
                "R2": {"lcsc": "C222", "basic": True},
            },
        )
        self.assertEqual(
            [(w["part"], w["kind"], w["severity"]) for w in warnings],
            [("R2", "part_lock_stale", "error")],
        )

    def test_parts_lock_matching_is_case_insensitive(self) -> None:
        rows = [
            {
                "designator": "r1",
                "lcsc": "C111",
                "lock_id": "R1",
                "lock": {"lcsc": "C111", "basic": True},
            }
        ]
        self.assertEqual(
            checks.bom_gate(
                rows,
                assembly=True,
                parts_lock={"r1": {"lcsc": "C111", "basic": True}},
            ),
            [],
        )

    def test_case_colliding_parts_lock_entries_block_assembly(self) -> None:
        warnings = checks.bom_gate(
            [],
            assembly=True,
            parts_lock={"R1": {"lcsc": "C111"}, "r1": {"lcsc": "C222"}},
        )
        self.assertEqual(
            [(w["part"], w["kind"], w["severity"]) for w in warnings],
            [
                ("R1", "part_lock_stale", "error"),
                ("r1", "part_lock_ambiguous", "error"),
            ],
        )

    def test_empty_parts_lock_identity_blocks_assembly(self) -> None:
        warnings = checks.bom_gate(
            [], assembly=True, parts_lock={"": {"lcsc": "C111"}}
        )
        self.assertEqual(warnings[0]["kind"], "part_lock_stale")
        self.assertEqual(warnings[0]["severity"], "error")


class Dedupe(unittest.TestCase):
    def test_exact_duplicates_dropped_order_kept(self) -> None:
        a = {"part": "R1", "kind": "k", "detail": "d", "severity": "error"}
        b = {"part": "R2", "kind": "k", "detail": "d", "severity": "error"}
        out = checks.dedupe([a, dict(a), b])
        self.assertEqual(out, [a, b])


if __name__ == "__main__":
    unittest.main()
