"""review.py — SVG + PNG normalization via the real toolchain helper."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from circuitproj import EnvGuard, FIXTURES, load_fixture  # noqa: E402

from circuitpy import review  # noqa: E402


class DoubleSidedDetection(unittest.TestCase):
    def test_good_board_is_single_sided(self) -> None:
        self.assertFalse(review.is_double_sided(load_fixture("good.circuit.json")))

    def test_bottom_pad_detected(self) -> None:
        cj = [{"type": "pcb_smtpad", "layer": "bottom"}]
        self.assertTrue(review.is_double_sided(cj))

    def test_bottom_component_detected(self) -> None:
        cj = [{"type": "pcb_component", "layer": "Bottom"}]
        self.assertTrue(review.is_double_sided(cj))


class WriteReview(EnvGuard, unittest.TestCase):
    def test_raster_fallback_when_build_pngs_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            review_dir = Path(tmp) / "main_review"
            written = review.write_review(
                circuit_json_path=FIXTURES / "good.circuit.json",
                review_dir=review_dir,
                built_schematic_png=None,
                built_pcb_png=None,
                double_sided=False,
            )
            for name in ("_schematic.svg", "_pcb.svg", "_schematic.png", "_pcb.png"):
                self.assertTrue((review_dir / name).is_file(), name)
                self.assertGreater((review_dir / name).stat().st_size, 0, name)
            self.assertNotIn("_pcb_bottom.png", written)

    def test_bottom_png_written_for_double_sided(self) -> None:
        cj = load_fixture("good.circuit.json")
        for element in cj:
            if isinstance(element, dict) and element.get("type") == "pcb_smtpad":
                element["layer"] = "bottom"
                break
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "flipped.circuit.json"
            source.write_text(json.dumps(cj), encoding="utf-8")
            review_dir = Path(tmp) / "main_review"
            written = review.write_review(
                circuit_json_path=source,
                review_dir=review_dir,
                built_schematic_png=None,
                built_pcb_png=None,
                double_sided=True,
            )
            self.assertIn("_pcb_bottom.png", written)
            self.assertGreater((review_dir / "_pcb_bottom.png").stat().st_size, 0)

    def test_native_build_pngs_copied_verbatim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_schematic = root / "schematic.png"
            fake_pcb = root / "pcb.png"
            fake_schematic.write_bytes(b"\x89PNG-fake-schematic")
            fake_pcb.write_bytes(b"\x89PNG-fake-pcb")
            review_dir = root / "main_review"
            review.write_review(
                circuit_json_path=FIXTURES / "good.circuit.json",
                review_dir=review_dir,
                built_schematic_png=fake_schematic,
                built_pcb_png=fake_pcb,
                double_sided=False,
            )
            self.assertEqual(
                (review_dir / "_schematic.png").read_bytes(), b"\x89PNG-fake-schematic"
            )
            self.assertEqual((review_dir / "_pcb.png").read_bytes(), b"\x89PNG-fake-pcb")


if __name__ == "__main__":
    unittest.main()
