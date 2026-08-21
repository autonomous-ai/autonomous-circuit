"""#29 — the golden-block library is the source of truth for a new board.

Measured 2026-08-21: weather-badge-16 through -25, eight projects created over
two and a half days, carried byte-identical `blocks/` stamped with wb-16's
creation minute and matching no version of the library. New boards had been
cloning the previous board's copy. The switch fix that unshorted every button
on every board with one sat in the library all afternoon and reached nothing.

The per-board freeze is deliberate and stays. Where the first copy comes from
is what these tests pin down.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from circuitpy import blocklib  # noqa: E402

REPO = Path(__file__).resolve().parents[3]


def _library(root: Path) -> Path:
    lib = root / "lib"
    (lib / "sw-tact").mkdir(parents=True)
    (lib / "sw-tact" / "sw-tact.tsx").write_text('pins=[["pin1","pin4"]]\n')
    (lib / "ldo-3v3").mkdir()
    (lib / "ldo-3v3" / "ldo-3v3.tsx").write_text("regulator\n")
    (lib / "sw-tact" / "BLOCK.md").write_text("# prose\n")
    return lib


def _project(root: Path) -> Path:
    proj = root / "proj"
    (proj / "boards").mkdir(parents=True)
    (proj / "product.json").write_text("{}")
    return proj


class FindingTheLibrary(unittest.TestCase):
    """`fab.catalog_root` carries the scar this function must not repeat: its
    first version counted `parents[n]` from `__file__`, which was right in the
    repo and wrong the moment the package was vendored a level deeper into the
    skill. Every BOM shipped a blank Footprint column and nothing said so."""

    def test_it_resolves_from_the_repo_layout(self) -> None:
        found = blocklib.library_root(
            REPO / "packages" / "circuitpy" / "src" / "circuitpy" / "blocklib.py"
        )
        self.assertEqual(found, REPO / "packages" / "golden-blocks" / "blocks")

    def test_it_resolves_from_the_vendored_layout(self) -> None:
        """The layout the predecessor broke on. Same function, one level deeper."""
        vendored = (REPO / "skills" / "circuitcode" / "scripts" / "packages"
                    / "circuitpy" / "blocklib.py")
        found = blocklib.library_root(vendored)
        self.assertEqual(found, REPO / "skills" / "circuitcode" / "blocks")

    def test_it_refuses_to_grade_a_board_against_its_own_copy(self) -> None:
        """A board directory has `product.json` beside its `blocks/`. Returning
        that would report every board as perfectly in sync, forever."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = _project(Path(tmp))
            (proj / "blocks" / "sw-tact").mkdir(parents=True)
            (proj / "blocks" / "sw-tact" / "sw-tact.tsx").write_text("x\n")
            self.assertIsNone(blocklib.library_root(proj / "boards" / "main.tsx"))

    def test_the_env_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib = _library(Path(tmp))
            import os
            os.environ["CIRCUIT_BLOCK_LIBRARY"] = str(lib)
            try:
                self.assertEqual(blocklib.library_root(), lib)
            finally:
                del os.environ["CIRCUIT_BLOCK_LIBRARY"]


class SeedingANewBoard(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lib = _library(self.root)
        self.proj = _project(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_project_with_no_blocks_gets_the_library(self) -> None:
        written = blocklib.seed_blocks(self.proj, self.lib)
        self.assertEqual(written, ["ldo-3v3", "sw-tact"])
        self.assertEqual(
            (self.proj / "blocks" / "sw-tact" / "sw-tact.tsx").read_text(),
            (self.lib / "sw-tact" / "sw-tact.tsx").read_text(),
        )

    def test_a_board_that_already_has_blocks_is_never_overwritten(self) -> None:
        """The one move this pipeline has been burned by: changing what a
        board's source means with no way back. Re-syncing is a deliberate act."""
        (self.proj / "blocks" / "sw-tact").mkdir(parents=True)
        old = self.proj / "blocks" / "sw-tact" / "sw-tact.tsx"
        old.write_text('pins=[["pin1","pin2"]]\n')  # the shorted pairing
        self.assertEqual(blocklib.seed_blocks(self.proj, self.lib), [])
        self.assertEqual(old.read_text(), 'pins=[["pin1","pin2"]]\n')

    def test_seeding_leaves_nothing_to_report(self) -> None:
        blocklib.seed_blocks(self.proj, self.lib)
        self.assertEqual(
            blocklib.drift_warnings(self.proj, is_first_build=True, library=self.lib),
            [],
        )


class TellingHistoryFromABug(unittest.TestCase):
    """Drift means two different things and they need different findings."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lib = _library(self.root)
        self.proj = _project(self.root)
        shutil.copytree(self.lib, self.proj / "blocks")
        # the wb-16..wb-25 shape: an older switch block, inherited
        (self.proj / "blocks" / "sw-tact" / "sw-tact.tsx").write_text(
            'pins=[["pin1","pin2"]]\n'
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_board_that_never_built_cannot_have_drifted(self) -> None:
        """Nothing has frozen yet, so the copy came from somewhere other than
        the library. That is #29, and it is a warning."""
        [w] = blocklib.drift_warnings(
            self.proj, is_first_build=True, library=self.lib
        )
        self.assertEqual(w["kind"], "block_library_not_seeded")
        self.assertEqual(w["severity"], "warning")
        self.assertIn("sw-tact", w["detail"])

    def test_a_board_with_a_history_is_the_freeze_working(self) -> None:
        [w] = blocklib.drift_warnings(
            self.proj, is_first_build=False, library=self.lib
        )
        self.assertEqual(w["kind"], "block_library_drift")
        self.assertEqual(w["severity"], "info")

    def test_a_matching_board_says_nothing(self) -> None:
        shutil.rmtree(self.proj / "blocks")
        shutil.copytree(self.lib, self.proj / "blocks")
        for first in (True, False):
            with self.subTest(first_build=first):
                self.assertEqual(
                    blocklib.drift_warnings(
                        self.proj, is_first_build=first, library=self.lib
                    ),
                    [],
                )

    def test_prose_drifting_is_not_a_finding(self) -> None:
        """BLOCK.md and REVIEW.md change for reasons that never move copper. A
        check that fires on prose is one people learn to scroll past."""
        (self.proj / "blocks" / "sw-tact" / "sw-tact.tsx").write_text(
            (self.lib / "sw-tact" / "sw-tact.tsx").read_text()
        )
        (self.proj / "blocks" / "sw-tact" / "BLOCK.md").write_text("# rewritten\n")
        self.assertEqual(
            blocklib.drift_warnings(self.proj, is_first_build=True, library=self.lib),
            [],
        )

    def test_a_block_the_board_never_got_is_reported(self) -> None:
        shutil.rmtree(self.proj / "blocks" / "ldo-3v3")
        [w] = blocklib.drift_warnings(
            self.proj, is_first_build=True, library=self.lib
        )
        self.assertIn("absent here", w["detail"])

    def test_no_library_is_said_out_loud_rather_than_passing(self) -> None:
        [w] = blocklib.drift_warnings(
            self.proj, is_first_build=True, library=self.root / "nope"
        )
        self.assertEqual(w["kind"], "block_library_unavailable")
        self.assertEqual(w["severity"], "info")


if __name__ == "__main__":
    unittest.main()
