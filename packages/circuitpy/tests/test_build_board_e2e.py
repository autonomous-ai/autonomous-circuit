"""build_board() end-to-end against the real pinned toolchain — full
artifact set, sidecar schema, sidecar-before-IR ordering, idempotent
short-circuit, and the failure contract. Parts engine off suite-wide, so
supplier numbers come from the parts.json lock (contract §1)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import circuitproj  # noqa: E402
from circuitproj import EnvGuard  # noqa: E402

from circuitpy import generation  # noqa: E402
from circuitpy.errors import (  # noqa: E402
    CompileError,
    ProjectShapeError,
    SpecValidationError,
)
from circuitpy.source_hash import board_source_hash  # noqa: E402

GOOD_PARTS = {
    "R1": {"lcsc": "C11702", "basic": True, "price": 0.01},
    "LED1": {"lcsc": "C965793", "basic": True, "price": 0.05},
}


class GoodBoardE2E(unittest.TestCase):
    """One real build in setUpClass; assertions fan out below."""

    tmp: tempfile.TemporaryDirectory
    root: Path
    result: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name).resolve() / "proj"
        circuitproj.write_project(cls.root, tsx=circuitproj.GOOD_TSX, parts=GOOD_PARTS)
        cls.boards = cls.root / "boards"
        cls.output = cls.boards / "main.circuit.json"
        cls.result = generation.build_board(cls.boards / "main.tsx", cls.output)
        cls.sidecar = json.loads(
            (cls.boards / "main.board.json").read_text(encoding="utf-8")
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    # -- artifact set --------------------------------------------------------

    def test_artifact_set_on_disk(self) -> None:
        for rel in (
            "main.circuit.json",
            "main.board.json",
            "main_review/_schematic.png",
            "main_review/_pcb.png",
            "main_review/_schematic.svg",
            "main_review/_pcb.svg",
            "main_fab/gerbers.zip",
            "main_fab/bom.csv",
            "main_fab/cpl.csv",
        ):
            self.assertTrue((self.boards / rel).is_file(), rel)

    def test_circuit_json_is_element_array(self) -> None:
        cj = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertIsInstance(cj, list)
        self.assertTrue(any(e.get("type") == "pcb_board" for e in cj))

    def test_workspace_stays_clean_of_build_litter(self) -> None:
        self.assertFalse((self.root / "dist").exists())
        self.assertFalse((self.root / ".tscircuit").exists())
        self.assertTrue((self.root / ".circuit").is_dir())  # tmp + caches live here

    def test_gerbers_zip_members(self) -> None:
        with zipfile.ZipFile(self.boards / "main_fab" / "gerbers.zip") as zf:
            names = zf.namelist()
        self.assertTrue(any(n.endswith(".gbr") for n in names))
        self.assertTrue(any(n.endswith(".drl") for n in names))
        self.assertFalse(any(n.endswith(".csv") for n in names))

    def test_bom_csv_carries_locked_parts(self) -> None:
        text = (self.boards / "main_fab" / "bom.csv").read_text(encoding="utf-8")
        self.assertIn("LCSC Part #", text.splitlines()[0])
        self.assertIn("C11702", text)
        self.assertIn("C965793", text)

    # -- sidecar schema ------------------------------------------------------

    def test_sidecar_is_canonical_json(self) -> None:
        raw = (self.boards / "main.board.json").read_text(encoding="utf-8")
        self.assertEqual(
            raw, json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"))
        )

    def test_sidecar_identity_block(self) -> None:
        self.assertEqual(self.sidecar["generator"], "circuitpy")
        self.assertEqual(self.sidecar["entryKind"], "board")
        source = self.sidecar["source"]
        self.assertEqual(source["kind"], "tsx")
        self.assertEqual(source["path"], "boards/main.tsx")
        identity = board_source_hash(self.boards / "main.tsx", self.root)
        self.assertEqual(source["fingerprint"], identity.source_fingerprint)
        self.assertEqual(source["hash"], identity.source_hash)

    def test_sidecar_board_block(self) -> None:
        board = self.sidecar["board"]
        self.assertEqual(board["path"], "main.circuit.json")
        self.assertEqual(board["name"], "test-board")
        self.assertEqual(board["widthMm"], 20)
        self.assertEqual(board["heightMm"], 20)
        self.assertEqual(board["layers"], 2)

    def test_sidecar_toolchain_block(self) -> None:
        from circuitpy import toolchain

        block = self.sidecar["toolchain"]
        versions = toolchain.versions()
        self.assertEqual(block["tscircuit"], versions["tscircuit"])
        self.assertEqual(block["checks"], versions["checks"])
        if versions["kicadCli"] is None:
            self.assertNotIn("kicadCli", block)  # omitted when absent
        else:
            self.assertEqual(block["kicadCli"], versions["kicadCli"])

    def test_sidecar_bom_block(self) -> None:
        bom = self.sidecar["bom"]
        self.assertEqual(bom["lines"], 2)
        self.assertEqual(bom["orderable"], 2)  # lock fills offline BOM
        self.assertEqual(bom["basicParts"], 2)
        self.assertEqual(bom["estimatedCostUsd"], 0.06)

    def test_sidecar_fab_block_and_ready_rule(self) -> None:
        fab_block = self.sidecar["fab"]
        self.assertEqual(fab_block["profile"], "jlcpcb")
        self.assertTrue(fab_block["assembly"])
        self.assertEqual(fab_block["packet"], "main_fab/")
        self.assertIn(fab_block["gerberSource"], ("tscircuit", "kicad-cli"))
        if fab_block["gerberSource"] == "tscircuit":
            self.assertFalse(fab_block["ready"])  # never ready without kicad
        warnings = (self.sidecar.get("validation") or {}).get("warnings") or []
        has_errors = any(w["severity"] == "error" for w in warnings)
        expected_ready = fab_block["gerberSource"] == "kicad-cli" and not has_errors
        self.assertEqual(fab_block["ready"], expected_ready)

    def test_order_md_only_when_ready(self) -> None:
        order = self.boards / "main_fab" / "ORDER.md"
        self.assertEqual(order.is_file(), bool(self.sidecar["fab"]["ready"]))

    def test_sidecar_artifacts_all_exist(self) -> None:
        artifacts = self.sidecar["artifacts"]
        self.assertEqual(artifacts["schematicPng"], "main_review/_schematic.png")
        self.assertEqual(artifacts["pcbPng"], "main_review/_pcb.png")
        for rel in artifacts.values():
            self.assertTrue((self.boards / rel).is_file(), rel)

    def test_warnings_severities_closed_set(self) -> None:
        warnings = (self.sidecar.get("validation") or {}).get("warnings") or []
        for warning in warnings:
            self.assertEqual(
                set(warning), {"part", "kind", "detail", "severity"}, warning
            )
            self.assertIn(warning["severity"], ("error", "warning", "info"))

    def test_good_board_has_zero_error_warnings(self) -> None:
        warnings = (self.sidecar.get("validation") or {}).get("warnings") or []
        self.assertFalse([w for w in warnings if w["severity"] == "error"], warnings)

    def test_kicad_absence_surfaces_honestly(self) -> None:
        warnings = (self.sidecar.get("validation") or {}).get("warnings") or []
        kinds = {w["kind"] for w in warnings}
        if not circuitproj.kicad_available():
            self.assertIn("kicad_unavailable", kinds)
            self.assertIn("unverified_gerbers", kinds)

    # -- ordering ------------------------------------------------------------

    def test_sidecar_lands_before_the_artifact_of_record(self) -> None:
        sidecar_mtime = (self.boards / "main.board.json").stat().st_mtime_ns
        output_mtime = self.output.stat().st_mtime_ns
        self.assertLessEqual(sidecar_mtime, output_mtime)

    # -- result dict ---------------------------------------------------------

    def test_result_shape(self) -> None:
        result = self.result
        self.assertEqual(result["circuit_json_path"], str(self.output))
        self.assertEqual(
            result["metadata_path"], str(self.boards / "main.board.json")
        )
        self.assertTrue(Path(str(result["schematic_png"])).is_file())
        self.assertTrue(Path(str(result["pcb_png"])).is_file())
        self.assertEqual(
            result["board"], {"width_mm": 20.0, "height_mm": 20.0, "layers": 2}
        )
        self.assertEqual(
            result["bom"],
            {"lines": 2, "orderable": 2, "estimated_cost_usd": 0.06},
        )
        self.assertEqual(result["fab"]["profile"], "jlcpcb")
        self.assertNotIn("unchanged", result)

    # -- idempotent short-circuit -------------------------------------------

    def test_unchanged_rerun_is_a_zero_write_no_op(self) -> None:
        tracked = [
            self.output,
            self.boards / "main.board.json",
            self.boards / "main_review" / "_pcb.png",
            self.boards / "main_fab" / "bom.csv",
        ]
        before = {p: p.stat().st_mtime_ns for p in tracked}
        rerun = generation.build_board(self.boards / "main.tsx", self.output)
        self.assertIs(rerun.get("unchanged"), True)
        self.assertEqual(rerun["warnings"], self.result["warnings"])
        for path, mtime in before.items():
            self.assertEqual(path.stat().st_mtime_ns, mtime, path)

    def test_product_edit_invalidates_the_short_circuit(self) -> None:
        product_path = self.root / "product.json"
        original = product_path.read_text(encoding="utf-8")
        try:
            product_path.write_text(
                original.replace("circuitpy test board", "edited"), encoding="utf-8"
            )
            identity = board_source_hash(self.boards / "main.tsx", self.root)
            prior = generation._unchanged_prior_result(
                sidecar_path=self.boards / "main.board.json",
                identity=identity,
                output_p=self.output,
                boards_dir=self.boards,
                fab_dir=self.boards / "main_fab",
            )
            self.assertIsNone(prior)
        finally:
            product_path.write_text(original, encoding="utf-8")

    def test_missing_artifact_invalidates_the_short_circuit(self) -> None:
        target = self.boards / "main_review" / "_schematic.svg"
        payload = target.read_bytes()
        try:
            target.unlink()
            identity = board_source_hash(self.boards / "main.tsx", self.root)
            # _schematic.svg is not in the artifacts map, so removing it does
            # NOT invalidate; removing a mapped artifact must.
            bom_csv = self.boards / "main_fab" / "bom.csv"
            bom_payload = bom_csv.read_bytes()
            bom_csv.unlink()
            prior = generation._unchanged_prior_result(
                sidecar_path=self.boards / "main.board.json",
                identity=identity,
                output_p=self.output,
                boards_dir=self.boards,
                fab_dir=self.boards / "main_fab",
            )
            self.assertIsNone(prior)
            bom_csv.write_bytes(bom_payload)
        finally:
            target.write_bytes(payload)


class ForceRegen(EnvGuard, unittest.TestCase):
    def test_force_regen_rebuilds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            circuitproj.write_project(root, tsx=circuitproj.GOOD_TSX, parts=GOOD_PARTS)
            output = root / "boards" / "main.circuit.json"
            first = generation.build_board(root / "boards" / "main.tsx", output)
            self.assertNotIn("unchanged", first)
            os.environ["CIRCUIT_FORCE_REGEN"] = "1"
            second = generation.build_board(root / "boards" / "main.tsx", output)
            self.assertNotIn("unchanged", second)


class BadBoardE2E(unittest.TestCase):
    """A board with a real defect still completes — errors are warnings the
    driver gates on, never exceptions (gate on parsed artifacts)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name).resolve() / "proj"
        circuitproj.write_project(cls.root, tsx=circuitproj.BAD_PORT_TSX)
        cls.boards = cls.root / "boards"
        cls.output = cls.boards / "main.circuit.json"
        cls.result = generation.build_board(cls.boards / "main.tsx", cls.output)
        cls.sidecar = json.loads(
            (cls.boards / "main.board.json").read_text(encoding="utf-8")
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_build_completes_with_error_warnings(self) -> None:
        warnings = self.result["warnings"]
        kinds = {w["kind"] for w in warnings}
        self.assertIn("source_trace_not_connected_error", kinds)
        self.assertTrue(any(w["severity"] == "error" for w in warnings))

    def test_never_fab_ready(self) -> None:
        self.assertFalse(self.sidecar["fab"]["ready"])
        self.assertFalse((self.boards / "main_fab" / "ORDER.md").exists())

    def test_core_artifacts_still_written(self) -> None:
        for rel in (
            "main.circuit.json",
            "main.board.json",
            "main_review/_schematic.png",
            "main_review/_pcb.png",
        ):
            self.assertTrue((self.boards / rel).is_file(), rel)


class FailureContract(EnvGuard, unittest.TestCase):
    def test_compile_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            circuitproj.write_project(root, tsx=circuitproj.COMPILE_ERROR_TSX)
            with self.assertRaises(CompileError):
                generation.build_board(
                    root / "boards" / "main.tsx",
                    root / "boards" / "main.circuit.json",
                )

    def test_bad_output_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            circuitproj.write_project(root)
            with self.assertRaises(ProjectShapeError):
                generation.build_board(
                    root / "boards" / "main.tsx", root / "boards" / "main.json"
                )

    def test_missing_product_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            circuitproj.write_project(root)
            (root / "product.json").unlink()
            with self.assertRaises(ProjectShapeError):
                generation.build_board(
                    root / "boards" / "main.tsx",
                    root / "boards" / "main.circuit.json",
                )

    def test_directory_without_board_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ProjectShapeError):
                generation.build_board(
                    Path(tmp), Path(tmp) / "main.circuit.json"
                )

    def test_unknown_fab_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            circuitproj.write_project(root)
            with self.assertRaises(ProjectShapeError):
                generation.build_board(
                    root / "boards" / "main.tsx",
                    root / "boards" / "main.circuit.json",
                    fab="oshpark",
                )

    def test_safety_refusal_happens_before_any_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            circuitproj.write_project(
                root,
                tsx='// mains-powered relay stage\n' + circuitproj.GOOD_TSX,
            )
            with self.assertRaises(SpecValidationError):
                generation.build_board(
                    root / "boards" / "main.tsx",
                    root / "boards" / "main.circuit.json",
                )
            self.assertFalse((root / ".circuit").exists())  # refused at spec time


if __name__ == "__main__":
    unittest.main()
