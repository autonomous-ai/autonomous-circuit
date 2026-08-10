"""``python scripts/bundle <epNNN.mp4 | .episode.json | dir>`` — build the
screening review bundle for the critic.

Thin wrapper over :func:`dramapy.review_bundle.build_review_bundle`: samples
frames through every shot, gathers the board/poster/metadata/audio, and
mechanically detects technical defects (including the rotation bug). Prints
exactly one JSON line — the manifest — on stdout (the skill's one-line
discipline; the parent reads ``stdout.splitlines()[-1]``).

The heavy import (dramapy) is deferred into ``main()`` so the report parser
below stays importable with no dependencies (the skill's tests exercise it
directly).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]  # skills/screening-room/scripts
SKILL_DIR = SCRIPTS_DIR.parent


def _dramapy_search_paths() -> list[str]:
    """Where to find ``dramapy``, most-specific first: a test/CI override, the
    vendored runtime copy (self-contained skill), then the repo source tree
    (dev, before vendoring)."""
    paths: list[str] = []
    override = os.environ.get("SCREENING_TEST_DRAMAPY_PATH") or os.environ.get(
        "DRAMACODE_TEST_DRAMAPY_PATH"
    )
    if override:
        paths.append(override)
    paths.append(str(SCRIPTS_DIR / "packages"))  # vendored: scripts/packages/dramapy/
    # Repo layout: skills/screening-room/scripts/bundle/cli.py → repo root is 4 up.
    repo_root = SCRIPTS_DIR.parents[2]
    paths.append(str(repo_root / "packages" / "dramapy" / "src"))
    return paths


def _ensure_dramapy_on_path() -> None:
    for p in _dramapy_search_paths():
        if p and p not in sys.path and Path(p).exists():
            sys.path.insert(0, p)


REPORT_FENCE = re.compile(r"```screening-report\s*\n(.*?)```", re.DOTALL)

REQUIRED_REPORT_KEYS = ("overall_1_10", "dimension_scores", "pass_at_bar", "notes")
RUBRIC_DIMENSIONS = (
    "hook",
    "story",
    "pacing",
    "consistency",
    "cinematography",
    "audio",
    "continuity",
    "technical",
    "shareability",
)
NOTE_DEPARTMENTS = frozenset(
    {
        "writer",
        "director",
        "cinematographer",
        "cast",
        "vfx",
        "editor",
        "colorist",
        "sound",
        "composer",
        "continuity",
        "technical",
    }
)
NOTE_SEVERITIES = frozenset({"blocker", "major", "minor"})


def parse_screening_report(text: str) -> dict | None:
    """Extract and parse the LAST ` ```screening-report ` fenced JSON block from
    ``text``. Returns the parsed dict, or ``None`` when absent / not valid JSON /
    missing required keys. Pure — no dramapy dependency (mirrors the driver's JS
    parser so the skill can self-check its own output)."""
    matches = REPORT_FENCE.findall(str(text or ""))
    if not matches:
        return None
    try:
        report = json.loads(matches[-1].strip())
    except (ValueError, TypeError):
        return None
    if not isinstance(report, dict):
        return None
    if any(key not in report for key in REQUIRED_REPORT_KEYS):
        return None
    if not isinstance(report.get("notes"), list):
        return None
    return report


def actionable_notes(report: dict) -> list[dict]:
    """Blocker/major notes — the ones that trigger a fix round."""
    notes = report.get("notes") or []
    return [
        n
        for n in notes
        if isinstance(n, dict) and n.get("severity") in {"blocker", "major"}
    ]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts/bundle",
        description=(
            "Build the screening-room review bundle for a rendered episode: "
            "sampled frames, board/poster, metadata, audio stats, and "
            "mechanically-detected technical defects (incl. the rotation bug)."
        ),
    )
    p.add_argument(
        "episode",
        help="epNNN.mp4, its .episode.json sidecar, or a project/episodes dir.",
    )
    p.add_argument("--stem", default=None, help="disambiguate a dir with several episodes")
    p.add_argument("--frames-per-shot", type=int, default=3)
    p.add_argument("--no-black-detect", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    _ensure_dramapy_on_path()
    try:
        from dramapy.review_bundle import build_review_bundle
    except Exception as exc:  # dramapy unavailable — report, don't crash
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"could not import dramapy.review_bundle: "
                    f"{type(exc).__name__}: {exc}",
                    "frames": [],
                    "defects": [],
                }
            )
        )
        return 2

    bundle = build_review_bundle(
        args.episode,
        stem=args.stem,
        frames_per_shot=args.frames_per_shot,
        detect_black=not args.no_black_detect,
    )
    print(json.dumps(bundle))
    return 0 if bundle.get("ok") else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
