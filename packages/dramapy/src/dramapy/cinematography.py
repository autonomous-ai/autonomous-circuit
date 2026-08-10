"""Director-grade prompt assembly for the cinematic (keyframe → image-to-video)
provider.

The hosted text-to-video providers (fal/minimax/dashscope) share the flat
:func:`dramapy.providers.base.build_shot_prompt`. The cinematic pipeline is
different in kind: it renders a *still keyframe* first (for character
consistency) and then animates it, so it needs two prompts per shot — a
composition prompt for the frame and a motion/camera prompt for the video —
plus a neutral portrait prompt for the one-time character reference.

Everything here is pure string assembly (no I/O, no network), so it is unit
testable and shared by nothing that runs on CI's network-free path.

Design rules (from ``knowledge/`` house style, applied to prompts):
  * A shot's *kind* picks the shot size and the camera move — establish is a
    wide slow push-in, dialogue a locked medium close-up, action is dynamic,
    insert is a macro detail. One grammar, keyed off ``shot.kind``.
  * The *series style* picks lens, lighting and color-grade language —
    photoreal-drama is anamorphic + shallow DoF + filmic grade; the drawn
    styles swap in their own vocabulary.
  * Dialogue gets a *visual* speaking cue only, never the spoken words — the
    line lives on the audio/subtitle track, and folding text into an image
    prompt reliably trips hosted content filters (verified against fal
    2026-08-09).
  * Every frame is vertical 9:16.
"""

from __future__ import annotations

from dramapy.providers.base import ShotContext
from dramapy.spec import ResolvedCharacter, ResolvedSeries

# shot.kind -> (shot size, camera move). Unknown kinds fall back to dialogue's
# medium framing rather than raising — resolution already constrains kind.
SHOT_GRAMMAR: dict[str, tuple[str, str]] = {
    "establish": (
        "wide establishing shot, full environment in frame",
        "slow cinematic push-in",
    ),
    "dialogue": (
        "medium close-up, subject framed head-and-shoulders",
        "locked-off frame with a subtle breathing handheld drift",
    ),
    "action": (
        "dynamic medium shot, energetic composition",
        "fast tracking move, motivated whip-pan, kinetic camera",
    ),
    "insert": (
        "extreme macro detail insert, single object filling the frame",
        "slow rack focus, shallow move across the detail",
    ),
}
_DEFAULT_GRAMMAR = SHOT_GRAMMAR["dialogue"]

# series.style -> lens / lighting / color-grade language for the keyframe.
STYLE_GRADE: dict[str, str] = {
    "photoreal-drama": (
        "photorealistic cinematic drama, anamorphic lens, shallow depth of "
        "field, natural motivated lighting, cinematic color grade, subtle "
        "filmic grain"
    ),
    "manhwa": (
        "manhwa webtoon illustration, bold clean ink linework, dramatic "
        "screentone shading, high-contrast cel color, sharp rim light"
    ),
    "anime": (
        "anime illustration, expressive features, painterly backgrounds, soft "
        "rim light, cel shading, atmospheric depth"
    ),
}

_SPEAKING_CUE = "mid-conversation, mouth slightly parted mid-word, emotive"
_VERTICAL = "vertical 9:16 composition"
_NEG_TEXT = "no on-screen text, no captions, no watermark, no logo"


def style_grade(series: ResolvedSeries) -> str:
    """Lens/lighting/grade language for a series style (falls through to the
    raw style string for a style we have no grammar for)."""
    return STYLE_GRADE.get(series.style, series.style)


def shot_grammar(kind: str) -> tuple[str, str]:
    """(shot size, camera move) for a shot kind."""
    return SHOT_GRAMMAR.get(kind, _DEFAULT_GRAMMAR)


def _character_clause(characters: tuple[ResolvedCharacter, ...]) -> str:
    """Name (+ short look) for each character, to anchor the reference edit."""
    parts: list[str] = []
    for character in characters:
        if character.look:
            parts.append(f"{character.name} ({character.look})")
        else:
            parts.append(character.name)
    return ", ".join(parts)


def _character_names(characters: tuple[ResolvedCharacter, ...]) -> str:
    """Names only — for the MOTION prompt, where the keyframe already carries
    every character's appearance, so repeating the full look is redundant (and
    with several characters it blows past the i2v model's prompt-length cap)."""
    return ", ".join(c.name for c in characters if c.name)


# Kling i2v rejects prompts over 2500 chars (HTTP 422 string_too_long). A
# multi-character shot whose author prompt embeds verbatim look descriptors can
# exceed that; cap the motion prompt with margin (the negative_prompt is separate
# and not counted). The keyframe holds identity, so a front-truncated motion
# prompt — action first — still drives the shot correctly.
MOTION_PROMPT_MAX = 2400


def _cap_prompt(text: str, limit: int = MOTION_PROMPT_MAX) -> str:
    """Truncate to <= limit chars at a clean sentence/word boundary."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = head.rfind(". ")
    if cut >= int(limit * 0.6):          # prefer a sentence boundary if not too early
        return head[:cut + 1]
    cut = head.rfind(" ")
    return (head[:cut] if cut > 0 else head).rstrip(" ,;")


def build_portrait_prompt(character: ResolvedCharacter, series: ResolvedSeries) -> str:
    """Neutral reference portrait for a character — generated once and reused
    as the identity anchor for every keyframe. Plain background, front-lit,
    styled to the series so the reference and the shots share a look."""
    parts = [
        f"cinematic character reference portrait of {character.name}",
        character.look or "a person",
        "head and shoulders, facing camera, neutral expression",
        "clean neutral studio background, soft even key light",
        style_grade(series),
        _VERTICAL,
    ]
    return ". ".join(part for part in parts if part)


def build_cinematic_prompt(ctx: ShotContext, *, stage: str) -> str:
    """Director-grade prompt for one stage of the cinematic pipeline.

    ``stage="keyframe"`` -> a still composition prompt (fed to the keyframe
    image model). ``stage="motion"`` -> a camera/motion prompt (fed to the
    image-to-video model, which already inherits the frame's look from the
    keyframe, so this leans on movement and performance).
    """
    shot = ctx.shot
    series = ctx.series
    size, camera = shot_grammar(shot.kind)

    if stage == "motion":
        # Names only — the keyframe already holds every character's appearance;
        # repeating full look descriptors here is redundant and overflows the
        # i2v prompt cap on multi-character shots.
        names = _character_names(ctx.characters)
        parts: list[str] = [camera]
        if names:
            parts.append(f"{names} holding a consistent look")
        if shot.prompt:
            parts.append(shot.prompt)
        if shot.emotion:
            parts.append(f"emotion: {shot.emotion}")
        if shot.kind == "dialogue":
            parts.append(_SPEAKING_CUE)
        parts.append("smooth natural motion, cinematic timing")
        # Cap the descriptive body BEFORE the fixed tail so the negative/vertical
        # cues always survive the length limit.
        body = _cap_prompt(". ".join(part for part in parts if part))
        return ". ".join([body, _NEG_TEXT, _VERTICAL])

    cast = _character_clause(ctx.characters)

    # stage == "keyframe" (default): a still frame.
    parts = [style_grade(series), size]
    if cast:
        parts.append(f"featuring {cast}")
    if shot.prompt:
        parts.append(shot.prompt)
    if shot.emotion:
        parts.append(f"mood: {shot.emotion}")
    if shot.kind == "dialogue":
        parts.append(_SPEAKING_CUE)
    parts.append(_NEG_TEXT)
    parts.append(_VERTICAL)
    return ". ".join(part for part in parts if part)
