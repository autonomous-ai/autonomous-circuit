"""spec.py — product.json resolution + the safety-envelope pre-flight."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import circuitproj  # noqa: E402

from circuitpy import spec  # noqa: E402
from circuitpy.errors import ProjectShapeError, SpecValidationError  # noqa: E402


class ProductResolution(unittest.TestCase):
    def _load(self, payload: object) -> spec.ResolvedProduct:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "product.json").write_text(json.dumps(payload), encoding="utf-8")
            return spec.load_product(root)

    def test_valid_product(self) -> None:
        product = self._load(circuitproj.product_dict())
        self.assertEqual(product.name, "test-board")
        self.assertEqual(product.power, "usb-c-5v")
        self.assertEqual(product.envelope_mm, (60.0, 40.0))
        self.assertTrue(product.assembly)
        self.assertEqual(product.fab, "jlcpcb")

    def test_missing_product_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ProjectShapeError):
                spec.load_product(Path(tmp))

    def test_bad_power_enum(self) -> None:
        with self.assertRaises(ProjectShapeError):
            self._load(circuitproj.product_dict(power="mains-230v"))

    def test_missing_name(self) -> None:
        payload = circuitproj.product_dict()
        del payload["name"]
        with self.assertRaises(ProjectShapeError):
            self._load(payload)

    def test_bad_envelope_shape(self) -> None:
        payload = circuitproj.product_dict()
        payload["envelopeMm"] = [60]
        with self.assertRaises(ProjectShapeError):
            self._load(payload)

    def test_envelope_optional(self) -> None:
        product = self._load(circuitproj.product_dict(envelope_mm=None))
        self.assertIsNone(product.envelope_mm)

    def test_unparseable_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "product.json").write_text("{nope", encoding="utf-8")
            with self.assertRaises(ProjectShapeError):
                spec.load_product(root)


class PartsLock(unittest.TestCase):
    def test_absent_lock_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(spec.load_parts(Path(tmp)), {})

    def test_broken_lock_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "parts.json").write_text("[broken", encoding="utf-8")
            with self.assertRaises(ProjectShapeError):
                spec.load_parts(root)

    def test_lock_entries_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "parts.json").write_text(
                json.dumps({"R1": {"lcsc": "C11702", "basic": True}}), encoding="utf-8"
            )
            parts = spec.load_parts(root)
            self.assertEqual(parts["R1"]["lcsc"], "C11702")


class SafetyEnvelope(unittest.TestCase):
    def _scan(self, source: str, *, rel: str = "boards/main.tsx") -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "product.json").write_text(
                json.dumps(circuitproj.product_dict()), encoding="utf-8"
            )
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
            product = spec.load_product(root)
            spec.preflight_safety([path], root, product)

    def _scan_ask(self, description: str) -> None:
        """The other half: screen the natural-language request itself."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = circuitproj.product_dict()
            product["description"] = description
            (root / "product.json").write_text(json.dumps(product), encoding="utf-8")
            path = root / "boards" / "main.tsx"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(circuitproj.GOOD_TSX, encoding="utf-8")
            spec.preflight_safety([path], root, spec.load_product(root))

    def test_clean_board_passes(self) -> None:
        self._scan(circuitproj.GOOD_TSX)

    # --- the ask, not only the source it turned into ----------------------
    #
    # preflight_safety scanned the board's source graph and nothing read
    # product.json's description — the natural-language request the whole
    # project was built from. A dangerous intent that compiles to
    # innocent-looking source walked straight through.

    def test_an_innocent_ask_passes(self) -> None:
        self._scan_ask("A USB-C desk weather card with three status LEDs")

    def test_a_mains_ask_is_refused_before_any_source_is_read(self) -> None:
        with self.assertRaises(SpecValidationError) as ctx:
            self._scan_ask("A smart switch for a 230V AC lamp")
        self.assertIn("safety_envelope", str(ctx.exception))
        self.assertIn("product.json", str(ctx.exception))

    def test_the_source_that_ask_compiles_to_is_beside_the_point(self) -> None:
        """The whole hole. The TSX here is the known-good board."""
        with self.assertRaises(SpecValidationError):
            self._scan_ask("A relay controller wired to line voltage")

    def test_a_negated_mention_is_not_a_violation(self) -> None:
        """Prose says this constantly and source almost never does."""
        self._scan_ask("A desk weather card, USB-C powered, no mains anywhere")

    def test_but_a_negator_that_is_not_adjacent_does_not_exonerate(self) -> None:
        """"no problem, switches mains" is the sentence this exists to catch."""
        with self.assertRaises(SpecValidationError):
            self._scan_ask("A relay board, no problem, switches mains")

    def test_a_bare_die_rf_ask_is_refused(self) -> None:
        with self.assertRaises(SpecValidationError):
            self._scan_ask("A badge with a bare-die RF section")

    def test_a_lithium_charging_ask_is_refused(self) -> None:
        """From `circuitlib.safety.CHARGER_PATTERNS`, which screens prose and
        carried three patterns this table had not."""
        with self.assertRaises(SpecValidationError):
            self._scan_ask("A lipo charger for a wearable")
        with self.assertRaises(SpecValidationError):
            self._scan_ask("A pack board using DW01 protection")

    def test_a_cell_format_on_its_own_is_a_known_hole(self) -> None:
        """Pinned as a limit rather than left to be rediscovered. Refusing a
        bare cell format would also refuse "a gauge for an 18650 pack, using
        the sealed block", which the envelope permits."""
        self._scan_ask("An 18650 charger board")

    def test_an_empty_description_screens_nothing_and_raises_nothing(self) -> None:
        self._scan_ask("")

    def test_low_voltages_pass(self) -> None:
        self._scan('const rail = "3.3V"; const usb = "5V"; const dc = "12V";\n')

    def test_mains_keyword_refused(self) -> None:
        with self.assertRaises(SpecValidationError) as ctx:
            self._scan("// hook this to mains power\n")
        self.assertIn("safety_envelope", str(ctx.exception))

    def test_vac_literal_refused(self) -> None:
        with self.assertRaises(SpecValidationError):
            self._scan('const v = "120VAC input stage"\n')

    def test_high_voltage_prop_refused(self) -> None:
        with self.assertRaises(SpecValidationError) as ctx:
            self._scan('<powersource voltage="48V" />\n')
        self.assertIn("48", str(ctx.exception))

    def test_voltage_at_ceiling_passes(self) -> None:
        self._scan('<powersource voltage="24V" />\n')

    def test_triac_refused(self) -> None:
        with self.assertRaises(SpecValidationError):
            self._scan('<chip name="U1" footprint="to220" /> // BT136 triac dimmer\n')

    def test_bare_die_rf_refused(self) -> None:
        with self.assertRaises(SpecValidationError):
            self._scan("// bare die RF section with antenna matching\n")

    def test_raw_charger_ic_outside_blocks_refused(self) -> None:
        with self.assertRaises(SpecValidationError) as ctx:
            self._scan('<chip name="U1" /> // TP4056 charger\n')
        self.assertIn("sealed", str(ctx.exception))

    def test_raw_charger_ic_inside_blocks_allowed(self) -> None:
        self._scan(
            '<chip name="U1" /> // TP4056 charger inside the sealed block\n',
            rel="blocks/battery-sealed.tsx",
        )

    def test_refusal_carries_file_and_line(self) -> None:
        with self.assertRaises(SpecValidationError) as ctx:
            self._scan("const a = 1\nconst b = 2 // mains here\n")
        self.assertIn("boards/main.tsx:2", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
