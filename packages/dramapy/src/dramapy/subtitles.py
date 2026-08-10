""".srt subtitles from dialogue lines, timed to their shots (contract §1).

``build_entries`` receives the stitched timeline (each rendered dialogue
shot with its start offset and actual clip duration) and produces
:class:`SubtitleEntry` rows; ``format_srt``/``write_srt`` emit the file.
Lines wrap at 42 columns; a line needing more than 2 rows sets
``overrun=True``, which :func:`collect_subtitle_warnings` turns into the
``subtitle_overrun`` info warning. Burn-in is done by the stitcher with
timed PNG overlays (this ffmpeg build has no libass ``subtitles`` filter) —
the .srt file itself is always the sidecar-of-record for dialogue timing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dramapy.text import SUBTITLE_MAX_ROWS, wrap_subtitle


@dataclass(frozen=True)
class SubtitleEntry:
    index: int
    shot_id: str
    start_s: float
    end_s: float
    lines: tuple[str, ...]
    overrun: bool

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def build_entries(
    timeline: list[tuple[str, str | None, float, float]],
) -> list[SubtitleEntry]:
    """Build entries from ``(shot_id, line, start_s, duration_s)`` rows.

    Rows with ``line is None`` (non-dialogue shots) are skipped; callers pass
    the full stitched timeline so offsets stay aligned with the episode.
    """
    entries: list[SubtitleEntry] = []
    for shot_id, line, start_s, duration_s in timeline:
        if line is None:
            continue
        wrapped = wrap_subtitle(line)
        entries.append(
            SubtitleEntry(
                index=len(entries) + 1,
                shot_id=shot_id,
                start_s=max(0.0, start_s),
                end_s=max(0.0, start_s) + max(0.2, duration_s),
                lines=tuple(wrapped),
                overrun=len(wrapped) > SUBTITLE_MAX_ROWS,
            )
        )
    return entries


def format_timestamp(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    return f"{total_s // 3600:02d}:{total_s % 3600 // 60:02d}:{total_s % 60:02d},{ms:03d}"


def format_srt(entries: list[SubtitleEntry]) -> str:
    blocks = [
        f"{entry.index}\n"
        f"{format_timestamp(entry.start_s)} --> {format_timestamp(entry.end_s)}\n"
        f"{entry.text}\n"
        for entry in entries
    ]
    return "\n".join(blocks)


def write_srt(entries: list[SubtitleEntry], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_srt(entries), encoding="utf-8")
    return path


def collect_subtitle_warnings(entries: list[SubtitleEntry]) -> list[dict]:
    """``subtitle_overrun`` (severity info) for every entry past 42x2."""
    return [
        {
            "part": entry.shot_id,
            "kind": "subtitle_overrun",
            "detail": (
                f"subtitle needs {len(entry.lines)} rows at 42 columns "
                f"(max {SUBTITLE_MAX_ROWS}): {entry.lines[0]}…"
            ),
            "severity": "info",
        }
        for entry in entries
        if entry.overrun
    ]
