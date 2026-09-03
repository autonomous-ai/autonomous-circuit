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


def integrated_md(**overrides: str) -> str:
    """A display module: finished, purchasable, and it will never radiate."""
    fields = {
        "class": "integrated-module",
        "mpn": "UG-2864HSWEG01",
        "lcsc": "C90100",
        "certification": "n/a — non-radiating integrated module",
        "integration": ("SSD1306 driver, the charge pump and its 2x 2.2uF, and "
                        "the 4.7k SDA/SCL pull-ups are all on the module — "
                        "datasheet p.3"),
        "footprint_source": "`easyeda:C90100`",
        "footprint_iou": "88.10",
        "typical_ma": "11 mA at 50% pixels (datasheet p.8)",
        "peak_ma": "27 mA all pixels on (datasheet p.8)",
        "v_in": "3.3 V (datasheet p.6)",
        "keepout": "n/a — no antenna",
        "pin_source": "datasheet p.4, 4-pin header G/V/C/D",
        "verified": "2026-09-03",
    }
    fields.update(overrides)
    rows = "\n".join(f"| `{k}` | {v} |" for k, v in fields.items() if v != "")
    return f"""# oled-ssd1306 — a 0.96in I2C display module

## Provenance

| field | value |
|---|---|
{rows}
"""


class IntegratedModules(unittest.TestCase):
    """The class the OLED exposed: a part that carries the whole circuit and
    can never hold a certificate, because it does not radiate."""

    def test_a_complete_integrated_module_grades_ok(self):
        r = grade_block.grade(integrated_md(), "oled-ssd1306")
        self.assertEqual((r["ok"], r["missing"], r["problems"]), (True, [], []))

    def test_it_may_skip_the_certificate(self):
        """No lab issues an FCC ID to a display. Demanding one refuses a part
        that satisfies the rule's own principle."""
        r = grade_block.grade(integrated_md(), "x")
        self.assertNotIn("certification", r["missing"])
        self.assertEqual(r["problems"], [])

    def test_but_not_the_integration_row(self):
        """That row is what replaces the certificate as evidence."""
        r = grade_block.grade(integrated_md(integration=""), "x")
        self.assertEqual(r["missing"], ["integration"])

    def test_integration_may_not_be_waved_away_with_na(self):
        r = grade_block.grade(integrated_md(integration="n/a"), "x")
        self.assertFalse(r["ok"])
        self.assertTrue(any("integration" in p for p in r["problems"]))

    def test_it_is_a_module_is_not_evidence(self):
        """A restatement of the class is not a list of parts and a page."""
        r = grade_block.grade(
            integrated_md(integration="everything is on the module"), "x")
        self.assertFalse(r["ok"])
        self.assertTrue(any("names no part and no page" in p
                            for p in r["problems"]))

    def test_the_other_classes_do_not_owe_an_integration_row(self):
        """Adding it to REQUIRED would have failed every block already
        sourced — a migration, not a rule."""
        for md in (block_md(), block_md(**{
                "class": "interconnect", "certification": "n/a — passive",
                "typical_ma": "n/a", "peak_ma": "n/a", "v_in": "n/a",
                "keepout": "n/a"})):
            r = grade_block.grade(md, "x")
            self.assertNotIn("integration", r["missing"])
            self.assertTrue(r["ok"], r)

    def test_a_radio_may_not_slip_in_through_this_class(self):
        """Class 3 is not a way around class 2. A transmitter with no
        certificate is bare silicon wearing a daughterboard, and the grader
        cannot read intent — but it can insist the class that skips the
        certificate still names what it carries, and the skill's own words
        refuse the rest."""
        r = grade_block.grade(integrated_md(integration="n/a"), "x")
        self.assertFalse(r["ok"])


class TheWorkedExample(unittest.TestCase):
    def test_servo_header_grades_ok(self):
        """The skill tells the reader to copy this block. It has to pass."""
        md = REPO / "packages/golden-blocks/blocks/servo-header/BLOCK.md"
        r = grade_block.grade(md.read_text(), "servo-header")
        self.assertEqual((r["ok"], r["missing"], r["problems"]),
                         (True, [], []))


if __name__ == "__main__":
    unittest.main()
