"""The score (:mod:`dramapy.audio`): a real generated cinematic bed (ElevenLabs
Music primary, Lyria 2 fallback) when a ``FAL_KEY`` is present, the offline
ffmpeg synth drone otherwise. The fal path is fully faked — no network — so the
suite stays offline; only the ffmpeg loop/trim/shape and the ducking/dynamic
filtergraphs are exercised for real (they must decode in the PATH ffmpeg)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramapy import audio, media  # noqa: E402
from dramapy.errors import ProviderError  # noqa: E402


def _wav_bytes(dur_s: float = 2.0, freq: int = 220) -> bytes:
    with tempfile.TemporaryDirectory(prefix="dramapy-music-src-") as d:
        path = Path(d) / "s.wav"
        media.run_ffmpeg([
            "-y", "-f", "lavfi", "-i", f"sine=f={freq}:r=48000:d={dur_s:.3f}",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(path),
        ])
        return path.read_bytes()


class FakeFal:
    """Stand-in for :class:`dramapy.fal_client.FalClient`. Records ``run``
    payloads; ``download`` writes prepared wav bytes. Config via class attrs."""

    calls: list[dict] = []
    wav_bytes: bytes = b""
    fail_all: bool = False
    fail_models: set[str] = set()

    def __init__(self, key: object = None) -> None:
        pass

    @classmethod
    def reset(cls, wav_bytes: bytes) -> None:
        cls.calls = []
        cls.wav_bytes = wav_bytes
        cls.fail_all = False
        cls.fail_models = set()

    def run(self, model_id: str, payload: dict, *, budget_s: float | None = None,
            label: str | None = None) -> dict:
        FakeFal.calls.append({"model_id": model_id, "payload": payload})
        if FakeFal.fail_all or model_id in FakeFal.fail_models:
            raise ProviderError("fake fal failure")
        return {"audio": {"url": "fake://score.wav"}}

    def download(self, url: str, target: Path) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(FakeFal.wav_bytes)
        return target


class _EnvGuard(unittest.TestCase):
    """Base: save/restore the env keys the score reads."""

    ENV = ("FAL_KEY", audio.MUSIC_ENV, audio.MUSIC_MODEL_ENV)

    def setUp(self) -> None:
        self._saved = {k: os.environ.get(k) for k in self.ENV}
        for k in self.ENV:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k, val in self._saved.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val


class BgmSpecSynthTests(_EnvGuard):
    """The offline synth bed and the plain resolution rules — no fal."""

    def test_none_mood_returns_none(self) -> None:
        self.assertIsNone(audio.bgm_spec(None, 5.0))

    def test_synth_bed_when_no_fal_key(self) -> None:
        spec = audio.bgm_spec("tense-strings", 5.0)
        assert spec is not None
        self.assertIn("sine", spec.input_expr)
        self.assertTrue(spec.filter_chain, "synth bed has a shaping chain")
        self.assertEqual("tense-strings", spec.mood)

    def test_synth_bed_has_rising_ramp(self) -> None:
        """Even the offline fallback builds toward the end (a time-varying ramp)."""
        spec = audio.bgm_spec("tense-strings", 8.0)
        assert spec is not None
        self.assertIn("volume=", spec.filter_chain)
        self.assertIn("eval=frame", spec.filter_chain)

    def test_unknown_mood_falls_back_to_neutral_drone(self) -> None:
        spec = audio.bgm_spec("mystery-mood", 3.0)
        assert spec is not None
        self.assertIn("sine", spec.input_expr)

    def test_video_music_off_returns_none_even_without_key(self) -> None:
        os.environ[audio.MUSIC_ENV] = "off"
        self.assertIsNone(audio.bgm_spec("tense-strings", 5.0))


class BgmSpecGeneratedScoreTests(_EnvGuard):
    """The generated-score path (ElevenLabs Music primary), faked (no network)."""

    def setUp(self) -> None:
        super().setUp()
        self._saved_fal = audio.FalClient
        os.environ["FAL_KEY"] = "fake-key"
        audio.FalClient = FakeFal  # type: ignore[misc]
        FakeFal.reset(_wav_bytes(dur_s=2.0))

    def tearDown(self) -> None:
        audio.FalClient = self._saved_fal  # type: ignore[misc]
        super().tearDown()

    def _fitted_path(self, spec: audio.BgmSpec) -> Path:
        self.assertTrue(spec.input_expr.startswith("amovie="), spec.input_expr)
        return Path(spec.input_expr[len("amovie="):])

    def test_short_cue_uses_elevenlabs_prompt_only(self) -> None:
        """Below the composition-plan floor → a prompt-only instrumental request."""
        spec = audio.bgm_spec("tense-strings", 5.0)
        assert spec is not None
        self.assertEqual("", spec.filter_chain, "the fitted bed is the finished file")
        self.assertEqual("tense-strings", spec.mood)
        fitted = self._fitted_path(spec)
        self.assertTrue(fitted.is_file())
        probe = media.probe_media(fitted)
        self.assertTrue(probe.has_audio)
        self.assertAlmostEqual(5.0, probe.duration_s, delta=0.3)
        self.assertEqual(1, len(FakeFal.calls))
        call = FakeFal.calls[0]
        self.assertEqual(audio.ELEVEN_MUSIC_MODEL, call["model_id"])
        self.assertTrue(call["payload"].get("force_instrumental"))
        self.assertIn("music_length_ms", call["payload"])
        prompt = call["payload"]["prompt"].lower()
        self.assertIn("instrumental", prompt)
        self.assertIn("strings", prompt)  # from the tense-strings mood prompt
        fitted.unlink(missing_ok=True)

    def test_full_episode_uses_composition_plan_arc(self) -> None:
        """A real-length episode → a native composition-plan arc, instrumental."""
        spec = audio.bgm_spec("tense-strings", 20.0, genre="revenge thriller")
        assert spec is not None
        self._fitted_path(spec).unlink(missing_ok=True)
        call = FakeFal.calls[0]
        self.assertEqual(audio.ELEVEN_MUSIC_MODEL, call["model_id"])
        self.assertNotIn("force_instrumental", call["payload"],
                         "force_instrumental is rejected alongside a composition_plan")
        plan = call["payload"]["composition_plan"]
        names = [s["section_name"] for s in plan["sections"]]
        self.assertIn("climax", names, "the arc must reach a climax")
        self.assertEqual(names, sorted(names, key=lambda n: _ARC_ORDER[n]),
                         "sections stay in chronological order")
        for section in plan["sections"]:
            self.assertGreaterEqual(section["duration_ms"], audio.MIN_SECTION_MS)
            self.assertLessEqual(section["duration_ms"], audio.MAX_SECTION_MS)
            self.assertEqual([], section["lines"], "instrumental: no lyrics")
            self.assertIn("vocals", section["negative_local_styles"])
        self.assertIn("vocals", plan["negative_global_styles"])
        # genre steers the global styles.
        self.assertTrue(any("revenge thriller" in s for s in plan["positive_global_styles"]))

    def test_video_music_off_returns_none(self) -> None:
        os.environ[audio.MUSIC_ENV] = "off"
        self.assertIsNone(audio.bgm_spec("tense-strings", 5.0))
        self.assertEqual([], FakeFal.calls, "off never calls fal")

    def test_video_music_synth_forces_synth_bed(self) -> None:
        os.environ[audio.MUSIC_ENV] = "synth"
        spec = audio.bgm_spec("tense-strings", 5.0)
        assert spec is not None
        self.assertIn("sine", spec.input_expr)
        self.assertTrue(spec.filter_chain)
        self.assertEqual([], FakeFal.calls, "synth mode never calls fal")

    def test_model_env_lyria_selects_lyria_first(self) -> None:
        os.environ[audio.MUSIC_MODEL_ENV] = "lyria"
        spec = audio.bgm_spec("tense-strings", 5.0)
        assert spec is not None
        self._fitted_path(spec).unlink(missing_ok=True)
        call = FakeFal.calls[0]
        self.assertEqual(audio.LYRIA_MODEL, call["model_id"])
        self.assertIn("negative_prompt", call["payload"])
        self.assertIn("vocals", call["payload"]["negative_prompt"].lower())

    def test_primary_failure_falls_back_to_lyria(self) -> None:
        FakeFal.fail_models = {audio.ELEVEN_MUSIC_MODEL}
        spec = audio.bgm_spec("tense-strings", 5.0)
        assert spec is not None
        self._fitted_path(spec).unlink(missing_ok=True)
        self.assertEqual(2, len(FakeFal.calls), "primary tried, then the fallback")
        self.assertEqual(audio.ELEVEN_MUSIC_MODEL, FakeFal.calls[0]["model_id"])
        self.assertEqual(audio.LYRIA_MODEL, FakeFal.calls[1]["model_id"])

    def test_both_models_fail_degrades_to_synth(self) -> None:
        FakeFal.fail_all = True
        spec = audio.bgm_spec("tense-strings", 5.0)
        assert spec is not None
        self.assertIn("sine", spec.input_expr, "must degrade to the synth bed")
        self.assertTrue(spec.filter_chain)
        self.assertEqual(2, len(FakeFal.calls), "both models were attempted")


_ARC_ORDER = {"hook": 0, "build": 1, "low": 2, "climax": 3, "resolve": 4}


class CompositionPlanTests(unittest.TestCase):
    """The composition-plan builder is pure — no fal, no env."""

    def test_plan_shortens_to_fit_minimum_section(self) -> None:
        # A 7 s episode holds at most two 3 s sections.
        plan = audio.composition_plan("tense-strings", None, 7.0)
        self.assertLessEqual(len(plan["sections"]), 2)
        self.assertGreaterEqual(len(plan["sections"]), 1)
        self.assertIn("climax", [s["section_name"] for s in plan["sections"]])

    def test_plan_uses_full_arc_when_long(self) -> None:
        plan = audio.composition_plan("melancholy", "romance", 30.0)
        names = [s["section_name"] for s in plan["sections"]]
        self.assertEqual(["hook", "build", "low", "climax", "resolve"], names)
        total = sum(s["duration_ms"] for s in plan["sections"])
        self.assertGreater(total, 0)

    def test_all_sections_within_schema_bounds(self) -> None:
        for dur in (3.0, 6.0, 12.0, 45.0, 120.0):
            plan = audio.composition_plan("neon-pulse", None, dur)
            for section in plan["sections"]:
                self.assertGreaterEqual(section["duration_ms"], audio.MIN_SECTION_MS)
                self.assertLessEqual(section["duration_ms"], audio.MAX_SECTION_MS)


class FiltergraphDecodeTests(unittest.TestCase):
    """The generated filter strings must decode in the real PATH ffmpeg."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dramapy-audiofx-"))

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dynamic_envelope_decodes(self) -> None:
        """The build-to-climax ``-af`` envelope applies cleanly and yields audio."""
        out = self.tmp / "env.wav"
        media.run_ffmpeg([
            "-y", "-f", "lavfi", "-i", "sine=f=220:r=48000:d=4",
            "-af", audio._dynamic_af(4.0),
            "-c:a", "pcm_s16le", str(out),
        ])
        self.assertTrue(media.probe_media(out).has_audio)

    def test_duck_filter_is_valid_sidechain_and_decodes(self) -> None:
        """The sidechain ducking node is a valid ``sidechaincompress`` and the
        two-input graph (bed + voice key) decodes to audio in the real ffmpeg."""
        node = audio.duck_filter("[bed]", "[key]", "[out]")
        self.assertIn("sidechaincompress", node)
        self.assertIn("threshold=", node)
        self.assertIn("ratio=", node)
        fmt = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        graph = f"[0:a]{fmt}[bed];[1:a]{fmt}[key];{node}"
        out = self.tmp / "duck.wav"
        media.run_ffmpeg([
            "-y",
            "-f", "lavfi", "-i", "sine=f=220:r=48000:d=4",  # bed
            "-f", "lavfi", "-i", "sine=f=660:r=48000:d=4",  # voice key
            "-filter_complex", graph, "-map", "[out]",
            "-c:a", "pcm_s16le", str(out),
        ])
        self.assertTrue(media.probe_media(out).has_audio)


if __name__ == "__main__":
    unittest.main()
