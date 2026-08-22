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

    # -- #27: the clearance margin pass -------------------------------------
    # `clearance_margin_warnings` is unit-tested against a real DRC fixture in
    # test_checks.py. What those tests cannot reach is the wiring: that a
    # second kicad-cli run actually happens on a real build, parses, and puts
    # the fab's own rules back. This class is the only place a real one runs.

    def _warnings(self) -> list:
        return (self.sidecar.get("validation") or {}).get("warnings") or []

    def test_the_margin_pass_ran_without_dying(self) -> None:
        """A pass that crashes is advisory-only by design, which is exactly the
        shape that goes unnoticed. If it fires here, the wiring is broken and
        every board silently loses the measurement."""
        excuses = [
            w["detail"] for w in self._warnings()
            if w["kind"] == "check_failed" and "margin pass" in w["detail"]
        ]
        self.assertEqual(excuses, [])

    def test_a_clean_skeleton_has_room_to_spare_and_the_pass_says_nothing(self) -> None:
        """The good board is 2 parts on a big envelope. Silence here is the
        pass working — anything else means it is reading the wrong number."""
        kinds = {w["kind"] for w in self._warnings()}
        self.assertNotIn("clearance_under_fab_floor", kinds)
        self.assertNotIn("clearance_no_margin", kinds)


@unittest.skipUnless(KICAD, "kicad-cli not installed")
class TheMarginPassLeavesTheGatesRulesBehind(unittest.TestCase):
    """#27. The work directory is deleted when a build finishes — that is the
    "workspace stays clean of build litter" contract — so the only way to see
    what the margin pass did to the `.kicad_pro` is to watch it happen.

    Two invariants, both invisible from the artifacts: the margin pass writes
    its report to its own file (overwriting `drc.json` would hand the gate's
    parser 399 findings taken at the wrong floor and block every board), and
    the last thing written to the project file is the fab's own rules, not our
    0.127mm design margin. A human opening the packet reads that file.
    """

    @classmethod
    def setUpClass(cls) -> None:
        from circuitpy import fab as fab_mod

        cls.drc_calls: list = []
        cls.pro_writes: list = []
        real_run, real_write = toolchain.run_kicad, fab_mod.write_kicad_project

        def spy_run(args, **kwargs):
            if list(args)[:2] == ["pcb", "drc"]:
                cls.drc_calls.append(list(args))
            return real_run(args, **kwargs)

        def spy_write(board_path, profile, **kwargs):
            cls.pro_writes.append(kwargs.get("clearance_mm"))
            return real_write(board_path, profile, **kwargs)

        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "proj"
        circuitproj.write_project(root, tsx=circuitproj.GOOD_TSX)
        toolchain.run_kicad = spy_run
        fab_mod.write_kicad_project = spy_write
        generation.fab_mod.write_kicad_project = spy_write
        try:
            generation.build_board(
                root / "boards" / "main.tsx", root / "boards" / "main.circuit.json"
            )
        finally:
            toolchain.run_kicad = real_run
            fab_mod.write_kicad_project = real_write
            generation.fab_mod.write_kicad_project = real_write

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_the_board_is_measured_twice_at_two_different_floors(self) -> None:
        self.assertEqual(len(self.drc_calls), 2, self.drc_calls)
        self.assertEqual(self.pro_writes[:2], [None, 0.127])

    def test_the_margin_report_never_overwrites_the_gates(self) -> None:
        outs = [c[c.index("-o") + 1] for c in self.drc_calls]
        self.assertEqual(len(set(outs)), 2, outs)
        self.assertTrue(outs[0].endswith("drc.json"), outs)
        self.assertTrue(outs[1].endswith("drc-margin.json"), outs)

    def test_the_last_word_on_the_project_file_is_the_fabs(self) -> None:
        """Not "a restore happened" — the *final* write, because anything
        written after our margin rules is what the packet ends up carrying."""
        self.assertIsNone(self.pro_writes[-1], self.pro_writes)

    def test_the_margin_pass_carries_the_gates_flags(self) -> None:
        """--all-track-errors or kicad stops at the first fault per track: on
        weather-badge-23 that is 300 clearance findings instead of 399."""
        self.assertIn("--all-track-errors", self.drc_calls[1])
        self.assertIn("--severity-all", self.drc_calls[1])


@unittest.skipUnless(KICAD, "kicad-cli not installed")
class TheSeededBoardSaysSoInItsSidecar(unittest.TestCase):
    """#29, end to end. `blocklib` is unit-tested on its own; what those tests
    cannot see is whether the finding survives `build_board` and reaches the
    sidecar the app reads.

    It did not, the first time. The findings were assembled at the top of the
    build and the escalation retry does `warnings = retry_warnings`, so the
    first board that needed a second routing attempt dropped them silently and
    shipped a verdict that said nothing about where its blocks came from. Found
    on a real build, not in review. They are now folded in after every path
    that can replace the list.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "proj"
        # write_project does not create blocks/ — that is the case under test.
        circuitproj.write_project(cls.root, tsx=circuitproj.GOOD_TSX)
        generation.build_board(
            cls.root / "boards" / "main.tsx",
            cls.root / "boards" / "main.circuit.json",
        )
        cls.sidecar = json.loads(
            (cls.root / "boards" / "main.board.json").read_text(encoding="utf-8")
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _warnings(self) -> list:
        return (self.sidecar.get("validation") or {}).get("warnings") or []

    def test_the_library_was_copied_in(self) -> None:
        blocks = self.root / "blocks"
        self.assertTrue(blocks.is_dir(), "the build did not seed blocks/")
        self.assertTrue(list(blocks.glob("*/*.tsx")), "seeded an empty blocks/")

    def test_the_sidecar_records_where_the_blocks_came_from(self) -> None:
        """The whole point. A board must be able to say which library it was
        built against; eight boards in a row could not, and nobody noticed for
        two and a half days."""
        seeded = [w for w in self._warnings() if w["kind"] == "block_library_seeded"]
        self.assertEqual(len(seeded), 1, [w["kind"] for w in self._warnings()])
        self.assertEqual(seeded[0]["severity"], "info")

    def test_a_freshly_seeded_board_is_not_also_accused_of_drifting(self) -> None:
        kinds = {w["kind"] for w in self._warnings()}
        self.assertNotIn("block_library_not_seeded", kinds)
        self.assertNotIn("block_library_drift", kinds)
        self.assertNotIn("block_library_unavailable", kinds)

    def test_none_of_it_blocks_the_board(self) -> None:
        from circuitpy import fab as fab_mod

        found = [w for w in self._warnings() if w["kind"].startswith("block_library")]
        self.assertTrue(fab_mod.fab_ready(found, "kicad-cli"))


if __name__ == "__main__":
    unittest.main()
