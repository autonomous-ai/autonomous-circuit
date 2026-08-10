"""The provider-independent voice layer (:mod:`dramapy.voices`) + its mix into
the episode by the stitcher.

``voice_for_tag`` is pure and always tested. ``build_voice_track`` is exercised
for real with macOS ``say`` in ONE test (speech energy present, duration ≈ the
episode span); the off-switch and no-``say`` fallback paths are fast and always
run. A tiny stitch test proves the voice track lands in the mixed audio."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramapy import media, voices  # noqa: E402
from dramapy.errors import ProviderError  # noqa: E402
from dramapy.spec import ResolvedSeries  # noqa: E402
from dramapy.stitch import ClipSegment, stitch_episode  # noqa: E402
from dramapy.voices import (  # noqa: E402
    FEMALE_VOICE,
    MALE_VOICE,
    ROTATION_VOICES,
    TTS_RATE_RANGE_WPM,
    build_voice_track,
    voice_for_tag,
    voice_for_tag_elevenlabs,
)


def _mean_volume_db(path: Path) -> float:
    """ffmpeg volumedetect mean_volume (dB) for a media file's audio track.
    Pure silence reports a very low / -inf floor; speech sits well above it."""
    proc = subprocess.run(
        [
            media.ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path),
            "-map", "0:a", "-af", "volumedetect", "-f", "null", "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    for line in proc.stderr.decode("utf-8", "replace").splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0].strip())
    raise AssertionError(f"no mean_volume in volumedetect output for {path}")


class VoiceMappingTests(unittest.TestCase):
    def test_voice_mapping_is_deterministic(self) -> None:
        for tag in ("f_low_calm", "m_deep_cold", "raspy_neutral", ""):
            self.assertEqual(voice_for_tag(tag), voice_for_tag(tag))
        low, high = TTS_RATE_RANGE_WPM
        voice_f, rate_f = voice_for_tag("f_low_calm")
        self.assertEqual(FEMALE_VOICE, voice_f)
        voice_m, rate_m = voice_for_tag("m_deep_cold")
        self.assertEqual(MALE_VOICE, voice_m)
        self.assertNotEqual(voice_f, voice_m, "f_ and m_ tags must differ")
        voice_other, rate_other = voice_for_tag("gravelly")
        self.assertIn(voice_other, ROTATION_VOICES)
        for rate in (rate_f, rate_m, rate_other):
            self.assertTrue(low <= rate <= high, rate)
        # Distinct female tags spread across rates (the hash matters).
        rates = {voice_for_tag(f"f_voice_{i}")[1] for i in range(12)}
        self.assertGreater(len(rates), 1, "rates must vary by tag hash")


def _entries() -> list[dict]:
    return [
        {"shot_id": "d1", "line": "You never told me he was alive.",
         "voice_tag": "f_low_calm", "start_s": 0.0, "duration_s": 2.5},
        {"shot_id": "d2", "line": "You never asked.",
         "voice_tag": "m_deep_cold", "start_s": 2.5, "duration_s": 2.0},
    ]


class BuildVoiceTrackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.get(voices.VOICES_ENV)
        self._saved_bin = voices._SAY_BIN

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop(voices.VOICES_ENV, None)
        else:
            os.environ[voices.VOICES_ENV] = self._saved
        voices._SAY_BIN = self._saved_bin

    def test_off_switch_returns_none(self) -> None:
        os.environ[voices.VOICES_ENV] = "off"
        self.assertIsNone(build_voice_track(_entries()))

    def test_no_say_fallback_returns_none(self) -> None:
        os.environ.pop(voices.VOICES_ENV, None)
        voices._SAY_BIN = None  # simulate a machine without `say`
        self.assertIsNone(build_voice_track(_entries()))

    def test_empty_entries_returns_none(self) -> None:
        os.environ.pop(voices.VOICES_ENV, None)
        self.assertIsNone(build_voice_track([]))
        self.assertIsNone(build_voice_track([{"shot_id": "x", "line": "  ",
                                              "voice_tag": "", "start_s": 0.0,
                                              "duration_s": 2.0}]))

    @unittest.skipUnless(shutil.which("say"), "macOS `say` not on PATH")
    def test_real_say_track_has_speech_energy(self) -> None:
        os.environ.pop(voices.VOICES_ENV, None)  # voices ON
        voiced: set[str] = set()
        track = build_voice_track(_entries(), voiced_out=voiced)
        self.assertIsNotNone(track)
        assert track is not None
        try:
            probe = media.probe_media(track)
            self.assertTrue(probe.has_audio, "voice track must carry audio")
            self.assertFalse(probe.has_video)
            # Duration ≈ the episode span (last line ends at 2.5 + 2.0 = 4.5s).
            self.assertAlmostEqual(4.5, probe.duration_s, delta=0.6)
            # Not pure silence: real speech energy.
            self.assertGreater(_mean_volume_db(track), -50.0)
            self.assertEqual({"d1", "d2"}, voiced)
        finally:
            track.unlink(missing_ok=True)


class VoiceMixStitchTests(unittest.TestCase):
    """The voice track lands in the episode audio; a silent base is always
    present even with no voice and no bed."""

    SERIES = ResolvedSeries(
        title="T", genre="revenge", style="photoreal-drama", aspect="9:16",
        resolution=(180, 320), fps=24, language="en",
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="dramapy-mix-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _silent_clip(self, name: str, duration_s: float) -> Path:
        path = self.tmp / name
        media.run_ffmpeg([
            "-y", "-f", "lavfi", "-i",
            f"color=c=0x224466:s=180x320:r=24:d={duration_s:.3f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-pix_fmt", "yuv420p", "-t", f"{duration_s:.3f}", str(path),
        ])
        return path

    def _voice_wav(self, name: str, duration_s: float, freq: int) -> Path:
        """A stand-in 'voice' track (loud tone) so the mix test needs no say."""
        path = self.tmp / name
        media.run_ffmpeg([
            "-y", "-f", "lavfi", "-i",
            f"sine=f={freq}:r=48000:d={duration_s:.3f}",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(path),
        ])
        return path

    def test_episode_always_has_audio_with_no_voice_no_bgm(self) -> None:
        seg = ClipSegment(shot_id="a", path=self._silent_clip("a.mp4", 2.0),
                          duration_s=2.0)
        out = self.tmp / "ep_silent.mp4"
        stitch_episode(segments=[seg], series=self.SERIES, output_path=out,
                       bgm_mood=None, voice_track=None)
        probe = media.probe_media(out)
        self.assertTrue(probe.has_audio, "episode must always carry an audio track")
        self.assertAlmostEqual(2.0, probe.duration_s, delta=0.3)

    def test_voice_track_is_mixed_into_episode(self) -> None:
        seg = ClipSegment(shot_id="a", path=self._silent_clip("v.mp4", 3.0),
                          duration_s=3.0)
        voice = self._voice_wav("voice.wav", 3.0, freq=440)
        out = self.tmp / "ep_voice.mp4"
        stitch_episode(segments=[seg], series=self.SERIES, output_path=out,
                       bgm_mood=None, voice_track=voice)
        probe = media.probe_media(out)
        self.assertTrue(probe.has_audio)
        self.assertAlmostEqual(3.0, probe.duration_s, delta=0.3)
        # The mixed tone is well above silence.
        self.assertGreater(_mean_volume_db(out), -40.0)


# -- ElevenLabs backend (fal), faked offline. -------------------------------


def _wav_bytes(dur_s: float = 1.0, freq: int = 330) -> bytes:
    """Real decodable wav bytes so the ffmpeg placement stage has audio to
    fit. Written by a fake ``download`` — no network."""
    with tempfile.TemporaryDirectory(prefix="dramapy-wavbytes-") as d:
        path = Path(d) / "s.wav"
        media.run_ffmpeg([
            "-y", "-f", "lavfi", "-i", f"sine=f={freq}:r=48000:d={dur_s:.3f}",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(path),
        ])
        return path.read_bytes()


class FakeFal:
    """Stand-in for :class:`dramapy.fal_client.FalClient`: records ``run``
    payloads, ``download`` writes prepared wav bytes. Config via class attrs so
    a no-arg ``FalClient()`` construction picks it up. Reset per test."""

    calls: list[dict] = []
    downloads: list[tuple[str, str]] = []
    wav_bytes: bytes = b""
    fail_all: bool = False

    def __init__(self, key: object = None) -> None:  # noqa: D401 — matches FalClient
        pass

    @classmethod
    def reset(cls, wav_bytes: bytes) -> None:
        cls.calls = []
        cls.downloads = []
        cls.wav_bytes = wav_bytes
        cls.fail_all = False

    def run(self, model_id: str, payload: dict, *, budget_s: float | None = None,
            label: str | None = None) -> dict:
        FakeFal.calls.append({"model_id": model_id, "payload": payload, "label": label})
        if FakeFal.fail_all:
            raise ProviderError("fake fal failure")
        return {"audio": {"url": f"fake://{len(FakeFal.calls)}.wav"}}

    def download(self, url: str, target: Path) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(FakeFal.wav_bytes)
        FakeFal.downloads.append((url, str(target)))
        return target


class ElevenLabsMappingTests(unittest.TestCase):
    def test_mapping_is_deterministic_and_split_by_sex(self) -> None:
        for tag in ("f_low_calm", "m_deep_cold", "gravelly", ""):
            self.assertEqual(
                voice_for_tag_elevenlabs(tag), voice_for_tag_elevenlabs(tag)
            )
        female = voice_for_tag_elevenlabs("f_low_calm")
        male = voice_for_tag_elevenlabs("m_deep_cold")
        self.assertIn(female, voices.ELEVEN_FEMALE_VOICES)
        self.assertIn(male, voices.ELEVEN_MALE_VOICES)
        self.assertNotIn(female, voices.ELEVEN_MALE_VOICES, "f_/m_ pools disjoint")
        # "female" must not be misread as "male" (substring trap).
        self.assertIn(voice_for_tag_elevenlabs("female_narrator"),
                      voices.ELEVEN_FEMALE_VOICES)
        # Neutral tags draw from both pools; distinct tags spread across names.
        names = {voice_for_tag_elevenlabs(f"f_voice_{i}") for i in range(20)}
        self.assertGreater(len(names), 1, "names must vary by tag hash")


class VoiceBackendSelectionTests(unittest.TestCase):
    ENV = ("FAL_KEY", voices.VOICES_ENV, voices.BACKEND_ENV)

    def setUp(self) -> None:
        self._saved_env = {k: os.environ.get(k) for k in self.ENV}
        self._saved_say = voices._say_bin
        for k in self.ENV:
            os.environ.pop(k, None)
        voices._say_bin = lambda: "/usr/bin/say"  # pretend `say` is present

    def tearDown(self) -> None:
        for k, val in self._saved_env.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val
        voices._say_bin = self._saved_say

    def test_fal_key_selects_elevenlabs(self) -> None:
        os.environ["FAL_KEY"] = "fake-key"
        self.assertEqual("elevenlabs", voices._voice_backend())

    def test_off_switch_beats_fal_key(self) -> None:
        os.environ["FAL_KEY"] = "fake-key"
        os.environ[voices.VOICES_ENV] = "off"
        self.assertIsNone(voices._voice_backend())

    def test_backend_pinned_to_say_beats_fal_key(self) -> None:
        os.environ["FAL_KEY"] = "fake-key"
        os.environ[voices.BACKEND_ENV] = "say"
        self.assertEqual("say", voices._voice_backend())

    def test_backend_elevenlabs_without_fal_key(self) -> None:
        os.environ[voices.BACKEND_ENV] = "elevenlabs"
        self.assertEqual("elevenlabs", voices._voice_backend())

    def test_say_when_no_fal_key(self) -> None:
        self.assertEqual("say", voices._voice_backend())

    def test_none_when_no_backend_available(self) -> None:
        voices._say_bin = lambda: None
        self.assertIsNone(voices._voice_backend())


class ElevenLabsBuildTests(unittest.TestCase):
    """``build_voice_track`` on the ElevenLabs backend, fully faked (no
    network, no `say` needed)."""

    ENV = ("FAL_KEY", voices.VOICES_ENV, voices.BACKEND_ENV)

    def setUp(self) -> None:
        self._saved_env = {k: os.environ.get(k) for k in self.ENV}
        self._saved_fal = voices.FalClient
        self._saved_say_bin = voices._say_bin
        self._saved_synth = voices._synthesize
        for k in self.ENV:
            os.environ.pop(k, None)
        os.environ["FAL_KEY"] = "fake-key"  # selects elevenlabs
        voices.FalClient = FakeFal
        FakeFal.reset(_wav_bytes())

    def tearDown(self) -> None:
        for k, val in self._saved_env.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val
        voices.FalClient = self._saved_fal
        voices._say_bin = self._saved_say_bin
        voices._synthesize = self._saved_synth

    def _entries_with_emotion(self) -> list[dict]:
        return [
            {"shot_id": "d1", "line": "You never told me he was alive.",
             "voice_tag": "f_low_calm", "emotion": "angry",
             "start_s": 0.0, "duration_s": 2.5},
            {"shot_id": "d2", "line": "You never asked.",
             "voice_tag": "m_deep_cold",  # no emotion → plain, neutral stability
             "start_s": 2.5, "duration_s": 2.0},
        ]

    def test_elevenlabs_backend_produces_track(self) -> None:
        voiced: set[str] = set()
        track = build_voice_track(self._entries_with_emotion(), voiced_out=voiced)
        self.assertIsNotNone(track)
        assert track is not None
        try:
            probe = media.probe_media(track)
            self.assertTrue(probe.has_audio)
            self.assertFalse(probe.has_video)
            self.assertAlmostEqual(4.5, probe.duration_s, delta=0.6)
            self.assertEqual({"d1", "d2"}, voiced)
            # Both lines went through eleven-v3 (not `say`).
            self.assertEqual(2, len(FakeFal.calls))
            for call in FakeFal.calls:
                self.assertEqual(voices.ELEVEN_MODEL, call["model_id"])
        finally:
            track.unlink(missing_ok=True)

    def test_voice_name_pinned_and_emotion_tag_prepended(self) -> None:
        track = build_voice_track(self._entries_with_emotion())
        self.assertIsNotNone(track)
        assert track is not None
        track.unlink(missing_ok=True)
        angry, plain = FakeFal.calls[0]["payload"], FakeFal.calls[1]["payload"]
        # Emotion → inline tag + low (expressive) stability.
        self.assertTrue(angry["text"].startswith("[angry] "), angry["text"])
        self.assertEqual(0.3, angry["stability"])
        # No emotion → plain text + neutral stability.
        self.assertFalse(plain["text"].startswith("["), plain["text"])
        self.assertEqual("You never asked.", plain["text"])
        self.assertEqual(0.5, plain["stability"])
        # Voice names are the deterministic pinned presets for each tag.
        self.assertEqual(voice_for_tag_elevenlabs("f_low_calm"), angry["voice"])
        self.assertEqual(voice_for_tag_elevenlabs("m_deep_cold"), plain["voice"])

    def test_per_line_failure_falls_back_to_say(self) -> None:
        FakeFal.fail_all = True  # every eleven-v3 call raises

        calls: list[str] = []

        def fake_say(line: str, voice: str, rate: int, out_path: Path) -> bool:
            calls.append(line)
            out_path.write_bytes(FakeFal.wav_bytes)  # a real wav for placement
            return True

        voices._say_bin = lambda: "/usr/bin/say"
        voices._synthesize = fake_say
        voiced: set[str] = set()
        track = build_voice_track(self._entries_with_emotion(), voiced_out=voiced)
        self.assertIsNotNone(track, "must fall back to say, not fail")
        assert track is not None
        track.unlink(missing_ok=True)
        self.assertEqual(2, len(FakeFal.calls), "eleven-v3 was attempted per line")
        self.assertEqual(2, len(calls), "say spoke every line after eleven failed")
        self.assertEqual({"d1", "d2"}, voiced)

    def test_off_switch_returns_none_even_with_fal_key(self) -> None:
        os.environ[voices.VOICES_ENV] = "off"
        self.assertIsNone(build_voice_track(self._entries_with_emotion()))
        self.assertEqual([], FakeFal.calls, "off switch never calls fal")


if __name__ == "__main__":
    unittest.main()
