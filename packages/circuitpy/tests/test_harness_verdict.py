"""`.harness/verdict.json` — the Harness DSH verdict (spec 1), derived from the sidecar.

The sidecar is the app's machine contract; the verdict is the same fact in the one shape
every domain harness shares, so a pane header can say "fab-ready" or "3 errors, 2 warnings".
Pure derivation is tested here without a toolchain; the e2e build asserts the file lands.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import generation  # noqa: E402


def _sidecar(*, ready: bool, warnings: list[dict] | None = None) -> dict:
    payload: dict = {
        "generator": "circuitpy",
        "fab": {"profile": "jlcpcb", "ready": ready, "assembly": True, "gerberSource": "kicad-cli"},
    }
    if warnings:
        payload["validation"] = {"warnings": warnings}
    return payload


class HarnessVerdictTest(unittest.TestCase):

    def test_ready_is_fab_ready_and_nothing_weaker(self):
        v = generation.harness_verdict(_sidecar(ready=True), artifact="boards/main.board.json")
        self.assertEqual(v["spec"], 1)
        self.assertIs(v["ready"], True)
        self.assertEqual(v["summary"], "Fab-ready")
        self.assertEqual(v["findings"], [])
        self.assertEqual(v["artifact"], "boards/main.board.json")
        self.assertRegex(v["updatedAt"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_findings_keep_severity_and_the_open_kind_and_count_into_the_summary(self):
        warnings = [
            {"part": "U3.pin7", "kind": "source_trace_not_connected_error", "detail": "pin 7 floats", "severity": "error"},
            {"part": "board", "kind": "dfm_trace_width", "detail": "0.15mm < 0.2mm", "severity": "error"},
            {"part": "R4", "kind": "part_drift", "detail": "lock says C1234", "severity": "warning"},
            {"part": "board", "kind": "kicad_unavailable", "detail": "no kicad-cli", "severity": "info"},
        ]
        v = generation.harness_verdict(_sidecar(ready=False, warnings=warnings), artifact="boards/main.board.json")
        self.assertIs(v["ready"], False)
        self.assertEqual(v["summary"], "2 errors, 1 warning")
        self.assertEqual(
            v["findings"][0],
            {"severity": "error", "kind": "source_trace_not_connected_error", "message": "pin 7 floats", "ref": "U3.pin7"},
        )
        self.assertEqual([f["severity"] for f in v["findings"]], ["error", "error", "warning", "info"])

    def test_a_clean_board_without_kicad_says_why_it_is_not_ready(self):
        warnings = [
            {"part": "board", "kind": "kicad_unavailable", "detail": "no kicad-cli", "severity": "info"},
            {"part": "board", "kind": "unverified_gerbers", "detail": "tscircuit export", "severity": "warning"},
        ]
        v = generation.harness_verdict(_sidecar(ready=False, warnings=warnings), artifact="boards/main.board.json")
        self.assertEqual(v["summary"], "1 warning, gerbers unverified — kicad-cli missing")

    def test_an_unknown_severity_degrades_to_info_rather_than_breaking_the_reader(self):
        v = generation.harness_verdict(
            _sidecar(ready=False, warnings=[{"part": "x", "kind": "k", "detail": "d", "severity": "fatal"}]),
            artifact="boards/main.board.json",
        )
        self.assertEqual(v["findings"][0]["severity"], "info")
        self.assertEqual(v["summary"], "Not fab-ready")

    def test_write_lands_atomically_under_dot_harness_with_a_project_relative_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sidecar_path = root / "boards" / "main.board.json"
            target = generation.write_harness_verdict(root, sidecar_path, _sidecar(ready=True))
            self.assertEqual(target, root / ".harness" / "verdict.json")
            on_disk = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["artifact"], "boards/main.board.json")
            self.assertIs(on_disk["ready"], True)
            self.assertFalse((root / ".harness" / "verdict.json.tmp").exists())

    def test_write_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # `.harness` exists as a FILE, so the directory cannot be made.
            (root / ".harness").write_text("in the way", encoding="utf-8")
            self.assertIsNone(
                generation.write_harness_verdict(root, root / "boards" / "main.board.json", _sidecar(ready=True))
            )


if __name__ == "__main__":
    unittest.main()
