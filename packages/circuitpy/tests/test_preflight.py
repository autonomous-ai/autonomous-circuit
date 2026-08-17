"""preflight.py — the placement verdict that does not pay for routing.

The loop an engineer lives in is place, check, place again, and the only check
that could see a placement cost a full build: 20-40 minutes, because the
compile *routes the board* and routing is the expensive part (ledger #48).
With `routingDisabled` the same board compiles in ~17s and the same checks run
in ~0.4s.

Two things have to be true for that to be worth having, and both are asserted
here: it must **catch a placement defect**, and it must **say what it did not
look at** rather than reading as a clean board.
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from circuitpy import preflight, toolchain  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = REPO_ROOT / "examples" / "hydrate-coaster"


def _toolchain_ready() -> bool:
    try:
        return Path(toolchain.tscircuit_cli_exe()).exists()
    except Exception:  # noqa: BLE001
        return False


class DisableRouting(unittest.TestCase):
    def test_the_prop_lands_on_the_board_tag(self) -> None:
        text, changed = preflight.disable_routing('export default () => (\n  <board width="20mm">\n')
        self.assertTrue(changed)
        self.assertIn("<board routingDisabled", text)

    def test_a_multiline_board_tag_is_still_patched_correctly(self) -> None:
        # The close of a board tag is often many lines down, past props that
        # carry `>` inside strings — which is why the prop goes in immediately
        # after `<board` rather than before the tag's own `>`.
        source = (
            'export default () => (\n'
            '  <board\n'
            '    width="80mm"\n'
            '    autorouterEffortLevel="5x"\n'
            '  >\n'
            '    <trace from=".U3 > .GPIO1" to="net.X" />\n'
            "  </board>\n)\n"
        )
        text, changed = preflight.disable_routing(source)
        self.assertTrue(changed)
        self.assertIn("<board routingDisabled\n", text)
        # And nothing else moved.
        self.assertIn('<trace from=".U3 > .GPIO1" to="net.X" />', text)

    def test_it_is_idempotent_and_refuses_a_file_with_no_board(self) -> None:
        once, _ = preflight.disable_routing("<board />")
        twice, changed = preflight.disable_routing(once)
        self.assertEqual(once, twice)
        self.assertFalse(changed)
        text, changed = preflight.disable_routing("export const X = 1")
        self.assertFalse(changed)
        self.assertEqual(text, "export const X = 1")


class Contract(unittest.TestCase):
    def test_a_missing_board_is_a_result_not_an_exception(self) -> None:
        result = preflight.preflight(REPO_ROOT, "boards/does-not-exist.tsx")
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "unavailable")
        # Even a failure carries the blind spots, because the caller prints
        # them and a missing list reads as "nothing was skipped".
        self.assertTrue(result["not_checked"])

    def test_the_blind_spots_name_routing_first(self) -> None:
        blind = json.dumps(preflight.NOT_CHECKED)
        self.assertIn("anything about routing", blind)
        self.assertIn("fab packet", blind)
        self.assertIn("KiCad", blind)


@unittest.skipUnless(EXAMPLE.is_dir() and _toolchain_ready(), "example board or pinned toolchain not present")
class RealBoard(unittest.TestCase):
    """The two claims the feature rests on, on a real board."""

    def test_a_shipped_board_comes_back_clean_and_says_what_it_skipped(self) -> None:
        result = preflight.preflight(EXAMPLE)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["verdict"], "clean", result["counts"])
        self.assertEqual(result["geometry"], "placement_only")
        # The routing findings are guaranteed by construction with the router
        # off, so they are dropped — and counted, because a check that silently
        # withholds findings is the thing this pipeline refuses to be.
        self.assertGreater(result["dropped_routing_findings"], 0)
        self.assertIn("anything about routing", json.dumps(result["not_checked"]))

    def test_a_part_dropped_on_another_is_caught(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="preflight-break-") as scratch:
            project = Path(scratch) / "board"
            shutil.copytree(
                EXAMPLE,
                project,
                ignore=shutil.ignore_patterns(".circuit", "*_fab", "*_review", "*.json"),
            )
            shutil.copy2(EXAMPLE / "product.json", project / "product.json")
            source = project / "boards" / "main.tsx"
            text = source.read_text(encoding="utf-8")
            broken = text.replace(
                '<SwTact name="SW1" signal="BTN_MUTE" pcbX={29} pcbY={-24}',
                '<SwTact name="SW1" signal="BTN_MUTE" pcbX={-20} pcbY={-22}',
                1,
            )
            self.assertNotEqual(broken, text, "the fixture placement moved under this test")
            source.write_text(broken, encoding="utf-8")

            result = preflight.preflight(project)
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(result["verdict"], "blocked")
            kinds = {w["kind"] for w in result["warnings"] if w["severity"] == "error"}
            # The three ways a board says "these two parts are in the same
            # place", all of them placement facts that need no copper.
            self.assertIn("pcb_courtyard_overlap_error", kinds)
            self.assertIn("pcb_footprint_overlap_error", kinds)
            self.assertIn("pcb_pad_pad_clearance_error", kinds)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
