"""fab.py — profile, packet writers, ORDER.md, fab-ready rule."""

from __future__ import annotations

import csv
import io
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

    def test_unknown_profile_raises(self) -> None:
        with self.assertRaises(ProjectShapeError):
            fab.get_profile("oshpark")


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

    def test_a_slug_id_reaches_the_row_through_its_refdes(self) -> None:
        # parts-book's ids are readable slugs and the designators live in
        # `refdes`, so id-only matching matched nothing on the one board that
        # locks its parts: hydrate-coaster pins 19 parts and not one is
        # called "U2".
        rows = fab.parse_exporter_bom(EXPORTER_BOM_OFFLINE)
        merged = fab.merge_parts_lock(
            rows,
            {"ams1117-3.3": {"lcsc": "C6186", "package": "SOT-223", "refdes": ["R1"]}},
        )
        self.assertEqual(merged[0]["lock_id"], "ams1117-3.3")
        self.assertEqual(merged[0]["lcsc"], "C6186")

    def test_a_designator_two_locks_claim_is_left_alone(self) -> None:
        # Picking one silently is how a board gets ordered with the wrong part.
        merged = fab.merge_parts_lock(
            [{"designator": "U2", "comment": "", "value": "", "footprint": "", "lcsc": ""}],
            {
                "ldo-a": {"lcsc": "C1", "refdes": ["U2"]},
                "ldo-b": {"lcsc": "C2", "refdes": ["U2"]},
            },
        )
        self.assertNotIn("lock_id", merged[0])
        self.assertEqual(merged[0]["lcsc"], "")

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

    def test_bom_golden_file_jlc_sample_shape(self) -> None:
        """Ledger #32 golden file: the exact bytes JLC's parts-match table is
        given. Same-part designators grouped on one line, natural-sorted
        (SW9 before SW10); a part with no identity stays its own line."""
        rows = [
            {"designator": "SW10", "comment": "TS-1187A", "value": "",
             "footprint": "SMD-4P", "lcsc": "C318884"},
            {"designator": "R1", "comment": "1k", "value": "",
             "footprint": "0402", "lcsc": "C11702"},
            {"designator": "SW9", "comment": "TS-1187A", "value": "",
             "footprint": "SMD-4P", "lcsc": "C318884"},
            {"designator": "SW11", "comment": "TS-1187A", "value": "",
             "footprint": "SMD-4P", "lcsc": "C318884"},
            {"designator": "TP1", "comment": "", "value": "",
             "footprint": "", "lcsc": ""},
            {"designator": "TP2", "comment": "", "value": "",
             "footprint": "", "lcsc": ""},
        ]
        path = fab.write_bom_csv(rows, self.dir / "bom.csv", PROFILE)
        self.assertEqual(
            path.read_bytes(),
            b"Comment,Designator,Footprint,LCSC Part #\r\n"
            b'TS-1187A,"SW9,SW10,SW11",SMD-4P,C318884\r\n'
            b"1k,R1,0402,C11702\r\n"
            b",TP1,,\r\n"
            b",TP2,,\r\n",
        )

    def test_the_locked_package_fills_the_footprint_column(self) -> None:
        """JLC's parts-match table reads this column as the one cross-check
        between the number you typed and the part you meant, and 9 of 18 lines
        on harness-puck shipped with it blank."""
        rows = fab.merge_parts_lock(
            fab.parse_exporter_bom(EXPORTER_BOM_OFFLINE),
            {"rp2040": {"lcsc": "C2040", "package": "LQFN-56(7x7)", "refdes": ["R1"]}},
        )
        with tempfile.TemporaryDirectory() as scratch:
            path = fab.write_bom_csv(rows, Path(scratch) / "bom.csv", PROFILE)
            written = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
        by_designator = {row["Designator"]: row for row in written}
        self.assertEqual(by_designator["R1"]["Footprint"], "LQFN-56(7x7)")
        # And the exporter's own footprint still wins where it has one: the
        # lock says what was ordered, the exporter says what was placed, and
        # disagreeing with the artifact is not this column's job.
        rows2 = fab.merge_parts_lock(
            fab.parse_exporter_bom(EXPORTER_BOM_WITH_PARTS),
            {"rp2040": {"lcsc": "C2040", "package": "LQFN-56(7x7)", "refdes": ["R1"]}},
        )
        with tempfile.TemporaryDirectory() as scratch:
            path = fab.write_bom_csv(rows2, Path(scratch) / "bom.csv", PROFILE)
            written2 = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
        self.assertEqual({row["Designator"]: row for row in written2}["R1"]["Footprint"], "0402")

    def test_bare_copper_never_reaches_the_parts_match_table(self) -> None:
        """EE review follow-up: TP1/TP2/TP3 shipped as three BOM lines with no
        part number — three red rows in JLC's matcher that the user has to work
        out how to dismiss. A test point is copper with a name; there is
        nothing to buy."""
        rows = [
            {"designator": "R1", "comment": "1k", "value": "",
             "footprint": "0402", "lcsc": "C11702"},
            {"designator": "TP1", "comment": "test point", "value": "",
             "footprint": "", "lcsc": ""},
        ]
        described = {
            "R1": {"comment": "1k", "placeable": True},
            "TP1": {"comment": "test point", "placeable": False},
        }
        path = fab.write_bom_csv(
            rows, self.dir / "bom.csv", PROFILE, described=described,
        )
        self.assertEqual(
            path.read_bytes(),
            b"Comment,Designator,Footprint,LCSC Part #\r\n1k,R1,0402,C11702\r\n",
        )

    def test_an_unsourced_real_part_stays_on_the_bom_showing_red(self) -> None:
        """The exclusion is narrow on purpose. "No part number" is not the
        test — a real part nobody has sourced yet belongs on the BOM so the
        shortfall is visible. Dropping it would hide exactly what the packet
        exists to report."""
        rows = [{"designator": "U9", "comment": "some-mcu", "value": "",
                 "footprint": "QFN-32", "lcsc": ""}]
        described = {"U9": {"comment": "some-mcu", "placeable": True}}
        path = fab.write_bom_csv(
            rows, self.dir / "bom.csv", PROFILE, described=described,
        )
        self.assertIn(b"U9", path.read_bytes())

    def test_the_catalog_is_found_from_the_vendored_runtime_too(self) -> None:
        """The bug this pins: `catalog_root` was one hard-coded `parents[4]`
        hop, correct in the repo and wrong in `skills/circuitcode/scripts/
        packages/circuitpy/` — the runtime that actually builds boards. It
        resolved to `skills/packages/parts-catalog/catalog`, which has never
        existed, so every BOM ever shipped had a blank Footprint column and
        nothing said so."""
        self.assertIsNotNone(fab.catalog_root())
        self.assertTrue(fab._catalog_packages())

    def test_a_rotation_is_not_printed_to_fourteen_decimals(self) -> None:
        """harness-puck shipped `202.49999999999994`. It places identically,
        and a reviewer who sees that stops trusting every other number in the
        packet."""
        text = (
            "Designator,Mid X,Mid Y,Layer,Rotation\n"
            "U1,1.0,2.0,top,202.49999999999994\n"
            "U2,3.0,4.0,top,-90\n"
        )
        path = fab.write_cpl_csv(text, self.dir / "cpl.csv", PROFILE)
        rows = list(csv.DictReader(io.StringIO(path.read_text())))
        self.assertEqual(rows[0]["Rotation"], "202.5")
        self.assertEqual(rows[1]["Rotation"], "270")

    def test_cpl_csv_columns_exact(self) -> None:
        path = fab.write_cpl_csv(EXPORTER_CPL, self.dir / "cpl.csv", PROFILE)
        parsed = list(csv.reader(io.StringIO(path.read_text())))
        self.assertEqual(parsed[0], list(PROFILE.cpl_columns))
        self.assertEqual(parsed[1][0], "R1")
        self.assertEqual(len(parsed), 3)

    def test_cpl_golden_file_jlc_sample_shape(self) -> None:
        """Ledger #32: JLC documents Rotation before Layer; the exporter
        emits Layer first. The shipped file follows the document."""
        path = fab.write_cpl_csv(EXPORTER_CPL, self.dir / "cpl.csv", PROFILE)
        self.assertEqual(
            path.read_bytes(),
            b"Designator,Mid X,Mid Y,Rotation,Layer\r\n"
            b"R1,-5.000,0.000,0,top\r\n"
            b"LED1,5.000,0.000,0,top\r\n",
        )

    def test_bom_grouping_summary_agrees_with_the_file(self) -> None:
        """ORDER.md quotes lines/orderable from bom_summary; those numbers
        must describe the grouped file JLC sees, not the per-placement rows."""
        rows = [
            {"designator": "SW1", "comment": "sw", "footprint": "SMD-4P",
             "lcsc": "C318884", "lock": {"basic": True, "price": 0.018}},
            {"designator": "SW2", "comment": "sw", "footprint": "SMD-4P",
             "lcsc": "C318884", "lock": {"basic": True, "price": 0.018}},
            {"designator": "U1", "comment": "mcu", "footprint": "QFN",
             "lcsc": "C2040", "lock": {"basic": False, "price": 1.0}},
        ]
        summary = fab.bom_summary(rows)
        self.assertEqual(summary["lines"], 2)
        self.assertEqual(summary["orderable"], 2)
        self.assertEqual(summary["basicParts"], 1)
        # Cost stays per placement: two switches cost two switches.
        self.assertEqual(summary["estimatedCostUsd"], 1.04)

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
    def _write(self, *, assembly: bool) -> str:
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
            )
            return path.read_text(encoding="utf-8")

    def test_assembly_walkthrough_exact_clicks(self) -> None:
        text = self._write(assembly=True)
        self.assertIn("cart.jlcpcb.com/quote", text)
        self.assertIn("Add gerber file", text)
        self.assertIn("PCB Assembly", text)
        self.assertIn("Process BOM & CPL", text)
        self.assertIn("placement preview", text)
        self.assertIn("safety net", text)  # the placement-preview warning
        self.assertIn("pin-1 orientation", text)
        self.assertIn("$4-20", text)
        self.assertIn("$75-110", text)
        self.assertIn("14/14", text)

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


class BomEnrichment(unittest.TestCase):
    """Ledger #32, second half: JLC's format with JLC's content.

    The exporter ships Comment and Footprint empty. JLC's parts-match table
    uses both to cross-check the part number against what the designer thought
    they were ordering, so a bare LCSC number removes the fab's only check.
    """

    def _design(self):
        return [
            {"type": "source_component", "source_component_id": "s1", "name": "R1",
             "ftype": "simple_resistor", "display_resistance": "1kΩ"},
            {"type": "source_component", "source_component_id": "s2", "name": "C1",
             "ftype": "simple_capacitor", "display_capacitance": "100nF"},
            {"type": "source_component", "source_component_id": "s3", "name": "Y1",
             "ftype": "simple_crystal", "frequency": 12000000},
            {"type": "source_component", "source_component_id": "s4", "name": "U1",
             "ftype": "simple_chip", "manufacturer_part_number": "RP2040"},
            {"type": "source_component", "source_component_id": "s5", "name": "SW1",
             "ftype": "simple_push_button"},
        ]

    def test_comment_comes_from_the_design(self) -> None:
        d = fab.describe_components(self._design())
        self.assertEqual(d["R1"]["comment"], "1kΩ")
        self.assertEqual(d["C1"]["comment"], "100nF")
        self.assertEqual(d["U1"]["comment"], "RP2040")

    def test_a_crystal_is_named_by_its_frequency(self) -> None:
        self.assertEqual(fab.describe_components(self._design())["Y1"]["comment"], "12MHz")

    def test_a_part_with_no_value_still_says_what_it_is(self) -> None:
        # Better a human word than an empty cell the fab cannot check.
        self.assertEqual(
            fab.describe_components(self._design())["SW1"]["comment"], "push button"
        )

    def test_package_is_never_guessed_from_geometry(self) -> None:
        """The first attempt measured copper and read every 0402 as an 0603.

        Land patterns are larger than the bodies they name, so a geometric
        guess is confidently wrong — worse than blank, because JLC's matcher
        flags a mismatch that does not exist. describe_components must not
        return a footprint at all; packages come from the catalog by LCSC.
        """
        for entry in fab.describe_components(self._design()).values():
            self.assertNotIn("footprint", entry)

    def test_catalog_packages_are_authoritative_and_optional(self) -> None:
        packages = fab._catalog_packages()
        self.assertIsInstance(packages, dict)
        if "C1525" in packages:  # the 100nF 0402 every board uses
            self.assertEqual(packages["C1525"], "0402")

    def test_a_grouped_row_is_described_by_its_first_designator(self) -> None:
        import tempfile
        rows = [{"designator": "R1,R2,R3", "comment": "", "value": "",
                 "footprint": "", "lcsc": "C25104"}]
        with tempfile.TemporaryDirectory() as tmp:
            out = fab.write_bom_csv(
                rows, Path(tmp) / "bom.csv", fab.get_profile("jlcpcb"),
                described={"R1": {"comment": "1kΩ"}},
            )
            body = out.read_text()
        self.assertIn("1kΩ", body, "the line must carry the value it shares")
        self.assertIn("R1,R2,R3", body)
