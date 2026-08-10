"""Stdlib text rendering + drawtext escaping.

The PATH ffmpeg on the reference machine (Homebrew 8.1.1) is built without
libfreetype/libass, so the ``drawtext`` and ``subtitles`` filters do not
exist. dramapy therefore renders every text label itself — an RGBA PNG
rasterized from the embedded public-domain 8x8 bitmap font
(:mod:`dramapy.font8x8`), zero dependencies beyond ``zlib``/``struct`` —
and composites it with ffmpeg's always-present ``overlay`` filter.

:func:`escape_drawtext` is still provided (and unit-tested) for ffmpeg
builds that *do* have drawtext, and as the reference implementation for the
skills track. It performs the two escaping levels the ffmpeg docs require:
the drawtext option value level and the filtergraph level.
"""

from __future__ import annotations

import struct
import textwrap
import unicodedata
import zlib
from pathlib import Path

from dramapy.font8x8 import FONT_8X8_BASIC

GLYPH_W = 8
GLYPH_H = 8

# Subtitle layout rule (contract §1): 42 chars x 2 lines before overrun.
SUBTITLE_MAX_COLS = 42
SUBTITLE_MAX_ROWS = 2

RGBA = tuple[int, int, int, int]

DEFAULT_FG: RGBA = (245, 245, 245, 255)
DEFAULT_BG: RGBA = (10, 12, 16, 175)


def escape_drawtext(text: str) -> str:
    """Escape arbitrary text for a drawtext ``text=`` value inside ``-vf``.

    Two levels, per the ffmpeg filtergraph-escaping docs:

    1. drawtext option value: ``\\`` and ``'`` and ``%`` (text expansion) and
       the option separator ``:`` are backslash-escaped.
    2. filtergraph level: the level-1 result's backslashes are doubled and
       the graph-special characters ``[ ] , ;`` are backslash-escaped.

    Newlines are preserved (drawtext renders them as line breaks).
    """
    level1 = text
    for ch in ("\\", "'", "%", ":"):
        level1 = level1.replace(ch, "\\" + ch)
    level2 = level1.replace("\\", "\\\\")
    for ch in ("[", "]", ",", ";"):
        level2 = level2.replace(ch, "\\" + ch)
    return level2


def _ascii_fold(text: str) -> str:
    """Best-effort fold to the printable ASCII range the bitmap font covers."""
    folded = unicodedata.normalize("NFKD", text)
    out: list[str] = []
    for ch in folded:
        if ch == "\t":
            out.append("  ")
        elif 0x20 <= ord(ch) < 0x7F:
            out.append(ch)
        elif unicodedata.combining(ch):
            continue
        else:
            out.append("?")
    return "".join(out)


def wrap_subtitle(line: str, *, width: int = SUBTITLE_MAX_COLS) -> list[str]:
    """Wrap a dialogue line to subtitle rows at ``width`` columns."""
    wrapped = textwrap.wrap(
        " ".join(line.split()), width=width, break_long_words=True
    )
    return wrapped or [""]


def subtitle_overruns(line: str) -> bool:
    """True when a line needs more than 42 chars x 2 rows (contract kind
    ``subtitle_overrun``, severity info)."""
    return len(wrap_subtitle(line)) > SUBTITLE_MAX_ROWS


def render_text_png(
    lines: list[str],
    path: Path,
    *,
    scale: int = 2,
    fg: RGBA = DEFAULT_FG,
    bg: RGBA = DEFAULT_BG,
    pad: int = 6,
    line_gap: int = 3,
    max_cols: int | None = None,
) -> Path:
    """Rasterize ``lines`` of text into an RGBA PNG at ``path``.

    Each glyph is 8x8 font pixels at ``scale``x magnification on a padded
    semi-opaque box. ``max_cols`` truncates each line (used for prompt
    excerpts). Returns ``path``.
    """
    scale = max(1, int(scale))
    clean: list[str] = []
    for line in lines:
        folded = _ascii_fold(line)
        if max_cols is not None and len(folded) > max_cols:
            folded = folded[: max(1, max_cols - 1)] + "~"
        clean.append(folded)
    if not clean:
        clean = [""]

    cols = max(len(line) for line in clean)
    cols = max(cols, 1)
    width = cols * GLYPH_W * scale + 2 * pad
    height = len(clean) * GLYPH_H * scale + (len(clean) - 1) * line_gap + 2 * pad

    buf = bytearray(bytes(bg) * (width * height))

    def put(x: int, y: int) -> None:
        offset = (y * width + x) * 4
        buf[offset : offset + 4] = bytes(fg)

    for row_index, line in enumerate(clean):
        y0 = pad + row_index * (GLYPH_H * scale + line_gap)
        for col_index, ch in enumerate(line):
            glyph = FONT_8X8_BASIC[ord(ch) if ord(ch) < 0x80 else 0x3F]
            x0 = pad + col_index * GLYPH_W * scale
            for gy in range(GLYPH_H):
                bits = glyph[gy]
                if not bits:
                    continue
                for gx in range(GLYPH_W):
                    if bits >> gx & 1:
                        for sy in range(scale):
                            for sx in range(scale):
                                put(x0 + gx * scale + sx, y0 + gy * scale + sy)

    _write_png(path, width, height, bytes(buf))
    return path


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def _write_png(path: Path, width: int, height: int, rgba: bytes) -> None:
    stride = width * 4
    raw = b"".join(
        b"\x00" + rgba[y * stride : (y + 1) * stride] for y in range(height)
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, 6))
        + _png_chunk(b"IEND", b"")
    )
