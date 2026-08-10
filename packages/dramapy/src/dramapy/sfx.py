"""Layered sound-effects track (contract §1, assembly stage).

Every provider renders **silent** video, so — like the voice track — the SFX are
generated once here and laid onto a single episode-length track that the stitcher
mixes over the footage (punchy, between/over the dialogue). Multiple cues may
overlap: the track is an ``amix`` of every placed effect at its shot's start.

Two backends, one placement path (``VIDEO_SFX_MODEL``):

* **Discrete cues** (``fal-ai/mmaudio-v2/text-to-audio``) — the default. For each
  shot, cues are derived from an explicit per-shot ``sfx`` list when present, else
  **inferred from the prompt keywords + shot kind** (dragon → roar; fire →
  whoosh/crackle; ice → crack; wings → wingbeat; impact → boom; storm → thunder;
  …). 1-2 cues per shot; identical cues are generated once and reused (a small
  cache) to cap cost. (The originally specced ``fal-ai/elevenlabs/sound-effects``
  is broken upstream — fal pins the deprecated ``eleven_text_to_sound_v0`` model
  and ElevenLabs 422s it, with no ``model_id`` override in the fal schema — so the
  same mmaudio-v2 family's text-to-audio endpoint generates discrete SFX instead.)
* **mmaudio-v2 video-to-audio** (``fal-ai/mmaudio-v2``, ``VIDEO_SFX_MODEL=mmaudio``)
  — **foley matched to the on-screen motion**. For action/establish shots that
  carry a rendered clip, the clip is sent (as a data URI — the shared client has
  no upload path, so this suits short clips) and mmaudio returns the clip *with*
  generated audio; that audio is extracted and placed for the whole shot. Explicit
  cues and shots without a clip still go through the discrete text-to-audio path.

Env: ``VIDEO_SFX`` — default **on** when ``FAL_KEY`` is set, ``off`` (or
0/false/no) disables. With no key there is no offline SFX synth: the track is
``None`` (the CI/eval path). ``VIDEO_SFX_MODEL`` — ``elevenlabs`` (default,
discrete text-to-audio cues) / ``mmaudio`` (video-matched foley). **Never
raises** — any failure drops that cue or the whole track (``None``); the episode
simply carries no SFX. The caller owns the returned file.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dramapy import media
from dramapy.fal_client import FalClient, first_url

SFX_ENV = "VIDEO_SFX"  # "off"/0/false/no disables; default on when FAL_KEY
SFX_MODEL_ENV = "VIDEO_SFX_MODEL"  # "elevenlabs" (default cues) / "mmaudio"

SFX_MODEL = "fal-ai/mmaudio-v2/text-to-audio"  # discrete cued stingers (text→audio)
SFX_MMAUDIO_MODEL = "fal-ai/mmaudio-v2"  # video-to-audio foley (motion-matched)
SFX_NEGATIVE_PROMPT = "music, speech, voice, dialogue, singing"  # keep SFX diegetic
SFX_DEFAULT_DURATION_S = 4.0  # when a cue has no fixed duration
SFX_BUDGET_S = 120.0  # per-cue fal budget
MAX_SFX_PER_SHOT = 2  # cost cap: at most this many discrete cues per shot
MMAUDIO_MAX_S = 30.0  # mmaudio duration ceiling (schema: 1-30 s)
_MMAUDIO_KINDS = {"action", "establish"}  # kinds worth motion-matched foley

_AFORMAT = "aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo"


@dataclass(frozen=True)
class SfxCue:
    """One discrete sound effect: a stable ``id`` (dedupe key), the ElevenLabs
    ``prompt`` text, and an optional fixed duration (``None`` = model auto)."""

    id: str
    prompt: str
    duration_s: float | None = None


# Ordered inference rules: (trigger keywords, cue). Keywords match whole prompt
# tokens (plurals handled by singularizing the token). First matches win; capped
# at MAX_SFX_PER_SHOT per shot. Earlier, more specific rules take precedence.
_RULES: tuple[tuple[tuple[str, ...], SfxCue], ...] = (
    (("dragon",), SfxCue("dragon_roar", "a monstrous dragon roar, deep and guttural, cinematic", 3.0)),
    (("wing", "wingbeat", "flap", "soar"), SfxCue("wingbeat", "powerful wing beats, heavy flaps of air", 2.5)),
    (("fire", "flame", "burning", "blaze", "inferno", "ablaze", "ember"),
     SfxCue("fire_whoosh", "a roaring whoosh of fire with crackling flames", 3.0)),
    (("ice", "frost", "freeze", "frozen", "icy"),
     SfxCue("ice_crack", "the sharp crack of freezing ice, brittle and cold", 2.0)),
    (("thunder", "lightning"), SfxCue("thunder", "a deep rumbling thunder clap", 3.0)),
    (("storm", "gale"), SfxCue("storm", "a howling storm with wind and distant thunder", 4.0)),
    (("explosion", "explode", "detonate", "blast"), SfxCue("explosion", "a massive explosion blast", 2.5)),
    (("collision", "collide", "impact", "crash", "smash", "slam", "boom"),
     SfxCue("impact_boom", "a heavy cinematic impact boom", 1.5)),
    (("sword", "blade", "clash", "steel"), SfxCue("sword_clash", "metallic sword clash, ringing steel", 1.5)),
    (("rain",), SfxCue("rain", "steady rainfall ambience", None)),
    (("wind", "gust"), SfxCue("wind", "a gust of howling wind", 3.0)),
    (("wave", "ocean", "splash", "water"), SfxCue("water", "a splash of water", 1.5)),
    (("door",), SfxCue("door", "a heavy door creaking open and slamming shut", 1.5)),
    (("footstep", "running", "sprint", "chase"), SfxCue("footsteps", "urgent footsteps running", 2.0)),
    (("glass", "shatter"), SfxCue("glass", "glass shattering", 1.0)),
    (("gun", "gunshot", "gunfire", "pistol", "rifle"), SfxCue("gunshot", "a sharp gunshot", 1.0)),
    (("engine", "car", "motor"), SfxCue("engine", "a revving car engine", 2.0)),
    (("clock", "ticking"), SfxCue("clock", "a ticking clock", 2.0)),
    (("heartbeat", "pulse"), SfxCue("heartbeat", "a slow heavy heartbeat", 3.0)),
)


# -- Env / model selection ----------------------------------------------------


def sfx_enabled() -> bool:
    """SFX are on unless ``VIDEO_SFX`` is a falsy switch — and only when a
    ``FAL_KEY`` is present (no offline SFX synth; no key → no SFX track)."""
    if os.environ.get(SFX_ENV, "").strip().lower() in {"off", "0", "false", "no"}:
        return False
    return bool(os.environ.get("FAL_KEY", "").strip())


def _sfx_model() -> str:
    """The selected SFX backend: ``"mmaudio"`` or ``"elevenlabs"`` (default)."""
    return "mmaudio" if os.environ.get(SFX_MODEL_ENV, "").strip().lower() == "mmaudio" else "elevenlabs"


# -- Cue inference ------------------------------------------------------------


def _tokens(prompt: str) -> set[str]:
    """Lowercase word tokens plus their singular forms (drop a trailing ``s``)."""
    words = re.findall(r"[a-z]+", (prompt or "").lower())
    tokens = set(words)
    tokens.update(w[:-1] for w in words if len(w) > 3 and w.endswith("s"))
    return tokens


def infer_cues(prompt: str, kind: str = "") -> list[SfxCue]:
    """Infer up to :data:`MAX_SFX_PER_SHOT` discrete cues from a shot's prompt
    keywords (and kind). Empty when nothing matches — no wasted generation."""
    tokens = _tokens(prompt)
    cues: list[SfxCue] = []
    seen: set[str] = set()
    for keywords, cue in _RULES:
        if any(kw in tokens for kw in keywords) and cue.id not in seen:
            cues.append(cue)
            seen.add(cue.id)
            if len(cues) >= MAX_SFX_PER_SHOT:
                break
    return cues


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40] or "sfx"


def _coerce_cue(item: object) -> SfxCue | None:
    """Turn one explicit ``sfx`` entry into a :class:`SfxCue`.

    A string reuses a known cue's polished prompt when it names one (e.g.
    ``"dragon roar"`` → the ``dragon_roar`` cue), else it is used verbatim. A dict
    may carry ``id``/``cue``, ``prompt``/``text``, and ``duration_s``/``duration``."""
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None
        known = infer_cues(text)
        if known:
            return known[0]
        return SfxCue(id=_slug(text), prompt=text)
    if isinstance(item, dict):
        text = str(item.get("prompt") or item.get("text") or item.get("id") or item.get("cue") or "").strip()
        if not text:
            return None
        cue_id = str(item.get("id") or item.get("cue") or _slug(text))
        dur = item.get("duration_s", item.get("duration"))
        duration = float(dur) if isinstance(dur, (int, float)) and not isinstance(dur, bool) else None
        return SfxCue(id=_slug(cue_id), prompt=text, duration_s=duration)
    return None


def _explicit_cues(items: object) -> list[SfxCue]:
    """Coerce an explicit ``sfx`` list into cues (deduped, capped)."""
    if not isinstance(items, (list, tuple)):
        return []
    cues: list[SfxCue] = []
    seen: set[str] = set()
    for item in items:
        cue = _coerce_cue(item)
        if cue is not None and cue.id not in seen:
            cues.append(cue)
            seen.add(cue.id)
            if len(cues) >= MAX_SFX_PER_SHOT:
                break
    return cues


def cues_for_entry(entry: dict) -> list[SfxCue]:
    """Discrete cues for one shot entry: explicit ``sfx`` list if present, else
    inferred from the prompt + kind."""
    explicit = entry.get("sfx")
    if explicit:
        return _explicit_cues(explicit)
    return infer_cues(str(entry.get("prompt") or ""), str(entry.get("kind") or ""))


def _foley_prompt(entry: dict) -> str:
    """A motion-matched foley prompt for mmaudio: a diegetic base plus any element
    words the inference caught (so a dragon/fire shot names them)."""
    elements = ", ".join(cue.id.replace("_", " ") for cue in infer_cues(str(entry.get("prompt") or "")))
    base = "cinematic sound effects and foley, diegetic, matched to the on-screen action"
    return f"{base}: {elements}" if elements else base


# -- Generation ---------------------------------------------------------------


@dataclass(frozen=True)
class _Job:
    start_s: float
    kind: str  # "cue" (text→audio) | "mmaudio" (video→audio)
    prompt: str
    dedup: str | None  # cache key for "cue"; None for unique "mmaudio"
    duration_s: float | None
    clip: Path | None


def _plan_jobs(entries: list[dict], model: str) -> list[_Job]:
    """Turn shot entries into audio jobs. In ``mmaudio`` mode, action/establish
    shots with a clip and no explicit cues get one motion-matched foley job; every
    other case yields discrete text-to-audio cue jobs."""
    jobs: list[_Job] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        start = max(0.0, float(entry.get("start_s") or 0.0))
        dur = max(0.5, float(entry.get("duration_s") or 0.0))
        kind = str(entry.get("kind") or "")
        clip = entry.get("clip_path")
        explicit = entry.get("sfx")
        if model == "mmaudio" and clip and kind in _MMAUDIO_KINDS and not explicit:
            clip_path = Path(str(clip))
            if clip_path.is_file():
                jobs.append(
                    _Job(
                        start_s=start,
                        kind="mmaudio",
                        prompt=_foley_prompt(entry),
                        dedup=None,
                        duration_s=min(MMAUDIO_MAX_S, dur),
                        clip=clip_path,
                    )
                )
                continue  # mmaudio covers this shot; skip discrete cues
        for cue in cues_for_entry(entry):
            jobs.append(
                _Job(
                    start_s=start,
                    kind="cue",
                    prompt=cue.prompt,
                    dedup=cue.id,
                    duration_s=cue.duration_s,
                    clip=None,
                )
            )
    return jobs


def _gen_cue(client: FalClient, job: _Job, out: Path) -> Path | None:
    """Generate one discrete cue via the text-to-audio SFX model. ``None`` on
    failure (the caller drops this cue)."""
    duration = max(1.0, min(MMAUDIO_MAX_S, job.duration_s or SFX_DEFAULT_DURATION_S))
    payload = {
        "prompt": job.prompt,
        "duration": duration,
        "negative_prompt": SFX_NEGATIVE_PROMPT,
    }
    try:
        result = client.run(SFX_MODEL, payload, budget_s=SFX_BUDGET_S, label=f"sfx {job.dedup}")
        url = first_url(result, "audio")
        if not url:
            return None
        client.download(url, out)
    except Exception:
        return None
    return out if out.is_file() and out.stat().st_size > 0 else None


def _gen_mmaudio(client: FalClient, job: _Job, work: Path, index: int, sample_rate: int) -> Path | None:
    """Generate motion-matched foley for a shot clip via mmaudio-v2, then extract
    its audio to a wav. ``None`` on failure (the caller drops this shot's SFX)."""
    if job.clip is None:
        return None
    try:
        video_url = FalClient.data_uri(job.clip)  # no upload path in the shared client
    except Exception:
        return None
    try:
        result = client.run(
            SFX_MMAUDIO_MODEL,
            {
                "video_url": video_url,
                "prompt": job.prompt,
                "duration": max(1.0, min(MMAUDIO_MAX_S, job.duration_s or 8.0)),
                "negative_prompt": "music, speech, voice, dialogue",
            },
            budget_s=SFX_BUDGET_S,
            label="sfx mmaudio",
        )
        url = first_url(result, "video", "audio")  # mmaudio returns the video+audio
        if not url:
            return None
        vid = work / f"mm_{index:03d}.mp4"
        client.download(url, vid)
    except Exception:
        return None
    wav = work / f"mm_{index:03d}.wav"
    try:
        media.run_ffmpeg(
            ["-y", "-i", str(vid), "-vn", "-c:a", "pcm_s16le", "-ar", str(sample_rate), "-ac", "2", str(wav)]
        )
    except (RuntimeError, TimeoutError):
        return None
    return wav if wav.is_file() and wav.stat().st_size > 0 else None


def build_sfx_track(entries: list[dict], *, sample_rate: int = 48000) -> Path | None:
    """Build one episode-length SFX wav from shot ``entries``.

    ``entries`` are dicts in episode-timeline order::

        {"shot_id": str, "kind": str, "prompt": str, "start_s": float,
         "duration_s": float, "sfx": list | None, "clip_path": str | None}

    Each shot yields discrete ElevenLabs cues (explicit ``sfx`` else inferred) or,
    in ``mmaudio`` mode, one motion-matched foley bed. Identical ElevenLabs cues
    are generated once and reused. Every produced clip is laid onto one track at
    its shot's ``start_s`` and mixed (overlaps allowed).

    Returns the wav path, or ``None`` when SFX are off (``VIDEO_SFX``/no key),
    nothing is cued, or nothing generated. **Never raises.** The caller owns the file.
    """
    if not sfx_enabled():
        return None
    model = _sfx_model()
    jobs = _plan_jobs(entries, model)
    if not jobs:
        return None

    try:
        client = FalClient()
    except Exception:
        return None

    aformat = _AFORMAT.format(sr=sample_rate)
    fd, out_name = tempfile.mkstemp(prefix="dramapy-sfx-", suffix=".wav")
    os.close(fd)
    out_path = Path(out_name)
    try:
        with tempfile.TemporaryDirectory(prefix="dramapy-sfx-") as workdir:
            work = Path(workdir)
            cue_cache: dict[str, Path | None] = {}  # dedup key → generated file
            placed: list[tuple[Path, float]] = []  # (audio file, start_s)
            for index, job in enumerate(jobs):
                if job.kind == "mmaudio":
                    audio = _gen_mmaudio(client, job, work, index, sample_rate)
                elif job.dedup is not None and job.dedup in cue_cache:
                    audio = cue_cache[job.dedup]  # reuse an identical cue
                else:
                    audio = _gen_cue(client, job, work / f"sfx_{index:03d}.mp3")
                    if job.dedup is not None:
                        cue_cache[job.dedup] = audio
                if audio is not None:
                    placed.append((audio, job.start_s))

            if not placed:
                out_path.unlink(missing_ok=True)
                return None

            args: list[str] = ["-y"]
            for audio, _ in placed:
                args += ["-i", str(audio)]

            graph: list[str] = []
            labels: list[str] = []
            for index, (_, start) in enumerate(placed):
                delay_ms = int(round(start * 1000))
                graph.append(
                    f"[{index}:a]aresample={sample_rate},{aformat},"
                    f"adelay={delay_ms}|{delay_ms}[e{index}]"
                )
                labels.append(f"[e{index}]")

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
                media.run_ffmpeg(args, timeout=max(30.0, len(placed) * 20.0))
            except (RuntimeError, TimeoutError):
                out_path.unlink(missing_ok=True)
                return None

        if not out_path.is_file() or out_path.stat().st_size == 0:
            out_path.unlink(missing_ok=True)
            return None
        return out_path
    except Exception:
        out_path.unlink(missing_ok=True)
        return None
