"""Build progress, written where the app can see it.

A board build runs seven stages and takes 45–90 seconds. Until now the user
watched a spinner for a minute and a half with no idea whether the toolchain
was compiling, waiting on KiCad, or hung — and a build that *is* hung looks
exactly like one that is working. Vibe solved the same problem for CAD with a
generation-status file (`cadpy/generation_status.py`); this is the circuit
equivalent, kept deliberately simpler.

Design notes:

* Written to ``.circuit/`` — the snapshotter and catalog both skip that
  directory, so progress updates never masquerade as new artifacts.
* Updated on **stage transitions only**, not on a heartbeat. Seven writes per
  build gives the user real progress without turning the event stream into a
  firehose; ``updatedAt`` still lets a reader spot a stalled stage.
* Best-effort throughout. Progress reporting must never be the reason a build
  fails, so every write swallows its errors.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

STATUS_FILENAME = "build-status.json"
SCHEMA_VERSION = 1

#: The user-facing stage list, in order. Keep in step with generation.py's
#: stages and with contract §1 — the numbers show up in the UI.
STAGES: tuple[tuple[str, str], ...] = (
    ("compile", "Compiling the board"),
    ("scan", "Reading the compiler's findings"),
    ("checks", "Running the independent checks"),
    ("substrate", "Cross-checking with KiCad"),
    ("dfm", "Checking it can be manufactured"),
    ("export", "Writing the fab packet"),
    ("render", "Drawing the schematic and board"),
)
STAGE_INDEX = {name: i for i, (name, _) in enumerate(STAGES)}


def status_path(project_root: Path) -> Path:
    return Path(project_root) / ".circuit" / STATUS_FILENAME


class BuildStatus:
    """Records which stage a build is on. Never raises."""

    def __init__(self, project_root: Path, *, stem: str) -> None:
        self._path = status_path(project_root)
        self._stem = stem
        self._run_id = f"{int(time.time() * 1000):x}-{os.getpid():x}"
        self._started = time.time()

    def _write(self, payload: dict) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            pass  # progress must never break a build

    def stage(self, name: str) -> None:
        """Mark a stage as started."""
        index = STAGE_INDEX.get(name)
        label = next((text for key, text in STAGES if key == name), name)
        self._write({
            "schema": SCHEMA_VERSION,
            "runId": self._run_id,
            "board": self._stem,
            "state": "running",
            "stage": name,
            "stageLabel": label,
            "stageIndex": (index or 0) + 1,
            "stageCount": len(STAGES),
            "startedAt": round(self._started, 3),
            "updatedAt": round(time.time(), 3),
        })

    def finish(self, *, ok: bool, detail: str = "") -> None:
        self._write({
            "schema": SCHEMA_VERSION,
            "runId": self._run_id,
            "board": self._stem,
            "state": "done" if ok else "failed",
            "stageIndex": len(STAGES),
            "stageCount": len(STAGES),
            "detail": detail[:300],
            "startedAt": round(self._started, 3),
            "updatedAt": round(time.time(), 3),
            "elapsedS": round(time.time() - self._started, 2),
        })

    def clear(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass


def read_status(project_root: Path) -> dict | None:
    """The current build status, or None. For the server/UI side."""
    try:
        return json.loads(status_path(project_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
