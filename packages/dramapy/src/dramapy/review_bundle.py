"""``dramapy.review_bundle`` — assemble the evidence a critic needs to judge a
rendered episode.

The dramacode loop closes on *structure* (does it render, does it obey the beat
law). It does not close on *quality* — is the hook sharp, do the faces stay the
same across shots, does the gut-punch land, is the whole thing accidentally
rotated 90°. That is a job for a viewer who actually WATCHES the cut. This
module builds the review bundle that viewer (the screening-room skill) reads:

* **Sampled frames** — for every shot, ~2-3 frames (early / mid / late) into
  ``<stem>_review/frames/``. The board shows only first-frames; sampling
  through each shot is how motion, mid-shot consistency, and defects that the
  first frame hides become visible.
* **The board + poster** — the contact sheet and cover the generator already
  wrote.
* **The metadata** — the ``.episode.json`` sidecar (durations, provider,
  validation warnings) and the per-shot spec (prompt, cast, line, emotion).
* **Audio stats** — via ffprobe/volumedetect: does the final cut carry audio,
  and at what level; is dialogue/score/sfx *expected* from the source.
* **Mechanical technical defects** — the things a machine catches better than
  an eye: wrong **orientation/aspect** (the live rotation bug — a shot whose
  display matrix is rotated or whose aspect ≠ the series aspect), duration
  drift, silent dialogue, missing shots, and (optional) all-black frames.

Everything is ffmpeg/ffprobe only and **nothing here raises fatally** — a
probe that fails appends to ``errors`` and the bundle still returns. The critic
gets what could be gathered, never a stack trace.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

SIDECAR_SUFFIX = ".episode.json"

# Aspect is "wrong" when it drifts more than this fraction from the series
# aspect. 2% swallows rounding (a 1080×1920 tile scaled to 270×480 stays
# exact) while a transposed clip (16:9 vs 9:16) is off by >200%.
ASPECT_TOLERANCE = 0.02
# Per-shot duration drift threshold — mirrors the contract's `duration_drift`
# warning (clip off spec by >15%).
DURATION_DRIFT_FRAC = 0.15
# Episode duration drift — mirrors the contract (episode off by >10%).
EPISODE_DRIFT_FRAC = 0.10
# A clip is "black" for defect purposes when black covers this fraction of it.
BLACK_FRACTION = 0.95


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe — never-raising thin wrappers
# ---------------------------------------------------------------------------


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(cmd: list[str], *, timeout: float = 60.0) -> subprocess.CompletedProcess | None:
    """Run a command; return the CompletedProcess or ``None`` on any failure.
    Never raises — the whole module is best-effort."""
    try:
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except Exception:
        return None


def _ffprobe_stream(path: Path) -> dict[str, Any] | None:
    """ffprobe the first video stream + format of ``path``. ``None`` when the
    file cannot be probed."""
    exe = _which("ffprobe")
    if not exe:
        return None
    proc = _run(
        [
            exe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    if proc is None or proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        return None


def _rotation_of(stream: dict[str, Any]) -> int:
    """Display rotation in degrees (0/90/180/270). Reads the modern Display
    Matrix ``side_data_list`` first, then the legacy ``tags.rotate``."""
    for side in stream.get("side_data_list") or []:
        if isinstance(side, dict) and "rotation" in side:
            try:
                return int(round(float(side["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(round(float(tags["rotate"]))) % 360
        except (TypeError, ValueError):
            pass
    return 0


def probe_clip(path: Path) -> dict[str, Any]:
    """Probe one clip into the facts the defect detector needs.

    Returns a dict with ``exists``, ``duration_s``, ``width``/``height`` (raw),
    ``rotation``, ``display_width``/``display_height`` (after rotation),
    ``aspect`` (display w/h), ``has_video``, ``has_audio``. Missing/unprobeable
    → ``exists=False`` with the rest ``None``/``False``.
    """
    info: dict[str, Any] = {
        "exists": Path(path).is_file(),
        "duration_s": None,
        "width": None,
        "height": None,
        "rotation": 0,
        "display_width": None,
        "display_height": None,
        "aspect": None,
        "has_video": False,
        "has_audio": False,
    }
    if not info["exists"]:
        return info
    payload = _ffprobe_stream(Path(path))
    if payload is None:
        return info

    fmt = payload.get("format") or {}
    try:
        info["duration_s"] = float(fmt.get("duration") or 0.0) or None
    except (TypeError, ValueError):
        info["duration_s"] = None

    for stream in payload.get("streams") or []:
        codec = stream.get("codec_type")
        if codec == "video" and not info["has_video"]:
            info["has_video"] = True
            w = int(stream.get("width") or 0) or None
            h = int(stream.get("height") or 0) or None
            info["width"], info["height"] = w, h
            rot = _rotation_of(stream)
            info["rotation"] = rot
            if w and h:
                if rot % 180 == 90:  # a quarter turn swaps the display box
                    info["display_width"], info["display_height"] = h, w
                else:
                    info["display_width"], info["display_height"] = w, h
                info["aspect"] = info["display_width"] / info["display_height"]
            if info["duration_s"] is None:
                try:
                    info["duration_s"] = float(stream.get("duration") or 0.0) or None
                except (TypeError, ValueError):
                    pass
        elif codec == "audio":
            info["has_audio"] = True
    return info


def measure_volume(path: Path) -> dict[str, Any]:
    """Mean/peak volume of the audio in ``path`` via ffmpeg ``volumedetect``.
    ``{has_audio, mean_volume_db, max_volume_db}`` — dB fields ``None`` when
    there is no audio track or the probe fails."""
    out: dict[str, Any] = {"has_audio": False, "mean_volume_db": None, "max_volume_db": None}
    probed = probe_clip(Path(path))
    out["has_audio"] = bool(probed.get("has_audio"))
    if not out["has_audio"]:
        return out
    exe = _which("ffmpeg")
    if not exe:
        return out
    proc = _run(
        [exe, "-hide_banner", "-nostdin", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"]
    )
    if proc is None:
        return out
    stderr = proc.stderr.decode("utf-8", "replace")
    for line in stderr.splitlines():
        line = line.strip()
        if "mean_volume:" in line:
            out["mean_volume_db"] = _parse_db(line.split("mean_volume:", 1)[1])
        elif "max_volume:" in line:
            out["max_volume_db"] = _parse_db(line.split("max_volume:", 1)[1])
    return out


def _parse_db(fragment: str) -> float | None:
    for token in fragment.replace("dB", " ").split():
        try:
            return float(token)
        except ValueError:
            continue
    return None


def _is_black(path: Path, duration_s: float | None) -> bool:
    """True when the clip is essentially all black (best-effort; never raises)."""
    exe = _which("ffmpeg")
    if not exe or not duration_s or duration_s <= 0:
        return False
    proc = _run(
        [
            exe,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-vf",
            "blackdetect=d=0:pic_th=0.98:pix_th=0.10",
            "-an",
            "-f",
            "null",
            "-",
        ]
    )
    if proc is None:
        return False
    stderr = proc.stderr.decode("utf-8", "replace")
    black = 0.0
    for line in stderr.splitlines():
        if "black_duration:" in line:
            try:
                black += float(line.split("black_duration:", 1)[1].split()[0])
            except (ValueError, IndexError):
                pass
    return black >= duration_s * BLACK_FRACTION


# ---------------------------------------------------------------------------
# Frame sampling
# ---------------------------------------------------------------------------


def _sample_times(duration_s: float, count: int) -> list[float]:
    """`count` timestamps spread across a clip of `duration_s`, kept off the
    exact head/tail. 3 → ~10% / 50% / 90%."""
    if duration_s <= 0:
        return [0.0]
    count = max(1, count)
    if count == 1:
        return [round(duration_s * 0.5, 3)]
    lo, hi = 0.1, 0.9
    step = (hi - lo) / (count - 1)
    return [round(duration_s * (lo + step * i), 3) for i in range(count)]


def sample_shot_frames(
    clip_path: Path,
    shot_id: str,
    frames_dir: Path,
    *,
    duration_s: float | None,
    count: int = 3,
) -> list[dict[str, Any]]:
    """Extract ``count`` frames from ``clip_path`` into ``frames_dir`` as
    ``<shot_id>__<i>.png``. Returns ``[{shot_id, t, path}]`` for the frames
    that were actually written; a shot that yields nothing returns ``[]``."""
    exe = _which("ffmpeg")
    if not exe or not Path(clip_path).is_file():
        return []
    frames_dir.mkdir(parents=True, exist_ok=True)
    dur = duration_s if (duration_s and duration_s > 0) else 1.0
    out: list[dict[str, Any]] = []
    for i, t in enumerate(_sample_times(dur, count)):
        target = frames_dir / f"{shot_id}__{i}.png"
        proc = _run(
            [
                exe,
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{t:.3f}",
                "-i",
                str(clip_path),
                "-frames:v",
                "1",
                "-update",
                "1",
                str(target),
            ]
        )
        if proc is not None and proc.returncode == 0 and target.is_file():
            out.append({"shot_id": shot_id, "t": t, "path": str(target)})
    return out


# ---------------------------------------------------------------------------
# Episode resolution (mp4 | sidecar | directory → the sidecar + friends)
# ---------------------------------------------------------------------------


def _resolve_sidecar(episode: Path, stem: str | None) -> Path | None:
    """Find the ``<stem>.episode.json`` for ``episode`` (a .mp4, a sidecar, or
    a directory holding one). ``None`` when it cannot be located."""
    episode = Path(episode).expanduser().resolve()
    if episode.is_file():
        if episode.name.endswith(SIDECAR_SUFFIX):
            return episode
        if episode.suffix.lower() == ".mp4":
            cand = episode.with_name(episode.stem + SIDECAR_SUFFIX)
            return cand if cand.is_file() else None
        return None
    if episode.is_dir():
        search = [episode]
        if (episode / "episodes").is_dir():
            search.append(episode / "episodes")
        for d in search:
            if stem:
                cand = d / f"{stem}{SIDECAR_SUFFIX}"
                if cand.is_file():
                    return cand
                continue
            sidecars = sorted(d.glob(f"*{SIDECAR_SUFFIX}"))
            if len(sidecars) == 1:
                return sidecars[0]
            if len(sidecars) > 1:
                # Ambiguous: caller must pass --stem. Best-effort → give up.
                return None
    return None


def _find_series_py(base_dir: Path) -> Path | None:
    for candidate in (base_dir, *base_dir.parents):
        series = candidate / "series.py"
        if series.is_file():
            return series
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# The bundle
# ---------------------------------------------------------------------------


def build_review_bundle(
    episode: Path | str,
    *,
    stem: str | None = None,
    frames_per_shot: int = 3,
    detect_black: bool = True,
) -> dict[str, Any]:
    """Assemble the review bundle for one rendered episode.

    ``episode`` may be the ``epNNN.mp4``, its ``.episode.json`` sidecar, or a
    directory containing one (``episodes/`` or the project root). Returns the
    manifest described in the module docstring; ``ok`` is ``False`` only when
    the episode could not be located at all.
    """
    errors: list[str] = []
    sidecar = _resolve_sidecar(Path(episode), stem)
    if sidecar is None:
        return {
            "ok": False,
            "error": (
                f"no {SIDECAR_SUFFIX} sidecar found for {episode} — render the "
                "episode with dramacode first"
            ),
            "frames": [],
            "board": None,
            "poster": None,
            "metadata": None,
            "audio_stats": {},
            "warnings": [],
            "shots": [],
            "defects": [],
            "errors": errors,
        }

    base_dir = sidecar.parent
    ep_stem = sidecar.name[: -len(SIDECAR_SUFFIX)]
    metadata = _read_json(sidecar) or {}
    if not metadata:
        errors.append(f"sidecar unreadable or empty: {sidecar.name}")

    ep_meta = metadata.get("episode") or {}
    resolution = ep_meta.get("resolution") or []
    series_json = _read_json(base_dir / "series.json") or _series_json_from_root(base_dir)
    series_aspect = _series_aspect(resolution, series_json)

    episode_mp4 = base_dir / f"{ep_stem}.mp4"
    review_dir = base_dir / f"{ep_stem}_review"
    shots_dir = base_dir / f"{ep_stem}_shots"
    frames_dir = review_dir / "frames"

    board = review_dir / "_board.png"
    poster = review_dir / "_poster.png"

    warnings = list((metadata.get("validation") or {}).get("warnings") or [])

    # -- Per-shot: spec (from shot sidecar) + measured (from ffprobe) + frames --
    shots: list[dict[str, Any]] = []
    defects: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    sidecar_shots = metadata.get("shots") or []

    for entry in sidecar_shots:
        if not isinstance(entry, dict):
            continue
        shot_id = str(entry.get("id", ""))
        rel = entry.get("path")
        clip_path = (base_dir / str(rel)) if rel else None
        spec = _read_json(base_dir / str(entry["jsonPath"])) if entry.get("jsonPath") else None
        spec = spec or {}

        probed = probe_clip(clip_path) if clip_path is not None else {"exists": False}
        measured_dur = probed.get("duration_s")
        # shot sidecar durationS is the SPEC duration; episode sidecar
        # shots[].durationS is the MEASURED one — prefer the probe, fall back.
        spec_dur = _as_float(spec.get("durationS"))
        if spec_dur is None:
            spec_dur = _as_float(entry.get("durationS"))

        status = str(entry.get("status", spec.get("status", "")))
        shot_row: dict[str, Any] = {
            "shot_id": shot_id,
            "kind": spec.get("kind", ""),
            "prompt": spec.get("prompt", ""),
            "cast": list(spec.get("cast") or []),
            "line": spec.get("line"),
            "emotion": spec.get("emotion"),
            "status": status,
            "path": str(clip_path) if clip_path is not None else None,
            "duration_s_spec": spec_dur,
            "duration_s_measured": measured_dur,
            "width": probed.get("width"),
            "height": probed.get("height"),
            "rotation": probed.get("rotation", 0),
            "display_width": probed.get("display_width"),
            "display_height": probed.get("display_height"),
            "aspect": probed.get("aspect"),
            "has_audio": bool(probed.get("has_audio")),
        }
        shots.append(shot_row)

        # -- defects for this shot --
        if status == "failed" or clip_path is None or not probed.get("exists"):
            defects.append(
                _defect("missing_shot", shot_id, f"shot {shot_id} produced no clip (status={status or 'missing'})", "blocker")
            )
            continue  # nothing else to probe on a missing clip

        # Orientation / aspect — THIS is the rotation bug the critic must catch.
        rot = probed.get("rotation", 0)
        shot_aspect = probed.get("aspect")
        rotated = bool(rot) and rot % 180 != 0
        aspect_off = (
            series_aspect is not None
            and shot_aspect is not None
            and abs(shot_aspect - series_aspect) / series_aspect > ASPECT_TOLERANCE
        )
        if rotated or aspect_off:
            reason = []
            if rotated:
                reason.append(f"display rotation {rot}°")
            if aspect_off:
                reason.append(
                    f"aspect {shot_aspect:.3f} vs series {series_aspect:.3f}"
                )
            detail = (
                f"shot {shot_id}: {probed.get('width')}x{probed.get('height')}"
                f" — {', '.join(reason)}"
            )
            defects.append(_defect("orientation_aspect", shot_id, detail, "blocker"))

        # Duration drift.
        if spec_dur and measured_dur and spec_dur > 0:
            drift = abs(measured_dur - spec_dur) / spec_dur
            if drift > DURATION_DRIFT_FRAC:
                defects.append(
                    _defect(
                        "duration_drift",
                        shot_id,
                        f"shot {shot_id}: {measured_dur:.2f}s vs spec {spec_dur:.2f}s "
                        f"({drift * 100:.0f}% off)",
                        "major",
                    )
                )

        # Silent dialogue.
        if shot_row["kind"] == "dialogue" and shot_row["line"] and not probed.get("has_audio"):
            defects.append(
                _defect("silent_dialogue", shot_id, f"dialogue shot {shot_id} has no audio track", "major")
            )

        # Black frames (optional, conservative).
        if detect_black and _is_black(clip_path, measured_dur):
            defects.append(
                _defect("black_frames", shot_id, f"shot {shot_id} is essentially all black", "major")
            )

        # -- sample frames through the shot --
        try:
            frames.extend(
                sample_shot_frames(
                    clip_path,
                    shot_id,
                    frames_dir,
                    duration_s=measured_dur or spec_dur,
                    count=frames_per_shot,
                )
            )
        except Exception as exc:  # pragma: no cover — best-effort
            errors.append(f"frame sampling failed for {shot_id}: {type(exc).__name__}: {exc}")

    # -- Episode-level duration drift --
    ep_measured = probe_clip(episode_mp4).get("duration_s") if episode_mp4.is_file() else None
    spec_total = sum(s["duration_s_spec"] for s in shots if s.get("duration_s_spec"))
    if ep_measured and spec_total and spec_total > 0:
        drift = abs(ep_measured - spec_total) / spec_total
        if drift > EPISODE_DRIFT_FRAC:
            defects.append(
                _defect(
                    "duration_drift",
                    None,
                    f"episode runs {ep_measured:.1f}s vs {spec_total:.1f}s of shots "
                    f"({drift * 100:.0f}% off)",
                    "major",
                )
            )

    # -- Audio stats (final cut) + intent inferred from the source --
    audio_stats = measure_volume(episode_mp4) if episode_mp4.is_file() else {
        "has_audio": False,
        "mean_volume_db": None,
        "max_volume_db": None,
    }
    voice_expected = any(s["kind"] == "dialogue" and s["line"] for s in shots)
    audio_stats["voice_expected"] = voice_expected
    audio_stats["dialogue_shots"] = sum(1 for s in shots if s["kind"] == "dialogue" and s["line"])
    source_py = base_dir / f"{ep_stem}.py"
    audio_stats["music_expected"], audio_stats["sfx_expected"] = _audio_intent_from_source(
        source_py if source_py.is_file() else None
    )
    if voice_expected and not audio_stats.get("has_audio"):
        defects.append(
            _defect("silent_dialogue", None, "episode has dialogue but the final cut carries no audio", "major")
        )

    return {
        "ok": True,
        "stem": ep_stem,
        "episode": {
            "path": ep_meta.get("path", episode_mp4.name),
            "number": ep_meta.get("number"),
            "title": ep_meta.get("title"),
            "duration_s": ep_meta.get("durationS"),
            "duration_s_measured": ep_measured,
            "fps": ep_meta.get("fps"),
            "resolution": list(resolution) if resolution else None,
            "aspect": series_aspect,
        },
        "series": series_json,
        "source": {
            "episode_source": str(source_py) if source_py.is_file() else None,
            "series_py": str(_find_series_py(base_dir) or "") or None,
        },
        "board": str(board) if board.is_file() else None,
        "poster": str(poster) if poster.is_file() else None,
        "frames": frames,
        "metadata": metadata,
        "audio_stats": audio_stats,
        "warnings": warnings,
        "shots": shots,
        "defects": defects,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _defect(kind: str, shot_id: str | None, detail: str, severity: str) -> dict[str, Any]:
    return {"kind": kind, "shot_id": shot_id, "detail": detail, "severity": severity}


def _as_float(value: Any) -> float | None:
    try:
        f = float(value)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _series_json_from_root(base_dir: Path) -> dict[str, Any] | None:
    series_py = _find_series_py(base_dir)
    if series_py is None:
        return None
    return _read_json(series_py.parent / "series.json")


def _series_aspect(resolution: Any, series_json: dict[str, Any] | None) -> float | None:
    """Series aspect as a float (display w/h). Prefer the resolution the
    sidecar recorded; fall back to the ``"9:16"`` string in series.json."""
    if isinstance(resolution, (list, tuple)) and len(resolution) == 2:
        try:
            w, h = float(resolution[0]), float(resolution[1])
            if w > 0 and h > 0:
                return w / h
        except (TypeError, ValueError):
            pass
    if series_json:
        aspect = series_json.get("aspect")
        if isinstance(aspect, str) and ":" in aspect:
            try:
                w, h = aspect.split(":", 1)
                fw, fh = float(w), float(h)
                if fw > 0 and fh > 0:
                    return fw / fh
            except (TypeError, ValueError):
                pass
    return None


def _audio_intent_from_source(source_py: Path | None) -> tuple[bool | None, bool | None]:
    """Best-effort read of whether the episode *intends* music / sfx, scanned
    from the source (the sidecar records neither). ``(None, None)`` when the
    source is unavailable — the critic then judges from the source directly."""
    if source_py is None:
        return None, None
    try:
        text = source_py.read_text(encoding="utf-8")
    except OSError:
        return None, None
    music = ("bgm=" in text.replace(" ", "")) and ("bgm=None" not in text.replace(" ", ""))
    sfx = "sfx" in text
    return music, sfx


# ---------------------------------------------------------------------------
# CLI — one JSON line on stdout (the skill's one-line discipline)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="review_bundle",
        description=(
            "Build the screening-room review bundle for a rendered episode: "
            "sampled frames, board/poster, metadata, audio stats, and "
            "mechanically-detected technical defects (incl. the rotation bug)."
        ),
    )
    parser.add_argument("episode", help="epNNN.mp4, its .episode.json, or a project/episodes dir")
    parser.add_argument("--stem", default=None, help="disambiguate a dir with several episodes")
    parser.add_argument("--frames-per-shot", type=int, default=3)
    parser.add_argument("--no-black-detect", action="store_true")
    args = parser.parse_args(argv)

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
