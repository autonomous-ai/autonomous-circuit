"""Deterministic episode verifiers — every warning kind in the contract §1
table, with the donor's never-raise discipline (cadpy ``checks.py``).

These run after shots render and surface structured warnings the review
loop gates on. **Checks never raise** — a verifier that itself errors is
reported as ``check_failed`` (severity warning), so validation can never
break generation. Severity is the driver's ONLY gate (``error`` blocks,
``warning`` reviews, ``info`` advises); the ``kind`` set is open.

Shot inputs are plain mappings (duck-typed, like everything in dramapy)
with the keys produced by generation's render loop:

    id, kind, spec_duration_s, status ("rendered"|"cached"|"failed"),
    clip_path (str|Path|None), duration_s, width, height, has_audio,
    error (optional detail for failed renders)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

# Drift thresholds (contract §1): a clip off its spec duration by >15%, or
# the episode off the summed spec by >10%, is a duration_drift warning.
CLIP_DRIFT_FRACTION = 0.15
EPISODE_DRIFT_FRACTION = 0.10

# A clip's aspect must match the series aspect within this relative
# tolerance (absorbs even-dimension rounding, e.g. 426x758 vs 9:16).
ASPECT_REL_TOLERANCE = 0.02

Warning = dict  # {"part": str, "kind": str, "detail": str, "severity": str}


def _warning(part: str, kind: str, detail: str, severity: str = "warning") -> Warning:
    return {"part": part, "kind": kind, "detail": detail, "severity": severity}


def _get(shot: Mapping, key: str, default: object = None) -> object:
    return shot.get(key, default) if isinstance(shot, Mapping) else getattr(shot, key, default)


def stitch_failed_warning(detail: str) -> Warning:
    """The ``stitch_failed`` (error) entry — kept alongside the other kinds
    for completeness; :func:`dramapy.generation.generate_episode` raises
    ``ExportError`` instead when the concat/mux itself fails, since the
    contract requires the final .mp4 to always exist on success."""
    return _warning("episode", "stitch_failed", detail, "error")


def check_shot_outputs(shots: Sequence[Mapping]) -> list[Warning]:
    """``render_failed`` + ``missing_shot`` (both severity error)."""
    warnings: list[Warning] = []
    for shot in shots:
        shot_id = str(_get(shot, "id", "shot"))
        status = _get(shot, "status")
        if status == "failed":
            detail = str(_get(shot, "error", "") or "provider returned failure")
            warnings.append(
                _warning(
                    shot_id,
                    "render_failed",
                    f"provider returned failure after retries: {detail}",
                    "error",
                )
            )
        clip_path = _get(shot, "clip_path")
        if clip_path is None or not Path(str(clip_path)).is_file():
            warnings.append(
                _warning(
                    shot_id,
                    "missing_shot",
                    "declared shot produced no clip",
                    "error",
                )
            )
    return warnings


def check_aspect(shots: Sequence[Mapping], *, aspect: str) -> list[Warning]:
    """``aspect_mismatch`` (error) — a clip's aspect ≠ series aspect."""
    try:
        aspect_w, aspect_h = (int(part) for part in aspect.split(":"))
    except ValueError:
        return [
            _warning(
                "episode",
                "check_failed",
                f"aspect check could not parse series aspect {aspect!r}",
            )
        ]
    warnings: list[Warning] = []
    for shot in shots:
        width = _get(shot, "width")
        height = _get(shot, "height")
        if not width or not height:
            continue
        expected = aspect_w / aspect_h
        actual = float(width) / float(height)
        if abs(actual - expected) > expected * ASPECT_REL_TOLERANCE:
            warnings.append(
                _warning(
                    str(_get(shot, "id", "shot")),
                    "aspect_mismatch",
                    f"clip is {width}x{height} ({actual:.3f}), series aspect is "
                    f"{aspect} ({expected:.3f})",
                    "error",
                )
            )
    return warnings


def check_duration_drift(
    shots: Sequence[Mapping],
    *,
    episode_duration_s: float | None,
    spec_total_s: float,
) -> list[Warning]:
    """``duration_drift`` (warning) — clip off spec by >15% or episode >10%."""
    warnings: list[Warning] = []
    for shot in shots:
        spec = _get(shot, "spec_duration_s")
        actual = _get(shot, "duration_s")
        if not spec or actual is None:
            continue
        drift = abs(float(actual) - float(spec)) / float(spec)
        if drift > CLIP_DRIFT_FRACTION:
            warnings.append(
                _warning(
                    str(_get(shot, "id", "shot")),
                    "duration_drift",
                    f"clip is {float(actual):.2f}s vs {float(spec):.2f}s spec "
                    f"({100 * drift:.0f}% off, threshold "
                    f"{100 * CLIP_DRIFT_FRACTION:.0f}%)",
                )
            )
    if episode_duration_s is not None and spec_total_s > 0:
        drift = abs(episode_duration_s - spec_total_s) / spec_total_s
        if drift > EPISODE_DRIFT_FRACTION:
            warnings.append(
                _warning(
                    "episode",
                    "duration_drift",
                    f"episode is {episode_duration_s:.2f}s vs {spec_total_s:.2f}s "
                    f"spec ({100 * drift:.0f}% off, threshold "
                    f"{100 * EPISODE_DRIFT_FRACTION:.0f}%)",
                )
            )
    return warnings


def check_silent_dialogue(
    shots: Sequence[Mapping], *, voiced_shot_ids: set[str] | None = None
) -> list[Warning]:
    """``silent_dialogue`` (warning) — a dialogue line that got NO spoken audio
    in the FINAL episode. Voices are assembled provider-independently now
    (:mod:`dramapy.voices`), not baked per-clip, so this reflects the episode's
    voice track, not the shot clip: ``voiced_shot_ids`` is the set of shots that
    landed a voice. A dialogue shot warns when it is absent from that set —
    i.e. the whole voice layer was off/unavailable (``None``/empty) or that one
    line failed to synthesize."""
    voiced = voiced_shot_ids or set()
    warnings: list[Warning] = []
    for shot in shots:
        if _get(shot, "kind") != "dialogue":
            continue
        clip_path = _get(shot, "clip_path")
        if clip_path is None:
            continue  # already a missing_shot error
        line = _get(shot, "line")
        if not (isinstance(line, str) and line.strip()):
            continue  # nothing to speak
        shot_id = str(_get(shot, "id", "shot"))
        if shot_id not in voiced:
            warnings.append(
                _warning(
                    shot_id,
                    "silent_dialogue",
                    "dialogue line got no spoken audio in the episode",
                )
            )
    return warnings


def check_hook(episode: object) -> list[Warning]:
    """``hook_too_long`` (warning) — first non-establish beat lands after
    ``hook_max_s`` (computed on spec durations, in declared order)."""
    hook_max_s = float(getattr(episode, "hook_max_s", 5.0))
    elapsed = 0.0
    for shot in getattr(episode, "shots", ()):
        if shot.kind != "establish":
            if elapsed > hook_max_s + 1e-9:
                return [
                    _warning(
                        shot.id,
                        "hook_too_long",
                        f"first non-establish beat ('{shot.id}') starts at "
                        f"{elapsed:.1f}s, after the {hook_max_s:g}s hook window",
                    )
                ]
            return []
        elapsed += float(shot.duration_s)
    return [
        _warning(
            "episode",
            "hook_too_long",
            "episode has no non-establish beat at all — the hook never lands",
        )
    ]


def check_cliffhanger(episode: object, shots: Sequence[Mapping]) -> list[Warning]:
    """``no_cliffhanger`` (warning) — cliffhanger unset, or the final
    declared shot produced no clip."""
    cliffhanger = getattr(episode, "cliffhanger", None)
    if not cliffhanger:
        return [
            _warning(
                "episode",
                "no_cliffhanger",
                "episode declares no cliffhanger — every episode must end on one",
            )
        ]
    if shots:
        final = shots[-1]
        clip_path = _get(final, "clip_path")
        if clip_path is None or not Path(str(clip_path)).is_file():
            return [
                _warning(
                    str(_get(final, "id", "shot")),
                    "no_cliffhanger",
                    "final shot produced no clip, so the declared cliffhanger "
                    f"({cliffhanger!r}) never lands",
                )
            ]
    return []


def coerce_functional_warnings(raw: object) -> list[Warning]:
    """Normalize project-declared envelope ``warnings`` (kind ``functional``).

    Defaults per the contract: ``part`` → ``"episode"`` (episode-level),
    ``kind`` → ``"functional"``, ``severity`` → ``"warning"`` (and coerced
    into the closed {error, warning, info} set). ``detail`` is required —
    entries without a usable detail are dropped. Never raises.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[Warning] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        detail = item.get("detail")
        if not isinstance(detail, str) or not detail.strip():
            continue
        kind = item.get("kind")
        part = item.get("part")
        severity = item.get("severity")
        out.append(
            _warning(
                part if isinstance(part, str) and part.strip() else "episode",
                kind if isinstance(kind, str) and kind.strip() else "functional",
                detail,
                severity
                if isinstance(severity, str) and severity in {"error", "warning", "info"}
                else "warning",
            )
        )
    return out


def collect_episode_warnings(
    *,
    episode: object,
    shots: Sequence[Mapping],
    aspect: str,
    episode_duration_s: float | None,
    voiced_shot_ids: set[str] | None = None,
) -> list[Warning]:
    """Run every verifier; a verifier that raises becomes ``check_failed``.

    ``voiced_shot_ids`` (the shots that got spoken audio in the assembled
    episode) drives ``silent_dialogue`` — with voices present a normal render
    produces zero silent_dialogue warnings."""
    named_checks: list[tuple[str, object]] = [
        ("shot_outputs", lambda: check_shot_outputs(shots)),
        ("aspect", lambda: check_aspect(shots, aspect=aspect)),
        (
            "duration_drift",
            lambda: check_duration_drift(
                shots,
                episode_duration_s=episode_duration_s,
                spec_total_s=sum(
                    float(_get(shot, "spec_duration_s", 0) or 0) for shot in shots
                ),
            ),
        ),
        (
            "silent_dialogue",
            lambda: check_silent_dialogue(shots, voiced_shot_ids=voiced_shot_ids),
        ),
        ("hook", lambda: check_hook(episode)),
        ("cliffhanger", lambda: check_cliffhanger(episode, shots)),
    ]
    warnings: list[Warning] = []
    for check_name, check in named_checks:
        try:
            warnings.extend(check())  # type: ignore[operator]
        except Exception as exc:  # never let a verifier break generation
            warnings.append(
                _warning(
                    "episode",
                    "check_failed",
                    f"{check_name} check raised {type(exc).__name__}: {exc}",
                )
            )
    return warnings
