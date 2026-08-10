"""End-to-end test for ``dramapy.generation.generate_episode`` — the §1
entry point the dramacode skill runner calls. Mirrors production: a real
project dir with ``series.py`` + ``episodes/ep001.py`` (dataclass bible,
per the contract example), the mock provider, ffprobe as the cheap probe.

Verifies the full §1 artifact set, the camelCase canonical-JSON sidecar,
the sidecar-before-mp4 ordering rule, and the snake_case return dict
mirroring the DramacodeResult success fields."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramaproj import ProviderEnvGuard  # noqa: E402
from dramapy import media  # noqa: E402
from dramapy.generation import generate_episode  # noqa: E402
from dramapy.source_hash import episode_source_hash  # noqa: E402


def _mean_volume_db(path: Path) -> float:
    """ffmpeg volumedetect mean_volume (dB) for a media file's audio track."""
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

SERIES_PY = '''
from dramapy.bible import Series, Character

SERIES = Series(
    title="Contract Bride of the Chaebol Heir",
    genre="revenge-romance",
    style="photoreal-drama",
    aspect="9:16", resolution=(1080, 1920), fps=24,
    language="en",
)
CAST = [
    Character(id="li_wei", name="Li Wei", look="woman, 28, sharp bob, gray suit",
              voice="f_low_calm", ref_images=[]),
    Character(id="dorian", name="Dorian Cross", look="man, 34, black coat",
              voice="m_deep_cold", ref_images=[]),
]
'''

EPISODE_PY = '''
import series
from dramapy.spec import Episode, Scene, Shot

def gen_episode():
    return {
        "episode": Episode(
            number=1, title="The Slap", hook_max_s=5.0,
            scenes=[
                Scene(id="s1", location="penthouse lobby, night, rain", shots=[
                    Shot(id="s1_01", kind="establish", duration_s=3,
                         prompt="rain-slick lobby doors, neon reflections"),
                    Shot(id="s1_02", kind="dialogue", duration_s=4, cast=["li_wei"],
                         line="You never told me he was alive.", emotion="dread",
                         prompt="close-up, trembling, letter in hand"),
                    Shot(id="s1_03", kind="action", duration_s=3, cast=["dorian"],
                         prompt="he turns from the window, slow, backlit"),
                ]),
            ],
            cliffhanger="freeze on the burning letter",
            bgm="tense-strings",
            burn_subtitles=True,
        ),
    }
'''


class GenerateEpisodeE2ETests(ProviderEnvGuard, unittest.TestCase):
    def test_full_artifact_set_at_series_resolution(self) -> None:
        # This is the ONE e2e test that exercises the real voice layer: flip
        # VIDEO_VOICES back on (conftest defaults it off for speed) so a normal
        # render bakes spoken dialogue and produces zero silent_dialogue
        # warnings when `say` is available.
        saved_voices = os.environ.get("VIDEO_VOICES")
        os.environ["VIDEO_VOICES"] = "on"
        if saved_voices is None:
            self.addCleanup(lambda: os.environ.pop("VIDEO_VOICES", None))
        else:
            self.addCleanup(lambda: os.environ.__setitem__("VIDEO_VOICES", saved_voices))
        say_available = shutil.which("say") is not None

        with tempfile.TemporaryDirectory(prefix="dramapy-e2e-") as tempdir:
            root = Path(tempdir)
            (root / "series.py").write_text(SERIES_PY.lstrip(), encoding="utf-8")
            episodes = root / "episodes"
            episodes.mkdir()
            (episodes / "ep001.py").write_text(EPISODE_PY.lstrip(), encoding="utf-8")

            output_path = episodes / "ep001.mp4"
            result = generate_episode(episodes / "ep001.py", output_path)

            # -- Artifact set (contract §1 table). --------------------------
            sidecar_path = episodes / "ep001.episode.json"
            srt_path = episodes / "ep001.srt"
            shots_dir = episodes / "ep001_shots"
            review_dir = episodes / "ep001_review"
            self.assertTrue(output_path.is_file(), "episode mp4 missing")
            self.assertTrue(sidecar_path.is_file(), "episode.json missing")
            self.assertTrue(srt_path.is_file(), "srt missing (episode has dialogue)")
            for shot_id in ("s1_01", "s1_02", "s1_03"):
                self.assertTrue((shots_dir / f"shot_{shot_id}.mp4").is_file())
                self.assertTrue((shots_dir / f"shot_{shot_id}.json").is_file())
            self.assertTrue((review_dir / "_poster.png").is_file())
            self.assertTrue((review_dir / "_board.png").is_file())

            # Ordering rule: sidecar readable before the final mp4 appears.
            self.assertLessEqual(
                sidecar_path.stat().st_mtime_ns,
                output_path.stat().st_mtime_ns,
                "sidecar must be written before the final mp4",
            )

            # -- ffprobe the episode: resolution, fps, audio, duration. -----
            probe = media.probe_media(output_path)
            self.assertEqual((1080, 1920), (probe.width, probe.height))
            self.assertEqual(24.0, probe.fps)
            self.assertTrue(probe.has_audio, "episode must carry an audio track")
            self.assertAlmostEqual(10.0, probe.duration_s, delta=0.6)
            # Episode audio carries speech energy when `say` is present (voices
            # are assembled at stitch, not baked per-shot).
            if say_available:
                self.assertGreater(
                    _mean_volume_db(output_path), -50.0,
                    "episode audio should carry spoken dialogue, not silence",
                )

            # Every mock shot clip is SILENT now — assembly owns audio.
            dialogue_probe = media.probe_media(shots_dir / "shot_s1_02.mp4")
            self.assertFalse(dialogue_probe.has_audio, "mock clips are silent")
            self.assertAlmostEqual(4.0, dialogue_probe.duration_s, delta=0.25)
            establish_probe = media.probe_media(shots_dir / "shot_s1_01.mp4")
            self.assertFalse(establish_probe.has_audio)
            self.assertEqual((1080, 1920), (establish_probe.width, establish_probe.height))

            # Review images: poster at clip resolution, board = 2x2 grid of
            # 240x426 tiles (+8 padding, +12 margins).
            poster_probe = media.probe_media(review_dir / "_poster.png")
            self.assertEqual((1080, 1920), (poster_probe.width, poster_probe.height))
            board_probe = media.probe_media(review_dir / "_board.png")
            self.assertEqual((512, 884), (board_probe.width, board_probe.height))

            # -- Sidecar: canonical bytes + camelCase schema. -----------------
            sidecar_text = sidecar_path.read_text(encoding="utf-8")
            sidecar = json.loads(sidecar_text)
            self.assertEqual(
                json.dumps(sidecar, sort_keys=True, separators=(",", ":")),
                sidecar_text,
                "sidecar must be canonical JSON (sort_keys, compact separators)",
            )
            self.assertEqual("dramapy", sidecar["generator"])
            self.assertEqual("episode", sidecar["entryKind"])
            self.assertEqual("python", sidecar["source"]["kind"])
            self.assertEqual("episodes/ep001.py", sidecar["source"]["path"])
            self.assertRegex(sidecar["source"]["hash"], r"^[0-9a-f]{64}$")
            self.assertRegex(sidecar["source"]["fingerprint"], r"^[0-9a-f]{64}$")
            identity = episode_source_hash(episodes / "ep001.py", root)
            self.assertEqual(identity.source_fingerprint, sidecar["source"]["fingerprint"])
            self.assertEqual("ep001.mp4", sidecar["episode"]["path"])
            self.assertEqual(1, sidecar["episode"]["number"])
            self.assertEqual("The Slap", sidecar["episode"]["title"])
            self.assertEqual(24, sidecar["episode"]["fps"])
            self.assertEqual([1080, 1920], sidecar["episode"]["resolution"])
            self.assertAlmostEqual(10.0, sidecar["episode"]["durationS"], delta=0.6)
            self.assertEqual({"name": "mock", "model": "animatic-2"}, sidecar["provider"])
            self.assertEqual(3, sidecar["validation"]["shotCount"])
            if say_available:
                self.assertNotIn(
                    "warnings",
                    sidecar["validation"],
                    "voiced episode must omit validation.warnings entirely",
                )
            else:
                # No `say` → dialogue is unvoiced → silent_dialogue only.
                self.assertEqual(
                    {"silent_dialogue"},
                    {w["kind"] for w in sidecar["validation"].get("warnings", [])},
                )
            # shots[] entries: {id, path, jsonPath, durationS, status}
            # (durationS/status added by the 2026-08-09 contract change).
            self.assertEqual(3, len(sidecar["shots"]))
            expected_durations = {"s1_01": 3.0, "s1_02": 4.0, "s1_03": 3.0}
            for entry in sidecar["shots"]:
                shot_id = entry["id"]
                self.assertEqual(
                    {"id", "path", "jsonPath", "durationS", "status"},
                    set(entry),
                )
                self.assertEqual(f"ep001_shots/shot_{shot_id}.mp4", entry["path"])
                self.assertEqual(f"ep001_shots/shot_{shot_id}.json", entry["jsonPath"])
                self.assertEqual("rendered", entry["status"])
                self.assertAlmostEqual(
                    expected_durations[shot_id], entry["durationS"], delta=0.25
                )
            self.assertEqual(
                ["s1_01", "s1_02", "s1_03"],
                [entry["id"] for entry in sidecar["shots"]],
            )

            # series.json (2026-08-09 contract change): the bible surfaced
            # for the cast panel, canonical JSON, at the project root.
            series_json_path = root / "series.json"
            self.assertTrue(series_json_path.is_file(), "series.json missing")
            series_json_text = series_json_path.read_text(encoding="utf-8")
            series_json = json.loads(series_json_text)
            self.assertEqual(
                json.dumps(series_json, sort_keys=True, separators=(",", ":")),
                series_json_text,
                "series.json must be canonical JSON",
            )
            self.assertEqual(
                {
                    "title": "Contract Bride of the Chaebol Heir",
                    "genre": "revenge-romance",
                    "style": "photoreal-drama",
                    "aspect": "9:16",
                    "fps": 24,
                    "cast": [
                        {"id": "li_wei", "name": "Li Wei",
                         "look": "woman, 28, sharp bob, gray suit",
                         "voice": "f_low_calm", "ref_images": []},
                        {"id": "dorian", "name": "Dorian Cross",
                         "look": "man, 34, black coat",
                         "voice": "m_deep_cold", "ref_images": []},
                    ],
                },
                series_json,
            )

            # Per-shot sidecar: canonical, carries prompt/cast/provider/renderMs/draws.
            shot_sidecar_text = (shots_dir / "shot_s1_02.json").read_text(encoding="utf-8")
            shot_sidecar = json.loads(shot_sidecar_text)
            self.assertEqual(
                json.dumps(shot_sidecar, sort_keys=True, separators=(",", ":")),
                shot_sidecar_text,
            )
            self.assertEqual("s1_02", shot_sidecar["id"])
            self.assertEqual(["li_wei"], shot_sidecar["cast"])
            self.assertEqual("close-up, trembling, letter in hand", shot_sidecar["prompt"])
            self.assertEqual({"name": "mock", "model": "animatic-2"}, shot_sidecar["provider"])
            self.assertEqual("rendered", shot_sidecar["status"])
            self.assertEqual(1, shot_sidecar["draws"])
            self.assertGreaterEqual(shot_sidecar["renderMs"], 0)
            self.assertEqual("You never told me he was alive.", shot_sidecar["line"])

            # -- Return dict: snake_case DramacodeResult success fields. -----
            self.assertEqual(str(output_path.resolve()), result["episode_path"])
            self.assertEqual(str(sidecar_path.resolve()), result["metadata_path"])
            self.assertEqual(str(srt_path.resolve()), result["srt_path"])
            self.assertEqual(3, result["shot_count"])
            self.assertAlmostEqual(10.0, result["duration_s"], delta=0.6)
            if say_available:
                self.assertEqual([], result["warnings"])
            else:
                self.assertEqual(
                    {"silent_dialogue"},
                    {w["kind"] for w in result["warnings"]},
                )
            self.assertEqual(
                ["rendered", "rendered", "rendered"],
                [entry["status"] for entry in result["shots"]],
            )
            for entry in result["shots"]:
                self.assertTrue(Path(entry["path"]).is_file())
                self.assertGreaterEqual(entry["duration_s"], 2.5)

            # -- .srt content sanity (full timing coverage lives in
            # test_subtitles_and_srt.py). --------------------------------------
            srt_text = srt_path.read_text(encoding="utf-8")
            self.assertIn("You never told me he was alive.", srt_text)
            self.assertRegex(
                srt_text, r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}"
            )
            match = re.search(r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) -->", srt_text)
            assert match is not None
            start_s = int(match.group(2)) * 60 + int(match.group(3)) + int(match.group(4)) / 1000
            self.assertAlmostEqual(3.0, start_s, delta=0.4)


if __name__ == "__main__":
    unittest.main()
