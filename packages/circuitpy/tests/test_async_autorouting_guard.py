"""Fail-closed reconciliation for tscircuit's swallowed async route errors."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import checks, generation  # noqa: E402


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "async-autorouting-failed.stdout.txt"
)


class AsyncAutoroutingOutputGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.output = FIXTURE.read_text(encoding="utf-8")

    def test_output_only_failure_is_serialized_as_blocking_error(self) -> None:
        elements = [{"type": "pcb_board", "pcb_board_id": "pcb_board_0"}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "circuit.json"
            path.write_text(json.dumps(elements), encoding="utf-8")

            changed = generation._reconcile_async_autorouting_failure(
                elements, self.output, path
            )

            self.assertTrue(changed)
            persisted = json.loads(path.read_text(encoding="utf-8"))

        errors = [e for e in persisted if e.get("type") == "pcb_autorouting_error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("only 20 of 31", errors[0]["message"])
        self.assertIn("artifact may be partial", errors[0]["message"])
        harvested = checks.harvest_circuit_json(persisted)
        self.assertTrue(
            any(
                finding["kind"] == "pcb_autorouting_error"
                and finding["severity"] == "error"
                for finding in harvested
            )
        )

    def test_matching_serialized_error_is_not_duplicated(self) -> None:
        elements = [
            {
                "type": "pcb_autorouting_error",
                "pcb_autorouting_error_id": "pcb_autorouting_error_0",
                "message": (
                    "Fanout failed: only 20 of 31 connections could escape "
                    "to the breakout boundary."
                ),
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "circuit.json"
            original = json.dumps(elements)
            path.write_text(original, encoding="utf-8")

            changed = generation._reconcile_async_autorouting_failure(
                elements, self.output, path
            )

            self.assertFalse(changed)
            self.assertEqual(len(elements), 1)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_successful_output_does_not_mutate_artifact(self) -> None:
        elements = [{"type": "pcb_board", "pcb_board_id": "pcb_board_0"}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "circuit.json"
            original = json.dumps(elements)
            path.write_text(original, encoding="utf-8")

            changed = generation._reconcile_async_autorouting_failure(
                elements,
                "Build complete\n  Circuits  1 passed\n\n✓ Done\n",
                path,
            )

            self.assertFalse(changed)
            self.assertEqual(elements, [{"type": "pcb_board", "pcb_board_id": "pcb_board_0"}])
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_non_routing_async_failure_refuses_partial_artifact(self) -> None:
        output = """
Async effect error in PcbCopperPourRender "PcbCopperPourRender":
Error: polygon solver emitted no valid material faces
    at CopperPour.render (index.js:1:2)
Build complete
  Circuits  1 passed
"""

        parsed = generation._async_effect_failures(output)
        self.assertEqual(
            parsed,
            [
                (
                    "PcbCopperPourRender",
                    "PcbCopperPourRender",
                    "polygon solver emitted no valid material faces",
                )
            ],
        )
        with self.assertRaisesRegex(
            generation.CompileError,
            r"PcbCopperPourRender.*polygon solver emitted no valid material faces",
        ):
            generation._refuse_non_routing_async_failures(
                output, "boards/main.tsx"
            )

    def test_autorouting_async_failure_uses_schema_reconciliation_not_compile_refusal(self) -> None:
        generation._refuse_non_routing_async_failures(
            self.output, "boards/main.tsx"
        )


if __name__ == "__main__":
    unittest.main()
