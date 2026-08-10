"""Video post-processing ceiling — the two fal steps that run *after* the
image-to-video clip is generated and *before* it is downloaded.

Both are pure transforms on a **fal URL** (the Kling i2v result is already a
fal-hosted url), so the pipeline chains them without touching disk:

    kling clip url → lip-sync (dialogue only) → upscale (every shot) → download

* **Lip-sync** (``fal-ai/sync-lipsync/v2``, ~$3/min) re-animates the mouth in a
  clip to match a spoken-audio track. We feed it the SAME ElevenLabs line the
  assembly stage will play, so the synced mouth matches the heard voice. This is
  a **pixels-only** step: the returned video's own audio is irrelevant (stitch
  drops every clip's audio at assembly and rebuilds the track), so we keep the
  frames and ignore the muxed audio.
* **Upscale** (``fal-ai/topaz/upscale/video``, ~$0.02/s → 1080p+) sharpens and
  enlarges every clip.

Both are best-effort at the call site: :class:`~dramapy.errors.ProviderError`
here means the provider falls back to the pre-step url and never fails the shot.

Env gates (read by the cinematic provider):
  * ``VIDEO_LIPSYNC`` — default on when ``FAL_KEY`` is set; ``off`` disables.
  * ``VIDEO_UPSCALE`` — default on when ``FAL_KEY`` is set; ``off`` disables.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from dramapy.errors import ProviderError
from dramapy.fal_client import FalClient, first_url
from dramapy import media

LIPSYNC_MODEL = "fal-ai/sync-lipsync/v2"
UPSCALE_MODEL = "fal-ai/topaz/upscale/video"
DEFAULT_BUDGET_S = 600.0
DEFAULT_UPSCALE_FACTOR = 2
# The spoken line is usually shorter than the clip. "silence" pads the audio to
# the video's length so the OUTPUT KEEPS THE FULL CLIP DURATION (mouth synced
# during speech, still after) — critical because the pipeline probes the clip's
# real duration to place it on the timeline. "cut_off" (the fal default) would
# truncate the clip to the line and shift the whole episode.
LIPSYNC_SYNC_MODE = "silence"

LIPSYNC_ENV = "VIDEO_LIPSYNC"  # "off"/0/false/no disables (default on w/ FAL_KEY)
UPSCALE_ENV = "VIDEO_UPSCALE"  # "off"/0/false/no disables (default on w/ FAL_KEY)

_OFF = {"off", "0", "false", "no"}


def _is_off(value: str) -> bool:
    return value.strip().lower() in _OFF


def _fal_key_present() -> bool:
    return bool(os.environ.get("FAL_KEY", "").strip())


def lipsync_enabled() -> bool:
    """True when the lip-sync step should run: a ``FAL_KEY`` is present and
    ``VIDEO_LIPSYNC`` is not an off switch."""
    if _is_off(os.environ.get(LIPSYNC_ENV, "")):
        return False
    return _fal_key_present()


def upscale_enabled() -> bool:
    """True when the upscale step should run: a ``FAL_KEY`` is present and
    ``VIDEO_UPSCALE`` is not an off switch."""
    if _is_off(os.environ.get(UPSCALE_ENV, "")):
        return False
    return _fal_key_present()


def lipsync(
    client: FalClient,
    video_url: str,
    audio_url: str,
    *,
    budget_s: float = DEFAULT_BUDGET_S,
) -> str:
    """Sync the mouth in ``video_url`` to ``audio_url`` (fal urls, no upload).

    Returns the synced clip's fal url. Raises :class:`ProviderError` on any
    failure or an empty result."""
    result = client.run(
        LIPSYNC_MODEL,
        {
            "video_url": video_url,
            "audio_url": audio_url,
            "sync_mode": LIPSYNC_SYNC_MODE,
        },
        budget_s=budget_s,
        label="lipsync",
    )
    url = first_url(result, "video")
    if not url:
        raise ProviderError(
            f"lipsync ({LIPSYNC_MODEL}) returned no video url: "
            f"{json.dumps(result)[:300]}"
        )
    return url


def upscale(
    client: FalClient,
    video_url: str,
    *,
    factor: int = DEFAULT_UPSCALE_FACTOR,
    budget_s: float = DEFAULT_BUDGET_S,
) -> str:
    """Upscale ``video_url`` (a fal url) by ``factor``.

    Returns the upscaled clip's fal url. Raises :class:`ProviderError` on any
    failure or an empty result."""
    result = client.run(
        UPSCALE_MODEL,
        {"video_url": video_url, "upscale_factor": factor},
        budget_s=budget_s,
        label="upscale",
    )
    url = first_url(result, "video")
    if not url:
        raise ProviderError(
            f"upscale ({UPSCALE_MODEL}) returned no video url: "
            f"{json.dumps(result)[:300]}"
        )
    return url


def _rotation_degrees(path: Path) -> int:
    """The clip's display rotation in degrees (0 if none). Reads both the
    modern displaymatrix side-data and the legacy tags.rotate."""
    try:
        out = subprocess.run(
            [
                media.ffprobe_exe(), "-v", "error", "-select_streams", "v:0",
                "-show_entries",
                "stream_tags=rotate:side_data=rotation",
                "-of", "json", str(path),
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30,
        )
        data = json.loads(out.stdout or "{}")
    except (subprocess.SubprocessError, ValueError, OSError):
        return 0
    stream = (data.get("streams") or [{}])[0]
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    tag = (stream.get("tags") or {}).get("rotate")
    if tag is not None:
        try:
            return int(round(float(tag))) % 360
        except (TypeError, ValueError):
            pass
    return 0


def normalize_orientation(path: Path) -> Path:
    """Guarantee an upright clip whose pixels match its dimensions.

    Cheap insurance against a hosted post step (e.g. an occasional Topaz run)
    emitting a rotate flag the stitch filtergraph would ignore, leaving a shot
    sideways in the final cut. NO-OP in the common case (no rotate flag AND
    portrait), so upright clips are never needlessly re-encoded. Only a
    flagged or landscape clip is re-encoded with the rotation BAKED upright
    and the flag stripped. Best-effort: any failure returns the clip untouched.
    """
    path = Path(path)
    try:
        rotation = _rotation_degrees(path)
        info = media.probe_media(path)
        landscape = bool(info.width and info.height and info.width > info.height)
    except Exception:
        return path
    if rotation == 0 and not landscape:
        return path  # already upright portrait — leave it alone
    tmp = path.with_name(f"{path.stem}.oriented{path.suffix}")
    try:
        # ffmpeg autorotate (default on) bakes any rotate flag into pixels on
        # decode; -metadata rotate=0 strips the now-stale flag; re-encode H.264
        # yuv420p so every clip is uniform for stitch.
        media.run_ffmpeg(
            [
                "-i", str(path), "-map", "0:v:0",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-metadata:s:v:0", "rotate=0", "-an",
                str(tmp),
            ],
            timeout=300,
        )
    except Exception:
        tmp.unlink(missing_ok=True)
        return path
    if tmp.is_file() and tmp.stat().st_size > 0:
        tmp.replace(path)
    return path
