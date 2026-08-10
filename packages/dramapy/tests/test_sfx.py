"""The layered SFX track (:mod:`dramapy.sfx`).

Cue inference and the explicit-``sfx`` path are pure and always tested. The fal
generation path (ElevenLabs sound-effects + mmaudio-v2) is fully faked — no
network — so the suite stays offline; only the ffmpeg placement/extraction and
the final stitch mix are exercised for real. With no ``FAL_KEY`` the track is
``None`` (the CI/eval path)."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramapy import media, sfx  # noqa: E402
from dramapy.errors import ProviderError  # noqa: E402
from dramapy.spec import ResolvedSeries  # noqa: E402
from dramapy.stitch import ClipSegment, stitch_episode  # noqa: E402


# -- fakes / fixtures ---------------------------------------------------------


def _wav_bytes(dur_s: float = 1.5, freq: int = 330) -> bytes:
    with tempfile.TemporaryDirectory(prefix="dramapy-sfxwav-") as d:
        path = Path(d) / "s.wav"
        media.run_ffmpeg([
            "-y", "-f", "lavfi", "-i", f"sine=f={freq}:r=48000:d={dur_s:.3f}",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(path),
        ])
        return path.read_bytes()


def _mp4_with_audio_bytes(dur_s: float = 2.0) -> bytes:
    """An mp4 carrying both video and audio — what mmaudio-v2 returns."""
    with tempfile.TemporaryDirectory(prefix="dramapy-sfxmp4-") as d:
        path = Path(d) / "v.mp4"
        media.run_ffmpeg([
            "-y",
            "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=24:d={dur_s:.3f}",
            "-f", "lavfi", "-i", f"sine=f=200:r=48000:d={dur_s:.3f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ])
        return path.read_bytes()


def _silent_clip(path: Path, dur_s: float) -> Path:
    media.run_ffmpeg([
        "-y", "-f", "lavfi", "-i",
        f"color=c=0x101820:s=160x284:r=24:d={dur_s:.3f}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-pix_fmt", "yuv420p", "-t", f"{dur_s:.3f}", str(path),
    ])
    return path


class FakeFal:
    """Stand-in for :class:`dramapy.fal_client.FalClient`. ``run`` records the
    call and returns the shape each model produces; ``download`` writes prepared
    bytes chosen by the target suffix (``.mp4`` = mmaudio video, else audio)."""

    calls: list[dict] = []
    wav_bytes: bytes = b""
    mp4_bytes: bytes = b""
    fail_all: bool = False

    def __init__(self, key: object = None) -> None:
        pass

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.wav_bytes = _wav_bytes()
        cls.mp4_bytes = _mp4_with_audio_bytes()
        cls.fail_all = False

    def run(self, model_id: str, payload: dict, *, budget_s=None, label=None) -> dict:
        FakeFal.calls.append({"model_id": model_id, "payload": payload})
        if FakeFal.fail_all:
            raise ProviderError("fake fal failure")
        if model_id == sfx.SFX_MMAUDIO_MODEL:
            return {"video": {"url": "fake://foley.mp4"}}
        return {"audio": {"url": "fake://sfx.mp3"}}

    def download(self, url: str, target: Path) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(FakeFal.mp4_bytes if str(target).endswith(".mp4") else FakeFal.wav_bytes)
        return target

    @staticmethod
    def data_uri(path: Path) -> str:
        return "data:video/mp4;base64,AAAA"


# -- cue inference (pure) -----------------------------------------------------


class InferCuesTests(unittest.TestCase):
    def _ids(self, prompt: str) -> list[str]:
        return [c.id for c in sfx.infer_cues(prompt)]

    def test_dragon_infers_roar(self) -> None:
        self.assertIn("dragon_roar", self._ids("a massive dragon rises over the city"))

    def test_fire_infers_whoosh_crackle(self) -> None:
        cues = sfx.infer_cues("the hall bursts into flame and fire")
        self.assertIn("fire_whoosh", [c.id for c in cues])
        self.assertIn("fire", cues[0].prompt.lower())

    def test_ice_infers_crack(self) -> None:
        self.assertIn("ice_crack", self._ids("frost creeps across the frozen lake"))

    def test_wings_infer_wingbeat(self) -> None:
        self.assertIn("wingbeat", self._ids("great wings beat against the storm"))

    def test_impact_infers_boom(self) -> None:
        self.assertIn("impact_boom", self._ids("the two knights collide in a violent crash"))

    def test_storm_and_thunder(self) -> None:
        self.assertIn("thunder", self._ids("thunder splits the sky"))
        self.assertIn("storm", self._ids("a raging storm at sea"))

    def test_capped_at_two_per_shot(self) -> None:
        cues = sfx.infer_cues("a dragon breathes fire while ice shards fly and thunder rolls")
        self.assertLessEqual(len(cues), sfx.MAX_SFX_PER_SHOT)
        self.assertEqual(["dragon_roar", "fire_whoosh"], [c.id for c in cues])

    def test_no_false_positive(self) -> None:
        self.assertEqual([], sfx.infer_cues("he turns slowly from the window and smiles"))


# -- explicit sfx field -------------------------------------------------------


class ExplicitCueTests(unittest.TestCase):
    def test_explicit_strings_reuse_known_cues(self) -> None:
        cues = sfx.cues_for_entry({"prompt": "a quiet room", "sfx": ["dragon roar", "sword clash"]})
        self.assertEqual(["dragon_roar", "sword_clash"], [c.id for c in cues])

    def test_explicit_overrides_inference(self) -> None:
        # A dragon prompt, but an explicit cue list → only the explicit cue.
        cues = sfx.cues_for_entry({"prompt": "a dragon roars", "sfx": ["a soft chime"]})
        self.assertEqual(1, len(cues))
        self.assertEqual("a soft chime", cues[0].prompt)

    def test_explicit_dict_form(self) -> None:
        cues = sfx.cues_for_entry(
            {"prompt": "x", "sfx": [{"id": "custom_bang", "prompt": "a strange bang", "duration_s": 2.0}]}
        )
        self.assertEqual(1, len(cues))
        self.assertEqual("custom_bang", cues[0].id)
        self.assertEqual("a strange bang", cues[0].prompt)
        self.assertEqual(2.0, cues[0].duration_s)


# -- env switches -------------------------------------------------------------


class EnvSwitchTests(unittest.TestCase):
    ENV = ("FAL_KEY", sfx.SFX_ENV, sfx.SFX_MODEL_ENV)

    def setUp(self) -> None:
        self._saved = {k: os.environ.get(k) for k in self.ENV}
        for k in self.ENV:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_disabled_without_key(self) -> None:
        self.assertFalse(sfx.sfx_enabled())
        self.assertIsNone(sfx.build_sfx_track([{"prompt": "a dragon", "start_s": 0, "duration_s": 3}]))

    def test_off_switch_disables_even_with_key(self) -> None:
        os.environ["FAL_KEY"] = "fake"
        os.environ[sfx.SFX_ENV] = "off"
        self.assertFalse(sfx.sfx_enabled())

    def test_on_with_key(self) -> None:
        os.environ["FAL_KEY"] = "fake"
        self.assertTrue(sfx.sfx_enabled())


# -- generation (faked fal) ---------------------------------------------------


class BuildTrackTests(unittest.TestCase):
    ENV = ("FAL_KEY", sfx.SFX_ENV, sfx.SFX_MODEL_ENV)

    def setUp(self) -> None:
        self._saved = {k: os.environ.get(k) for k in self.ENV}
        for k in self.ENV:
            os.environ.pop(k, None)
        os.environ["FAL_KEY"] = "fake"
        self._saved_fal = sfx.FalClient
        sfx.FalClient = FakeFal  # type: ignore[misc]
        FakeFal.reset()
        self.tmp = Path(tempfile.mkdtemp(prefix="dramapy-sfxtest-"))

    def tearDown(self) -> None:
        sfx.FalClient = self._saved_fal  # type: ignore[misc]
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_layered_track_built_and_has_audio(self) -> None:
        entries = [
            {"shot_id": "a", "kind": "action", "prompt": "a dragon roars", "start_s": 0.0, "duration_s": 3.0},
            {"shot_id": "b", "kind": "action", "prompt": "the hall bursts into fire", "start_s": 3.0, "duration_s": 3.0},
        ]
        track = sfx.build_sfx_track(entries)
        self.assertIsNotNone(track)
        assert track is not None
        try:
            probe = media.probe_media(track)
            self.assertTrue(probe.has_audio)
            self.assertGreater(probe.duration_s, 3.0, "track spans past the 2nd cue's start")
            self.assertEqual(2, len(FakeFal.calls))
            self.assertEqual(sfx.SFX_MODEL, FakeFal.calls[0]["model_id"])
            self.assertIn("prompt", FakeFal.calls[0]["payload"])
            self.assertIn("duration", FakeFal.calls[0]["payload"])
        finally:
            track.unlink(missing_ok=True)

    def test_identical_cues_generate_once(self) -> None:
        entries = [
            {"shot_id": "a", "kind": "action", "prompt": "a dragon roars", "start_s": 0.0, "duration_s": 3.0},
            {"shot_id": "b", "kind": "action", "prompt": "another dragon roars", "start_s": 4.0, "duration_s": 3.0},
        ]
        track = sfx.build_sfx_track(entries)
        assert track is not None
        try:
            self.assertEqual(1, len(FakeFal.calls), "the identical dragon cue is cached")
            self.assertTrue(media.probe_media(track).has_audio)
        finally:
            track.unlink(missing_ok=True)

    def test_explicit_sfx_honored_end_to_end(self) -> None:
        entries = [{"shot_id": "a", "kind": "insert", "prompt": "a still photo",
                    "start_s": 0.0, "duration_s": 2.0, "sfx": ["ice crack"]}]
        track = sfx.build_sfx_track(entries)
        assert track is not None
        try:
            self.assertEqual(1, len(FakeFal.calls))
            self.assertIn("ice", FakeFal.calls[0]["payload"]["prompt"].lower())
        finally:
            track.unlink(missing_ok=True)

    def test_no_cues_returns_none(self) -> None:
        entries = [{"shot_id": "a", "kind": "dialogue", "prompt": "she smiles",
                    "start_s": 0.0, "duration_s": 3.0}]
        self.assertIsNone(sfx.build_sfx_track(entries))
        self.assertEqual([], FakeFal.calls)

    def test_off_switch_returns_none(self) -> None:
        os.environ[sfx.SFX_ENV] = "off"
        entries = [{"shot_id": "a", "kind": "action", "prompt": "a dragon", "start_s": 0.0, "duration_s": 3.0}]
        self.assertIsNone(sfx.build_sfx_track(entries))
        self.assertEqual([], FakeFal.calls)

    def test_mmaudio_mode_uses_video_to_audio_for_action_clip(self) -> None:
        os.environ[sfx.SFX_MODEL_ENV] = "mmaudio"
        clip = _silent_clip(self.tmp / "shot.mp4", 2.0)
        entries = [{"shot_id": "a", "kind": "action", "prompt": "a dragon dives",
                    "start_s": 0.0, "duration_s": 2.0, "clip_path": str(clip)}]
        track = sfx.build_sfx_track(entries)
        assert track is not None
        try:
            self.assertEqual(1, len(FakeFal.calls))
            call = FakeFal.calls[0]
            self.assertEqual(sfx.SFX_MMAUDIO_MODEL, call["model_id"])
            self.assertTrue(call["payload"]["video_url"].startswith("data:"))
            self.assertTrue(media.probe_media(track).has_audio)
        finally:
            track.unlink(missing_ok=True)

    def test_mmaudio_mode_falls_back_to_cues_without_clip(self) -> None:
        os.environ[sfx.SFX_MODEL_ENV] = "mmaudio"
        entries = [{"shot_id": "a", "kind": "dialogue", "prompt": "a dragon roars",
                    "start_s": 0.0, "duration_s": 3.0}]  # dialogue, no clip → discrete cue
        track = sfx.build_sfx_track(entries)
        assert track is not None
        try:
            self.assertEqual(sfx.SFX_MODEL, FakeFal.calls[0]["model_id"])
        finally:
            track.unlink(missing_ok=True)


# -- the full cinematic mix (stitch) decodes ----------------------------------


class MixDecodeTests(unittest.TestCase):
    SERIES = ResolvedSeries(
        title="T", genre="revenge", style="photoreal-drama", aspect="9:16",
        resolution=(160, 284), fps=24, language="en",
    )

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dramapy-mix-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wav(self, name: str, dur: float, freq: int) -> Path:
        path = self.tmp / name
        media.run_ffmpeg([
            "-y", "-f", "lavfi", "-i", f"sine=f={freq}:r=48000:d={dur:.3f}",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(path),
        ])
        return path

    def test_full_mix_voice_ducked_score_and_sfx_decodes(self) -> None:
        """voice + synth score (ducked via sidechain) + SFX all mix to a valid
        episode with an audio track. The synth score exercises the duck path
        with no FAL_KEY."""
        seg = ClipSegment(shot_id="a", path=_silent_clip(self.tmp / "a.mp4", 4.0), duration_s=4.0)
        voice = self._wav("voice.wav", 4.0, 440)
        sfx_track = self._wav("sfx.wav", 2.0, 900)
        out = self.tmp / "ep.mp4"
        prev_key = os.environ.pop("FAL_KEY", None)  # force the synth score bed
        try:
            stitch_episode(
                segments=[seg], series=self.SERIES, output_path=out,
                bgm_mood="tense-strings", voice_track=voice, sfx_track=sfx_track,
            )
        finally:
            if prev_key is not None:
                os.environ["FAL_KEY"] = prev_key
        probe = media.probe_media(out)
        self.assertTrue(probe.has_audio)
        self.assertAlmostEqual(4.0, probe.duration_s, delta=0.3)


if __name__ == "__main__":
    unittest.main()
