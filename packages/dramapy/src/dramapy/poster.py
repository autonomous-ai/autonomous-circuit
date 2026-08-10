"""Review images (contract §1): ``_poster.png`` and ``_board.png``.

* ``_poster.png`` — the cover frame: the first dialogue shot's mid-frame,
  else the first shot's mid-frame. The publish path resolves it **by this
  filename**.
* ``_board.png`` — the contact sheet the review loop reads: the first frame
  of every rendered shot, labeled with shot id + duration, tiled into a
  grid (``concat`` of single frames → ``tile``, unused cells filled dark).

ffmpeg/ffprobe only; labels are stdlib-rasterized PNGs (no drawtext in the
PATH ffmpeg build). Failures raise ``RuntimeError`` — generation wraps them
in :class:`~dramapy.errors.ExportError`.
"""

from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dramapy import media
from dramapy.text import render_text_png

POSTER_NAME = "_poster.png"
BOARD_NAME = "_board.png"

TILE_WIDTH = 240
GRID_BG = "0x0B0C10"
TILE_BG = "0x14141A"


@dataclass(frozen=True)
class BoardItem:
    """One board cell: a rendered clip + its label facts."""

    shot_id: str
    clip_path: Path
    duration_s: float
    is_dialogue: bool = False


def write_poster(items: list[BoardItem], review_dir: Path) -> Path:
    """Extract the poster frame (first dialogue shot's mid-frame, else the
    first shot's)."""
    if not items:
        raise ValueError("write_poster needs at least one rendered shot")
    chosen = next((item for item in items if item.is_dialogue), items[0])
    midpoint = max(0.0, chosen.duration_s / 2.0)
    review_dir.mkdir(parents=True, exist_ok=True)
    target = review_dir / POSTER_NAME
    media.run_ffmpeg(
        [
            "-y",
            "-ss",
            f"{midpoint:.3f}",
            "-i",
            str(chosen.clip_path),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(target),
        ]
    )
    return target


def _grid(count: int) -> tuple[int, int]:
    if count <= 1:
        return 1, 1
    columns = 2 if count <= 4 else 3 if count <= 9 else 4
    return columns, math.ceil(count / columns)


def write_board(
    items: list[BoardItem],
    review_dir: Path,
    *,
    series_resolution: tuple[int, int],
) -> Path:
    """Tile the first frame of every shot, labeled ``<id> <dur>s``."""
    if not items:
        raise ValueError("write_board needs at least one rendered shot")
    width, height = series_resolution
    tile_w = TILE_WIDTH
    tile_h = max(2, round(tile_w * height / width / 2) * 2)
    columns, rows = _grid(len(items))
    review_dir.mkdir(parents=True, exist_ok=True)
    target = review_dir / BOARD_NAME

    with tempfile.TemporaryDirectory(prefix="dramapy-board-") as tempdir:
        tempdir_p = Path(tempdir)
        args: list[str] = ["-y"]
        for item in items:
            args += ["-i", str(item.clip_path)]
        label_count = len(items)
        for index, item in enumerate(items):
            label_png = render_text_png(
                [f"{item.shot_id}  {item.duration_s:.1f}s"],
                tempdir_p / f"label_{index}.png",
                scale=1,
                max_cols=28,
            )
            args += ["-i", str(label_png)]

        graph: list[str] = []
        for index in range(len(items)):
            graph.append(
                f"[{index}:v]trim=end_frame=1,setpts=PTS-STARTPTS,"
                f"scale={tile_w}:{tile_h}:force_original_aspect_ratio=decrease,"
                f"pad={tile_w}:{tile_h}:(ow-iw)/2:(oh-ih)/2:color={TILE_BG},"
                f"setsar=1[b{index}]"
            )
            graph.append(
                f"[b{index}][{label_count + index}:v]"
                f"overlay=x=6:y=main_h-overlay_h-6[t{index}]"
            )
        tiles = "".join(f"[t{index}]" for index in range(len(items)))
        if len(items) == 1:
            graph.append(f"{tiles}pad=iw+24:ih+24:12:12:color={GRID_BG}[grid]")
        else:
            graph.append(
                f"{tiles}concat=n={len(items)}:v=1:a=0,"
                f"tile={columns}x{rows}:padding=8:margin=12:color={GRID_BG}[grid]"
            )

        args += [
            "-filter_complex",
            ";".join(graph),
            "-map",
            "[grid]",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(target),
        ]
        media.run_ffmpeg(args)
    return target
