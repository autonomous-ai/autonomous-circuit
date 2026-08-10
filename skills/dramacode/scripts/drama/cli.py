"""``python scripts/drama <episodes/epNNN.py | project_dir> [flags]`` — primary tool.

Hands the user's episode off to ``dramapy.generation.generate_episode``
inside a sandboxed subprocess. dramapy loads the episode module (a single
``.py`` defining ``gen_episode()``, or a directory containing ``main.py``),
renders every shot through the selected provider, and writes the full
contract §1 artifact set next to ``output_path``:

  <stem>.mp4  <stem>.episode.json  <stem>.srt  <stem>_shots/  <stem>_review/

Prints a single JSON line on stdout matching contract §3 ``DramacodeResult``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common.runner import WALL_CLOCK_TIMEOUT_S, run_sandboxed_sync


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts/drama",
        description=(
            "Render a drama episode (episodes/epNNN.py defining gen_episode(), "
            "or a project directory containing main.py) into the mp4 + sidecar "
            "artifact set via the dramapy pipeline."
        ),
    )
    p.add_argument(
        "input",
        type=Path,
        help=(
            "Path to an episode .py file (defines gen_episode() at module "
            "scope — the normal case: episodes/ep001.py) OR a directory "
            "containing a main.py. The project root (the dir holding "
            "series.py) is put on sys.path so episode files can `import "
            "series`."
        ),
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Where to write artifacts (default: same directory as the input).",
    )
    p.add_argument(
        "--stem",
        default=None,
        help="Override the output filename stem (default: input file's stem).",
    )
    p.add_argument(
        "--provider",
        default=None,
        choices=("mock", "fal"),
        help=(
            "Render backend. Default: env VIDEO_PROVIDER, else 'mock' "
            "(ffmpeg-synthesized placeholders — free, offline). 'fal' hits "
            "Wan 2.2 and needs FAL_KEY."
        ),
    )
    p.add_argument(
        "--wall-clock-s",
        type=float,
        default=WALL_CLOCK_TIMEOUT_S,
        help=(
            f"Wall-clock timeout for the worker subprocess (seconds, default "
            f"{WALL_CLOCK_TIMEOUT_S}). Raise for long episodes on real "
            "providers."
        ),
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    input_path = args.input
    if not input_path.exists():
        print(json.dumps({
            "ok": False,
            "error": {
                "code": "VALIDATION_FAILED",
                "message": f"input not found: {input_path}",
            },
        }))
        return 2

    # Directory mode requires a main.py entrypoint — fail fast (donor rule).
    if input_path.is_dir() and not (input_path / "main.py").exists():
        print(json.dumps({
            "ok": False,
            "error": {
                "code": "VALIDATION_FAILED",
                "message": (
                    f"project directory {input_path} has no main.py — pass an "
                    "episode file (episodes/epNNN.py) or create a main.py "
                    "that defines gen_episode()"
                ),
            },
        }))
        return 2

    if input_path.is_file() and input_path.suffix != ".py":
        print(json.dumps({
            "ok": False,
            "error": {
                "code": "VALIDATION_FAILED",
                "message": (
                    f"input must be a .py episode file or a project dir, got "
                    f"{input_path.name} — never point the generator at a "
                    "generated artifact"
                ),
            },
        }))
        return 2

    resolved = input_path.resolve()
    stem = args.stem or (resolved.stem if resolved.is_file() else resolved.name)
    out_dir = args.out_dir or (
        resolved.parent if resolved.is_file() else resolved
    )

    payload = run_sandboxed_sync(
        source_path=resolved,
        out_dir=out_dir.resolve(),
        stem=stem,
        provider=args.provider,
        wall_clock_s=args.wall_clock_s,
    )

    print(json.dumps(payload))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
