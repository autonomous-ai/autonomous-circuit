from __future__ import annotations

import importlib.machinery
import importlib.util
import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from scripts.sync_golden_blocks import sync_project


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "review-packet"

loader = importlib.machinery.SourceFileLoader("review_packet_under_test", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
review_packet = importlib.util.module_from_spec(spec)
loader.exec_module(review_packet)
if str(review_packet.CIRCUITPY_SRC) not in sys.path:
    sys.path.insert(0, str(review_packet.CIRCUITPY_SRC))


class ReviewPacketFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "demo"
        (self.project / "boards").mkdir(parents=True)
        (self.project / "blocks").mkdir()
        (self.project / "boards" / "main.tsx").write_text(
            'import { Thing } from "../blocks/thing/thing"\nexport default Thing\n',
            encoding="utf-8",
        )
        source = Path(self.temp.name) / "golden"
        source.mkdir()
        (source / "glue.tsx").write_text(
            "export const Glue = () => null\n", encoding="utf-8"
        )
        (source / "thing").mkdir()
        (source / "thing" / "thing.tsx").write_text(
            "export const Thing = () => null\n", encoding="utf-8"
        )
        sync_project(self.project, blocks=["thing"], source=source)
        (self.project / "product.json").write_text(
            json.dumps({"name": "demo", "power": "usb-c-5v"}),
            encoding="utf-8",
        )
        (self.project / "parts.json").write_text("{}\n", encoding="utf-8")
        evidence = {
            "main.circuit.json",
            *review_packet.BOARD_ARTIFACT_KEYS.values(),
        }
        for relative in evidence:
            artifact = self.project / "boards" / relative
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"fixture\n")
        (self.project / "boards" / "main.circuit.json").write_text(
            "[]\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _sidecar(self) -> dict:
        from circuitpy.generation import (
            _current_toolchain_block,
            _publish_routing_attempt_evidence,
            _routing_attempt_evidence,
            pipeline_revision,
        )
        from circuitpy.source_hash import board_source_hash

        identity = board_source_hash(
            self.project / "boards" / "main.tsx", self.project
        )
        staged_attempts = self.project / ".staged-attempts"
        attempt_record = _routing_attempt_evidence(
            attempt_index=1,
            effort="default",
            warnings=[],
            circuit_json_path=self.project / "boards" / "main.circuit.json",
            staged_dir=staged_attempts,
            stem="main",
        )
        _publish_routing_attempt_evidence(
            staged_attempts, self.project / "boards", "main"
        )
        return {
            "generatorRevision": pipeline_revision(),
            "toolchain": _current_toolchain_block(),
            "source": {
                "path": "boards/main.tsx",
                "hash": identity.source_hash,
                "fingerprint": identity.source_fingerprint,
            },
            "board": {"path": "main.circuit.json"},
            "build": {
                "autorouterEffort": "default",
                "attempts": 1,
                "blockingByAttempt": [0],
                "attemptEvidence": [attempt_record],
            },
            "artifacts": dict(review_packet.BOARD_ARTIFACT_KEYS),
            "validation": {"warnings": []},
            "bom": {"lines": 1, "orderable": 1},
            "fab": {
                "ready": True,
                "profile": "jlcpcb",
                "gerberSource": "kicad-cli",
            },
        }

    def test_current_sidecar_is_accepted(self) -> None:
        self.assertIsNone(
            review_packet.sidecar_freshness_error(self.project, self._sidecar())
        )

    def test_imported_block_edit_invalidates_fingerprint(self) -> None:
        sidecar = self._sidecar()
        (self.project / "blocks" / "thing" / "thing.tsx").write_text(
            "export const Thing = () => 2\n", encoding="utf-8"
        )

        error = review_packet.sidecar_freshness_error(self.project, sidecar)

        self.assertIsNotNone(error)
        self.assertEqual(
            error,
            "golden-block snapshot drift: snapshot changed thing/thing.tsx",
        )

    def test_entry_edit_invalidates_hash_and_fingerprint(self) -> None:
        sidecar = self._sidecar()
        (self.project / "boards" / "main.tsx").write_text(
            'import { Thing } from "../blocks/thing"\nexport default () => Thing\n',
            encoding="utf-8",
        )

        error = review_packet.sidecar_freshness_error(self.project, sidecar)

        self.assertIsNotNone(error)
        self.assertIn("entry hash", error)
        self.assertIn("source fingerprint", error)

    def test_missing_or_modified_golden_snapshot_lock_is_rejected(self) -> None:
        sidecar = self._sidecar()
        lock = self.project / "golden-blocks.lock.json"
        original = lock.read_bytes()

        lock.unlink()
        missing = review_packet.sidecar_freshness_error(self.project, sidecar)
        self.assertIn("golden-block snapshot is not locked", missing)

        lock.write_bytes(original)
        data = json.loads(lock.read_text(encoding="utf-8"))
        data["treeSha256"] = "0" * 64
        lock.write_text(json.dumps(data), encoding="utf-8")
        invalid = review_packet.sidecar_freshness_error(self.project, sidecar)
        self.assertIn("treeSha256 does not match", invalid)

    def test_legacy_sidecar_without_fingerprint_is_rejected(self) -> None:
        sidecar = self._sidecar()
        del sidecar["source"]["fingerprint"]

        error = review_packet.sidecar_freshness_error(self.project, sidecar)

        self.assertEqual(error, "main.board.json has no source.fingerprint")

    def test_stale_pipeline_revision_is_rejected(self) -> None:
        sidecar = self._sidecar()
        sidecar["generatorRevision"] = "old-pipeline"

        error = review_packet.sidecar_freshness_error(self.project, sidecar)

        self.assertIsNotNone(error)
        self.assertIn("pipeline revision", error)

    def test_stale_patched_toolchain_is_rejected(self) -> None:
        sidecar = self._sidecar()
        sidecar["toolchain"]["coreBundleSha256"] = "0" * 64

        error = review_packet.sidecar_freshness_error(self.project, sidecar)

        self.assertIsNotNone(error)
        self.assertIn("toolchain identity", error)

    def test_source_fresh_but_unready_sidecar_is_rejected(self) -> None:
        sidecar = self._sidecar()
        sidecar["fab"]["ready"] = False

        error = review_packet.sidecar_freshness_error(self.project, sidecar)

        self.assertEqual(error, "main.board.json does not prove fab.ready == true")

    def test_source_fresh_but_incomplete_sidecar_is_rejected(self) -> None:
        cases = {
            "validation": "main.board.json has no validation block",
            "build": "main.board.json build (missing or not an object)",
            "bom": "main.board.json has no bom block",
            "artifacts": "main.board.json has no artifact manifest",
        }
        for field, expected in cases.items():
            with self.subTest(field=field):
                sidecar = self._sidecar()
                del sidecar[field]
                self.assertEqual(
                    review_packet.sidecar_freshness_error(self.project, sidecar),
                    expected,
                )

    def test_routing_attempt_evidence_must_select_the_published_circuit(self) -> None:
        sidecar = self._sidecar()
        sidecar["build"]["attemptEvidence"][0]["circuitSha256"] = "0" * 64

        error = review_packet.sidecar_freshness_error(self.project, sidecar)

        self.assertEqual(
            error,
            "main.board.json build.attemptEvidence[0].circuitPath is not its "
            "canonical content-addressed path",
        )

    def test_self_consistent_fabricated_scan_is_independently_rejected(self) -> None:
        from circuitpy import generation

        sidecar = self._sidecar()
        record = sidecar["build"]["attemptEvidence"][0]
        scan_path = self.project / "boards" / record["preExportScanPath"]
        payload = json.loads(scan_path.read_text(encoding="utf-8"))
        payload["warnings"] = [
            {
                "part": "TR_FAKE",
                "kind": "pcb_trace_error",
                "detail": "fabricated but internally content-addressed",
                "severity": "error",
            }
        ]
        forged = generation._canonical_json(payload).encode("utf-8")
        digest = hashlib.sha256(forged).hexdigest()
        relative = generation._attempt_relative_path(
            "main", 1, digest, "pre-export-scan.json"
        )
        (self.project / "boards" / relative).write_bytes(forged)
        record["preExportScanPath"] = relative
        record["preExportScanSha256"] = digest
        record["blocking"] = 1
        record["routingBlocking"] = 1
        record["blockingKinds"] = {"pcb_trace_error": 1}
        sidecar["build"]["blockingByAttempt"] = [1]
        sidecar["validation"]["warnings"] = payload["warnings"]
        sidecar["fab"]["ready"] = False

        error = review_packet.sidecar_freshness_error(self.project, sidecar)

        self.assertEqual(
            error,
            "main.board.json build.attemptEvidence[0].preExportScanPath does "
            "not match an independent current-toolchain scan",
        )

    def test_missing_required_artifact_is_rejected(self) -> None:
        sidecar = self._sidecar()
        missing = self.project / "boards" / sidecar["artifacts"]["gerbers"]
        missing.unlink()

        error = review_packet.sidecar_freshness_error(self.project, sidecar)

        self.assertEqual(
            error,
            "main.board.json artifacts.gerbers is missing: main_fab/gerbers.zip",
        )

    def test_main_refuses_before_writing_any_review_output(self) -> None:
        sidecar = self._sidecar()
        (self.project / "boards" / "main.board.json").write_text(
            json.dumps(sidecar), encoding="utf-8"
        )
        (self.project / "blocks" / "thing" / "thing.tsx").write_text(
            "export const Thing = () => 3\n", encoding="utf-8"
        )
        marker = self.project / "REVIEW.md"
        marker.write_text("keep me\n", encoding="utf-8")
        examples = Path(self.temp.name) / "examples"
        examples.mkdir()
        self.project.rename(examples / self.project.name)
        project = examples / self.project.name
        empty_blocks = Path(self.temp.name) / "golden-blocks"
        empty_blocks.mkdir()
        stderr = io.StringIO()

        with (
            mock.patch.object(review_packet, "EXAMPLES_DIR", examples),
            mock.patch.object(review_packet, "BLOCKS_DIR", empty_blocks),
            redirect_stderr(stderr),
        ):
            result = review_packet.main([])

        self.assertEqual(result, 1)
        self.assertEqual((project / "REVIEW.md").read_text(), "keep me\n")
        self.assertFalse((project / "findings.json").exists())
        self.assertIn("demo", stderr.getvalue())
        self.assertIn("refusing to publish", stderr.getvalue())

    def test_main_does_not_skip_a_product_with_no_sidecar(self) -> None:
        marker = self.project / "REVIEW.md"
        marker.write_text("keep me\n", encoding="utf-8")
        examples = Path(self.temp.name) / "examples"
        examples.mkdir()
        self.project.rename(examples / self.project.name)
        project = examples / self.project.name
        empty_blocks = Path(self.temp.name) / "golden-blocks"
        empty_blocks.mkdir()
        stderr = io.StringIO()

        with (
            mock.patch.object(review_packet, "EXAMPLES_DIR", examples),
            mock.patch.object(review_packet, "BLOCKS_DIR", empty_blocks),
            redirect_stderr(stderr),
        ):
            result = review_packet.main([])

        self.assertEqual(result, 1)
        self.assertEqual((project / "REVIEW.md").read_text(), "keep me\n")
        self.assertIn("missing boards/main.board.json", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
