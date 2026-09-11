"""build-status.json names the process writing it.

The app's driver kills a stopped turn's provider, but a generator the agent
backgrounded is not that provider's child. Measured 2026-09-10 (pomodoro-puck
run #4): a build launched 13:01:58 survived the 13:05:38 stop and landed at
13:09:26, after the verdict had been read and both undo copies deleted. The
pid and pgid let the driver tell a live build from a stale status file, and
reach the one that is live.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import status  # noqa: E402


class BuildStatusTest(unittest.TestCase):

    def test_stage_and_finish_carry_the_writer_pid_and_pgid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            s = status.BuildStatus(root, stem="main")
            s.stage("compile")
            running = json.loads(status.status_path(root).read_text())
            self.assertEqual(running["state"], "running")
            self.assertEqual(running["pid"], os.getpid())
            self.assertEqual(running["pgid"], os.getpgid(0))
            s.finish(ok=True, detail="0 blocking")
            done = json.loads(status.status_path(root).read_text())
            self.assertEqual(done["state"], "done")
            self.assertEqual(done["pid"], os.getpid())


class RepairHistoryTest(unittest.TestCase):
    """A `--recheck` used to reset `repairMode.repairs` to []; history accumulates now."""

    def test_history_is_prior_history_plus_prior_round(self):
        from circuitpy import generation
        with tempfile.TemporaryDirectory() as tmp:
            sc = Path(tmp) / "main.board.json"
            self.assertEqual(generation._repair_history_on_record(sc), [])
            sc.write_text(json.dumps({"build": {"repairMode": {
                "history": [{"op": "move_via", "via": "v1"}],
                "repairs": [{"op": "move_point", "trace": "t", "index": 3}],
            }}}))
            hist = generation._repair_history_on_record(sc)
            self.assertEqual([h["op"] for h in hist], ["move_via", "move_point"])
            self.assertEqual(generation._repairs_on_record(sc), 2)
            sc.write_text("{not json")
            self.assertEqual(generation._repair_history_on_record(sc), [])

class RefillDefaultTest(unittest.TestCase):
    """`CIRCUIT_KICAD_REFILL`: the re-pour is on unless told off."""

    def setUp(self):
        from circuitpy import generation
        self.generation = generation

    def test_unset_and_anything_else_means_refill(self):
        for v in (None, "", "1", "yes", "please"):
            self.assertTrue(self.generation._refill_wanted(v), v)

    def test_only_an_explicit_off_keeps_the_converters_fills(self):
        for v in ("0", "off", "false", "no", " OFF "):
            self.assertFalse(self.generation._refill_wanted(v), v)

