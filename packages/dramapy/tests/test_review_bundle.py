"""Tests for ``dramapy.review_bundle`` — the evidence pack the screening-room
critic reads.

Renders a real episode with the mock provider (no network), then:

* asserts frame sampling writes files into ``<stem>_review/frames/`` and the
  manifest gathers board / poster / metadata / warnings / audio stats;
* corrupts one shot with ffmpeg ``transpose`` (dimension swap) and one with a
  display-matrix rotation, and asserts BOTH are flagged as
  ``orientation_aspect`` defects — this is the live rotation bug the critic is
  the safety net for;
* stretches one shot's clip and asserts a ``duration_drift`` defect;
* proves the builder never raises fatally on a bad/absent episode.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
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
from dramapy import media  # noqa: E402
from dramapy import review_bundle  # noqa: E402
from dramapy.generation import generate_episode  # noqa: E402


def _render(project_root: Path) -> Path:
    """Render a 4-shot, dialogue-free episode with the mock provider so the
    clean baseline carries no audio-related defects. Returns the .mp4 path."""
    write_series(project_root)
    envelope = {
        "episode": episode_dict(
            [
                shot("s1_01", "establish", 3.0, prompt="wide, rain-slick lobby"),
                shot("s1_02", "action", 3.0, prompt="he turns from the window"),
                shot("s1_03", "action", 3.0, prompt="she steps forward, slow"),
                shot("s1_04", "insert", 2.0, prompt="a letter, burning"),
            ],
            burn_subtitles=False,
        ),
    }
    write_episode(project_root, envelope, name="ep001")
    out = project_root / "episodes" / "ep001.mp4"
    generate_episode(project_root / "episodes" / "ep001.py", out, provider="mock")
    return out


def _transpose_clip(path: Path) -> None:
    """Overwrite a clip with a transposed (dimension-swapped) copy — a
    landscape clip in a portrait series."""
    tmp = path.with_suffix(".swap.mp4")
    media.run_ffmpeg(
        ["-y", "-i", str(path), "-vf", "transpose=1", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(tmp)]
    )
    tmp.replace(path)


def _rotate_metadata(path: Path) -> None:
    """Overwrite a clip with a display-matrix rotation (dims unchanged) — the
    exact shape of the real rotation bug (a portrait clip flagged 90°)."""
    tmp = path.with_suffix(".rot.mp4")
    media.run_ffmpeg(
        ["-y", "-display_rotation", "90", "-i", str(path), "-c", "copy", str(tmp)]
    )
    tmp.replace(path)


def _stretch_clip(path: Path, seconds: float) -> None:
    """Overwrite a clip with one of a very different duration (same size)."""
    info = media.probe_media(path)
    w, h = info.width or 270, info.height or 480
    tmp = path.with_suffix(".long.mp4")
    media.run_ffmpeg(
        ["-y", "-f", "lavfi", "-i", f"color=c=0x223344:s={w}x{h}:r=24:d={seconds:.3f}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", f"{seconds:.3f}", str(tmp)]
    )
    tmp.replace(path)


class ReviewBundleTest(ProviderEnvGuard, unittest.TestCase):
    def test_clean_bundle_has_frames_board_poster_and_no_defects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            mp4 = _render(root)
            bundle = review_bundle.build_review_bundle(mp4, frames_per_shot=3)

            self.assertTrue(bundle["ok"], bundle)
            self.assertEqual(bundle["stem"], "ep001")
            self.assertEqual(len(bundle["shots"]), 4)

            # ~3 frames per shot, actually written to disk, under _review/frames/.
            self.assertGreaterEqual(len(bundle["frames"]), 4 * 3 - 1)
            for frame in bundle["frames"]:
                self.assertIn("shot_id", frame)
                self.assertIn("t", frame)
                self.assertTrue(Path(frame["path"]).is_file(), frame)
                self.assertIn("_review/frames", frame["path"].replace("\\", "/"))

            self.assertTrue(Path(bundle["board"]).is_file())
            self.assertTrue(Path(bundle["poster"]).is_file())
            self.assertEqual(bundle["metadata"]["generator"], "dramapy")
            self.assertIn("has_audio", bundle["audio_stats"])
            self.assertFalse(bundle["audio_stats"]["voice_expected"])  # no dialogue

            # A well-rendered, dialogue-free mock episode has no technical defects.
            self.assertEqual(bundle["defects"], [], bundle["defects"])

    def test_flags_transposed_and_rotated_clips_as_orientation_defects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            mp4 = _render(root)
            shots_dir = root / "episodes" / "ep001_shots"
            _transpose_clip(shots_dir / "shot_s1_02.mp4")   # dimension swap
            _rotate_metadata(shots_dir / "shot_s1_03.mp4")  # display matrix rotation
            bundle = review_bundle.build_review_bundle(mp4, frames_per_shot=2)

        orient = [d for d in bundle["defects"] if d["kind"] == "orientation_aspect"]
        flagged = {d["shot_id"] for d in orient}
        self.assertIn("s1_02", flagged, bundle["defects"])
        self.assertIn("s1_03", flagged, bundle["defects"])
        self.assertTrue(all(d["severity"] == "blocker" for d in orient))
        # The rotated shot's probe recorded the rotation.
        s3 = next(s for s in bundle["shots"] if s["shot_id"] == "s1_03")
        self.assertEqual(s3["rotation"] % 180, 90)

    def test_flags_duration_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            mp4 = _render(root)
            # s1_01 spec is 3s; stretch its clip to 8s → ~166% drift.
            _stretch_clip(root / "episodes" / "ep001_shots" / "shot_s1_01.mp4", 8.0)
            bundle = review_bundle.build_review_bundle(mp4, frames_per_shot=2)

        drift = [d for d in bundle["defects"] if d["kind"] == "duration_drift"]
        self.assertTrue(any(d["shot_id"] == "s1_01" for d in drift), bundle["defects"])

    def test_never_raises_fatally_on_bad_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = review_bundle.build_review_bundle(Path(tmp) / "nope.mp4")
            self.assertFalse(missing["ok"])
            self.assertIn("frames", missing)  # shape stays intact

            # A sidecar that points at clips which do not exist → missing_shot
            # defects, still no exception.
            base = Path(tmp)
            (base / "ep002.episode.json").write_text(
                '{"generator":"dramapy","episode":{"path":"ep002.mp4",'
                '"resolution":[270,480]},"shots":[{"id":"s1","path":'
                '"ep002_shots/shot_s1.mp4","jsonPath":"ep002_shots/shot_s1.json",'
                '"status":"failed"}],"validation":{"warnings":[]}}',
                encoding="utf-8",
            )
            bundle = review_bundle.build_review_bundle(base / "ep002.episode.json")
            self.assertTrue(bundle["ok"])
            self.assertTrue(any(d["kind"] == "missing_shot" for d in bundle["defects"]))


if __name__ == "__main__":
    unittest.main()
