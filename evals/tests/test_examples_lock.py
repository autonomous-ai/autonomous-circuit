"""Fast sidecar-evidence tests for ``evals/examples_lock.py``.

The fixtures are deliberately not valid tscircuit projects: this lock hashes
source text and reads committed JSON/files only, so a test must never invoke
Node, the parts engine, or the board toolchain.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "evals"))

import examples_lock  # noqa: E402
from circuitpy.generation import (  # noqa: E402
    GENERATOR_NAME,
    _current_toolchain_block,
    pipeline_revision,
)
from circuitpy.source_hash import board_source_hash  # noqa: E402
from scripts.sync_golden_blocks import sync_project  # noqa: E402


def _write_fixture(root: Path) -> tuple[Path, Path, Path]:
    examples = root / "examples"
    project = examples / "demo-board"
    boards = project / "boards"
    blocks = project / "blocks"
    boards.mkdir(parents=True)
    blocks.mkdir()

    (boards / "main.tsx").write_text(
        'import { Helper } from "../blocks/helper/helper"\n'
        "export default () => <Helper />\n",
        encoding="utf-8",
    )
    golden = root / "golden"
    (golden / "helper").mkdir(parents=True)
    (golden / "glue.tsx").write_text(
        "export const Glue = () => null\n", encoding="utf-8"
    )
    (golden / "helper" / "helper.tsx").write_text(
        "export const Helper = () => <resistor name=\"R1\" resistance=\"1k\" />\n",
        encoding="utf-8",
    )
    sync_project(project, blocks=["helper"], source=golden)
    (project / "product.json").write_text(
        json.dumps({"name": "demo", "requirements": ["test"]}) + "\n",
        encoding="utf-8",
    )
    (project / "parts.json").write_text(
        json.dumps({"R1": {"lcsc": "C0001"}}) + "\n",
        encoding="utf-8",
    )

    (boards / "main.circuit.json").write_text("[]\n", encoding="utf-8")
    review = boards / "main_review"
    review.mkdir()
    (review / "_pcb.png").write_bytes(b"committed-png")
    (review / "_schematic.png").write_bytes(b"committed-schematic-png")

    identity = board_source_hash(boards / "main.tsx", project)
    sidecar = {
        "generator": GENERATOR_NAME,
        "generatorRevision": pipeline_revision(),
        "toolchain": _current_toolchain_block(),
        "source": {
            "kind": "tsx",
            "path": identity.source_path,
            "hash": identity.source_hash,
            "fingerprint": identity.source_fingerprint,
        },
        "board": {"path": "main.circuit.json"},
        "build": {
            "autorouterEffort": "default",
            "attempts": 1,
            "blockingByAttempt": [1],
            "attemptEvidence": [
                {
                    "effort": "default",
                    "status": "completed",
                    "circuitSha256": hashlib.sha256(b"[]\n").hexdigest(),
                    "blocking": 1,
                    "routingBlocking": 0,
                    "blockingKinds": {"drc_violation": 1},
                }
            ],
        },
        "bom": {"lines": 1},
        "fab": {"ready": True},
        "validation": {
            "warnings": [{"severity": "error", "kind": "drc_violation"}]
        },
        "artifacts": {
            "pcbPng": "main_review/_pcb.png",
            "schematicPng": "main_review/_schematic.png",
        },
    }
    (boards / "main.board.json").write_text(
        json.dumps(sidecar, sort_keys=True), encoding="utf-8"
    )

    baseline = root / "examples-baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "note": "legacy baseline without evidence metadata",
                "updatedAt": "2000-01-01",
                "boards": {
                    "demo-board": {
                        "blocking": 1,
                        "fabReady": True,
                        "blockingKinds": ["drc_violation"],
                        "bomLines": 1,
                        "autorouterEffort": "default",
                    }
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return examples, project, baseline


class ExamplesLockEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_fresh_sidecar_is_current_and_legacy_baseline_still_passes(self) -> None:
        examples, _project, baseline = _write_fixture(self.root / "fresh")
        with mock.patch.object(examples_lock, "EXAMPLES", examples):
            measured = examples_lock.current(rebuild=False)["demo-board"]
        self.assertEqual(measured["evidence"]["status"], "Fresh")
        self.assertTrue(measured["fabReady"])
        self.assertEqual(measured["blocking"], 1)

        output = io.StringIO()
        with (
            mock.patch.object(examples_lock, "EXAMPLES", examples),
            mock.patch.object(examples_lock, "BASELINE", baseline),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(examples_lock.main(["examples_lock.py"]), 0)
        self.assertIn("1 example boards checked, 0 regressions", output.getvalue())

    def test_entry_import_product_and_parts_drift_are_stale(self) -> None:
        examples, project, _baseline = _write_fixture(self.root / "drift")
        targets = {
            "entry": project / "boards" / "main.tsx",
            "import": project / "blocks" / "helper" / "helper.tsx",
            "product": project / "product.json",
            "parts": project / "parts.json",
        }
        with mock.patch.object(examples_lock, "EXAMPLES", examples):
            for label, target in targets.items():
                with self.subTest(label=label):
                    original = target.read_text(encoding="utf-8")
                    try:
                        target.write_text(original + "\n", encoding="utf-8")
                        measured = examples_lock.current(False)["demo-board"]
                        expected_status = (
                            "InvalidGoldenBlockSnapshot"
                            if label == "import"
                            else "StaleSidecar"
                        )
                        self.assertEqual(
                            measured["evidence"]["status"], expected_status
                        )
                        self.assertFalse(measured["fabReady"])
                        self.assertEqual(measured["blocking"], 10_000)
                        self.assertEqual(
                            measured["blockingKinds"], [expected_status]
                        )
                    finally:
                        target.write_text(original, encoding="utf-8")

    def test_missing_sidecar_and_declared_artifact_are_explicit(self) -> None:
        missing_sidecar_root = self.root / "missing-sidecar"
        examples, project, _baseline = _write_fixture(missing_sidecar_root)
        (project / "boards" / "main.board.json").unlink()
        with mock.patch.object(examples_lock, "EXAMPLES", examples):
            measured = examples_lock.current(False)["demo-board"]
        self.assertEqual(measured["evidence"]["status"], "MissingSidecar")
        self.assertFalse(measured["fabReady"])

    def test_pipeline_and_toolchain_drift_are_stale(self) -> None:
        for field, value, status in (
            ("generatorRevision", "old-pipeline", "StalePipeline"),
            ("toolchain", {"tscircuit": "old-pin"}, "StaleToolchain"),
        ):
            with self.subTest(field=field):
                examples, project, _baseline = _write_fixture(
                    self.root / f"stale-{field}"
                )
                sidecar_path = project / "boards" / "main.board.json"
                sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
                sidecar[field] = value
                sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
                with mock.patch.object(examples_lock, "EXAMPLES", examples):
                    measured = examples_lock.current(False)["demo-board"]
                self.assertEqual(measured["evidence"]["status"], status)
                self.assertFalse(measured["fabReady"])
                self.assertEqual(measured["blocking"], 10_000)

        missing_artifact_root = self.root / "missing-artifact"
        examples, project, _baseline = _write_fixture(missing_artifact_root)
        (project / "boards" / "main_review" / "_pcb.png").unlink()
        with mock.patch.object(examples_lock, "EXAMPLES", examples):
            measured = examples_lock.current(False)["demo-board"]
        self.assertEqual(measured["evidence"]["status"], "MissingArtifact")
        self.assertEqual(
            measured["evidence"]["missingArtifacts"], ["main_review/_pcb.png"]
        )
        self.assertFalse(measured["fabReady"])

    def test_missing_or_drifted_golden_snapshot_is_invalid_evidence(self) -> None:
        examples, project, _baseline = _write_fixture(self.root / "block-lock")
        lock = project / "golden-blocks.lock.json"
        original = lock.read_bytes()

        lock.unlink()
        with mock.patch.object(examples_lock, "EXAMPLES", examples):
            missing = examples_lock.current(False)["demo-board"]
        self.assertEqual(
            missing["evidence"]["status"], "InvalidGoldenBlockSnapshot"
        )
        self.assertIn("not locked", missing["evidence"]["detail"])

        lock.write_bytes(original)
        (project / "blocks" / "helper" / "BLOCK.md").write_text(
            "unlocked extra file\n", encoding="utf-8"
        )
        with mock.patch.object(examples_lock, "EXAMPLES", examples):
            drifted = examples_lock.current(False)["demo-board"]
        self.assertEqual(
            drifted["evidence"]["status"], "InvalidGoldenBlockSnapshot"
        )
        self.assertEqual(drifted["blocking"], 10_000)
        self.assertFalse(drifted["fabReady"])

    def test_incomplete_sidecars_fail_closed_and_cannot_be_accepted(self) -> None:
        def remove_validation(sidecar: dict) -> None:
            sidecar.pop("validation")

        def remove_fab(sidecar: dict) -> None:
            sidecar.pop("fab")

        def remove_bom(sidecar: dict) -> None:
            sidecar.pop("bom")

        def remove_board(sidecar: dict) -> None:
            sidecar.pop("board")

        def remove_build(sidecar: dict) -> None:
            sidecar.pop("build")

        def corrupt_attempt_hash(sidecar: dict) -> None:
            sidecar["build"]["attemptEvidence"][0]["circuitSha256"] = "0" * 64

        def remove_pcb_png(sidecar: dict) -> None:
            sidecar["artifacts"].pop("pcbPng")

        def remove_schematic_png(sidecar: dict) -> None:
            sidecar["artifacts"].pop("schematicPng")

        cases = {
            "validation": remove_validation,
            "fab": remove_fab,
            "bom": remove_bom,
            "board-artifact": remove_board,
            "build-evidence": remove_build,
            "attempt-artifact-hash": corrupt_attempt_hash,
            "pcb-artifact-key": remove_pcb_png,
            "schematic-artifact-key": remove_schematic_png,
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                examples, project, baseline = _write_fixture(
                    self.root / f"incomplete-{label}"
                )
                sidecar_path = project / "boards" / "main.board.json"
                sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
                mutate(sidecar)
                sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

                original_baseline = baseline.read_bytes()
                with mock.patch.object(examples_lock, "EXAMPLES", examples):
                    measured = examples_lock.current(False)["demo-board"]
                self.assertEqual(
                    measured["evidence"]["status"], "IncompleteSidecar"
                )
                self.assertEqual(measured["blocking"], 10_000)
                self.assertFalse(measured["fabReady"])

                output = io.StringIO()
                with (
                    mock.patch.object(examples_lock, "EXAMPLES", examples),
                    mock.patch.object(examples_lock, "BASELINE", baseline),
                    contextlib.redirect_stdout(output),
                ):
                    result = examples_lock.main(
                        ["examples_lock.py", "--accept"]
                    )
                self.assertEqual(result, 1)
                self.assertEqual(baseline.read_bytes(), original_baseline)
                self.assertIn("REFUSED", output.getvalue())
                self.assertIn("IncompleteSidecar", output.getvalue())

    def test_clean_validation_block_without_warnings_is_fresh(self) -> None:
        examples, project, _baseline = _write_fixture(
            self.root / "clean-validation"
        )
        sidecar_path = project / "boards" / "main.board.json"
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar["validation"] = {}
        sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

        with mock.patch.object(examples_lock, "EXAMPLES", examples):
            measured = examples_lock.current(False)["demo-board"]
        self.assertEqual(measured["evidence"]["status"], "Fresh")
        self.assertEqual(measured["blocking"], 0)
        self.assertTrue(measured["fabReady"])

    def test_missing_canonical_board_artifact_is_explicit(self) -> None:
        examples, project, _baseline = _write_fixture(
            self.root / "missing-board-artifact"
        )
        (project / "boards" / "main.circuit.json").unlink()
        with mock.patch.object(examples_lock, "EXAMPLES", examples):
            measured = examples_lock.current(False)["demo-board"]
        self.assertEqual(measured["evidence"]["status"], "MissingArtifact")
        self.assertEqual(
            measured["evidence"]["missingArtifacts"], ["main.circuit.json"]
        )
        self.assertFalse(measured["fabReady"])

    def test_accept_refuses_stale_missing_and_crashed_evidence(self) -> None:
        for mode in ("stale", "missing"):
            with self.subTest(mode=mode):
                examples, project, baseline = _write_fixture(self.root / mode)
                if mode == "stale":
                    product = project / "product.json"
                    product.write_text(
                        product.read_text(encoding="utf-8") + "\n",
                        encoding="utf-8",
                    )
                else:
                    (project / "boards" / "main.board.json").unlink()
                original_baseline = baseline.read_bytes()
                output = io.StringIO()
                with (
                    mock.patch.object(examples_lock, "EXAMPLES", examples),
                    mock.patch.object(examples_lock, "BASELINE", baseline),
                    contextlib.redirect_stdout(output),
                ):
                    result = examples_lock.main(
                        ["examples_lock.py", "--accept"]
                    )
                self.assertEqual(result, 1)
                self.assertEqual(baseline.read_bytes(), original_baseline)
                self.assertIn("REFUSED", output.getvalue())

        examples, _project, baseline = _write_fixture(self.root / "crashed")
        original_baseline = baseline.read_bytes()
        crashed = {
            "demo-board": examples_lock.invalid_measurement(
                "BuildCrashed", detail="synthetic crash"
            )
        }
        output = io.StringIO()
        with (
            mock.patch.object(examples_lock, "EXAMPLES", examples),
            mock.patch.object(examples_lock, "BASELINE", baseline),
            mock.patch.object(examples_lock, "current", return_value=crashed),
            contextlib.redirect_stdout(output),
        ):
            result = examples_lock.main(
                ["examples_lock.py", "--rebuild", "--accept"]
            )
        self.assertEqual(result, 1)
        self.assertEqual(baseline.read_bytes(), original_baseline)
        self.assertIn("BuildCrashed", output.getvalue())

    def test_accept_refuses_real_ratchet_regressions(self) -> None:
        for regression in ("blocking", "fab-ready"):
            with self.subTest(regression=regression):
                examples, project, baseline = _write_fixture(
                    self.root / f"regression-{regression}"
                )
                sidecar_path = project / "boards" / "main.board.json"
                sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
                if regression == "blocking":
                    sidecar["validation"]["warnings"].append(
                        {"severity": "error", "kind": "new_drc_violation"}
                    )
                else:
                    sidecar["fab"]["ready"] = False
                sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

                original_baseline = baseline.read_bytes()
                output = io.StringIO()
                with (
                    mock.patch.object(examples_lock, "EXAMPLES", examples),
                    mock.patch.object(examples_lock, "BASELINE", baseline),
                    contextlib.redirect_stdout(output),
                ):
                    result = examples_lock.main(
                        ["examples_lock.py", "--accept"]
                    )

                self.assertEqual(result, 1)
                self.assertEqual(baseline.read_bytes(), original_baseline)
                self.assertIn("REFUSED", output.getvalue())
                self.assertIn("regression", output.getvalue())


if __name__ == "__main__":
    unittest.main()
