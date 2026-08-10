"""``python scripts/check <episodes/epNNN.py | project_dir>`` — validate cheaply.

Runs the episode through the dramapy pipeline on the **mock** provider into
a tempdir, then deletes the artifacts. Prints ``{ok, duration_s,
shot_count, warnings, error?}`` on stdout — the path fields from the full
``scripts/drama`` JSON are stripped because they pointed at a tempdir.

Use this to sanity-check an episode's structure (validators + beat-law
warnings) before paying for a real render.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common.runner import run_sandboxed_sync


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="scripts/check",
        description=(
            "Validate a drama episode: spec shape, shot rules, beat-law "
            "warnings — mock provider, artifacts discarded."
        ),
    )
    p.add_argument("input", type=Path)
    p.add_argument(
        "--wall-clock-s",
        type=float,
        default=60.0,
        help="Wall-clock timeout (seconds, default 60 — mock renders are fast).",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    if not args.input.exists():
        print(json.dumps({
            "ok": False,
            "error": {
                "code": "VALIDATION_FAILED",
                "message": f"input not found: {args.input}",
            },
        }))
        return 2

    if args.input.is_dir() and not (args.input / "main.py").exists():
        print(json.dumps({
            "ok": False,
            "error": {
                "code": "VALIDATION_FAILED",
                "message": f"project directory {args.input} has no main.py",
            },
        }))
        return 2

    resolved = args.input.resolve()
    stem = resolved.stem if resolved.is_file() else resolved.name

    tmp = Path(tempfile.mkdtemp(prefix="dramacode-check-"))
    try:
        payload = run_sandboxed_sync(
            source_path=resolved,
            out_dir=tmp,
            stem=stem or "ep",
            provider="mock",
            wall_clock_s=args.wall_clock_s,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Strip the path fields — they were tempfiles.
    for k in ("episode_path", "srt_path", "metadata_path", "shots"):
        payload.pop(k, None)
    print(json.dumps(payload))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
