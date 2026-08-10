"""The score: a real generated cinematic instrumental when a ``FAL_KEY`` is
present, a mood-keyed ffmpeg-synth drone otherwise (contract §1: ``bgm`` is a
dramalib.audio mood key or ``None``).

Not a flat loop — a **dynamic score that follows the episode arc**, ducked under
dialogue at mix time (:func:`duck_filter`, a sidechain compressor keyed on the
voice track) so lines stay intelligible and the music rises back between them.

Two model backends behind one interface (``VIDEO_MUSIC_MODEL``):

* **ElevenLabs Music** (``fal-ai/elevenlabs/music``) — the **primary** studio-grade
  cinematic model (default when ``FAL_KEY`` is set). For a real episode it builds
  the arc **natively** via a ``composition_plan``: ordered sections (hook → build →
  emotional low → climax → cliffhanger) each with their own duration and intensity
  styles, so the tension/heartbreak/climax shape is composed into the music rather
  than faked with volume automation. Very short cues fall back to a prompt-only
  request. Instrumental is enforced with empty section lyrics + ``vocals`` negative
  styles (``force_instrumental`` is rejected alongside a plan — a live schema quirk).
* **Lyria 2** (``fal-ai/lyria2``) — the **fallback** (and selectable via
  ``VIDEO_MUSIC_MODEL=lyria``). Prompt-only text-to-music; its flat bed gets the
  build-to-climax volume envelope baked in at fit time. (``minimax-music`` needs a
  ``reference_audio_url`` — a continuation model, not text-to-music — so it is unused.)

Whichever model runs, the bed is looped/trimmed to the episode duration and
returned to the stitcher as a lavfi ``amovie=`` source. The primary is tried
first, the other second; on both failing (or no key / ``VIDEO_MUSIC=synth``) the
offline synth drone runs — a mood-keyed lavfi source plus a shaping chain and a
comma-free rising ramp so even the fallback builds toward the end.

``VIDEO_MUSIC``: ``off`` → no bed (``None``); ``synth`` → force the synth drone;
else the generated score when possible, synth on any failure or missing key.
``VIDEO_MUSIC_MODEL``: ``elevenlabs`` (default) / ``lyria`` picks the primary.
The generated bed file is a temp file cleaned up at process exit; the stitcher
only reads it, never owns it.
"""

from __future__ import annotations

import atexit
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dramapy import media
from dramapy.fal_client import FalClient, first_url

MUSIC_ENV = "VIDEO_MUSIC"  # "off" → no bed, "synth" → force synth, else auto
MUSIC_MODEL_ENV = "VIDEO_MUSIC_MODEL"  # "elevenlabs" (default) / "lyria"

ELEVEN_MUSIC_MODEL = "fal-ai/elevenlabs/music"  # primary: studio cinematic music
LYRIA_MODEL = "fal-ai/lyria2"  # fallback: prompt-only text-to-music
MUSIC_MODEL = ELEVEN_MUSIC_MODEL  # back-compat alias: the default primary model

# Steer both models away from vocals so the bed stays a score, never a song.
MUSIC_NEGATIVE_PROMPT = "vocals, singing, lyrics, spoken word, choir, low quality"
MUSIC_BUDGET_S = 180.0  # fal budget per generation; failure → next model → synth

# ElevenLabs composition-plan limits (from the live schema).
MIN_SECTION_MS = 3000  # a plan section must be ≥ 3 s
MAX_SECTION_MS = 120000  # …and ≤ 120 s
COMPOSITION_MIN_S = 6.0  # below this, a plan can't hold ≥2 sections → prompt-only

# -- The build-to-climax arc (fractions of the episode + target levels). ------
# Used two ways: as ElevenLabs plan sections (native dynamics) and as the baked
# volume envelope for the flat Lyria/prompt beds. Fractions of the episode
# duration; levels are linear volume multipliers (1.0 = the bed at full level).
SCORE_T_HOOK = 0.15  # end of the hold under the opening hook
SCORE_T_LOW = 0.60  # the emotional low point
SCORE_T_CLIMAX = 0.88  # the climax swell peaks here
SCORE_V_HOOK = 0.78  # level under the hook
SCORE_V_LOW = 0.55  # the softer/heartbreaking dip
SCORE_V_CLIMAX = 1.00  # full-level climax
SCORE_V_END = 0.82  # settle into the cliffhanger

# -- Ducking (score under dialogue). The mixer applies this sidechain
# compressor to the bed, keyed on the spoken-voice track: the score drops
# ~8-10 dB while a line plays and rises back in the gaps. Lives here (the score
# owns how it sits under dialogue); the stitcher wires the labels. -----------
DUCK_THRESHOLD = 0.02  # sidechain (voice) level above which the bed compresses
DUCK_RATIO = 8.0  # firm gain reduction → a clear ~8-10 dB duck (tuned live)
DUCK_ATTACK_MS = 15.0  # clamp fast when a line starts
DUCK_RELEASE_MS = 350.0  # ease the score back up between lines
DUCK_MAKEUP = 1.0  # no make-up gain — the bed stays under the voice


@dataclass(frozen=True)
class BgmSpec:
    """One lavfi input expression + the shaping chain applied to it."""

    input_expr: str
    filter_chain: str
    mood: str


# mood → (source template with {d} duration slot, shaping chain).
MOODS: dict[str, tuple[str, str]] = {
    "tense-strings": ("sine=f=110:r=48000:d={d}", "tremolo=f=0.4:d=0.8"),
    "romance-warm": ("sine=f=262:r=48000:d={d}", "tremolo=f=0.25:d=0.4"),
    "melancholy": ("sine=f=98:r=48000:d={d}", "tremolo=f=0.3:d=0.6"),
    "neon-pulse": ("sine=f=165:r=48000:d={d}", "tremolo=f=6:d=0.6"),
    "rain-ambient": (
        "anoisesrc=colour=brown:r=48000:amplitude=0.5:seed=7:d={d}",
        "lowpass=f=900",
    ),
}
_FALLBACK: tuple[str, str] = ("sine=f=131:r=48000:d={d}", "tremolo=f=0.2:d=0.5")

# mood → a rich cinematic instrumental-score prompt (style/instrumentation/arc).
# The prompt-only path (short cues, Lyria). The optional series genre is prepended.
MOOD_PROMPTS: dict[str, str] = {
    "tense-strings": (
        "epic dark orchestral score, soaring strings and taiko drums, ostinato "
        "building relentless suspense to a devastating climax, cinematic film score"
    ),
    "romance-warm": (
        "warm romantic orchestral score, tender piano and lush strings, swelling "
        "hopeful and emotional, cinematic film score"
    ),
    "melancholy": (
        "melancholic cinematic score, sorrowful solo piano and aching cello, "
        "slow and heartbreaking, minor key, sparse strings, film score"
    ),
    "neon-pulse": (
        "dark cinematic synthwave score, driving arpeggios and pulsing bass over "
        "orchestral strings, neon nightscape building to a climax, film score"
    ),
    "rain-ambient": (
        "atmospheric cinematic score, calm ambient pads and soft piano over rain, "
        "sparse and brooding, slowly rising, film score"
    ),
}
_MOOD_PROMPT_FALLBACK = (
    "cinematic orchestral underscore, strings and low brass, building tension, "
    "film score"
)

# mood → style tokens for the ElevenLabs composition plan's global styles.
MOOD_STYLES: dict[str, list[str]] = {
    "tense-strings": ["epic dark orchestral", "soaring strings", "taiko drums", "suspenseful"],
    "romance-warm": ["warm orchestral", "tender piano", "lush strings", "hopeful"],
    "melancholy": ["melancholic", "solo piano", "aching cello", "sorrowful", "minor key"],
    "neon-pulse": ["dark synthwave", "driving arpeggios", "pulsing bass", "orchestral hybrid"],
    "rain-ambient": ["atmospheric ambient", "soft piano", "brooding pads"],
}
_STYLE_FALLBACK = ["cinematic orchestral", "strings", "low brass", "tense"]
_BASE_STYLES = ["cinematic film score", "instrumental", "high quality"]
_NEG_STYLES = ["vocals", "lyrics", "singing", "spoken word", "low quality"]

# The dramatic arc as (section_name, intensity styles, weight, priority). Weight
# splits the duration; priority picks which phases survive when the episode is too
# short to hold all five (higher survives). Climax and build always win first.
_ARC: tuple[tuple[str, list[str], float, int], ...] = (
    ("hook", ["restrained", "sparse", "ominous low strings"], 0.15, 1),
    ("build", ["rising tension", "layered strings and percussion", "accelerating"], 0.30, 3),
    ("low", ["heartbreaking", "solo piano", "intimate", "quiet"], 0.15, 2),
    ("climax", ["full orchestra", "taiko drums", "devastating climax", "maximum intensity"], 0.28, 4),
    ("resolve", ["unresolved sting", "tense cliffhanger", "suspended"], 0.12, 0),
)


def _synth_bgm_spec(mood: str, duration_s: float) -> BgmSpec:
    """The offline synth bed: a mood-keyed lavfi drone, its shaping chain, then a
    comma-free rising-intensity ramp so even the fallback builds toward the end."""
    template, chain = MOODS.get(mood, _FALLBACK)
    dur = max(0.1, duration_s)
    # A gentle linear swell from SCORE_V_HOOK to full over the bed's length. No
    # commas, so it drops straight into the stitcher's filter_complex.
    ramp = f"volume='{SCORE_V_HOOK:g}+{1.0 - SCORE_V_HOOK:g}*t/{dur:.3f}':eval=frame"
    return BgmSpec(
        input_expr=template.format(d=f"{dur:.3f}"),
        filter_chain=f"{chain},{ramp}",
        mood=mood,
    )


def _music_prompt(mood: str, genre: str | None) -> str:
    """Build the prompt-only instrumental prompt from the mood key (+ genre)."""
    base = MOOD_PROMPTS.get(mood, _MOOD_PROMPT_FALLBACK)
    genre_part = f"{genre.strip()} " if genre and genre.strip() else ""
    return f"{genre_part}{base}, instrumental"


def _global_styles(mood: str, genre: str | None) -> list[str]:
    """Global style tokens for a composition plan: genre + mood + base cinematic."""
    styles: list[str] = []
    if genre and genre.strip():
        styles.append(genre.strip())
    styles += MOOD_STYLES.get(mood, _STYLE_FALLBACK)
    styles += _BASE_STYLES
    return styles


def composition_plan(mood: str, genre: str | None, duration_s: float) -> dict:
    """Build an ElevenLabs ``composition_plan`` whose sections trace the drama arc.

    Sections are chosen from :data:`_ARC` (climax/build always survive; the rest
    drop out as the episode shortens so every section stays ≥ ``MIN_SECTION_MS``),
    kept in chronological order, and given a share of the duration by weight
    (clamped to the schema's 3-120 s per-section bounds). Lyrics are empty and
    vocals are negated so the result is instrumental."""
    d_ms = max(MIN_SECTION_MS, int(duration_s * 1000))
    max_sections = max(1, min(len(_ARC), d_ms // MIN_SECTION_MS))
    # Keep the highest-priority phases, then restore chronological order.
    survivors = sorted(
        sorted(range(len(_ARC)), key=lambda i: _ARC[i][3], reverse=True)[:max_sections]
    )
    chosen = [_ARC[i] for i in survivors]
    weight_sum = sum(w for _, _, w, _ in chosen) or 1.0
    sections = []
    for name, styles, weight, _ in chosen:
        dur_ms = int(round(d_ms * weight / weight_sum))
        dur_ms = max(MIN_SECTION_MS, min(MAX_SECTION_MS, dur_ms))
        sections.append(
            {
                "section_name": name,
                "lines": [],  # instrumental — no lyrics
                "positive_local_styles": list(styles),
                "negative_local_styles": list(_NEG_STYLES),
                "duration_ms": dur_ms,
            }
        )
    return {
        "positive_global_styles": _global_styles(mood, genre),
        "negative_global_styles": list(_NEG_STYLES),
        "sections": sections,
    }


def _dynamic_af(duration_s: float) -> str:
    """The build-to-climax volume envelope as a single ``-af`` filter string.

    A piecewise-linear curve over ``duration_s``: hold under the hook, dip to the
    emotional low, swell to a full-level climax, settle into the cliffhanger.
    Returned as a standalone ``-af`` argument (its ``if()`` commas are safe in an
    argv element — this never goes inside a ``filter_complex``)."""
    d = max(0.1, duration_s)
    h, low, c = SCORE_T_HOOK * d, SCORE_T_LOW * d, SCORE_T_CLIMAX * d
    seg_hl = max(0.001, low - h)
    seg_lc = max(0.001, c - low)
    seg_ce = max(0.001, d - c)
    expr = (
        f"if(lt(t,{h:.3f}),{SCORE_V_HOOK:g},"
        f"if(lt(t,{low:.3f}),"
        f"{SCORE_V_HOOK:g}+({SCORE_V_LOW:g}-{SCORE_V_HOOK:g})*(t-{h:.3f})/{seg_hl:.3f},"
        f"if(lt(t,{c:.3f}),"
        f"{SCORE_V_LOW:g}+({SCORE_V_CLIMAX:g}-{SCORE_V_LOW:g})*(t-{low:.3f})/{seg_lc:.3f},"
        f"{SCORE_V_CLIMAX:g}+({SCORE_V_END:g}-{SCORE_V_CLIMAX:g})*(t-{c:.3f})/{seg_ce:.3f})))"
    )
    return f"volume='{expr}':eval=frame"


def duck_filter(main_label: str, key_label: str, out_label: str) -> str:
    """A ``sidechaincompress`` node: duck ``main_label`` (the score) whenever
    ``key_label`` (the voice) is loud, writing ``out_label``. The score drops
    ~8-10 dB under a line and rises back in the gaps (RMS detection, averaged
    across channels so a mono-ish voice still ducks both bed channels)."""
    return (
        f"{main_label}{key_label}sidechaincompress="
        f"threshold={DUCK_THRESHOLD:g}:ratio={DUCK_RATIO:g}:"
        f"attack={DUCK_ATTACK_MS:g}:release={DUCK_RELEASE_MS:g}:"
        f"makeup={DUCK_MAKEUP:g}:detection=rms:link=average{out_label}"
    )


def _lavfi_escape(path: str) -> str:
    """Escape a filesystem path for use as an ``amovie=`` filter argument.
    Temp paths are alphanumeric + ``/-_.`` (no colons), so this is defensive."""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _fit_to_duration(src: Path, duration_s: float, *, shape: bool) -> Path | None:
    """Loop/trim ``src`` to exactly ``duration_s`` into a new temp wav, cleaned up
    at process exit. ``shape`` bakes the build-to-climax envelope in (for flat
    beds); a natively-dynamic composition-plan bed passes ``shape=False`` and is
    only loop/trimmed. Returns the wav path, or ``None`` on ffmpeg failure."""
    fd, out_name = tempfile.mkstemp(prefix="dramapy-music-", suffix=".wav")
    os.close(fd)
    out_path = Path(out_name)
    atexit.register(lambda p=out_path: p.unlink(missing_ok=True))
    af = _dynamic_af(duration_s) if shape else "anull"
    try:
        media.run_ffmpeg(
            [
                "-y",
                "-stream_loop", "-1",  # loop a short source; a no-op for long ones
                "-i", str(src),
                "-t", f"{max(0.1, duration_s):.3f}",  # trim to the episode length
                "-af", af,
                "-c:a", "pcm_s16le",
                "-ar", "48000",
                "-ac", "2",
                str(out_path),
            ],
            timeout=max(30.0, duration_s * 2),
        )
    except (RuntimeError, TimeoutError):
        out_path.unlink(missing_ok=True)
        return None
    if not out_path.is_file() or out_path.stat().st_size == 0:
        out_path.unlink(missing_ok=True)
        return None
    return out_path


def _music_models() -> list[str]:
    """Ordered model ids to try: the selected primary first, the other as fallback."""
    if os.environ.get(MUSIC_MODEL_ENV, "").strip().lower() == "lyria":
        return [LYRIA_MODEL, ELEVEN_MUSIC_MODEL]
    return [ELEVEN_MUSIC_MODEL, LYRIA_MODEL]  # default: ElevenLabs primary


def _payload_for(model: str, mood: str, duration_s: float, genre: str | None) -> tuple[dict, bool]:
    """The fal payload for ``model`` plus whether the bed needs the baked envelope
    (``shape``). ElevenLabs with a plan is natively dynamic (``shape=False``);
    ElevenLabs prompt-only and Lyria are flat (``shape=True``)."""
    if model == ELEVEN_MUSIC_MODEL:
        if duration_s >= COMPOSITION_MIN_S:
            return (
                {
                    "composition_plan": composition_plan(mood, genre, duration_s),
                    "output_format": "mp3_44100_128",
                },
                False,
            )
        length_ms = max(MIN_SECTION_MS, min(600000, int(duration_s * 1000)))
        return (
            {
                "prompt": _music_prompt(mood, genre),
                "music_length_ms": length_ms,
                "force_instrumental": True,  # allowed without a composition_plan
                "output_format": "mp3_44100_128",
            },
            True,
        )
    # Lyria 2 — prompt-only text-to-music.
    return (
        {"prompt": _music_prompt(mood, genre), "negative_prompt": MUSIC_NEGATIVE_PROMPT},
        True,
    )


def _generate_one(
    client: FalClient, model: str, mood: str, duration_s: float, genre: str | None
) -> BgmSpec | None:
    """Generate a bed with one ``model``, fit it, and return a lavfi ``amovie=``
    :class:`BgmSpec`. ``None`` on any failure so the caller tries the next model."""
    src_path: Path | None = None
    try:
        payload, shape = _payload_for(model, mood, duration_s, genre)
        result = client.run(model, payload, budget_s=MUSIC_BUDGET_S, label=f"music {mood}")
        url = first_url(result, "audio")
        if not url:
            return None
        fd, dl_name = tempfile.mkstemp(prefix="dramapy-music-src-", suffix=".mp3")
        os.close(fd)
        src_path = Path(dl_name)
        client.download(url, src_path)
        fitted = _fit_to_duration(src_path, duration_s, shape=shape)
        if fitted is None:
            return None
        return BgmSpec(
            input_expr=f"amovie={_lavfi_escape(str(fitted))}",
            filter_chain="",  # the file is the finished bed; only volume+duck are added
            mood=mood,
        )
    except Exception:
        return None
    finally:
        if src_path is not None:
            src_path.unlink(missing_ok=True)


def _generate_music_bed(mood: str, duration_s: float, genre: str | None) -> BgmSpec | None:
    """Generate a cinematic bed: the selected primary model, then the other as a
    fallback. Returns ``None`` only when every model fails — **never raises**."""
    try:
        client = FalClient()
    except Exception:
        return None
    for model in _music_models():
        bed = _generate_one(client, model, mood, duration_s, genre)
        if bed is not None:
            return bed
    return None


def bgm_spec(
    mood: str | None, duration_s: float, *, genre: str | None = None
) -> BgmSpec | None:
    """Resolve a mood key to a :class:`BgmSpec` of ``duration_s`` seconds.

    ``None`` mood → no bed. ``VIDEO_MUSIC=off`` → no bed. Otherwise a generated
    cinematic score (ElevenLabs Music primary, Lyria 2 fallback; dynamic arc
    native or baked) when ``FAL_KEY`` is set and ``VIDEO_MUSIC`` is not ``synth``;
    on any failure (or ``synth`` / no key) the offline synth drone, which falls
    back to a neutral drone for unknown moods. ``genre`` enriches the generated
    music when supplied (the stitcher passes ``series.genre``)."""
    if mood is None:
        return None
    music_env = os.environ.get(MUSIC_ENV, "").strip().lower()
    if music_env == "off":
        return None
    if music_env != "synth" and os.environ.get("FAL_KEY", "").strip():
        generated = _generate_music_bed(mood, duration_s, genre)
        if generated is not None:
            return generated
    return _synth_bgm_spec(mood, duration_s)
