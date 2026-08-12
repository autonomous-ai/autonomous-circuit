"""kicad-cli-dependent paths — skip (never fail) when kicad is absent
(contract §1 test discipline). On a kicad box these verify the second
substrate end-to-end: conversion, ERC/DRC report parsing, and the shipping
gerber path flipping fab.gerberSource to kicad-cli."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import circuitproj  # noqa: E402

from circuitpy import generation, toolchain  # noqa: E402

KICAD = circuitproj.kicad_available()


class KicadGerberCommandPolicy(unittest.TestCase):
    def test_shipping_silkscreen_is_clipped_to_soldermask_openings(self) -> None:
        args = generation._kicad_gerber_export_args(
            Path("/tmp/board-gerbers"), Path("/tmp/board.kicad_pcb")
        )

        self.assertEqual(args[:3], ["pcb", "export", "gerbers"])
        self.assertIn("--subtract-soldermask", args)
        self.assertLess(args.index("--subtract-soldermask"), args.index("-o"))


@unittest.skipUnless(KICAD, "kicad-cli not installed")
class KicadSecondSubstrate(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "proj"
        circuitproj.write_project(cls.root, tsx=circuitproj.GOOD_TSX)
        cls.boards = cls.root / "boards"
        cls.result = generation.build_board(
            cls.boards / "main.tsx", cls.boards / "main.circuit.json"
        )
        cls.sidecar = json.loads(
            (cls.boards / "main.board.json").read_text(encoding="utf-8")
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_version_probed_into_sidecar(self) -> None:
        self.assertTrue(self.sidecar["toolchain"].get("kicadCli"))
        self.assertEqual(
            self.sidecar["toolchain"]["kicadCli"], toolchain.versions()["kicadCli"]
        )

    def test_gerbers_come_from_kicad(self) -> None:
        self.assertEqual(self.sidecar["fab"]["gerberSource"], "kicad-cli")

    def test_no_unverified_gerbers_warning(self) -> None:
        warnings = (self.sidecar.get("validation") or {}).get("warnings") or []
        kinds = {w["kind"] for w in warnings}
        self.assertNotIn("unverified_gerbers", kinds)
        self.assertNotIn("kicad_unavailable", kinds)

    def test_shipping_silkscreen_does_not_cover_mask_openings(self) -> None:
        warnings = (self.sidecar.get("validation") or {}).get("warnings") or []
        kinds = {w["kind"] for w in warnings}
        self.assertNotIn("gerber_silk_over_pad", kinds)


if __name__ == "__main__":
    unittest.main()
