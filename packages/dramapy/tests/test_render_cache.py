"""Render cache + source fingerprint behavior (contract §1).

Second run of an identical episode: every shot status ``"cached"`` and the
run is faster. Editing ``series.py`` shifts ``source.fingerprint`` (the
whole-graph hash folds the bible) while a comment-only edit leaves the
render-cache keys — and therefore the cache hits — intact."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramaproj import (  # noqa: E402
    ProviderEnvGuard,
    episode_dict,
    shot,
    write_episode,
    write_series,
)
from dramapy.generation import generate_episode  # noqa: E402
from dramapy.render_cache import render_cache_dir, shot_cache_key  # noqa: E402
from dramapy.spec import ResolvedSeries, ResolvedShot  # noqa: E402


class CacheKeyTests(unittest.TestCase):
    """Scene id and provider salt determine pixels/audio for the animatic
    mock (per-scene palette; TTS mode + voice-map version), so they must
    fold into the key (2026-08-09 animatic change)."""

    SHOT = ResolvedShot(
        id="k1", kind="dialogue", duration_s=3.0, prompt="close",
        cast=("mei",), line="Run.", emotion=None,
    )
    SERIES = ResolvedSeries(
        title="T", genre="revenge", style="photoreal-drama", aspect="9:16",
        resolution=(270, 480), fps=24, language="en",
    )

    def _key(self, **overrides: str) -> str:
        kwargs = dict(
            shot=self.SHOT,
            series=self.SERIES,
            provider_name="mock",
            provider_model="animatic-2",
            scene_id="s1",
            provider_salt="v2",
        )
        kwargs.update(overrides)
        return shot_cache_key(**kwargs)

    def test_key_is_deterministic(self) -> None:
        self.assertEqual(self._key(), self._key())

    def test_scene_id_changes_key(self) -> None:
        self.assertNotEqual(self._key(), self._key(scene_id="s2"))

    def test_provider_salt_changes_key(self) -> None:
        self.assertNotEqual(
            self._key(), self._key(provider_salt="tts=beep:voices=v1")
        )

    def test_model_changes_key(self) -> None:
        self.assertNotEqual(
            self._key(), self._key(provider_model="lavfi-mock")
        )


class RenderCacheTests(ProviderEnvGuard, unittest.TestCase):
    def test_second_run_hits_cache_and_fingerprint_tracks_series(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dramapy-cache-") as tempdir:
            root = Path(tempdir)
            write_series(root)
            script = write_episode(
                root,
                {
                    "episode": episode_dict(
                        [
                            shot("c1", "establish", 3, prompt="street"),
                            shot("c2", "dialogue", 3, cast=["li_wei"], line="Run."),
                            shot("c3", "action", 3, prompt="sprint"),
                        ],
                        cliffhanger="she stops dead",
                    )
                },
            )
            out = root / "episodes" / "ep001.mp4"
            sidecar = root / "episodes" / "ep001.episode.json"

            started = time.monotonic()
            first = generate_episode(script, out)
            first_elapsed = time.monotonic() - started
            self.assertEqual(
                ["rendered"] * 3, [entry["status"] for entry in first["shots"]]
            )
            cache_files = sorted(render_cache_dir(root).glob("*.mp4"))
            self.assertEqual(3, len(cache_files), "every rendered shot is cached")
            fingerprint_1 = json.loads(sidecar.read_text(encoding="utf-8"))["source"][
                "fingerprint"
            ]

            started = time.monotonic()
            second = generate_episode(script, out)
            second_elapsed = time.monotonic() - started
            self.assertEqual(
                ["cached"] * 3, [entry["status"] for entry in second["shots"]]
            )
            self.assertLess(
                second_elapsed,
                first_elapsed,
                f"cached run must be faster (first={first_elapsed:.2f}s, "
                f"second={second_elapsed:.2f}s)",
            )

            # Comment-only series edit: fingerprint moves, cache still hits
            # (the render key hashes content that didn't change).
            series_path = root / "series.py"
            series_path.write_text(
                series_path.read_text(encoding="utf-8") + "\n# bible v2 note\n",
                encoding="utf-8",
            )
            third = generate_episode(script, out)
            fingerprint_3 = json.loads(sidecar.read_text(encoding="utf-8"))["source"][
                "fingerprint"
            ]
            self.assertNotEqual(
                fingerprint_1,
                fingerprint_3,
                "editing series.py must invalidate the episode fingerprint",
            )
            self.assertEqual(
                ["cached"] * 3, [entry["status"] for entry in third["shots"]]
            )

            # A *meaningful* bible edit (style change) shifts the render keys:
            # everything re-renders.
            write_series(
                root,
                series={
                    "title": "Contract Bride",
                    "genre": "revenge-romance",
                    "style": "anime",
                    "aspect": "9:16",
                    "resolution": (270, 480),
                    "fps": 24,
                    "language": "en",
                },
            )
            fourth = generate_episode(script, out)
            self.assertEqual(
                ["rendered"] * 3, [entry["status"] for entry in fourth["shots"]]
            )
            self.assertEqual(6, len(sorted(render_cache_dir(root).glob("*.mp4"))))


if __name__ == "__main__":
    unittest.main()
