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
