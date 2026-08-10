"""Idempotent regeneration: a no-op re-run performs ZERO writes.

The driver's review rounds re-invoke the generator after every build; without
the short-circuit each round rewrites byte-identical artifacts, moves mtimes,
and re-fires artifact_changed for the whole episode. These tests pin the
contract: unchanged fingerprint + provider + artifacts on disk → the prior
result comes back (flagged ``unchanged``) and no file is touched.
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramapy.generation import generate_episode  # noqa: E402

SERIES = textwrap.dedent(
    """
    SERIES = {"title": "Idem", "genre": "revenge", "style": "photoreal-drama",
              "aspect": "9:16", "resolution": (540, 960), "fps": 24, "language": "en"}
    CAST = [{"id": "mei", "name": "Mei", "look": "sharp bob", "voice": "f_low",
             "ref_images": []}]
    """
).strip()

EPISODE = textwrap.dedent(
    """
    def gen_episode():
        return {"episode": {
            "number": 1, "title": "Idem Ep", "hook_max_s": 5.0,
            "scenes": [{"id": "s1", "location": "lobby", "shots": [
                {"id": "a", "kind": "establish", "duration_s": 3, "prompt": "doors"},
                {"id": "b", "kind": "dialogue", "duration_s": 3, "cast": ["mei"],
                 "line": "Still here.", "prompt": "close"},
            ]}],
            "cliffhanger": "freeze", "bgm": None, "burn_subtitles": True,
        }}
    """
).strip()


def _mtimes(base: Path) -> dict[str, float]:
    out = {}
    for p in sorted(base.rglob("*")):
        if p.is_file() and ".video" not in p.parts and "__pycache__" not in p.parts:
            out[str(p.relative_to(base))] = p.stat().st_mtime_ns
    return out


class IdempotentRegenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dramapy-idem-"))
        self.proj = self.tmp / "proj"
        (self.proj / "episodes").mkdir(parents=True)
        (self.proj / "series.py").write_text(SERIES)
        self.src = self.proj / "episodes" / "ep001.py"
        self.src.write_text(EPISODE)
        self.out = self.proj / "episodes" / "ep001.mp4"
        os.environ.pop("VIDEO_FORCE_REGEN", None)

    def test_second_run_is_zero_write_and_flagged(self):
        first = generate_episode(self.src, self.out)
        self.assertNotIn("unchanged", first)
        before = _mtimes(self.proj)
        second = generate_episode(self.src, self.out)
        after = _mtimes(self.proj)
        self.assertTrue(second.get("unchanged"))
        self.assertEqual(before, after, "no-op re-run must not touch any file")
        self.assertEqual(
            {s["status"] for s in second["shots"]}, {"cached"}
        )
        self.assertEqual(second["shot_count"], first["shot_count"])
        self.assertAlmostEqual(
            second["duration_s"], first["duration_s"], places=3
        )
        self.assertTrue(str(second["srt_path"]).endswith("ep001.srt"))

    def test_force_regen_overrides(self):
        generate_episode(self.src, self.out)
        before = _mtimes(self.proj)
        os.environ["VIDEO_FORCE_REGEN"] = "1"
        try:
            result = generate_episode(self.src, self.out)
        finally:
            os.environ.pop("VIDEO_FORCE_REGEN", None)
        self.assertNotIn("unchanged", result)
        self.assertNotEqual(before, _mtimes(self.proj))

    def test_missing_artifact_forces_full_run(self):
        generate_episode(self.src, self.out)
        self.out.unlink()
        result = generate_episode(self.src, self.out)
        self.assertNotIn("unchanged", result)
        self.assertTrue(self.out.exists())

    def test_missing_shot_in_sidecar_forces_full_run(self):
        """A prior sidecar listing FEWER shots than the episode defines (a shot
        failed/was skipped last run) must NOT short-circuit — the episode is
        incomplete. Regression: this silently no-op'd a re-run after partial
        failures, because every listed shot existed so the check passed."""
        import json

        generate_episode(self.src, self.out)
        sidecar = self.out.parent / "ep001.episode.json"
        meta = json.loads(sidecar.read_text())
        meta["shots"] = meta["shots"][:-1]  # simulate one shot missing/failed
        sidecar.write_text(json.dumps(meta))
        result = generate_episode(self.src, self.out)
        self.assertNotIn("unchanged", result)

    def test_source_edit_forces_full_run(self):
        generate_episode(self.src, self.out)
        (self.proj / "series.py").write_text(
            SERIES.replace("sharp bob", "silver hair")
        )
        result = generate_episode(self.src, self.out)
        self.assertNotIn("unchanged", result)

    def test_provider_model_change_forces_full_run(self):
        """A provider model bump (animatic-1 → animatic-2, when the mock became
        silent) must flip the short-circuit so pre-voice episodes re-render on
        the next run."""
        import json

        generate_episode(self.src, self.out)
        sidecar = self.out.parent / "ep001.episode.json"
        prior = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(
            {"name": "mock", "model": "animatic-2"}, prior["provider"]
        )
        # Simulate an episode rendered by the pre-voice (animatic-1) mock.
        prior["provider"]["model"] = "animatic-1"
        sidecar.write_text(
            json.dumps(prior, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        result = generate_episode(self.src, self.out)
        self.assertNotIn("unchanged", result, "model drift must re-run")
        rewritten = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual("animatic-2", rewritten["provider"]["model"])


if __name__ == "__main__":
    unittest.main()
