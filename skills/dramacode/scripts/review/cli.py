"""``python scripts/review <episodes_dir | project_dir | .mp4 | .episode.json>`` — QA pass.

Reads the ``.episode.json`` sidecar the generator wrote, re-surfaces its
``validation.warnings``, and hands back the two review images the loop
must actually LOOK at:

  <stem>_review/_board.png   contact sheet — first frame of every shot
  <stem>_review/_poster.png  cover frame

If either PNG is missing it is regenerated — via dramapy's own renderer
when the vendored package exposes one, else by ffmpeg directly (first
frames tiled 4-across). Rendering never raises; a failed render yields a
null path, not an error.

Prints a single JSON line on stdout::

  {
    "ok": true,
    "stem": "ep001",
    "warnings": [ { "part", "kind", "detail", "severity" }, ... ],
    "board_png": "<abs path>",     // null when unrenderable
    "poster_png": "<abs path>",
    "shots": [ { "id", "path", "json_path" }, ... ]
  }
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
for _p in (SCRIPTS_DIR, SCRIPTS_DIR / "packages"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
# Tests inject a stub dramapy the same way the runner does.
_test_dramapy = os.environ.get("DRAMACODE_TEST_DRAMAPY_PATH")
if _test_dramapy and _test_dramapy not in sys.path:
    sys.path.insert(0, _test_dramapy)


SIDECAR_SUFFIX = ".episode.json"
BOARD_COLUMNS = 4
BOARD_TILE = "270:480"  # 9:16 tile, quarter-res of 1080x1920


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts/review",
        description=(
            "Re-surface a generated episode's validation warnings and its "
            "_board.png contact sheet + _poster.png cover, regenerating the "
            "PNGs when missing."
        ),
    )
    p.add_argument(
        "input",
        type=Path,
        help=(
            "A directory holding <stem>.episode.json (episodes/ or the "
            "project root), or a path to a .mp4 / .episode.json directly."
        ),
    )
    p.add_argument(
        "--stem",
        default=None,
        help="Disambiguate when a directory holds multiple .episode.json files.",
    )
    return p


def _resolve_sidecar(input_path: Path, stem: str | None) -> Path | None:
    """Find the ``<stem>.episode.json`` sidecar for the given input.

    Raises ``ValueError`` when a directory holds several sidecars and no
    ``--stem`` was given — silently picking one would review the wrong
    episode (donor rule). A project root without sidecars falls through to
    its ``episodes/`` subdirectory.
    """
    input_path = input_path.resolve()
    if input_path.is_file():
        if input_path.name.endswith(SIDECAR_SUFFIX):
            return input_path
        if input_path.suffix == ".mp4":
            cand = input_path.with_name(input_path.stem + SIDECAR_SUFFIX)
            return cand if cand.is_file() else None
        return None
    if input_path.is_dir():
        search_dirs = [input_path]
        if (input_path / "episodes").is_dir():
            search_dirs.append(input_path / "episodes")
        for d in search_dirs:
            if stem:
                cand = d / f"{stem}{SIDECAR_SUFFIX}"
                if cand.is_file():
                    return cand
                continue
            sidecars = sorted(d.glob(f"*{SIDECAR_SUFFIX}"))
            if len(sidecars) > 1:
                names = ", ".join(s.name[: -len(SIDECAR_SUFFIX)] for s in sidecars)
                raise ValueError(
                    f"{d} holds {len(sidecars)} {SIDECAR_SUFFIX} sidecars "
                    f"({names}) — pass --stem to pick one"
                )
            if sidecars:
                return sidecars[0]
        return None
    return None


def _err(message: str, code: str = "VALIDATION_FAILED") -> int:
    print(json.dumps({"ok": False, "error": {"code": code, "message": message}}))
    return 2


def _ffmpeg(*args: str, timeout: float = 30.0) -> bool:
    """Run ffmpeg quietly; True on success. Never raises."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
            capture_output=True, timeout=timeout,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _render_poster_ffmpeg(episode_mp4: Path, out_png: Path) -> bool:
    return _ffmpeg("-i", str(episode_mp4), "-frames:v", "1", str(out_png))


def _render_board_ffmpeg(shot_mp4s: list[Path], out_png: Path) -> bool:
    """Tile the first frame of every shot clip, 4 across (contact sheet)."""
    if not shot_mp4s:
        return False
    with tempfile.TemporaryDirectory(prefix="dramacode-board-") as tmp:
        frames: list[Path] = []
        for i, clip in enumerate(shot_mp4s, start=1):
            frame = Path(tmp) / f"frame_{i:03d}.png"
            if _ffmpeg("-i", str(clip), "-frames:v", "1",
                       "-vf", f"scale={BOARD_TILE}", str(frame)):
                frames.append(frame)
        if not frames:
            return False
        # Renumber contiguously in case a clip failed to decode.
        for i, frame in enumerate(frames, start=1):
            frame.rename(Path(tmp) / f"tile_{i:03d}.png")
        cols = min(BOARD_COLUMNS, len(frames))
        rows = math.ceil(len(frames) / cols)
        return _ffmpeg(
            "-framerate", "1",
            "-i", str(Path(tmp) / "tile_%03d.png"),
            "-filter_complex", f"tile={cols}x{rows}",
            "-frames:v", "1",
            str(out_png),
            timeout=60.0,
        )


def _try_dramapy_renderer(fn_names: Sequence[str], *args) -> bool:
    """Prefer dramapy's own board/poster renderer when it ships one.

    The contract doesn't freeze this API, so every lookup is getattr-guarded
    — absence just routes to the ffmpeg fallback."""
    try:
        import dramapy  # noqa: F401
        import dramapy.review as review_mod  # type: ignore[import-not-found]
    except Exception:
        return False
    for name in fn_names:
        fn = getattr(review_mod, name, None)
        if callable(fn):
            try:
                fn(*args)
                return True
            except Exception:
                return False
    return False


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if not args.input.exists():
        return _err(f"input not found: {args.input}")

    try:
        sidecar = _resolve_sidecar(args.input, args.stem)
    except ValueError as exc:
        return _err(str(exc))
    if sidecar is None:
        return _err(
            f"no {SIDECAR_SUFFIX} sidecar found for {args.input} — generate "
            "the episode first with scripts/drama"
        )

    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception as exc:
        return _err(f"failed to read sidecar {sidecar.name}: {exc}")

    base_dir = sidecar.parent
    stem = sidecar.name[: -len(SIDECAR_SUFFIX)]
    warnings = (meta.get("validation") or {}).get("warnings", []) or []

    review_dir = base_dir / f"{stem}_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    board = review_dir / "_board.png"
    poster = review_dir / "_poster.png"
    episode_mp4 = base_dir / f"{stem}.mp4"

    shots_meta = meta.get("shots", []) or []
    shots = [
        {
            "id": str(s.get("id", "")),
            "path": str(base_dir / str(s.get("path", ""))),
            "json_path": str(base_dir / str(s.get("jsonPath", ""))),
        }
        for s in shots_meta
    ]
    shot_clips = [Path(s["path"]) for s in shots if Path(s["path"]).is_file()]

    if not poster.is_file():
        if not _try_dramapy_renderer(("render_poster",), episode_mp4, poster):
            if episode_mp4.is_file():
                _render_poster_ffmpeg(episode_mp4, poster)
    if not board.is_file():
        if not _try_dramapy_renderer(("render_board",), sidecar, board):
            _render_board_ffmpeg(shot_clips, board)

    print(json.dumps({
        "ok": True,
        "stem": stem,
        "warnings": warnings,
        "board_png": str(board) if board.is_file() else None,
        "poster_png": str(poster) if poster.is_file() else None,
        "shots": shots,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
