"""fab.py — profile, packet writers, ORDER.md, fab-ready rule."""

from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import circuitproj  # noqa: E402  (sys.path bootstrap)

from circuitpy import fab  # noqa: E402
from circuitpy.errors import ProjectShapeError  # noqa: E402

PROFILE = fab.get_profile("jlcpcb")

EXPORTER_BOM_WITH_PARTS = (
    "Designator,Comment,Value,Footprint,JLCPCB Part #\n"
    "R1,1k,1k,0402,C11702\n"
    "LED1,red,,0402,C965793\n"
)
EXPORTER_BOM_OFFLINE = (
    "Designator,Comment,Value,Footprint\nR1,1k,1k,\nLED1,,,\n"
)
EXPORTER_CPL = (
    "Designator,Mid X,Mid Y,Layer,Rotation\n"
    "R1,-5.000,0.000,top,0\nLED1,5.000,0.000,top,0\n"
)


class Profiles(unittest.TestCase):
    def test_jlcpcb_exists(self) -> None:
        self.assertEqual(PROFILE.id, "jlcpcb")
        self.assertEqual(PROFILE.standard_thickness_mm, 1.6)
        # Block at the fab's floor, warn at our preference (see FabProfile).
        self.assertEqual(PROFILE.min_trace_mm, 0.10)
        self.assertEqual(PROFILE.warn_trace_mm, 0.15)
        self.assertEqual(PROFILE.min_via_to_copper_mm, 0.20)

    def test_unknown_profile_raises(self) -> None:
        with self.assertRaises(ProjectShapeError):
            fab.get_profile("oshpark")

    def test_product_clearance_tightens_kicad_rules_but_keeps_engine_slack(self) -> None:
        project = json.loads(fab.kicad_project_json(PROFILE, min_clearance_mm=0.15))
        self.assertEqual(
            project["board"]["design_settings"]["rules"]["min_clearance"], 0.14
        )
        self.assertEqual(project["net_settings"]["classes"][0]["clearance"], 0.14)


class ExporterParsing(unittest.TestCase):
    def test_bom_with_part_numbers(self) -> None:
        rows = fab.parse_exporter_bom(EXPORTER_BOM_WITH_PARTS)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["designator"], "R1")
        self.assertEqual(rows[0]["lcsc"], "C11702")
        self.assertEqual(rows[0]["footprint"], "0402")

    def test_offline_bom_has_empty_lcsc(self) -> None:
        rows = fab.parse_exporter_bom(EXPORTER_BOM_OFFLINE)
        self.assertEqual([row["lcsc"] for row in rows], ["", ""])

    def test_rows_without_designator_skipped(self) -> None:
        rows = fab.parse_exporter_bom("Designator,Comment\n,orphan\nR1,ok\n")
        self.assertEqual(len(rows), 1)


class DnpFiltering(unittest.TestCase):
    def test_compiled_dnp_identity_filters_bom_and_cpl_without_prefix_guesses(self) -> None:
        elements = [
            {"type": "source_component", "source_component_id": "s1", "name": "N1"},
            {"type": "source_component", "source_component_id": "s2", "name": "N99"},
            {"type": "source_component", "source_component_id": "s3", "name": "TP1"},
            {"type": "pcb_component", "source_component_id": "s1", "do_not_place": True},
            {"type": "pcb_component", "source_component_id": "s2", "do_not_place": False},
            {"type": "pcb_component", "source_component_id": "s3", "do_not_place": False},
        ]
        excluded = fab.do_not_place_designators(elements)
        self.assertEqual(excluded, {"N1"})

        bom = fab.parse_exporter_bom(
            "Designator,Comment,Value,Footprint,JLCPCB Part #\n"
            "N1,hidden,,,\n"
            "N99,real-node,,0402,C11702\n"
            "TP1,assembled-probe,,SMD,C1\n"
        )
        kept_bom = fab.exclude_designators_from_bom(bom, excluded)
        self.assertEqual(
            [row["designator"] for row in kept_bom], ["N99", "TP1"]
        )

        cpl = (
            "Designator,Mid X,Mid Y,Layer,Rotation\n"
            "N1,0,0,top,0\n"
            "N99,1,0,top,0\n"
            "TP1,2,0,top,0\n"
        )
        kept_cpl = list(csv.DictReader(io.StringIO(
            fab.exclude_designators_from_cpl(cpl, excluded)
        )))
        self.assertEqual(
            [row["Designator"] for row in kept_cpl], ["N99", "TP1"]
        )


class LockMerge(unittest.TestCase):
    def test_lock_fills_missing_lcsc(self) -> None:
        rows = fab.parse_exporter_bom(EXPORTER_BOM_OFFLINE)
        merged = fab.merge_parts_lock(rows, {"R1": {"lcsc": "C11702", "basic": True}})
        self.assertEqual(merged[0]["lcsc"], "C11702")
        self.assertEqual(merged[0]["lock_id"], "R1")
        self.assertEqual(merged[1]["lcsc"], "")

    def test_lock_never_overrides_exporter_lcsc(self) -> None:
        rows = fab.parse_exporter_bom(EXPORTER_BOM_WITH_PARTS)
        merged = fab.merge_parts_lock(rows, {"R1": {"lcsc": "C999"}})
        self.assertEqual(merged[0]["lcsc"], "C11702")  # drift is the BOM gate's call

    def test_match_is_case_insensitive(self) -> None:
        merged = fab.merge_parts_lock(
            [{"designator": "r1", "comment": "", "value": "", "footprint": "", "lcsc": ""}],
            {"R1": {"lcsc": "C1"}},
        )
        self.assertEqual(merged[0]["lcsc"], "C1")


class BomSummary(unittest.TestCase):
    def test_summary_counts(self) -> None:
        rows = [
            {"designator": "R1", "lcsc": "C1", "lock": {"basic": True, "price": 0.01}},
            {"designator": "U1", "lcsc": "C2", "lock": {"basic": False, "price": 1.5}},
            {"designator": "J1", "lcsc": ""},
        ]
        summary = fab.bom_summary(rows)
        self.assertEqual(summary["lines"], 3)
        self.assertEqual(summary["orderable"], 2)
        self.assertEqual(summary["basicParts"], 1)
        self.assertEqual(summary["estimatedCostUsd"], 1.51)

    def test_cost_omitted_when_unpriced(self) -> None:
        summary = fab.bom_summary([{"designator": "R1", "lcsc": "C1"}])
        self.assertNotIn("estimatedCostUsd", summary)


class PacketWriters(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_bom_csv_columns_exact(self) -> None:
        rows = fab.merge_parts_lock(
            fab.parse_exporter_bom(EXPORTER_BOM_WITH_PARTS), {}
        )
        path = fab.write_bom_csv(rows, self.dir / "bom.csv", PROFILE)
        parsed = list(csv.reader(io.StringIO(path.read_text())))
        self.assertEqual(parsed[0], list(PROFILE.bom_columns))
        self.assertEqual(parsed[1], ["1k", "R1", "0402", "C11702"])

    def test_cpl_csv_columns_exact(self) -> None:
        path = fab.write_cpl_csv(EXPORTER_CPL, self.dir / "cpl.csv", PROFILE)
        parsed = list(csv.reader(io.StringIO(path.read_text())))
        self.assertEqual(parsed[0], list(PROFILE.cpl_columns))
        self.assertEqual(parsed[1][0], "R1")
        self.assertEqual(len(parsed), 3)

    def test_repackage_keeps_only_gerber_members(self) -> None:
        source = self.dir / "src.zip"
        with zipfile.ZipFile(source, "w") as zf:
            zf.writestr("F_Cu.gbr", "gerber")
            zf.writestr("drill.drl", "drill")
            zf.writestr("bom.csv", "bom")
            zf.writestr("pick_and_place.csv", "cpl")
        dest = fab.repackage_gerbers(source, self.dir / "gerbers.zip")
        with zipfile.ZipFile(dest) as zf:
            self.assertEqual(sorted(zf.namelist()), ["F_Cu.gbr", "drill.drl"])

    def test_zip_directory_gerbers(self) -> None:
        gerber_dir = self.dir / "g"
        gerber_dir.mkdir()
        (gerber_dir / "board-F_Cu.gbr").write_text("x")
        (gerber_dir / "board.drl").write_text("y")
        dest = fab.zip_directory_gerbers(gerber_dir, self.dir / "out.zip")
        with zipfile.ZipFile(dest) as zf:
            self.assertEqual(len(zf.namelist()), 2)


class OrderMd(unittest.TestCase):
    def _write(self, *, assembly: bool, assembly_tier: str = "economic") -> str:
        with tempfile.TemporaryDirectory() as tmp:
            path = fab.write_order_md(
                Path(tmp) / "ORDER.md",
                product_name="desk-air-monitor",
                assembly=assembly,
                profile=PROFILE,
                board_width_mm=58.4,
                board_height_mm=38.0,
                layers=2,
                bom={"lines": 14, "orderable": 14, "basicParts": 9, "estimatedCostUsd": 11.2},
                assembly_tier=assembly_tier,
            )
            return path.read_text(encoding="utf-8")

    def test_assembly_walkthrough_exact_clicks(self) -> None:
        text = self._write(assembly=True)
        self.assertIn("cart.jlcpcb.com/quote", text)
        self.assertIn("Add gerber file", text)
        self.assertIn("PCB Assembly", text)
        self.assertIn("PCBA Type **Economic**, Assembly Side **Top**", text)
        self.assertIn("Process BOM & CPL", text)
        self.assertIn("placement preview", text)
        self.assertIn("safety net", text)  # the placement-preview warning
        self.assertIn("pin-1 orientation", text)
        self.assertIn("$4-20", text)
        self.assertIn("$75-110", text)
        self.assertIn("14/14", text)

    def test_standard_assembly_walkthrough_selects_both_sides(self) -> None:
        text = self._write(assembly=True, assembly_tier="standard")
        self.assertIn("JLCPCB standard PCBA", text)
        self.assertIn("PCBA Type **Standard**, Assembly Side **Both Sides**", text)

    def test_bare_pcb_walkthrough_skips_assembly(self) -> None:
        text = self._write(assembly=False)
        self.assertIn("bare-PCB order", text)
        self.assertNotIn("Process BOM & CPL", text)


class FabReady(unittest.TestCase):
    def test_requires_kicad_gerbers(self) -> None:
        self.assertFalse(fab.fab_ready([], "tscircuit"))
        self.assertTrue(fab.fab_ready([], "kicad-cli"))

    def test_error_warning_blocks(self) -> None:
        warnings = [{"part": "R1", "kind": "x", "detail": "d", "severity": "error"}]
        self.assertFalse(fab.fab_ready(warnings, "kicad-cli"))

    def test_warning_and_info_do_not_block(self) -> None:
        warnings = [
            {"part": "R1", "kind": "x", "detail": "d", "severity": "warning"},
            {"part": "R2", "kind": "y", "detail": "d", "severity": "info"},
        ]
        self.assertTrue(fab.fab_ready(warnings, "kicad-cli"))


if __name__ == "__main__":
    unittest.main()
