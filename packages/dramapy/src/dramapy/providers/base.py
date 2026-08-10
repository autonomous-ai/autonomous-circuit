"""Provider abstraction (contract §1): ``Provider.render_shot(ctx) -> Path``.

A provider turns one :class:`ShotContext` into one clip file at
``ctx.output_path``. Implementations: **mock** (ffmpeg-synthesized
placeholder, the only provider tests exercise) and the hosted backends
**fal** / **dashscope** / **minimax** (network code fully isolated in their
modules; never exercised in tests — CI never hits a network).

Failures raise :class:`~dramapy.errors.ProviderError` with a clear message;
``ctx.max_render_s`` is the per-shot budget every implementation honors.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dramapy.spec import ResolvedCharacter, ResolvedSeries, ResolvedShot


@dataclass(frozen=True)
class ShotContext:
    """Everything a provider needs to render one shot."""

    shot: ResolvedShot
    series: ResolvedSeries
    characters: tuple[ResolvedCharacter, ...]
    output_path: Path
    max_render_s: float | None = None
    scene_id: str = ""  # the enclosing scene (mock: per-scene palette)
    # World-consistency anchors (optional; the cinematic provider caches a
    # reference per distinct location/prop and stacks it into the keyframe edit).
    # ``location`` is the enclosing scene's ``location``; ``props`` are recurring
    # objects to hold consistent. Both default empty, so a provider that ignores
    # them (mock/fal/…) and any caller that omits them are unaffected.
    location: str = ""
    props: tuple[str, ...] = ()
    # Continuity seed (#34): the previous shot's tail frame, when this shot
    # chains off it (see ``dramapy.continuity.chain_plan``). A provider that
    # supports it seeds image-to-video from this frame instead of a fresh
    # keyframe, so a continuous run reads as one take. Default None; mock/fal
    # and any caller that omits it are unaffected.
    first_frame: Path | None = None


class Provider:
    """Render backend interface. Subclasses set ``name``/``model`` (these are
    written into sidecars and folded into render-cache keys). ``cache_salt``
    is extra provider state that changes clip bytes without changing the
    model id (e.g. the mock's TTS mode); it is folded into render-cache keys
    but never written to sidecars."""

    name: str = "base"
    model: str = ""
    cache_salt: str = ""

    def render_shot(self, ctx: ShotContext) -> Path:
        raise NotImplementedError


def build_shot_prompt(ctx: ShotContext) -> str:
    """Shared text-to-video prompt assembly for the hosted providers."""
    shot = ctx.shot
    series = ctx.series
    parts: list[str] = []
    style = {
        "photoreal-drama": "photorealistic cinematic drama, natural light, shallow depth of field",
        "manhwa": "manhwa webtoon style, bold ink lines, dramatic screentones",
        "anime": "anime style, expressive faces, painterly backgrounds",
    }.get(series.style, series.style)
    parts.append(style)
    if shot.prompt:
        parts.append(shot.prompt)
    if shot.emotion:
        parts.append(f"emotion: {shot.emotion}")
    for character in ctx.characters:
        if character.look:
            parts.append(f"{character.name}: {character.look}")
    # A dialogue shot gets a VISUAL speaking cue only — never the words. The
    # spoken line lives on the audio/subtitle track; the video model generates
    # silent footage, and folding dark dialogue into the image prompt does
    # nothing useful while reliably tripping hosted content filters (verified
    # against fal 2026-08-09: a revenge line returned content_policy_violation).
    if shot.kind == "dialogue":
        parts.append("the character is speaking, mouth moving, emotive close-up")
    parts.append("vertical 9:16 short-drama shot")
    return ". ".join(parts)
