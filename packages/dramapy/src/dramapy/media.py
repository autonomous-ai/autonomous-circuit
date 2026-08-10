"""Thin subprocess wrappers around the PATH ``ffmpeg``/``ffprobe`` binaries.

dramapy's only media runtime is ffmpeg-via-subprocess (zero Python
dependencies, contract §2 prereq). Helpers here raise plain ``RuntimeError``
/ ``TimeoutError`` — callers wrap them into the contract's
:class:`~dramapy.errors.GenerationError` subclasses (``ProviderError`` in
providers, ``ExportError`` in stitch/poster).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Sequence

_STDERR_TAIL_CHARS = 800


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    return exe


def ffprobe_exe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe not found on PATH")
    return exe


def run_ffmpeg(args: Sequence[str], *, timeout: float | None = None) -> None:
    """Run ``ffmpeg <args>`` quietly; raise ``RuntimeError`` with the stderr
    tail on failure, ``TimeoutError`` on budget exhaustion."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", *args]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"ffmpeg timed out after {timeout:g}s: {' '.join(args[:6])}…"
        ) from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"ffmpeg failed (exit {proc.returncode}): "
            f"{stderr[-_STDERR_TAIL_CHARS:] or 'no stderr'}"
        )


@dataclass(frozen=True)
class MediaInfo:
    duration_s: float
    width: int | None
    height: int | None
    fps: float | None
    has_video: bool
    has_audio: bool


def probe_media(path: Path | str) -> MediaInfo:
    """ffprobe a media file into a :class:`MediaInfo`. Raises ``RuntimeError``
    when the file cannot be probed (missing, empty, not a media file)."""
    cmd = [
        ffprobe_exe(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"ffprobe failed for {path}: {stderr[-300:]}")
    payload = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")

    duration = 0.0
    fmt = payload.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_video = False
    has_audio = False
    for stream in payload.get("streams") or []:
        codec_type = stream.get("codec_type")
        if codec_type == "video" and not has_video:
            has_video = True
            width = int(stream.get("width") or 0) or None
            height = int(stream.get("height") or 0) or None
            rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or ""
            fps = _parse_rate(rate) or _parse_rate(stream.get("r_frame_rate") or "")
            if not duration:
                try:
                    duration = float(stream.get("duration") or 0.0)
                except (TypeError, ValueError):
                    pass
        elif codec_type == "audio":
            has_audio = True
    return MediaInfo(
        duration_s=duration,
        width=width,
        height=height,
        fps=fps,
        has_video=has_video,
        has_audio=has_audio,
    )


def _parse_rate(rate: str) -> float | None:
    try:
        value = Fraction(rate)
    except (ValueError, ZeroDivisionError):
        return None
    if value <= 0:
        return None
    return float(value)
