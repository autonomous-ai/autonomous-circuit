"""Content-addressed render cache under ``<project>/.video/render-cache/``.

A shot clip is keyed by ``sha256`` of the canonical-JSON of everything that
determines its pixels: the shot spec, the enclosing scene id, the series
style/resolution/fps, the provider (name + model + cache salt), and the
fingerprints of the cast members appearing in the shot (contract §1 +
2026-08-09 animatic change). A re-stitch after an edit re-renders only
changed shots; a cache hit surfaces as shot status ``"cached"``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from dramapy.spec import ResolvedCharacter, ResolvedSeries, ResolvedShot

CACHE_VERSION = 2  # 2: animatic mock — sceneId + providerSalt join the key
CACHE_DIR_PARTS = (".video", "render-cache")


def render_cache_dir(project_root: Path) -> Path:
    return project_root.joinpath(*CACHE_DIR_PARTS)


def shot_cache_key(
    *,
    shot: ResolvedShot,
    series: ResolvedSeries,
    provider_name: str,
    provider_model: str,
    characters: tuple[ResolvedCharacter, ...] = (),
    scene_id: str = "",
    location: str = "",
    provider_salt: str = "",
) -> str:
    """sha256 hex over the canonical shot/scene/style/provider/cast payload."""
    payload = {
        "cacheVersion": CACHE_VERSION,
        "shot": {
            "id": shot.id,
            "kind": shot.kind,
            "durationS": shot.duration_s,
            "prompt": shot.prompt,
            "line": shot.line,
            "emotion": shot.emotion,
            "cast": list(shot.cast),
        },
        "sceneId": scene_id,
        "providerSalt": provider_salt,
        # Only keyed when present, so mock/fal (no location) keep their existing
        # cache keys — no global bust — while a location edit correctly
        # re-renders the consistency-aware (cinematic) world anchor.
        **({"location": location} if location else {}),
        "style": series.style,
        "aspect": series.aspect,
        "resolution": list(series.resolution),
        "fps": series.fps,
        "provider": {"name": provider_name, "model": provider_model},
        "cast": [
            {
                "id": member.id,
                "look": member.look,
                "voice": member.voice,
                "refImages": list(member.ref_images),
            }
            for member in characters
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cache_clip_path(project_root: Path, key: str) -> Path:
    return render_cache_dir(project_root) / f"{key}.mp4"


def cache_lookup(project_root: Path, key: str) -> Path | None:
    """The cached clip for ``key``, or ``None``. Empty files never hit."""
    candidate = cache_clip_path(project_root, key)
    try:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    except OSError:
        return None
    return None


def cache_store(project_root: Path, key: str, clip_path: Path) -> Path:
    """Copy a freshly rendered clip into the cache (best-effort atomic)."""
    target = cache_clip_path(project_root, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    shutil.copy2(clip_path, partial)
    partial.replace(target)
    return target
