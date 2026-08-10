"""Stitch rendered shot clips into the final episode (contract §1).

One ffmpeg pass: every clip is re-encoded to a uniform H.264/AAC yuv420p
stream at the series resolution/fps (hard cuts via the ``concat`` filter,
``+faststart`` for scrub-while-download). **Clip audio is dropped** — every
provider (fal/Wan/MiniMax and the mock animatic) renders silent video, so
the episode's audio is assembled here into a **cinematic three-layer mix**
over a silent base pinned to the episode length (``amix normalize=0`` so the
levels are honest):

* **Voice** (``voice_track`` from :mod:`dramapy.voices`) — 0 dB, primary and
  always intelligible.
* **Score** (the dynamic bed from :mod:`dramapy.audio`) — mixed under at
  :data:`BGM_MIX_DB`, and **ducked under dialogue**: when a voice track is
  present it is split and used as the sidechain key of a compressor
  (:func:`dramapy.audio.duck_filter`), so the score drops ~8-10 dB while a line
  plays and swells back in the gaps and over score-only shots (e.g. the climax).
* **SFX** (``sfx_track`` from :mod:`dramapy.sfx`) — layered effects at
  :data:`SFX_MIX_DB`, punchy over the action beats.

The silent base guarantees an audio track even with no voice, no score, and no
SFX. Optional subtitle burn-in is a chain of timed PNG overlays (no libass in
the PATH ffmpeg).

The video filtergraph per segment::

    [i:v]fps=F,scale=W:H:force_original_aspect_ratio=decrease,
         pad=W:H:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p[vi]

Failures raise plain ``RuntimeError`` (from :mod:`dramapy.media`) — the
generation wrapper converts them to :class:`~dramapy.errors.ExportError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dramapy import media
from dramapy.audio import bgm_spec, duck_filter
from dramapy.spec import ResolvedSeries

BGM_MIX_DB = -12.0  # score under dialogue (ducked lower still under a live line)
SFX_MIX_DB = -5.0  # layered SFX: punchy over the action, below the voice
_AFORMAT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


@dataclass(frozen=True)
class ClipSegment:
    """One stitched segment: a rendered clip plus its probed facts. Clip audio
    is no longer used (dialogue is assembled from the voice track), so no
    ``has_audio`` flag is carried."""

    shot_id: str
    path: Path
    duration_s: float


@dataclass(frozen=True)
class SubtitleOverlay:
    """A pre-rendered subtitle PNG shown between ``start_s`` and ``end_s``."""

    png_path: Path
    start_s: float
    end_s: float


def stitch_episode(
    *,
    segments: list[ClipSegment],
    series: ResolvedSeries,
    output_path: Path,
    bgm_mood: str | None = None,
    subtitle_overlays: list[SubtitleOverlay] | None = None,
    voice_track: Path | None = None,
    sfx_track: Path | None = None,
) -> Path:
    """Concat ``segments`` into ``output_path`` with the cinematic three-layer
    mix over a silent base: ``voice_track`` (spoken dialogue, 0 dB), the dynamic
    score (:data:`BGM_MIX_DB`, ducked under the voice via a sidechain
    compressor), and ``sfx_track`` (layered effects, :data:`SFX_MIX_DB`). Raises
    ``RuntimeError`` on ffmpeg failure, ``ValueError`` when there is nothing to
    stitch."""
    if not segments:
        raise ValueError("stitch_episode needs at least one segment")

    width, height = series.resolution
    fps = series.fps
    overlays = subtitle_overlays or []
    total_s = sum(segment.duration_s for segment in segments)
    base_dur = max(0.1, total_s)

    args: list[str] = ["-y"]
    graph: list[str] = []

    # Clip inputs: 0..N-1 (video only — their audio, if any, is discarded).
    for segment in segments:
        args += ["-i", str(segment.path)]
    next_input = len(segments)

    # Silent base: always present, exactly the episode length. It anchors the
    # mixed-audio duration and guarantees an audio track even with no voice
    # and no bed.
    args += [
        "-f",
        "lavfi",
        "-t",
        f"{base_dur:.3f}",
        "-i",
        "anullsrc=r=48000:cl=stereo",
    ]
    base_input = next_input
    next_input += 1

    # Spoken-dialogue voice track (assembly stage; see dramapy.voices).
    voice_input: int | None = None
    if voice_track is not None:
        args += ["-i", str(voice_track)]
        voice_input = next_input
        next_input += 1

    # Score input (generated a beat longer than the episode; trimmed by the mix).
    # ``series.genre`` steers the generated cinematic score toward the drama.
    bgm = bgm_spec(bgm_mood, total_s + 1.0, genre=series.genre)
    bgm_input: int | None = None
    if bgm is not None:
        args += ["-f", "lavfi", "-i", bgm.input_expr]
        bgm_input = next_input
        next_input += 1

    # SFX input (episode-length layered effects track; trimmed/padded by the mix).
    sfx_input: int | None = None
    if sfx_track is not None:
        args += ["-i", str(sfx_track)]
        sfx_input = next_input
        next_input += 1

    # Subtitle overlay inputs.
    overlay_inputs: list[int] = []
    for overlay in overlays:
        args += ["-i", str(overlay.png_path)]
        overlay_inputs.append(next_input)
        next_input += 1

    # Per-segment video normalization → concat (video only).
    for index, segment in enumerate(segments):
        graph.append(
            f"[{index}:v]fps={fps},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,format=yuv420p[v{index}]"
        )
    vpairs = "".join(f"[v{index}]" for index in range(len(segments)))
    graph.append(f"{vpairs}concat=n={len(segments)}:v=1:a=0[vcat]")

    # Cinematic mix: silent base (first → pins duration) + voice (0 dB, primary)
    # + score (BGM_MIX_DB, ducked under the voice) + SFX (SFX_MIX_DB, layered).
    graph.append(f"[{base_input}:a]{_AFORMAT}[abase]")
    audio_labels = ["[abase]"]

    # Voice, fitted to the episode. When a score is present the voice is split so
    # one copy plays and the other keys the score's sidechain compressor.
    voice_main: str | None = None
    voice_key: str | None = None
    if voice_input is not None:
        graph.append(
            f"[{voice_input}:a]aresample=48000,{_AFORMAT},"
            f"apad=whole_dur={base_dur:.3f},atrim=0:{base_dur:.3f}[voicefit]"
        )
        if bgm_input is not None:
            graph.append("[voicefit]asplit=2[voicemain][voicekey]")
            voice_main, voice_key = "[voicemain]", "[voicekey]"
        else:
            voice_main = "[voicefit]"
        audio_labels.append(voice_main)

    # Score: dynamic bed → level → trim, then duck under the voice key if present.
    if bgm_input is not None:
        chain = f"{bgm.filter_chain}," if bgm.filter_chain else ""
        graph.append(
            f"[{bgm_input}:a]{chain}volume={BGM_MIX_DB:g}dB,"
            f"atrim=0:{base_dur:.3f},{_AFORMAT}[bgmlvl]"
        )
        if voice_key is not None:
            graph.append(duck_filter("[bgmlvl]", voice_key, "[bgm]"))
        else:
            graph.append("[bgmlvl]anull[bgm]")
        audio_labels.append("[bgm]")

    # SFX: layered effects track, fitted to the episode and mixed in punchy.
    if sfx_input is not None:
        graph.append(
            f"[{sfx_input}:a]aresample=48000,{_AFORMAT},"
            f"apad=whole_dur={base_dur:.3f},atrim=0:{base_dur:.3f},"
            f"volume={SFX_MIX_DB:g}dB[sfx]"
        )
        audio_labels.append("[sfx]")

    if len(audio_labels) == 1:
        audio_label = audio_labels[0]  # just the silent base
    else:
        graph.append(
            "".join(audio_labels)
            + f"amix=inputs={len(audio_labels)}:duration=first"
            ":dropout_transition=0:normalize=0[aout]"
        )
        audio_label = "[aout]"

    # Timed subtitle burn-in overlays.
    video_label = "[vcat]"
    for overlay_index, (overlay, input_index) in enumerate(
        zip(overlays, overlay_inputs)
    ):
        out_label = f"[vs{overlay_index}]"
        graph.append(
            f"{video_label}[{input_index}:v]overlay="
            "x=(main_w-overlay_w)/2:y=main_h-overlay_h-round(main_h*0.06)"
            f":enable='between(t,{overlay.start_s:.3f},{overlay.end_s:.3f})'"
            f"{out_label}"
        )
        video_label = out_label

    output_path.parent.mkdir(parents=True, exist_ok=True)
    args += [
        "-filter_complex",
        ";".join(graph),
        "-map",
        video_label,
        "-map",
        audio_label,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    media.run_ffmpeg(args)
    return output_path
