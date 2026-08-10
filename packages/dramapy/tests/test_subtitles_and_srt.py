""".srt content and timing (contract §1: written when any dialogue, timed
to the shots' positions in the stitched episode)."""

from __future__ import annotations

import re
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
from dramapy.generation import generate_episode  # noqa: E402
from dramapy.subtitles import (  # noqa: E402
    build_entries,
    format_srt,
    format_timestamp,
)

TIMESTAMP_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def _seconds(hh: str, mm: str, ss: str, ms: str) -> float:
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


class SrtUnitTests(unittest.TestCase):
    def test_format_timestamp(self) -> None:
        self.assertEqual("00:00:00,000", format_timestamp(0.0))
        self.assertEqual("00:00:03,250", format_timestamp(3.25))
        self.assertEqual("01:02:03,004", format_timestamp(3723.004))

    def test_build_entries_skips_non_dialogue_and_wraps(self) -> None:
        long_line = "a" * 100
        entries = build_entries(
            [
                ("s1", None, 0.0, 3.0),
                ("s2", "Hello there.", 3.0, 4.0),
                ("s3", long_line, 7.0, 3.0),
            ]
        )
        self.assertEqual(2, len(entries))
        self.assertEqual(1, entries[0].index)
        self.assertEqual("s2", entries[0].shot_id)
        self.assertEqual(3.0, entries[0].start_s)
        self.assertEqual(7.0, entries[0].end_s)
        self.assertFalse(entries[0].overrun)
        self.assertTrue(entries[1].overrun, "100 chars needs 3 rows at 42 cols")
        self.assertEqual(3, len(entries[1].lines))

    def test_format_srt_blocks(self) -> None:
        entries = build_entries([("s2", "Hello.", 1.5, 2.0)])
        text = format_srt(entries)
        self.assertEqual(
            "1\n00:00:01,500 --> 00:00:03,500\nHello.\n",
            text,
        )


class SrtE2ETests(ProviderEnvGuard, unittest.TestCase):
    def test_srt_timed_to_stitched_shots(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dramapy-srt-") as tempdir:
            root = Path(tempdir)
            write_series(root)
            script = write_episode(
                root,
                {
                    "episode": episode_dict(
                        [
                            shot("open", "establish", 3, prompt="street"),
                            shot("d1", "dialogue", 4, cast=["li_wei"],
                                 line="You never told me he was alive."),
                            shot("d2", "dialogue", 3, cast=["dorian"],
                                 line="You never asked."),
                        ],
                        cliffhanger="the phone rings",
                    )
                },
            )
            result = generate_episode(script, root / "episodes" / "ep001.mp4")
            srt_path = root / "episodes" / "ep001.srt"
            self.assertEqual(str(srt_path.resolve()), result["srt_path"])

            blocks = srt_path.read_text(encoding="utf-8").strip().split("\n\n")
            self.assertEqual(2, len(blocks))

            first_lines = blocks[0].split("\n")
            self.assertEqual("1", first_lines[0])
            match = TIMESTAMP_RE.match(first_lines[1])
            assert match is not None
            start = _seconds(*match.groups()[:4])
            end = _seconds(*match.groups()[4:])
            self.assertAlmostEqual(3.0, start, delta=0.3)  # after the establish
            self.assertAlmostEqual(7.0, end, delta=0.4)
            self.assertEqual("You never told me he was alive.", first_lines[2])

            second_lines = blocks[1].split("\n")
            self.assertEqual("2", second_lines[0])
            match2 = TIMESTAMP_RE.match(second_lines[1])
            assert match2 is not None
            self.assertAlmostEqual(7.0, _seconds(*match2.groups()[:4]), delta=0.4)
            self.assertEqual("You never asked.", second_lines[2])

    def test_no_srt_without_dialogue(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dramapy-nosrt-") as tempdir:
            root = Path(tempdir)
            write_series(root)
            script = write_episode(
                root,
                {
                    "episode": episode_dict(
                        [shot("only", "establish", 3, prompt="skyline")],
                        cliffhanger="a hand grabs the rail",
                    )
                },
            )
            result = generate_episode(script, root / "episodes" / "ep001.mp4")
            self.assertNotIn("srt_path", result)
            self.assertFalse((root / "episodes" / "ep001.srt").exists())


if __name__ == "__main__":
    unittest.main()
