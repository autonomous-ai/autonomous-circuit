"""checks.py — harvesters over real built circuit.json fixtures + synthetic
geometry. Every harvester must be never-raise."""

from __future__ import annotations

import math
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import circuitproj  # noqa: E402
from circuitproj import EnvGuard, FIXTURES, load_fixture  # noqa: E402

from circuitpy import checks, fab  # noqa: E402
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


def _routed_rail(
    *,
    net: str = "V5",
    width: float = 0.2,
    is_power: bool = True,
    is_ground: bool = False,
    poured: bool = False,
) -> list[dict]:
    """One rail net + one signal net, each routed at ``width``."""
    elements: list[dict] = [
        {"type": "source_net", "source_net_id": "source_net_1", "name": net,
         "is_power": is_power, "is_ground": is_ground},
        {"type": "source_net", "source_net_id": "source_net_2", "name": "BTN1",
         "is_power": False, "is_ground": False},
        {"type": "source_trace", "source_trace_id": "source_trace_1",
         "connected_source_net_ids": ["source_net_1"]},
        {"type": "source_trace", "source_trace_id": "source_trace_2",
         "connected_source_net_ids": ["source_net_2"]},
        {"type": "pcb_trace", "source_trace_id": "source_trace_1",
         "route": [{"width": width}, {"width": 0.8}]},
        {"type": "pcb_trace", "source_trace_id": "source_trace_2",
         "route": [{"width": width}]},
    ]
    if poured:
        elements.append(
            {"type": "pcb_copper_pour", "pcb_copper_pour_id": "pcb_copper_pour_1",
             "source_net_id": "source_net_1"}
        )
    return elements


class PowerWidthFloor(unittest.TestCase):
    """Ledger #31: power at signal width warns; a signal at signal width is fine."""

    def test_rail_at_signal_width_warns_once(self) -> None:
        warnings = checks.power_width_warnings(_routed_rail(), PROFILE)
        self.assertEqual([w["kind"] for w in warnings], ["dfm_power_trace_width"])
        self.assertEqual(warnings[0]["part"], "V5")
        self.assertEqual(warnings[0]["severity"], "warning")
        self.assertIn("0.2mm", warnings[0]["detail"])
        self.assertIn("0.5mm", warnings[0]["detail"])

    def test_rail_at_the_floor_is_silent(self) -> None:
        warnings = checks.power_width_warnings(_routed_rail(width=0.5), PROFILE)
        self.assertEqual(warnings, [])

    def test_signal_net_never_flagged(self) -> None:
        warnings = checks.power_width_warnings(
            _routed_rail(net="LED_DATA", is_power=False), PROFILE
        )
        self.assertEqual(warnings, [])

    def test_name_marks_a_rail_when_flags_are_absent(self) -> None:
        warnings = checks.power_width_warnings(
            _routed_rail(net="VCC", is_power=False), PROFILE
        )
        self.assertEqual(len(warnings), 1)

    def test_routed_ground_warns(self) -> None:
        warnings = checks.power_width_warnings(
            _routed_rail(net="GND", is_power=False, is_ground=True), PROFILE
        )
        self.assertEqual(len(warnings), 1)

    def test_poured_ground_is_exempt(self) -> None:
        warnings = checks.power_width_warnings(
            _routed_rail(net="GND", is_power=False, is_ground=True, poured=True),
            PROFILE,
        )
        self.assertEqual(warnings, [])

    def test_never_raises_on_garbage(self) -> None:
        warnings = checks.power_width_warnings([{"type": None}, "junk"], PROFILE)  # type: ignore[list-item]
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


class KicadRepeatCollapsing(unittest.TestCase):
    """One line per rule that floods, and per-instance lines for one that does
    not. Measured on a real board: 529 KiCad findings became 26, which is the
    difference between a verdict an agent reads and one it skims."""

    @staticmethod
    def _report(count: int, *, type_tag: str = "endpoint_off_grid") -> dict:
        return {
            "violations": [
                {
                    "type": type_tag,
                    "severity": "warning",
                    "description": "Symbol pin or wire end off connection grid",
                    "items": [{"description": f"Pad R{i}.pin1"}],
                }
                for i in range(1, count + 1)
            ]
        }

    def test_a_rule_that_floods_becomes_one_line_with_its_count(self) -> None:
        warnings = checks.parse_kicad_report(self._report(133), kind="drc_violation")
        self.assertEqual(len(warnings), 1)
        self.assertIn("x133", warnings[0]["detail"])
        self.assertIn("endpoint_off_grid", warnings[0]["detail"])

    def test_the_collapsed_line_still_says_what_the_rule_was(self) -> None:
        detail = checks.parse_kicad_report(self._report(10), kind="drc_violation")[0]["detail"]
        self.assertIn("off connection grid", detail)   # the example survives
        self.assertIn("R1", detail)                    # and where to look
        self.assertIn("kicad report", detail)          # and where the rest are

    def test_a_rule_that_fires_twice_is_two_problems_and_stays_split(self) -> None:
        """Below the threshold each violation keeps its own line and its own
        location. A rule that fires twice really is two problems."""
        warnings = checks.parse_kicad_report(self._report(2), kind="drc_violation")
        self.assertEqual(len(warnings), 2)
        self.assertNotIn("x2", warnings[0]["detail"])

    def test_severity_is_part_of_the_group_so_an_error_never_hides_in_infos(self) -> None:
        """`clearance` keeps whatever severity KiCad gave it — unlike the
        noise-floor tags, which `_kicad_severity` pins to info. One blocking
        clearance among seven advisory ones must stay visible as its own line."""
        report = self._report(8, type_tag="clearance")
        report["violations"][0]["severity"] = "error"
        warnings = checks.parse_kicad_report(report, kind="drc_violation")
        errors = [w for w in warnings if w["severity"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertNotIn("x8", errors[0]["detail"])

    def test_different_rules_never_merge(self) -> None:
        report = {
            "violations": (
                self._report(6, type_tag="silk_overlap")["violations"]
                + self._report(6, type_tag="lib_symbol_issues")["violations"]
            )
        }
        warnings = checks.parse_kicad_report(report, kind="drc_violation")
        tags = {w["detail"].split("]")[0] for w in warnings}
        self.assertEqual(len(warnings), 2)
        self.assertEqual(len(tags), 2)

    def test_nothing_is_dropped_only_folded(self) -> None:
        """Every rule that fired still appears. Collapsing is not filtering —
        silence would read as a pass."""
        report = {
            "violations": (
                self._report(20, type_tag="a")["violations"]
                + self._report(1, type_tag="b")["violations"]
                + self._report(5, type_tag="c")["violations"]
            )
        }
        warnings = checks.parse_kicad_report(report, kind="drc_violation")
        tags = {w["detail"][1:w["detail"].index("]")] for w in warnings}
        self.assertEqual(tags, {"a", "b", "c"})


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

    def test_board_over_the_sample_tier_names_the_money(self) -> None:
        # Ledger #30: 112x90 misses the $2 tier by 12mm and quoted $8.90.
        cj = [self._board(width=112, height=90)]
        warnings = [w for w in checks.dfm_warnings(cj, _product(envelope=(112, 90)), PROFILE)
                    if w["kind"] == "dfm_price_tier"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["severity"], "warning")
        self.assertIn("12mm", warnings[0]["detail"])
        self.assertIn("$2", warnings[0]["detail"])
        self.assertIn("$8.90", warnings[0]["detail"])

    def test_board_inside_the_tier_is_silent(self) -> None:
        cj = [self._board(width=100, height=100)]
        warnings = [w for w in checks.dfm_warnings(cj, _product(envelope=(100, 100)), PROFILE)
                    if w["kind"] == "dfm_price_tier"]
        self.assertEqual(warnings, [])

    def test_tier_fit_counts_either_orientation(self) -> None:
        cj = [self._board(width=80, height=100)]
        warnings = [w for w in checks.dfm_warnings(cj, _product(envelope=(80, 100)), PROFILE)
                    if w["kind"] == "dfm_price_tier"]
        self.assertEqual(warnings, [])

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


class PadShape(unittest.TestCase):
    """What the hole-clearance gate thinks a pad is.

    Ledger #41: it thought every pad was the stadium inscribed in its width
    and height, on the x-axis. That is the right shape for exactly one of the
    five kinds of pad a board carries, and being wrong about the other four is
    what produced the last blocking finding on terminal-keyboard — plus three
    blind spots nobody had looked for.
    """

    def _hole_gap(self, pad: dict, x: float, y: float,
                  drill: float = 0.3) -> float:
        """The clearance the gate measures between ``pad`` and a drill at
        ``(x, y)``, read back out of the finding it writes."""
        # The pad is on its own net. Without that, copper inside the via's
        # annular ring is exempt (it is the ring, not a clearance), and the
        # geometry under test here never reaches the rule.
        pad = {**pad, "pcb_port_id": "pp1", "layer": "top"}
        elements = [
            {"type": "pcb_board", "width": 20, "height": 20, "thickness": 1.6,
             "center": {"x": 0, "y": 0}},
            pad,
            {"type": "pcb_port", "pcb_port_id": "pp1", "source_port_id": "sp1",
             "x": 0.0, "y": 0.0},
            {"type": "source_port", "source_port_id": "sp1", "name": "VCC",
             "subcircuit_connectivity_map_key": "pad_net"},
            {"type": "pcb_via", "pcb_via_id": "v1", "x": x, "y": y,
             "hole_diameter": drill, "outer_diameter": drill + 0.3,
             "subcircuit_connectivity_map_key": "other_net"},
        ]
        found = [w for w in checks.dfm_warnings(elements, _product(), PROFILE)
                 if w["kind"] == "dfm_hole_clearance"]
        if not found:
            return math.inf
        return float(re.search(r"passes ([\d.]+)mm", found[0]["detail"]).group(1))

    def test_a_rotated_pill_is_measured_where_its_copper_is(self) -> None:
        """U4 on terminal-keyboard: 2.25 x 0.63mm at 90 degrees, so the copper
        is 2.25mm tall and 0.63mm wide, not the other way round."""
        pad = {"type": "pcb_smtpad", "pcb_smtpad_id": "p1",
               "shape": "rotated_pill", "x": 0.0, "y": 0.0,
               "width": 2.25, "height": 0.63, "ccw_rotation": 90.0}
        # Off the pad's long end — the near side once the rotation is read.
        self.assertAlmostEqual(self._hole_gap(pad, 0.0, 1.405), 0.130, places=3)
        # Off its narrow side, at the same distance from the centre: clear.
        self.assertEqual(self._hole_gap(pad, -1.405, 0.0), math.inf)

    def test_a_rectangle_keeps_its_corners(self) -> None:
        """The inscribed stadium rounds 1.0mm square pad corners inward by
        0.207mm, which is more than the whole clearance rule."""
        pad = {"type": "pcb_smtpad", "pcb_smtpad_id": "p1", "shape": "rect",
               "x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
        # Diagonally off the corner (0.5, 0.5), 0.1mm of copper-to-drill gap.
        corner = 0.5 + (0.15 + 0.1) / math.sqrt(2)
        self.assertLess(self._hole_gap(pad, corner, corner), 0.2)

    def test_a_circle_pad_is_its_own_radius(self) -> None:
        """A circle pad carries ``radius`` and no ``width``. Reading the
        radius *as* the width halved every round pad on every board — the
        DebugPort test points are 1.0mm across and were modelled at 0.5mm."""
        pad = {"type": "pcb_smtpad", "pcb_smtpad_id": "p1", "shape": "circle",
               "x": 0.0, "y": 0.0, "radius": 0.5}
        # 0.13mm from the real 0.5mm radius; 0.38mm from the modelled 0.25mm.
        self.assertAlmostEqual(self._hole_gap(pad, 0.78, 0.0), 0.130, places=3)

    def test_a_polygon_pad_is_measured_at_all(self) -> None:
        """A polygon pad has no width or height, so it failed the numeric
        guard and was dropped — including the four shell pads on every USB-C
        receptacle we ship, which sit beside the drills this gate exists for."""
        pad = {"type": "pcb_smtpad", "pcb_smtpad_id": "p1", "shape": "polygon",
               "points": [{"x": -0.5, "y": -0.5}, {"x": 0.5, "y": -0.5},
                          {"x": 0.5, "y": 0.5}, {"x": -0.5, "y": 0.5}]}
        self.assertAlmostEqual(self._hole_gap(pad, 0.0, 0.78), 0.130, places=3)

    def test_edge_clearance_reads_the_same_shapes(self) -> None:
        """The board-edge rule had the identical blind spot: a polygon pad has
        no width, no height and no centre, so it was skipped — on a board whose
        USB-C shell pads are polygons and sit at the edge by design."""
        overhang = [
            {"type": "pcb_board", "width": 20, "height": 20, "thickness": 1.6,
             "center": {"x": 0, "y": 0}},
            {"type": "pcb_smtpad", "pcb_smtpad_id": "p1", "shape": "polygon",
             "points": [{"x": 9.9, "y": -0.5}, {"x": 10.4, "y": -0.5},
                        {"x": 10.4, "y": 0.5}, {"x": 9.9, "y": 0.5}]},
        ]
        found = [w for w in checks.dfm_warnings(overhang, _product(), PROFILE)
                 if w["kind"] == "dfm_edge_clearance"]
        self.assertEqual([w["severity"] for w in found], ["error"])

    def test_an_unrotated_pill_is_unchanged(self) -> None:
        """The one shape the old model got right stays right, to the digit."""
        self.assertEqual(
            checks._stadium(1.0, 2.0, 2.25, 0.63),
            checks._stadium(1.0, 2.0, 2.25, 0.63, 0.0),
        )
        ax, ay, bx, by, r = checks._stadium(0.0, 0.0, 2.25, 0.63)
        self.assertAlmostEqual(r, 0.315)
        self.assertAlmostEqual(bx - ax, 1.62)
        self.assertAlmostEqual(ay, 0.0)
        self.assertAlmostEqual(by, 0.0)


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


class PowerWidthMeasuresTheRailNotTheTaper(unittest.TestCase):
    """`dfm_power_trace_width` used to be unsatisfiable by construction.

    The router **tapers into a terminal pad narrower than the track** — that is
    a documented behaviour of the shipped autorouter, not a defect — so the
    minimum width over a whole route is always the pad, and on a board whose
    pads are 0.2mm (every 0402, every QFN) the minimum can never reach a 0.5mm
    floor. The check reported that minimum, so it fired on every board forever
    whatever anyone did. Measured 2026-08-17: with V3_3 declared at its own
    measured 0.4mm ceiling, terminal-keyboard still reported "routed at
    0.2125mm" — the taper, not the rail.
    """

    def _board(self, run_width: float, pad_width: float) -> list[dict]:
        # A 10mm rail: wide along its run, tapering to the pad over the last
        # half-millimetre at each end.
        route = [{"x": 0.0, "y": 0.0, "width": pad_width, "route_type": "wire", "layer": "top"}]
        route.append({"x": 0.5, "y": 0.0, "width": pad_width, "route_type": "wire", "layer": "top"})
        for x in (2.0, 4.0, 6.0, 8.0):
            route.append({"x": x, "y": 0.0, "width": run_width, "route_type": "wire", "layer": "top"})
        route.append({"x": 9.5, "y": 0.0, "width": pad_width, "route_type": "wire", "layer": "top"})
        route.append({"x": 10.0, "y": 0.0, "width": pad_width, "route_type": "wire", "layer": "top"})
        return [
            {"type": "source_net", "source_net_id": "net_1", "name": "V5", "is_power": True},
            {"type": "source_trace", "source_trace_id": "t1", "connected_source_net_ids": ["net_1"]},
            {"type": "pcb_trace", "pcb_trace_id": "pt1", "source_trace_id": "t1", "route": route},
        ]

    def test_a_wide_rail_that_tapers_into_its_pads_is_not_reported(self) -> None:
        profile = fab.get_profile("jlcpcb")
        wide = self._board(run_width=float(profile.warn_power_trace_mm), pad_width=0.2)
        self.assertEqual(checks.power_width_warnings(wide, profile), [])

    def test_a_thin_rail_is_still_reported_and_names_both_numbers(self) -> None:
        profile = fab.get_profile("jlcpcb")
        thin = self._board(run_width=0.2, pad_width=0.15)
        found = checks.power_width_warnings(thin, profile)
        self.assertEqual(len(found), 1, found)
        detail = found[0]["detail"]
        self.assertIn("runs at 0.2mm", detail)
        # And the neck is named as the router's doing rather than the board's,
        # so nobody spends a build trying to fix it.
        self.assertIn("necks to 0.15mm at its pads", detail)
        self.assertIn("not something a width can fix", detail)


class SilkscreenLegibility(unittest.TestCase):
    """Two labels printed on top of each other read as one word.

    An engineer building `usb-c-breakout` (2026-08-17) shipped a first build
    whose pad legends read "VBUSGND" — two labels fused — and the only thing
    that caught it was looking at the render. A board can be electrically
    perfect and useless at the bench because the pad you need is the one whose
    name ran into its neighbour's.
    """

    def _label(self, text: str, x: float, y: float, size: float = 1.0, **extra) -> dict:
        return {
            "type": "pcb_silkscreen_text",
            "text": text,
            "font_size": size,
            "layer": "top",
            "anchor_alignment": "center",
            "anchor_position": {"x": x, "y": y},
            **extra,
        }

    def test_two_labels_on_top_of_each_other_are_reported_with_both_names(self) -> None:
        found = checks.silk_overlap_warnings([
            self._label("VBUS", 0, 0),
            self._label("GND", 1.0, 0),
        ])
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0]["kind"], "silk_text_overlap")
        self.assertIn("VBUS", found[0]["detail"])
        self.assertIn("GND", found[0]["detail"])

    def test_labels_that_clear_each_other_are_left_alone(self) -> None:
        # A check that cries wolf about legible silkscreen is one people switch
        # off, so the model deliberately under-estimates the glyph box.
        self.assertEqual(
            checks.silk_overlap_warnings([
                self._label("VBUS", 0, 0),
                self._label("GND", 6.0, 0),
            ]),
            [],
        )
        # Different layers cannot collide.
        self.assertEqual(
            checks.silk_overlap_warnings([
                self._label("VBUS", 0, 0),
                dict(self._label("GND", 0.2, 0), layer="bottom"),
            ]),
            [],
        )

    def test_a_rotated_label_is_skipped_rather_than_approximated(self) -> None:
        # A 90-degree label's box is not this box turned, and guessing is how a
        # check earns its reputation for false positives.
        self.assertEqual(
            checks.silk_overlap_warnings([
                self._label("VBUS", 0, 0),
                self._label("GND", 0.2, 0, ccw_rotation=90),
            ]),
            [],
        )

    def test_it_never_raises_on_junk(self) -> None:
        for junk in ([], [None], [{"type": "pcb_silkscreen_text"}],
                     [{"type": "pcb_silkscreen_text", "text": "X", "font_size": "big"}]):
            self.assertEqual(checks.silk_overlap_warnings(junk), [])


class FloatingNets(unittest.TestCase):
    """A wire nobody drew leaves no element to be wrong about.

    Every other connectivity check in this stack asks whether a connection
    that exists is correct. This one asks whether a connection that should
    exist is there at all — and the first time it ran over the repo it found
    dead wiring on three boards carrying `fab.ready: true`.
    """

    @staticmethod
    def _board(traces):
        """Two parts, three nets, and whatever traces the test wants."""
        elements = [
            {"type": "source_component", "source_component_id": "sc_u3", "name": "U3"},
            {"type": "source_component", "source_component_id": "sc_j1", "name": "J1"},
            {"type": "source_port", "source_port_id": "sp_u3_dp", "source_component_id": "sc_u3"},
            {"type": "source_port", "source_port_id": "sp_u3_vcc", "source_component_id": "sc_u3"},
            {"type": "source_port", "source_port_id": "sp_j1_dp", "source_component_id": "sc_j1"},
            {"type": "source_port", "source_port_id": "sp_j1_vcc", "source_component_id": "sc_j1"},
            {"type": "source_net", "source_net_id": "sn_dp", "name": "USB_DP"},
            {"type": "source_net", "source_net_id": "sn_v5", "name": "V5"},
            {"type": "source_net", "source_net_id": "sn_tp", "name": "TP_SPARE"},
        ]
        return elements + list(traces)

    @staticmethod
    def _trace(ports, nets):
        return {
            "type": "source_trace",
            "connected_source_port_ids": list(ports),
            "connected_source_net_ids": list(nets),
        }

    def test_a_net_that_reaches_one_part_is_reported_with_the_part_named(self) -> None:
        # USB_DP is wired to the MCU and to nothing else — the exact shape of
        # sensor-node-mini, which composes usb-c-power (no data pins) and
        # leaves the RP2040's USB pair terminating in air.
        board = self._board([
            self._trace(["sp_u3_dp"], ["sn_dp"]),
            self._trace(["sp_u3_vcc", "sp_j1_vcc"], ["sn_v5"]),
        ])
        found = checks.floating_net_warnings(board)
        self.assertEqual([w["kind"] for w in found], ["net_reaches_one_part"])
        self.assertEqual(found[0]["part"], "U3")
        self.assertIn("USB_DP", found[0]["detail"])
        self.assertEqual(found[0]["severity"], "warning")

    def test_a_net_two_parts_share_is_never_reported(self) -> None:
        board = self._board([
            self._trace(["sp_u3_dp", "sp_j1_dp"], ["sn_dp"]),
            self._trace(["sp_u3_vcc", "sp_j1_vcc"], ["sn_v5"]),
        ])
        self.assertEqual(checks.floating_net_warnings(board), [])

    def test_connectivity_is_followed_through_port_to_port_traces(self) -> None:
        # A trace may join two pins and name no net at all. Counting a net's
        # own members would call this net lonely when it is fully wired: the
        # net reaches U3 directly, and J1 only through the second trace.
        board = self._board([
            self._trace(["sp_u3_dp"], ["sn_dp"]),
            self._trace(["sp_u3_dp", "sp_j1_dp"], []),
            self._trace(["sp_u3_vcc", "sp_j1_vcc"], ["sn_v5"]),
        ])
        self.assertEqual(checks.floating_net_warnings(board), [])

    def test_a_test_point_net_is_the_pad_being_the_point(self) -> None:
        board = self._board([
            self._trace(["sp_u3_dp", "sp_j1_dp"], ["sn_dp"]),
            self._trace(["sp_u3_vcc", "sp_j1_vcc"], ["sn_v5"]),
            self._trace(["sp_u3_vcc"], ["sn_tp"]),
        ])
        self.assertEqual(checks.floating_net_warnings(board), [])

    def test_a_declared_net_wired_to_nothing_is_reported_against_the_board(self) -> None:
        board = self._board([
            self._trace(["sp_u3_dp", "sp_j1_dp"], ["sn_dp"]),
            self._trace(["sp_u3_vcc", "sp_j1_vcc"], ["sn_v5"]),
        ])
        # sn_tp is exempt by name; give the board a plain one that goes nowhere.
        board.append({"type": "source_net", "source_net_id": "sn_dead", "name": "SENSOR_INT"})
        found = checks.floating_net_warnings(board)
        self.assertEqual([w["kind"] for w in found], ["net_reaches_nothing"])
        self.assertEqual(found[0]["part"], "board")

    def test_it_never_raises_on_junk(self) -> None:
        for junk in ([], [None], [{"type": "source_net"}],
                     [{"type": "source_trace", "connected_source_port_ids": None}]):
            self.assertEqual(checks.floating_net_warnings(junk), [])
