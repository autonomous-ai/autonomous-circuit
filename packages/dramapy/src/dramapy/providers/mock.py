"""Mock provider — deterministic SILENT animatic clips (contract §1, model
``animatic-2``).

The mock renders a watchable animatic per shot — motion and look — as **silent
video**, pure lavfi + overlays, zero network, zero extra deps. Audio no longer
lives in the clip: spoken dialogue is generated once at the assembly stage
(:mod:`dramapy.voices`) and mixed under the whole episode by
:mod:`dramapy.stitch`, so every provider's episodes get voices from one code
path. That is why the mock's shots are silent — assembly owns audio.

* **Motion** — a seeded camera move per shot kind via ``zoompan``: establish =
  slow push 100→108% (direction seeded by shot id), action = faster push
  100→115%, dialogue = gentle seeded drift at 105%, insert = quick punch-in to
  112% over the first half second, then hold.
* **Look** — the style-keyed gradient background (photoreal-drama →
  slate/teal, manhwa → warm, anime → violet) with a per-SCENE palette
  variation (the scene id hashes into a bounded hue/value shift so scenes read
  as distinct rooms), film grain (``noise``), and a subtle vignette.
* **Dialogue text** — dialogue shots still burn a larger, wrapped dialogue row
  near the bottom of the frame (a visual, not audio); the spoken line rides the
  episode voice track.

Every clip is fully deterministic for a given shot id + scene id + spec. The
render-cache key folds the provider model (``animatic-2``), the scene id, and
the mock's :attr:`~MockProvider.cache_salt`, so look changes here invalidate
stale clips; the model-id bump ``animatic-1 → animatic-2`` (shots became
silent) invalidates every pre-voice clip and flips the idempotent
short-circuit so old episodes re-render once.

Text is rasterized by :mod:`dramapy.text` (stdlib bitmap font) and composited
with ``overlay`` — the PATH ffmpeg has no drawtext filter (built without
libfreetype), see :mod:`dramapy.text` for the escaping helper kept for builds
that do.

Regression tests use this provider only; it never touches a network.
"""

from __future__ import annotations

import colorsys
import hashlib
import tempfile
import textwrap
from pathlib import Path

from dramapy import media
from dramapy.errors import ProviderError
from dramapy.providers.base import Provider, ShotContext
from dramapy.text import GLYPH_W, render_text_png

MODEL = "animatic-2"  # bumping this invalidates cached clips AND flips the
# idempotent short-circuit in generation.py — old episodes re-render.

# Style-keyed gradient palettes: (c0, c1) hex without '#'.
STYLE_PALETTES: dict[str, tuple[str, str]] = {
    "photoreal-drama": ("232E38", "10616F"),  # slate / teal
    "manhwa": ("6E4123", "E0A860"),  # warm umber / amber
    "anime": ("241540", "7C3AED"),  # deep violet / violet
}
DEFAULT_PALETTE: tuple[str, str] = ("1C1F26", "3E4756")  # neutral slate

PROMPT_EXCERPT_COLS = 36

# -- Motion (zoompan) constants. --------------------------------------------
ESTABLISH_ZOOM = 0.08  # slow push 100→108%
ACTION_ZOOM = 0.15  # faster push 100→115%
DIALOGUE_ZOOM = 1.05  # constant frame with a gentle drift
INSERT_ZOOM = 0.12  # quick punch-in to 112%, then hold
GRAIN_STRENGTH = 6  # noise=alls=… film grain

# Salt version for the render cache (was "tts=…:voices=…"; audio left the clip).
CACHE_SALT = "v2"


def shot_seed(shot_id: str) -> int:
    """Stable integer seed for a shot id (sha256-derived, platform-free)."""
    return int.from_bytes(hashlib.sha256(shot_id.encode("utf-8")).digest()[:8], "big")


def style_palette(style: str) -> tuple[str, str]:
    return STYLE_PALETTES.get(style, DEFAULT_PALETTE)


def scene_palette(style: str, scene_id: str | None) -> tuple[str, str]:
    """The style palette, varied per scene: the scene id hashes into a
    bounded hue rotation (±45°), a saturation scale (0.75-1.35), and a small
    value nudge (±0.06) so scenes read as distinct rooms while staying in
    the style's family. Empty scene id → the base palette unchanged."""
    base = style_palette(style)
    if not scene_id:
        return base
    seed = int.from_bytes(
        hashlib.sha256(f"scene:{scene_id}".encode("utf-8")).digest()[:8], "big"
    )
    hue_shift = ((seed % 91) - 45) / 360.0
    saturation_scale = 0.75 + ((seed >> 8) % 61) / 100.0
    value_shift = (((seed >> 16) % 25) - 12) / 200.0
    return (
        _shift_hex(base[0], hue_shift, saturation_scale, value_shift),
        _shift_hex(base[1], hue_shift, saturation_scale, value_shift),
    )


def _shift_hex(
    hex_color: str, hue_shift: float, saturation_scale: float, value_shift: float
) -> str:
    red, green, blue = (
        int(hex_color[index : index + 2], 16) / 255.0 for index in (0, 2, 4)
    )
    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    hue = (hue + hue_shift) % 1.0
    saturation = min(1.0, max(0.0, saturation * saturation_scale))
    value = min(1.0, max(0.0, value + value_shift))
    shifted = colorsys.hsv_to_rgb(hue, saturation, value)
    return "".join(f"{round(channel * 255):02X}" for channel in shifted)


def gradient_source(
    shot_id: str,
    style: str,
    *,
    width: int,
    height: int,
    fps: int,
    duration_s: float,
    scene_id: str | None = None,
) -> str:
    """The deterministic lavfi ``gradients`` expression for a shot."""
    seed = shot_seed(shot_id)
    c0, c1 = scene_palette(style, scene_id)
    x0 = seed % max(1, width // 2)
    y0 = (seed >> 8) % max(1, height // 3)
    x1 = width - 1 - ((seed >> 16) % max(1, width // 3))
    y1 = height - 1 - ((seed >> 24) % max(1, height // 4))
    return (
        f"gradients=s={width}x{height}:c0=0x{c0}:c1=0x{c1}"
        f":x0={x0}:y0={y0}:x1={x1}:y1={y1}"
        f":seed={seed % 2**31}:r={fps}:d={duration_s:.3f}:speed=0.02"
    )


def motion_filter(
    kind: str,
    shot_id: str,
    *,
    width: int,
    height: int,
    fps: int,
    duration_s: float,
) -> str:
    """The seeded ``zoompan`` camera move for a shot kind (module docstring
    has the per-kind table). Deterministic per shot id."""
    seed = shot_seed(shot_id)
    frames = max(1, round(duration_s * fps))
    center_x = "iw/2-(iw/zoom/2)"
    center_y = "ih/2-(ih/zoom/2)"
    if kind == "action":
        zoom = f"1+{ACTION_ZOOM}*in/{frames}"
    elif kind == "insert":
        punch_frames = max(1, fps // 2)
        zoom = f"min(1+{INSERT_ZOOM}*in/{punch_frames},{1 + INSERT_ZOOM})"
    elif kind == "dialogue":
        zoom = f"{DIALOGUE_ZOOM}"
        phase = (seed % 628) / 100.0  # 0..2π, seeded sway phase
        center_x = f"iw/2-(iw/zoom/2)+iw*0.006*sin(in/{fps}+{phase:.2f})"
    else:  # establish (and any future kind): slow push, direction seeded
        if (seed >> 33) & 1:
            zoom = f"{1 + ESTABLISH_ZOOM}-{ESTABLISH_ZOOM}*in/{frames}"
        else:
            zoom = f"1+{ESTABLISH_ZOOM}*in/{frames}"
    return (
        f"zoompan=z='{zoom}':x='{center_x}':y='{center_y}'"
        f":d=1:s={width}x{height}:fps={fps}"
    )


def dialogue_rows(line: str, width: int) -> tuple[list[str], int]:
    """The on-clip dialogue text: rows wrapped to fit the frame plus the
    glyph scale. Scale rounds up from the old ``width // 360`` row so the
    line is readable on a phone; rows wrap to whatever column count fits at
    that scale (≤3 rows, then truncate with ``~``)."""
    scale = max(1, round(width / 270))
    cols = max(16, (width - 64) // (GLYPH_W * scale))
    rows = textwrap.wrap(
        " ".join(line.split()), width=cols, break_long_words=True
    ) or [""]
    if len(rows) > 3:
        rows = rows[:3]
        rows[-1] = rows[-1][: max(1, cols - 1)] + "~"
    return rows, scale


class MockProvider(Provider):
    name = "mock"
    model = MODEL

    @property
    def cache_salt(self) -> str:  # type: ignore[override]
        """The animatic version. Audio left the clip (assembly owns voices), so
        the salt no longer carries a TTS mode — just a version bump."""
        return CACHE_SALT

    def render_shot(self, ctx: ShotContext) -> Path:
        shot = ctx.shot
        series = ctx.series
        width, height = series.resolution
        duration = float(shot.duration_s)
        fps = series.fps
        seed = shot_seed(shot.id)
        label_scale = max(1, width // 420)

        ctx.output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="dramapy-mock-") as tempdir:
            tempdir_p = Path(tempdir)
            header_png = render_text_png(
                [
                    f"{shot.id}  {shot.kind}  {duration:.1f}s",
                    shot.prompt or "(no prompt)",
                ],
                tempdir_p / "header.png",
                scale=label_scale,
                max_cols=PROMPT_EXCERPT_COLS,
            )

            args: list[str] = [
                "-y",
                "-f",
                "lavfi",
                "-i",
                gradient_source(
                    shot.id,
                    series.style,
                    width=width,
                    height=height,
                    fps=fps,
                    duration_s=duration,
                    scene_id=ctx.scene_id,
                ),
                "-i",
                str(header_png),
            ]
            # Animatic base: camera move + film grain + vignette, then the
            # small shot-id/prompt header pinned to the top-left corner.
            graph = [
                "[0:v]"
                + motion_filter(
                    shot.kind,
                    shot.id,
                    width=width,
                    height=height,
                    fps=fps,
                    duration_s=duration,
                )
                + f",noise=all_seed={seed % 100000}:alls={GRAIN_STRENGTH}:allf=t"
                + ",vignette[vbase]",
                "[vbase][1:v]overlay=x=16:y=16[vh]",
            ]
            video_label = "[vh]"
            next_input = 2

            if shot.kind == "dialogue" and shot.line is not None:
                rows, line_scale = dialogue_rows(shot.line, width)
                line_png = render_text_png(
                    rows,
                    tempdir_p / "line.png",
                    scale=line_scale,  # larger dialogue row, wrapped to fit
                )
                args += ["-i", str(line_png)]
                graph.append(
                    f"{video_label}[{next_input}:v]"
                    "overlay=x=(main_w-overlay_w)/2"
                    ":y=main_h-overlay_h-round(main_h*0.08)[vl]"
                )
                video_label = "[vl]"
                next_input += 1

            # Final format pass keeps the overlay output 4:2:0 for x264. The
            # clip is silent — assembly (dramapy.voices + stitch) owns audio.
            graph.append(f"{video_label}format=yuv420p[vout]")

            args += ["-filter_complex", ";".join(graph), "-map", "[vout]"]
            args += [
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(fps),
                "-t",
                f"{duration:.3f}",
                "-movflags",
                "+faststart",
                str(ctx.output_path),
            ]

            try:
                media.run_ffmpeg(args, timeout=ctx.max_render_s)
            except TimeoutError as exc:
                raise ProviderError(
                    f"mock render for shot '{shot.id}' timed out after "
                    f"{ctx.max_render_s:g}s"
                ) from exc
            except RuntimeError as exc:
                raise ProviderError(
                    f"mock render for shot '{shot.id}' failed: {exc}"
                ) from exc

        if not ctx.output_path.is_file() or ctx.output_path.stat().st_size == 0:
            raise ProviderError(
                f"mock render for shot '{shot.id}' produced no clip at "
                f"{ctx.output_path}"
            )
        return ctx.output_path
