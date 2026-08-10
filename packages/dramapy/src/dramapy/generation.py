"""``generate_episode()`` — the public §1 entry point of dramapy.

Mirrors the donor's ``cadpy.generation.generate_step`` wrapper: polymorphic
input (an ``episodes/epNNN.py`` file or a directory containing ``main.py``),
the project root (the directory holding ``series.py``) placed on
``sys.path`` for the duration of the project code run, envelope resolution
(``{"episode": ..., "warnings": [...]}`` — the only accepted shape), the
error hierarchy from :mod:`dramapy.errors` re-exported here verbatim, and
the full §1 artifact set written in the contract's order — the
``.episode.json`` sidecar lands **before** the final ``.mp4`` so
``artifact_changed`` for the episode fires after its metadata is readable
(the stitched video is assembled in ``.video/tmp`` and moved into place
last).

``SyntaxError`` and ``ImportError`` from project code propagate untouched.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

from dramapy import checks
from dramapy import media
from dramapy import render_cache
from dramapy import sfx as sfx_mod
from dramapy import subtitles as subtitles_mod
from dramapy import voices
from dramapy.errors import (
    EnvelopeError,
    ExportError,
    GenerationError,
    GeneratorRuntimeError,
    ProjectShapeError,
    ProviderError,
    SpecValidationError,
)
from dramapy.poster import BoardItem, write_board, write_poster
from dramapy.providers import make_provider
from dramapy.providers.base import Provider, ShotContext
from dramapy.source_hash import PythonSourceHash, episode_source_hash
from dramapy.spec import (
    ResolvedCharacter,
    ResolvedEpisode,
    ResolvedSeries,
    resolve_cast,
    resolve_episode,
    resolve_series,
)
from dramapy.stitch import ClipSegment, SubtitleOverlay, stitch_episode
from dramapy.text import render_text_png

__all__ = [
    "generate_episode",
    "GenerationError",
    "ProjectShapeError",
    "GeneratorRuntimeError",
    "SpecValidationError",
    "ProviderError",
    "ExportError",
]

GENERATOR_NAME = "dramapy"
DEFAULT_PROVIDER = "mock"
PROVIDER_ENV = "VIDEO_PROVIDER"
RENDER_ATTEMPTS = 2  # one retry for transient failures; timeouts don't retry
DEFAULT_RENDER_CONCURRENCY = 4  # shots fan across a bounded thread pool


def _render_concurrency() -> int:
    """How many shots to render at once. VIDEO_RENDER_CONCURRENCY overrides;
    1 = serial. Clamped to [1, 16] — beyond that hosted rate limits and local
    ffmpeg contention dominate."""
    raw = os.environ.get("VIDEO_RENDER_CONCURRENCY", "").strip()
    if not raw:
        return DEFAULT_RENDER_CONCURRENCY
    try:
        return max(1, min(16, int(raw)))
    except ValueError:
        return DEFAULT_RENDER_CONCURRENCY


# ---------------------------------------------------------------------------
# Project discovery + module loading.
# ---------------------------------------------------------------------------


def _resolve_script_path(source_path: Path) -> Path:
    if source_path.is_dir():
        script_path = source_path / "main.py"
        if not script_path.is_file():
            raise ProjectShapeError(
                f"directory input must contain main.py: {source_path}"
            )
        return script_path
    if not source_path.is_file():
        raise ProjectShapeError(f"episode source not found: {source_path}")
    if source_path.suffix != ".py":
        raise ProjectShapeError(
            f"episode source must be a .py file or a directory with main.py "
            f"(got {source_path.name})"
        )
    return source_path


def _find_project_root(script_path: Path) -> Path:
    """The nearest ancestor directory containing ``series.py``."""
    for candidate in (script_path.parent, *script_path.parent.parents):
        if (candidate / "series.py").is_file():
            return candidate
    raise ProjectShapeError(
        f"no series.py found in any parent of {script_path} — a drama project "
        "root must contain the series bible"
    )


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _load_and_run_project(
    script_path: Path, project_root: Path
) -> tuple[ResolvedSeries, tuple[ResolvedCharacter, ...], object]:
    """Import series.py + the episode module (project root on ``sys.path``),
    call ``gen_episode()``, and return the resolved bible + raw envelope.

    ``sys.path`` and project-owned ``sys.modules`` entries are restored
    afterwards so back-to-back projects never cross-contaminate.
    """
    sys_path_snapshot = list(sys.path)
    modules_before = set(sys.modules)
    # Never let another project's bible answer this project's `import series`.
    stale = sys.modules.get("series")
    if stale is not None and not (
        getattr(stale, "__file__", None)
        and Path(stale.__file__).resolve() == (project_root / "series.py").resolve()
    ):
        del sys.modules["series"]

    try:
        sys.path.insert(0, str(project_root))

        try:
            series_module = importlib.import_module("series")
        except (SyntaxError, ImportError):
            raise
        except Exception as exc:
            raise GeneratorRuntimeError(
                f"failed to load series.py: {type(exc).__name__}: {exc}"
            ) from exc

        raw_series = getattr(series_module, "SERIES", None)
        if raw_series is None:
            raise ProjectShapeError("series.py must define a module-level SERIES")
        series = resolve_series(raw_series)
        cast = resolve_cast(getattr(series_module, "CAST", None))

        module_name = (
            "_dramapy_ep_"
            + str(script_path.resolve())
            .replace("/", "_")
            .replace("\\", "_")
            .replace("-", "_")
            .replace(".", "_")
        )
        module_spec = importlib.util.spec_from_file_location(module_name, script_path)
        if module_spec is None or module_spec.loader is None:
            raise GeneratorRuntimeError(
                f"failed to load episode module from {script_path}"
            )
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_name] = module
        try:
            module_spec.loader.exec_module(module)
        except (SyntaxError, ImportError):
            raise
        except GenerationError:
            raise
        except Exception as exc:
            raise GeneratorRuntimeError(
                f"failed to load {script_path.name}: {type(exc).__name__}: {exc}"
            ) from exc

        generator = getattr(module, "gen_episode", None)
        if not callable(generator):
            raise ProjectShapeError(
                f"{script_path.name} does not define a callable gen_episode()"
            )

        try:
            raw_envelope = generator()
        except (SyntaxError, ImportError):
            raise
        except GenerationError:
            raise
        except AssertionError as exc:
            # A project's validate() uses `assert` to hard-block impossible
            # beats. Surface the assert message cleanly (contract §1).
            raise ProjectShapeError(
                f"validation failed: {exc}" if str(exc) else "validation failed"
            ) from exc
        except Exception as exc:
            raise GeneratorRuntimeError(
                f"gen_episode() raised {type(exc).__name__}: {exc}"
            ) from exc

        return series, cast, raw_envelope
    finally:
        sys.path[:] = sys_path_snapshot
        for name in set(sys.modules) - modules_before:
            module = sys.modules.get(name)
            module_file = getattr(module, "__file__", None)
            if name.startswith("_dramapy_ep_") or (
                module_file and _is_under(Path(module_file), project_root)
            ):
                del sys.modules[name]


def _normalize_envelope(raw: object, *, script_name: str) -> tuple[object, object]:
    """Contract §1: the envelope dict is the ONLY accepted return shape; no
    legacy forms; unknown keys raise ``TypeError`` (via :class:`EnvelopeError`,
    which is also a :class:`ProjectShapeError`)."""
    if not isinstance(raw, dict):
        raise EnvelopeError(
            f"{script_name} gen_episode() must return an envelope dict "
            f"{{'episode': ..., 'warnings': [...]}} (got {type(raw).__name__})"
        )
    allowed = {"episode", "warnings"}
    extra = sorted(str(key) for key in raw if key not in allowed)
    if extra:
        raise EnvelopeError(
            f"{script_name} gen_episode() envelope has unsupported key(s): "
            f"{', '.join(extra)}"
        )
    if "episode" not in raw:
        raise EnvelopeError(
            f"{script_name} gen_episode() envelope must define 'episode'"
        )
    return raw["episode"], raw.get("warnings")


# ---------------------------------------------------------------------------
# Sidecar writers (camelCase, canonical JSON: sort_keys, (",", ":")).
# ---------------------------------------------------------------------------


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _write_shot_sidecar(
    *,
    path: Path,
    shot,
    provider: Provider,
    status: str,
    render_ms: int,
    draws: int,
    cache_key: str,
    error: str | None,
) -> None:
    payload: dict[str, object] = {
        "id": shot.id,
        "kind": shot.kind,
        "durationS": shot.duration_s,
        "prompt": shot.prompt,
        "cast": list(shot.cast),
        "provider": {"name": provider.name, "model": provider.model},
        "renderMs": int(render_ms),
        "draws": int(draws),
        "status": status,
        "cacheKey": cache_key,
    }
    if shot.line is not None:
        payload["line"] = shot.line
    if shot.emotion is not None:
        payload["emotion"] = shot.emotion
    if error:
        payload["error"] = error
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(payload), encoding="utf-8")


def _write_series_json(
    project_root: Path,
    series: ResolvedSeries,
    cast: tuple[ResolvedCharacter, ...],
) -> Path:
    """Write ``<project>/series.json`` — the bible surfaced for the cast
    panel (contract change 2026-08-09, additive). Derived from ``series.py``
    on every generation; canonical JSON like the other sidecars."""
    payload: dict[str, object] = {
        "title": series.title,
        "genre": series.genre,
        "style": series.style,
        "aspect": series.aspect,
        "fps": series.fps,
        "cast": [
            {
                "id": member.id,
                "name": member.name,
                "look": member.look,
                "voice": member.voice,
                "ref_images": list(member.ref_images),
            }
            for member in cast
        ],
    }
    path = project_root / "series.json"
    path.write_text(_canonical_json(payload), encoding="utf-8")
    return path


def _write_episode_sidecar(
    *,
    path: Path,
    output_path: Path,
    identity: PythonSourceHash,
    episode: ResolvedEpisode,
    series: ResolvedSeries,
    provider: Provider,
    duration_s: float,
    warnings: list[dict],
    shot_entries: list[dict],
) -> None:
    validation: dict[str, object] = {
        "durationS": round(duration_s, 3),
        "shotCount": len(episode.shots),
    }
    if warnings:
        validation["warnings"] = warnings
    payload: dict[str, object] = {
        "generator": GENERATOR_NAME,
        "entryKind": "episode",
        "source": {
            "kind": "python",
            "path": identity.source_path,
            "hash": identity.source_hash,
            "fingerprint": identity.source_fingerprint,
        },
        "episode": {
            "path": output_path.name,
            "number": episode.number,
            "title": episode.title,
            "durationS": round(duration_s, 3),
            "fps": series.fps,
            "resolution": list(series.resolution),
        },
        "provider": {"name": provider.name, "model": provider.model},
        "validation": validation,
        "shots": shot_entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# The public entry point.
# ---------------------------------------------------------------------------


def generate_episode(
    source_path: Path | str,
    output_path: Path | str,
    *,
    provider: str | None = None,
    max_render_s: float | None = None,
) -> dict[str, object]:
    """Generate one episode per contract §1 and return the snake_case dict
    mirroring the DramacodeResult success fields (§3)."""
    source_p = Path(source_path).expanduser().resolve()
    output_p = Path(output_path).expanduser().resolve()
    if output_p.suffix.lower() != ".mp4":
        raise ProjectShapeError(f"output_path must end in .mp4 (got {output_p.name})")

    script_path = _resolve_script_path(source_p)
    project_root = _find_project_root(script_path)

    series, cast, raw_envelope = _load_and_run_project(script_path, project_root)
    raw_episode, raw_warnings = _normalize_envelope(
        raw_envelope, script_name=script_path.name
    )
    cast_by_id = {member.id: member for member in cast}
    episode = resolve_episode(raw_episode, cast_ids=frozenset(cast_by_id))
    functional_warnings = checks.coerce_functional_warnings(raw_warnings)

    provider_name = (
        provider
        if provider is not None
        else os.environ.get(PROVIDER_ENV, "").strip() or DEFAULT_PROVIDER
    )
    provider_impl = make_provider(provider_name)

    identity = episode_source_hash(script_path, project_root)

    parent = output_p.parent
    stem = output_p.stem
    shots_dir = parent / f"{stem}_shots"
    review_dir = parent / f"{stem}_review"
    sidecar_path = parent / f"{stem}.episode.json"
    srt_path = parent / f"{stem}.srt"

    # Idempotent regeneration: when nothing that feeds the artifacts changed
    # (source fingerprint, provider) and every artifact is still on disk,
    # return the prior result with ZERO writes. This keeps the driver's
    # review rounds and no-op re-runs instant and event-quiet — without it
    # every craft round rewrites byte-identical files and re-fires
    # artifact_changed for the whole episode. VIDEO_FORCE_REGEN=1 overrides.
    if os.environ.get("VIDEO_FORCE_REGEN", "").strip().lower() not in {"1", "true", "yes"}:
        prior = _unchanged_prior_result(
            sidecar_path=sidecar_path,
            identity=identity,
            provider_impl=provider_impl,
            output_p=output_p,
            srt_path=srt_path,
            review_dir=review_dir,
            parent=parent,
            episode=episode,
        )
        if prior is not None:
            return prior

    try:
        _write_series_json(project_root, series, cast)
    except OSError as exc:
        raise ExportError(f"failed to write series.json: {exc}") from exc

    shots_dir.mkdir(parents=True, exist_ok=True)
    for stale in shots_dir.glob("shot_*"):
        if stale.suffix in {".mp4", ".json"}:
            stale.unlink(missing_ok=True)

    all_shots = episode.shots
    scene_of = {
        shot.id: scene.id for scene in episode.scenes for shot in scene.shots
    }
    # The enclosing scene's location, per shot — lets a consistency-aware
    # provider (cinematic) cache one world anchor per distinct location and
    # stack it into the keyframe, so the WORLD holds across shots, not just
    # faces. Empty when a scene has no location; providers that ignore it
    # (mock/fal) are unaffected.
    location_of = {
        shot.id: (getattr(scene, "location", "") or "")
        for scene in episode.scenes
        for shot in scene.shots
    }
    provider_salt = str(getattr(provider_impl, "cache_salt", "") or "")

    # -- Render every shot (cache-aware, mock/fal/hosted behind Provider). --
    # Each shot is independent (distinct clip path, cache key, sidecar), so
    # they fan across a bounded thread pool — hosted providers are I/O-bound
    # on their poll loops, and a 15-shot episode drops from ~30 min serial to
    # roughly the slowest single shot. Concurrency: VIDEO_RENDER_CONCURRENCY
    # (default 4); 1 forces the old serial path.
    def _render_one(shot):
        clip_path = shots_dir / f"shot_{shot.id}.mp4"
        shot_characters = tuple(cast_by_id[member_id] for member_id in shot.cast)
        cache_key = render_cache.shot_cache_key(
            shot=shot,
            series=series,
            provider_name=provider_impl.name,
            provider_model=provider_impl.model,
            characters=shot_characters,
            scene_id=scene_of.get(shot.id, ""),
            location=location_of.get(shot.id, ""),
            provider_salt=provider_salt,
        )
        started = time.monotonic()
        status = "failed"
        draws = 0
        error_detail: str | None = None

        cached = render_cache.cache_lookup(project_root, cache_key)
        if cached is not None:
            shutil.copy2(cached, clip_path)
            status = "cached"
        else:
            ctx = ShotContext(
                shot=shot,
                series=series,
                characters=shot_characters,
                output_path=clip_path,
                max_render_s=max_render_s,
                scene_id=scene_of.get(shot.id, ""),
                location=location_of.get(shot.id, ""),
            )
            for attempt in range(1, RENDER_ATTEMPTS + 1):
                draws = attempt
                try:
                    provider_impl.render_shot(ctx)
                    status = "rendered"
                    error_detail = None
                    break
                except GenerationError as exc:
                    error_detail = str(exc)
                    if "timed out" in error_detail:
                        break  # the budget is spent; retrying doubles the waste
                except Exception as exc:  # a broken provider must not crash the run
                    error_detail = f"{type(exc).__name__}: {exc}"
            if status == "rendered":
                render_cache.cache_store(project_root, cache_key, clip_path)
            else:
                clip_path.unlink(missing_ok=True)

        render_ms = int((time.monotonic() - started) * 1000)

        info: dict[str, object] = {
            "id": shot.id,
            "kind": shot.kind,
            "spec_duration_s": shot.duration_s,
            "status": status,
            "clip_path": clip_path if clip_path.is_file() else None,
            "duration_s": None,
            "width": None,
            "height": None,
            "has_audio": False,
            "error": error_detail,
            "cache_key": cache_key,
            "render_ms": render_ms,
            "draws": draws,
            "line": shot.line,
        }
        if info["clip_path"] is not None:
            try:
                probed = media.probe_media(clip_path)
                info["duration_s"] = probed.duration_s
                info["width"] = probed.width
                info["height"] = probed.height
                info["has_audio"] = probed.has_audio
            except Exception as exc:
                info["probe_error"] = f"{type(exc).__name__}: {exc}"

        _write_shot_sidecar(
            path=shots_dir / f"shot_{shot.id}.json",
            shot=shot,
            provider=provider_impl,
            status=status,
            render_ms=render_ms,
            draws=draws,
            cache_key=cache_key,
            error=error_detail,
        )
        return info

    concurrency = _render_concurrency()
    if concurrency <= 1 or len(all_shots) <= 1:
        shot_infos = [_render_one(shot) for shot in all_shots]
    else:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(concurrency, len(all_shots))
        ) as pool:
            # map preserves input order — shot_infos stays in episode order.
            shot_infos = list(pool.map(_render_one, all_shots))

    stitchable = [
        info
        for info in shot_infos
        if info["clip_path"] is not None and info["duration_s"] is not None
    ]
    if not stitchable:
        first_error = next(
            (info["error"] for info in shot_infos if info["error"]), "no clips"
        )
        raise ProviderError(
            f"no shots rendered ({len(all_shots)} declared) — first failure: "
            f"{first_error}"
        )

    # -- Subtitles + voice track (both timed to the stitched clip timeline). -
    # A shot's spoken voice tag is its first cast member's Character.voice.
    voice_tag_by_shot = {
        shot.id: (cast_by_id[shot.cast[0]].voice if shot.cast else "")
        for scene in episode.scenes
        for shot in scene.shots
    }
    emotion_by_shot = {
        shot.id: (shot.emotion or "")
        for scene in episode.scenes
        for shot in scene.shots
    }
    timeline: list[tuple[str, str | None, float, float]] = []
    voice_entries: list[dict] = []
    elapsed = 0.0
    for info in stitchable:
        duration = float(info["duration_s"])  # type: ignore[arg-type]
        shot_id = str(info["id"])
        is_dialogue = info["kind"] == "dialogue"
        line = info["line"] if is_dialogue else None
        timeline.append((shot_id, line, elapsed, duration))
        if is_dialogue and isinstance(line, str) and line.strip():
            voice_entries.append(
                {
                    "shot_id": shot_id,
                    "line": line,
                    "voice_tag": voice_tag_by_shot.get(shot_id, ""),
                    "emotion": emotion_by_shot.get(shot_id, ""),
                    "start_s": elapsed,
                    "duration_s": duration,
                }
            )
        elapsed += duration
    subtitle_entries = subtitles_mod.build_entries(timeline)
    srt_written = False
    if subtitle_entries:
        subtitles_mod.write_srt(subtitle_entries, srt_path)
        srt_written = True

    # -- Spoken dialogue, generated once here (provider-independent). Mixed
    # under the video by the stitcher; None (no say / VIDEO_VOICES=off / all
    # failed) degrades to a subtitles-only episode. ``voiced_shot_ids`` feeds
    # the silent_dialogue check.
    voiced_shot_ids: set[str] = set()
    voice_track = voices.build_voice_track(voice_entries, voiced_out=voiced_shot_ids)

    # -- Layered SFX track (assembly stage; see dramapy.sfx). Cues come from the
    # same shots the render iterated: explicit per-shot ``sfx`` or inferred from
    # the prompt keywords + kind, placed at each shot's stitched-timeline start.
    # None (no key / VIDEO_SFX=off / nothing cued) degrades to no SFX. -------
    shot_by_id = {shot.id: shot for scene in episode.scenes for shot in scene.shots}
    clip_by_shot = {str(info["id"]): info["clip_path"] for info in stitchable}
    sfx_entries: list[dict] = []
    for shot_id, _line, start_s, duration_s in timeline:
        shot = shot_by_id.get(shot_id)
        if shot is None:
            continue
        clip = clip_by_shot.get(shot_id)
        sfx_entries.append(
            {
                "shot_id": shot_id,
                "kind": shot.kind,
                "prompt": shot.prompt,
                "start_s": start_s,
                "duration_s": duration_s,
                "sfx": getattr(shot, "sfx", None),
                "clip_path": str(clip) if clip is not None else None,
            }
        )
    sfx_track = sfx_mod.build_sfx_track(sfx_entries)

    # -- Stitch to a temp path; the final .mp4 lands AFTER the sidecar. ------
    tmp_dir = project_root / ".video" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_output = tmp_dir / f"{stem}-{os.getpid()}.mp4"

    overlays: list[SubtitleOverlay] = []
    overlay_dir = tmp_dir / f"{stem}-{os.getpid()}-subs"
    if episode.burn_subtitles and subtitle_entries:
        overlay_dir.mkdir(parents=True, exist_ok=True)
        scale = max(1, series.resolution[0] // 360)
        for entry in subtitle_entries:
            png = render_text_png(
                list(entry.lines),
                overlay_dir / f"sub_{entry.index:03d}.png",
                scale=scale,
            )
            overlays.append(
                SubtitleOverlay(png_path=png, start_s=entry.start_s, end_s=entry.end_s)
            )

    segments = [
        ClipSegment(
            shot_id=str(info["id"]),
            path=Path(str(info["clip_path"])),
            duration_s=float(info["duration_s"]),  # type: ignore[arg-type]
        )
        for info in stitchable
    ]
    try:
        stitch_episode(
            segments=segments,
            series=series,
            output_path=tmp_output,
            bgm_mood=episode.bgm,
            subtitle_overlays=overlays,
            voice_track=voice_track,
            sfx_track=sfx_track,
        )
        episode_duration_s = media.probe_media(tmp_output).duration_s
    except Exception as exc:
        tmp_output.unlink(missing_ok=True)
        shutil.rmtree(overlay_dir, ignore_errors=True)
        if voice_track is not None:
            voice_track.unlink(missing_ok=True)
        if sfx_track is not None:
            sfx_track.unlink(missing_ok=True)
        raise ExportError(
            f"failed to stitch {output_p.name}: {type(exc).__name__}: {exc}"
        ) from exc
    shutil.rmtree(overlay_dir, ignore_errors=True)
    if voice_track is not None:
        voice_track.unlink(missing_ok=True)
    if sfx_track is not None:
        sfx_track.unlink(missing_ok=True)

    # -- Checks (never raise) + review images + sidecar + final rename. ------
    warnings = checks.collect_episode_warnings(
        episode=episode,
        shots=shot_infos,
        aspect=series.aspect,
        episode_duration_s=episode_duration_s,
        voiced_shot_ids=voiced_shot_ids,
    )
    for info in shot_infos:
        if "probe_error" in info:
            warnings.append(
                checks._warning(
                    str(info["id"]),
                    "check_failed",
                    f"clip probe raised: {info['probe_error']}",
                )
            )
    warnings.extend(subtitles_mod.collect_subtitle_warnings(subtitle_entries))
    warnings.extend(functional_warnings)

    board_items = [
        BoardItem(
            shot_id=str(info["id"]),
            clip_path=Path(str(info["clip_path"])),
            duration_s=float(info["duration_s"]),  # type: ignore[arg-type]
            is_dialogue=info["kind"] == "dialogue",
        )
        for info in stitchable
    ]
    try:
        write_poster(board_items, review_dir)
        write_board(board_items, review_dir, series_resolution=series.resolution)
    except Exception as exc:
        tmp_output.unlink(missing_ok=True)
        raise ExportError(
            f"failed to write review images for {output_p.name}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    shot_entries = []
    for info in shot_infos:
        entry: dict[str, object] = {
            "id": str(info["id"]),
            "jsonPath": f"{shots_dir.name}/shot_{info['id']}.json",
            "status": str(info["status"]),
        }
        if info["clip_path"] is not None:
            entry["path"] = f"{shots_dir.name}/shot_{info['id']}.mp4"
        if info["duration_s"] is not None:
            # Measured via ffprobe — the storyboard strip's seek offsets
            # read this (contract change 2026-08-09).
            entry["durationS"] = round(float(info["duration_s"]), 3)  # type: ignore[arg-type]
        shot_entries.append(entry)

    try:
        _write_episode_sidecar(
            path=sidecar_path,
            output_path=output_p,
            identity=identity,
            episode=episode,
            series=series,
            provider=provider_impl,
            duration_s=episode_duration_s,
            warnings=warnings,
            shot_entries=shot_entries,
        )
    except Exception as exc:
        tmp_output.unlink(missing_ok=True)
        raise ExportError(
            f"failed to write metadata sidecar: {type(exc).__name__}: {exc}"
        ) from exc

    # Ordering rule: the sidecar is already on disk; the episode .mp4 appears
    # (and gets its mtime) last, so artifact_changed fires after metadata is
    # readable.
    try:
        parent.mkdir(parents=True, exist_ok=True)
        if _is_same_filesystem(tmp_output, parent):
            os.replace(tmp_output, output_p)
        else:  # pragma: no cover — projects and outputs usually share a disk
            shutil.copy2(tmp_output, output_p)
            tmp_output.unlink(missing_ok=True)
        os.utime(output_p, None)
    except OSError as exc:
        raise ExportError(
            f"failed to move stitched episode into place: {exc}"
        ) from exc

    result_shots = [
        {
            "id": str(info["id"]),
            "path": str(info["clip_path"]) if info["clip_path"] is not None else None,
            "duration_s": (
                float(info["duration_s"])
                if info["duration_s"] is not None
                else float(info["spec_duration_s"])  # type: ignore[arg-type]
            ),
            "status": str(info["status"]),
        }
        for info in shot_infos
    ]
    result: dict[str, object] = {
        "episode_path": str(output_p),
        "metadata_path": str(sidecar_path),
        "duration_s": float(episode_duration_s),
        "shot_count": len(all_shots),
        "shots": result_shots,
        "warnings": warnings,
    }
    if srt_written:
        result["srt_path"] = str(srt_path)
    return result


def _unchanged_prior_result(
    *,
    sidecar_path: Path,
    identity,
    provider_impl,
    output_p: Path,
    srt_path: Path,
    review_dir: Path,
    parent: Path,
    episode,
) -> dict[str, object] | None:
    """Return the §3-shaped result reconstructed from the existing sidecar
    when the prior run is provably still valid; None means run for real.
    Conservative on every doubt: unreadable sidecar, fingerprint or provider
    drift, or any missing artifact falls through to full generation."""
    try:
        prior = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(prior, dict):
        return None
    source = prior.get("source") or {}
    prov = prior.get("provider") or {}
    if source.get("fingerprint") != identity.source_fingerprint:
        return None
    if (
        prov.get("name") != provider_impl.name
        or prov.get("model") != provider_impl.model
    ):
        return None
    if not output_p.exists():
        return None
    for name in ("_poster.png", "_board.png"):
        if not (review_dir / name).exists():
            return None
    shot_entries = prior.get("shots")
    if not isinstance(shot_entries, list) or not shot_entries:
        return None
    # Every shot the episode defines must be present in the prior sidecar. A
    # shorter/mismatched set means shots failed or were skipped last run, so the
    # episode is NOT complete — falling through to a full render. (Without this,
    # a re-run after partial failures was a silent no-op: the sidecar listed only
    # the shots that succeeded, all of which existed, so the check passed.)
    prior_ids = {str(e.get("id")) for e in shot_entries if isinstance(e, dict)}
    if prior_ids != {shot.id for shot in episode.shots}:
        return None
    for entry in shot_entries:
        if not isinstance(entry, dict) or not entry.get("jsonPath"):
            return None
        if not (parent / str(entry["jsonPath"])).exists():
            return None
        rel = entry.get("path")
        if rel and not (parent / str(rel)).exists():
            return None
    needs_srt = any(
        shot.kind == "dialogue" and shot.line for shot in episode.shots
    )
    if needs_srt and not srt_path.exists():
        return None

    episode_meta = prior.get("episode") or {}
    validation = prior.get("validation") or {}
    duration_s = episode_meta.get("durationS")
    if not isinstance(duration_s, (int, float)):
        return None
    result_shots = []
    for entry in shot_entries:
        rel = entry.get("path")
        result_shots.append(
            {
                "id": str(entry.get("id")),
                "path": str(parent / str(rel)) if rel else None,
                "duration_s": float(entry.get("durationS") or 0.0),
                "status": "cached",
            }
        )
    result: dict[str, object] = {
        "episode_path": str(output_p),
        "metadata_path": str(sidecar_path),
        "duration_s": float(duration_s),
        "shot_count": len(shot_entries),
        "shots": result_shots,
        "warnings": list(validation.get("warnings") or []),
        "unchanged": True,
    }
    if srt_path.exists():
        result["srt_path"] = str(srt_path)
    return result


def _is_same_filesystem(path_a: Path, path_b: Path) -> bool:
    try:
        return path_a.stat().st_dev == path_b.stat().st_dev
    except OSError:
        return False
