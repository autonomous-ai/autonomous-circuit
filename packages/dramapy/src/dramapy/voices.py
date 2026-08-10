"""Provider-independent spoken dialogue (contract §1, assembly stage).

Every provider renders **silent** video — the hosted models (fal/Wan/MiniMax)
return footage with no usable speech, and the mock is a silent animatic. So
voices are generated **once, here, at assembly**: this module speaks each
dialogue line, lays every clip onto a single episode-length track at its
stitched-timeline offset, and hands one wav to
:func:`dramapy.stitch.stitch_episode`, which mixes it over the video (0 dB,
primary) under the BGM bed (−14 dB). One code path gives **every** provider's
episodes voices.

Two backends, one placement path:

* **ElevenLabs** (``fal-ai/elevenlabs/tts/eleven-v3`` via the shared
  :class:`~dramapy.fal_client.FalClient`) — the expressive default whenever a
  ``FAL_KEY`` is present (and not forced off). Each ``Character.voice`` tag is
  pinned to one ElevenLabs preset voice so a character sounds identical episode
  after episode, and a per-line emotion is passed as an inline ``[angry]`` /
  ``[whispers]`` tag with a matching stability.
* **macOS ``say``** — the offline fallback (and the CI/eval path, which has no
  ``FAL_KEY``). Provider-independent, deterministic, zero network.

Backend selection (:func:`_voice_backend`): ``VIDEO_VOICES=off`` kills the
whole layer; otherwise ``elevenlabs`` when ``VIDEO_VOICE_BACKEND=elevenlabs``
or (``FAL_KEY`` set and ``VIDEO_VOICE_BACKEND`` is not ``say``); else ``say``
when available; else ``None``.

Public functions:

* :func:`voice_for_tag` — the deterministic ``Character.voice`` tag →
  (``say`` voice, rate wpm) mapping, stable across episodes.
* :func:`voice_for_tag_elevenlabs` — the deterministic tag → ElevenLabs preset
  voice **name** mapping (f_* → female pool, m_* → male pool), stable across
  episodes.
* :func:`build_voice_track` — synthesize + place dialogue into one wav.

Degradation is never an error: no backend at all (non-darwin CI, no ``FAL_KEY``),
the ``VIDEO_VOICES=off`` kill switch, or a synthesis failure return ``None`` (or
drop/downgrade the failed line — a failed ElevenLabs line falls back to ``say``,
never crashes). The burned subtitles always carry the words. The output is
deterministic for fixed inputs (the tag→voice mapping is a stable hash; ``say``
is deterministic; ffmpeg placement is pure — ElevenLabs itself is not
bit-deterministic, but the *casting* is).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from dramapy import media
from dramapy.fal_client import FalClient, first_url

# -- Voice mapping (moved here from providers/mock.py; provider-independent). --
VOICE_MAP_VERSION = 1  # bump when the table below changes
FEMALE_VOICE = "Samantha"  # en_US
MALE_VOICE = "Daniel"  # en_GB
ROTATION_VOICES = ("Karen", "Moira", "Tessa")  # en_AU / en_IE / en_ZA
TTS_RATE_RANGE_WPM = (170, 200)

# -- ElevenLabs backend (fal-ai/elevenlabs/tts/eleven-v3). -------------------
ELEVEN_VOICE_MAP_VERSION = 2  # bump when either preset pool below changes
ELEVEN_MODEL = "fal-ai/elevenlabs/tts/eleven-v3"  # expressive, inline tags
# One preset name per tag, pinned by tag hash so a character is consistent
# across episodes. f_* → female pool, m_* → male pool, neutral → both. Names are
# the eleven-v3 default library (the model's own `voice` examples) — the legacy
# v2 names (Bella/Domi/Elli/Adam/Antoni/…) 422 with "Voice not found".
ELEVEN_FEMALE_VOICES = (
    "Rachel", "Aria", "Sarah", "Laura", "Charlotte", "Alice", "Matilda",
    "Jessica", "Lily",
)
ELEVEN_MALE_VOICES = (
    "Roger", "Charlie", "George", "Callum", "Liam", "Will", "Eric", "Brian",
    "Bill",
)
ELEVEN_SIMILARITY_BOOST = 0.75  # steady timbre for a pinned character voice
ELEVEN_BUDGET_S = 120.0  # per-line fal budget; a slow line falls back to `say`
BACKEND_ENV = "VIDEO_VOICE_BACKEND"  # "elevenlabs" / "say" force; else auto

# Common emotion word → eleven-v3 inline tag (goes IN the text: "[angry] …").
# Unmapped emotions get no tag (the line is spoken plainly).
EMOTION_TO_ELEVEN_TAG: dict[str, str] = {
    "angry": "angry", "anger": "angry", "furious": "angry", "rage": "angry",
    "sad": "sad", "sorrow": "sad", "grief": "sad", "crying": "sad",
    "tearful": "sad", "heartbroken": "sad",
    "happy": "happy", "joy": "happy", "joyful": "happy", "cheerful": "happy",
    "excited": "happy", "elated": "happy",
    "nervous": "nervous", "anxious": "nervous", "afraid": "nervous",
    "scared": "nervous", "fear": "nervous", "fearful": "nervous",
    "dread": "nervous", "worried": "nervous", "uneasy": "nervous",
    "whisper": "whispers", "whispers": "whispers", "whispering": "whispers",
    "shout": "shouting", "shouting": "shouting", "yell": "shouting",
    "yelling": "shouting", "screaming": "shouting", "scream": "shouting",
    "soft": "softly", "softly": "softly", "gentle": "softly", "tender": "softly",
}
# Expressive emotions want a low stability (more range); calm ones want high.
_ELEVEN_EXPRESSIVE_TAGS = {"angry", "happy", "shouting", "nervous", "sad"}
_ELEVEN_CALM_TAGS = {"softly", "whispers"}
_ELEVEN_STABILITY_EXPRESSIVE = 0.3
_ELEVEN_STABILITY_CALM = 0.6
_ELEVEN_STABILITY_DEFAULT = 0.5  # no emotion → neutral delivery

# -- Assembly-stage placement. -----------------------------------------------
SPEECH_FADE_S = 0.15  # fade-out window when speech outruns its shot
SAY_TIMEOUT_S = 30.0  # per-line `say` budget; a slow line degrades to silence
VOICES_ENV = "VIDEO_VOICES"  # "off" (or 0/false/no) disables the whole layer

# Indirection so tests can force the no-`say` fallback (set to None). Kept as a
# name rather than a hard "say" literal precisely so it is monkeypatchable.
_SAY_BIN = "say"

_AFORMAT = "aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo"


def voice_for_tag(voice_tag: str) -> tuple[str, int]:
    """Deterministic ``say`` voice + rate (wpm) for a ``Character.voice`` tag.

    Female-ish tags (``f_…``/female/woman/girl) → Samantha, male-ish
    (``m_…``/male/man/boy) → Daniel, anything else rotates through
    Karen/Moira/Tessa by tag hash. Rate varies 170-200 wpm by tag hash for
    distinctness. Same tag → same voice + rate, episode after episode."""
    tag = (voice_tag or "").strip().lower()
    seed = int.from_bytes(
        hashlib.sha256(f"voice:{tag}".encode("utf-8")).digest()[:8], "big"
    )
    low, high = TTS_RATE_RANGE_WPM
    rate = low + seed % (high - low + 1)
    if any(marker in tag for marker in ("f_", "female", "woman", "girl")):
        return FEMALE_VOICE, rate  # check female first: "female" ⊃ "male"
    if any(marker in tag for marker in ("m_", "male", "man", "boy")):
        return MALE_VOICE, rate
    return ROTATION_VOICES[seed % len(ROTATION_VOICES)], rate


def voice_for_tag_elevenlabs(voice_tag: str) -> str:
    """Deterministic ElevenLabs preset voice **name** for a ``Character.voice``
    tag, stable across episodes so a character keeps one voice.

    Female-ish tags (``f_…``/female/woman/girl) pick from
    :data:`ELEVEN_FEMALE_VOICES`, male-ish (``m_…``/male/man/boy) from
    :data:`ELEVEN_MALE_VOICES`, anything else from both pools — the exact name
    chosen by tag hash. Same tag → same name, episode after episode."""
    tag = (voice_tag or "").strip().lower()
    seed = int.from_bytes(
        hashlib.sha256(f"eleven-voice:{tag}".encode("utf-8")).digest()[:8], "big"
    )
    if any(marker in tag for marker in ("f_", "female", "woman", "girl")):
        return ELEVEN_FEMALE_VOICES[seed % len(ELEVEN_FEMALE_VOICES)]  # female ⊃ male
    if any(marker in tag for marker in ("m_", "male", "man", "boy")):
        return ELEVEN_MALE_VOICES[seed % len(ELEVEN_MALE_VOICES)]
    pool = ELEVEN_FEMALE_VOICES + ELEVEN_MALE_VOICES
    return pool[seed % len(pool)]


def voices_enabled() -> bool:
    """The layer is on unless ``VIDEO_VOICES`` is a falsy switch (off/0/…) or
    ``say`` is unavailable."""
    if os.environ.get(VOICES_ENV, "").strip().lower() in {"off", "0", "false", "no"}:
        return False
    return _say_bin() is not None


def _say_bin() -> str | None:
    """The resolved ``say`` executable, or ``None`` when unavailable."""
    if not _SAY_BIN:
        return None
    return shutil.which(_SAY_BIN)


def _voices_off() -> bool:
    """The ``VIDEO_VOICES`` kill switch (off/0/false/no)."""
    return os.environ.get(VOICES_ENV, "").strip().lower() in {"off", "0", "false", "no"}


def _voice_backend() -> str | None:
    """Which synthesis backend to use: ``"elevenlabs"``, ``"say"``, or ``None``.

    ``VIDEO_VOICES=off`` → ``None`` (whole layer off). Otherwise ElevenLabs when
    ``VIDEO_VOICE_BACKEND=elevenlabs`` **or** a ``FAL_KEY`` is set and the
    backend is not pinned to ``say``; else ``say`` when available; else
    ``None``. (Selecting ``elevenlabs`` here does not guarantee the client
    constructs — a missing key at build time degrades to ``say`` per line.)"""
    if _voices_off():
        return None
    backend = os.environ.get(BACKEND_ENV, "").strip().lower()
    if backend == "elevenlabs":
        return "elevenlabs"
    if os.environ.get("FAL_KEY", "").strip() and backend != "say":
        return "elevenlabs"
    if _say_bin() is not None:
        return "say"
    return None


def _synthesize(line: str, voice: str, rate: int, out_path: Path) -> bool:
    """Speak ``line`` to an AIFF at ``out_path`` via ``say``. The text travels
    through a file (``-f``) so a line starting with ``-`` can never parse as a
    flag. Returns ``False`` on any failure (the caller drops that line)."""
    text_path = out_path.with_suffix(".txt")
    try:
        text_path.write_text(line + "\n", encoding="utf-8")
        proc = subprocess.run(
            [
                _SAY_BIN,
                "-v",
                voice,
                "-r",
                str(int(rate)),
                "-o",
                str(out_path),
                "-f",
                str(text_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=SAY_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    try:
        return proc.returncode == 0 and out_path.stat().st_size > 0
    except OSError:
        return False


def _eleven_text(line: str, emotion: str) -> str:
    """The eleven-v3 input text: an inline ``[tag]`` prefix when the emotion
    maps to a known tag, else the plain line."""
    tag = EMOTION_TO_ELEVEN_TAG.get((emotion or "").strip().lower())
    return f"[{tag}] {line}" if tag else line


def _eleven_stability(emotion: str) -> float:
    """Stability (0-1) for an emotion: low for expressive, high for calm, a
    neutral middle when there is no (mapped) emotion."""
    tag = EMOTION_TO_ELEVEN_TAG.get((emotion or "").strip().lower())
    if tag in _ELEVEN_EXPRESSIVE_TAGS:
        return _ELEVEN_STABILITY_EXPRESSIVE
    if tag in _ELEVEN_CALM_TAGS:
        return _ELEVEN_STABILITY_CALM
    return _ELEVEN_STABILITY_DEFAULT


def _synthesize_elevenlabs(
    client: FalClient, text: str, voice_name: str, stability: float, out_path: Path
) -> bool:
    """Speak ``text`` with ElevenLabs eleven-v3 (pinned ``voice_name``) to
    ``out_path`` via ``client``. Returns ``False`` on any failure — the caller
    then falls back to ``say`` for this line (a voice failure never crashes)."""
    try:
        result = client.run(
            ELEVEN_MODEL,
            {
                "text": text,
                "voice": voice_name,
                "stability": stability,
                "similarity_boost": ELEVEN_SIMILARITY_BOOST,
            },
            budget_s=ELEVEN_BUDGET_S,
            label=f"voice {voice_name}",
        )
        url = first_url(result, "audio")
        if not url:
            return False
        client.download(url, out_path)
    except Exception:
        return False
    try:
        return out_path.is_file() and out_path.stat().st_size > 0
    except OSError:
        return False


def _synthesize_line(
    entry: dict, index: int, work: Path, backend: str, client: FalClient | None
) -> Path | None:
    """Synthesize one dialogue ``entry`` to an audio file under ``work``, or
    ``None`` when neither backend produced audio.

    ElevenLabs is tried first when selected; a per-line failure falls back to
    ``say`` (so a single flaky line degrades to the offline voice, never to a
    crashed render). ``say`` is skipped when unavailable."""
    line = str(entry.get("line") or "")
    voice_tag = str(entry.get("voice_tag") or "")
    if backend == "elevenlabs" and client is not None:
        name = voice_for_tag_elevenlabs(voice_tag)
        emotion = str(entry.get("emotion") or "")
        text = _eleven_text(line, emotion)
        out = work / f"eleven_{index:03d}.mp3"
        if _synthesize_elevenlabs(client, text, name, _eleven_stability(emotion), out):
            return out
        # per-line failure → fall through to `say` for just this line.
    if _say_bin() is not None:
        voice, rate = voice_for_tag(voice_tag)
        aiff = work / f"say_{index:03d}.aiff"
        if _synthesize(line, voice, rate, aiff):
            return aiff
    return None


def build_voice_track(
    entries: list[dict],
    *,
    sample_rate: int = 48000,
    voiced_out: set[str] | None = None,
) -> Path | None:
    """Build one episode-length spoken-dialogue wav from ``entries``.

    ``entries`` are dicts for the dialogue shots, in episode-timeline order::

        {"shot_id": str, "line": str, "voice_tag": str,
         "start_s": float, "duration_s": float, "emotion": str | None}

    Each line is spoken by the selected backend (:func:`_voice_backend`):
    ElevenLabs eleven-v3 with the tag's pinned preset voice and an inline
    emotion tag when ``FAL_KEY`` is present, else macOS ``say`` with the
    deterministic voice+rate for the tag. Each clip is then fitted to its shot:
    trimmed with a 0.15 s fade-out when the speech outruns the shot,
    silence-padded when it is shorter. Every clip is laid onto one silent base
    at its ``start_s``; the result is a single stereo wav covering the whole
    episode (0 → the last line's end). ``emotion`` is optional — an entry
    without it is spoken plainly.

    Returns the wav path, or ``None`` when the layer is off
    (``VIDEO_VOICES=off``), no backend is available, there is no dialogue, or
    synthesis produced nothing. **Never raises** — any voice failure degrades to
    ``None`` (or, per line, to ``say``) and the subtitles still carry the words.
    When ``voiced_out`` is given, it is filled with the shot ids that actually
    got spoken audio (so the caller's ``silent_dialogue`` check knows what
    landed in the episode).

    The caller owns the returned file and must delete it.
    """
    backend = _voice_backend()
    if backend is None:
        return None
    dialogue = [
        entry
        for entry in entries
        if isinstance(entry, dict) and str(entry.get("line") or "").strip()
    ]
    if not dialogue:
        return None

    # Construct the fal client once for the whole track. A missing key (or any
    # construction failure) degrades the whole track to `say` when available.
    client: FalClient | None = None
    if backend == "elevenlabs":
        try:
            client = FalClient()
        except Exception:
            # No usable key (e.g. VIDEO_VOICE_BACKEND=elevenlabs but no FAL_KEY):
            # degrade the whole track to `say`, or to silence if it is absent.
            if _say_bin() is None:
                return None
            client, backend = None, "say"

    aformat = _AFORMAT.format(sr=sample_rate)
    fd, out_name = tempfile.mkstemp(prefix="dramapy-voice-", suffix=".wav")
    os.close(fd)
    out_path = Path(out_name)
    try:
        with tempfile.TemporaryDirectory(prefix="dramapy-voices-") as workdir:
            work = Path(workdir)
            placed: list[tuple[Path, float, float, str]] = []
            for index, entry in enumerate(dialogue):
                shot_id = str(entry.get("shot_id") or f"d{index}")
                synth = _synthesize_line(entry, index, work, backend, client)
                if synth is not None:
                    placed.append(
                        (
                            synth,
                            max(0.0, float(entry.get("start_s") or 0.0)),
                            max(0.05, float(entry.get("duration_s") or 0.0)),
                            shot_id,
                        )
                    )
            if not placed:
                out_path.unlink(missing_ok=True)
                return None

            total_s = max(start + dur for _, start, dur, _ in placed)

            args: list[str] = ["-y"]
            for aiff, _, _, _ in placed:
                args += ["-i", str(aiff)]

            graph: list[str] = []
            labels: list[str] = []
            for index, (_, start, dur, _) in enumerate(placed):
                delay_ms = int(round(start * 1000))
                fade_start = max(0.0, dur - SPEECH_FADE_S)
                # trim to the shot → fade the tail → pad to the shot → shift to
                # the shot's start. Padding is silent, so fading it is a no-op;
                # the fade only bites when the speech is the thing being cut.
                graph.append(
                    f"[{index}:a]aresample={sample_rate},{aformat},"
                    f"atrim=0:{dur:.3f},"
                    f"afade=t=out:st={fade_start:.3f}:d={SPEECH_FADE_S:g},"
                    f"apad=whole_dur={dur:.3f},"
                    f"adelay={delay_ms}|{delay_ms}[s{index}]"
                )
                labels.append(f"[s{index}]")

            if len(labels) == 1:
                out_label = labels[0]
            else:
                graph.append(
                    "".join(labels)
                    + f"amix=inputs={len(labels)}:duration=longest"
                    ":dropout_transition=0:normalize=0[mix]"
                )
                out_label = "[mix]"

            args += [
                "-filter_complex",
                ";".join(graph),
                "-map",
                out_label,
                "-c:a",
                "pcm_s16le",
                "-ar",
                str(sample_rate),
                "-ac",
                "2",
                str(out_path),
            ]
            try:
                media.run_ffmpeg(args, timeout=max(30.0, total_s * 4))
            except (RuntimeError, TimeoutError):
                out_path.unlink(missing_ok=True)
                return None

        if not out_path.is_file() or out_path.stat().st_size == 0:
            out_path.unlink(missing_ok=True)
            return None
        if voiced_out is not None:
            voiced_out.update(shot_id for _, _, _, shot_id in placed)
        return out_path
    except Exception:
        # The whole layer is best-effort: any unexpected failure degrades to
        # silence rather than breaking the episode.
        out_path.unlink(missing_ok=True)
        return None
