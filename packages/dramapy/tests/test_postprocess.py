"""postprocess — offline unit tests for the lip-sync + upscale fal steps.

Network transport is never exercised (a fake stands in for FalClient). These
tests pin the model ids, payload shapes, return-url extraction, error surfacing,
and the env gates the cinematic provider reads.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramapy import postprocess  # noqa: E402
from dramapy.errors import ProviderError  # noqa: E402


class FakeFal:
    """Records run() calls; returns configurable results."""

    def __init__(self, result_for=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._result_for = result_for or {}

    def run(self, model_id, payload, *, budget_s=None, label=None):
        self.calls.append((model_id, dict(payload)))
        for needle, result in self._result_for.items():
            if needle in model_id:
                return result
        return {}


class LipsyncTest(unittest.TestCase):
    def test_lipsync_calls_model_and_returns_video_url(self) -> None:
        fake = FakeFal({"sync-lipsync": {"video": {"url": "https://f/synced.mp4"}}})
        url = postprocess.lipsync(fake, "https://f/clip.mp4", "https://f/line.mp3",
                                  budget_s=5)
        self.assertEqual(url, "https://f/synced.mp4")
        model_id, payload = fake.calls[0]
        self.assertEqual(model_id, postprocess.LIPSYNC_MODEL)
        self.assertEqual(model_id, "fal-ai/sync-lipsync/v2")
        self.assertEqual(payload, {"video_url": "https://f/clip.mp4",
                                   "audio_url": "https://f/line.mp3",
                                   "sync_mode": "silence"})

    def test_lipsync_raises_when_no_url(self) -> None:
        fake = FakeFal({"sync-lipsync": {"unexpected": True}})
        with self.assertRaises(ProviderError):
            postprocess.lipsync(fake, "https://f/clip.mp4", "https://f/line.mp3")


class UpscaleTest(unittest.TestCase):
    def test_upscale_calls_model_and_returns_video_url(self) -> None:
        fake = FakeFal({"topaz": {"video": {"url": "https://f/up.mp4"}}})
        url = postprocess.upscale(fake, "https://f/clip.mp4", factor=2, budget_s=5)
        self.assertEqual(url, "https://f/up.mp4")
        model_id, payload = fake.calls[0]
        self.assertEqual(model_id, postprocess.UPSCALE_MODEL)
        self.assertEqual(model_id, "fal-ai/topaz/upscale/video")
        self.assertEqual(payload, {"video_url": "https://f/clip.mp4",
                                   "upscale_factor": 2})

    def test_upscale_default_factor_is_2(self) -> None:
        fake = FakeFal({"topaz": {"video": {"url": "https://f/up.mp4"}}})
        postprocess.upscale(fake, "https://f/clip.mp4")
        _, payload = fake.calls[0]
        self.assertEqual(payload["upscale_factor"], 2)

    def test_upscale_raises_when_no_url(self) -> None:
        fake = FakeFal({"topaz": {}})
        with self.assertRaises(ProviderError):
            postprocess.upscale(fake, "https://f/clip.mp4")


class EnvGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = {k: os.environ.get(k)
                      for k in ("FAL_KEY", "VIDEO_LIPSYNC", "VIDEO_UPSCALE")}

    def tearDown(self) -> None:
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _set(self, **env) -> None:
        for k in ("FAL_KEY", "VIDEO_LIPSYNC", "VIDEO_UPSCALE"):
            os.environ.pop(k, None)
        for k, v in env.items():
            os.environ[k] = v

    def test_default_on_when_fal_key_present(self) -> None:
        self._set(FAL_KEY="k")
        self.assertTrue(postprocess.lipsync_enabled())
        self.assertTrue(postprocess.upscale_enabled())

    def test_off_when_no_fal_key(self) -> None:
        self._set()  # no FAL_KEY
        self.assertFalse(postprocess.lipsync_enabled())
        self.assertFalse(postprocess.upscale_enabled())

    def test_lipsync_off_switch(self) -> None:
        for value in ("off", "0", "false", "no", "OFF"):
            self._set(FAL_KEY="k", VIDEO_LIPSYNC=value)
            self.assertFalse(postprocess.lipsync_enabled(), value)
            self.assertTrue(postprocess.upscale_enabled())  # independent

    def test_upscale_off_switch(self) -> None:
        for value in ("off", "0", "false", "no", "OFF"):
            self._set(FAL_KEY="k", VIDEO_UPSCALE=value)
            self.assertFalse(postprocess.upscale_enabled(), value)
            self.assertTrue(postprocess.lipsync_enabled())  # independent


if __name__ == "__main__":
    unittest.main()
