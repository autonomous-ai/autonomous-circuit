"""The grader has to fail the blocks that look finished and are not.

A provenance table is easy to write and easy to write badly: an `n/a` where a
number belongs, a confident sentence where a datasheet page belongs, a peak
current below the typical one. Every case here is a block that would read fine
to a person skimming and would still put the wrong rail on a board.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "grade_block",
    Path(__file__).resolve().parents[1] / "scripts" / "grade-block.py",
)
grade_block = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(grade_block)

REPO = Path(__file__).resolve().parents[3]


def block_md(**overrides: str) -> str:
    """A certified-module table that grades ok, minus whatever is overridden.

    Pass ``field=""`` to drop a row entirely — that is the missing-field case,
    which is different from the row being present and wrong.
    """
    fields = {
        "class": "certified-module",
        "mpn": "ESP32-C3-MINI-1",
        "lcsc": "C2934569",
        "certification": "FCC ID 2AC7Z-ESP32C3MINI1",
        "footprint_source": "`easyeda:C2934569`",
        "footprint_iou": "91.20",
        "typical_ma": "80 mA (datasheet p.24)",
        "peak_ma": "335 mA (datasheet p.24, TX 802.11b)",
        "v_in": "3.0–3.6 V (datasheet p.12)",
        "keepout": "antenna zone, datasheet p.9",
        "pin_source": "datasheet p.7, pin definitions",
        "verified": "2026-08-28",
    }
    fields.update(overrides)
    rows = "\n".join(
        f"| `{k}` | {v} |" for k, v in fields.items() if v != ""
    )
    return f"""# esp32-c3-mini — a certified Wi-Fi module

## Provenance

| field | value |
|---|---|
{rows}

## Ports

| port | meaning |
|---|---|
| `net.V3_3` | in: supply |
"""


class Grading(unittest.TestCase):
    def test_a_complete_table_grades_ok(self):
        r = grade_block.grade(block_md(), "esp32-c3-mini")
        self.assertEqual((r["ok"], r["missing"], r["problems"]),
                         (True, [], []))

    def test_no_provenance_section_is_every_field_missing(self):
        r = grade_block.grade("# x\n\n## Ports\n\n| a | b |\n", "x")
        self.assertFalse(r["ok"])
        self.assertEqual(sorted(r["missing"]), sorted(grade_block.REQUIRED))

    def test_a_dropped_row_is_named_not_merely_counted(self):
        r = grade_block.grade(block_md(peak_ma=""), "x")
        self.assertEqual(r["missing"], ["peak_ma"])

    def test_a_module_may_not_answer_na_to_its_current(self):
        """The number the LDO and the bulk cap are sized on."""
        r = grade_block.grade(block_md(peak_ma="n/a"), "x")
        self.assertFalse(r["ok"])
        self.assertTrue(any("peak_ma" in p for p in r["problems"]))

    def test_an_interconnect_may(self):
        r = grade_block.grade(block_md(
            **{"class": "interconnect", "certification": "n/a — passive",
               "typical_ma": "n/a", "peak_ma": "n/a", "v_in": "n/a",
               "keepout": "n/a"}), "x")
        self.assertEqual(r["problems"], [])
        self.assertTrue(r["ok"])

    def test_a_class_we_do_not_source_is_refused(self):
        """`bare-ic` is the invented-circuit case wearing a table."""
        r = grade_block.grade(block_md(**{"class": "bare-ic"}), "x")
        self.assertFalse(r["ok"])
        self.assertTrue(any("gaps entry" in p for p in r["problems"]))

    def test_peak_below_typical_is_caught(self):
        r = grade_block.grade(block_md(peak_ma="20 mA (p.24)"), "x")
        self.assertFalse(r["ok"])
        self.assertTrue(any("below typical" in p for p in r["problems"]))

    def test_a_module_with_no_certification_identifier_is_bare_silicon(self):
        r = grade_block.grade(block_md(certification="yes"), "x")
        self.assertFalse(r["ok"])
        self.assertTrue(any("bare silicon" in p for p in r["problems"]))

    def test_an_unorderable_part_fails(self):
        r = grade_block.grade(block_md(lcsc="see LCSC"), "x")
        self.assertFalse(r["ok"])
        self.assertTrue(any("C-number" in p for p in r["problems"]))

    def test_a_footprint_iou_with_no_number_fails(self):
        r = grade_block.grade(block_md(footprint_iou="matches well"), "x")
        self.assertFalse(r["ok"])
        self.assertTrue(any("footprint_iou" in p for p in r["problems"]))

    def test_an_undated_verification_fails(self):
        """Stock, price and footprints drift; the date is part of the claim."""
        r = grade_block.grade(block_md(verified="recently"), "x")
        self.assertFalse(r["ok"])
        self.assertTrue(any("ISO date" in p for p in r["problems"]))

    def test_a_pin_source_citing_nothing_fails(self):
        r = grade_block.grade(block_md(pin_source="the usual order"), "x")
        self.assertFalse(r["ok"])
        self.assertTrue(any("pin_source" in p for p in r["problems"]))

    def test_a_parts_table_elsewhere_cannot_answer_for_provenance(self):
        """The false pass this parser exists to avoid."""
        text = """# x

## Parts

| Refdes | mpn | lcsc |
|---|---|---|
| J10 | BX_PM2 | C18078126 |
"""
        r = grade_block.grade(text, "x")
        self.assertFalse(r["ok"])
        self.assertEqual(sorted(r["missing"]), sorted(grade_block.REQUIRED))


class TheWorkedExample(unittest.TestCase):
    def test_servo_header_grades_ok(self):
        """The skill tells the reader to copy this block. It has to pass."""
        md = REPO / "packages/golden-blocks/blocks/servo-header/BLOCK.md"
        r = grade_block.grade(md.read_text(), "servo-header")
        self.assertEqual((r["ok"], r["missing"], r["problems"]),
                         (True, [], []))


if __name__ == "__main__":
    unittest.main()
