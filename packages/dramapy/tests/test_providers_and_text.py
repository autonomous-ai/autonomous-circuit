"""Mock-provider (silent animatic) determinism — seeded motion, per-scene
palettes, the dialogue text row — plus the drawtext escaping helper and the
stdlib PNG rasterizer. The voice mapping and spoken-audio layer moved out of
the provider to :mod:`dramapy.voices` (tested in test_voices.py); the mock now
renders silent video."""

from __future__ import annotations

import sys
import tempfile
import unittest
import zlib
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramapy import media  # noqa: E402
from dramapy.providers import make_provider  # noqa: E402
from dramapy.providers.base import ShotContext  # noqa: E402
from dramapy.providers.mock import (  # noqa: E402
    CACHE_SALT,
    DEFAULT_PALETTE,
    MODEL,
    MockProvider,
    dialogue_rows,
    gradient_source,
    motion_filter,
    scene_palette,
    shot_seed,
    style_palette,
)
from dramapy.text import GLYPH_W  # noqa: E402
from dramapy.spec import (  # noqa: E402
    ResolvedCharacter,
    ResolvedSeries,
    ResolvedShot,
)
from dramapy.text import escape_drawtext, render_text_png, wrap_subtitle  # noqa: E402


class MockDeterminismTests(unittest.TestCase):
    def test_shot_seed_is_stable(self) -> None:
        self.assertEqual(shot_seed("s1_01"), shot_seed("s1_01"))
        self.assertNotEqual(shot_seed("s1_01"), shot_seed("s1_02"))

    def test_gradient_source_is_deterministic_and_style_keyed(self) -> None:
        kwargs = dict(width=1080, height=1920, fps=24, duration_s=4.0)
        photoreal = gradient_source("s1_01", "photoreal-drama", **kwargs)
        self.assertEqual(photoreal, gradient_source("s1_01", "photoreal-drama", **kwargs))
        self.assertIn("0x232E38", photoreal)  # slate
        self.assertIn("0x10616F", photoreal)  # teal
        self.assertIn("s=1080x1920", photoreal)
        self.assertIn("r=24", photoreal)
        self.assertIn("d=4.000", photoreal)

        manhwa = gradient_source("s1_01", "manhwa", **kwargs)
        self.assertIn("0xE0A860", manhwa)  # warm
        anime = gradient_source("s1_01", "anime", **kwargs)
        self.assertIn("0x7C3AED", anime)  # violet
        self.assertEqual(DEFAULT_PALETTE, style_palette("claymation"))

    def test_registry_returns_mock(self) -> None:
        provider = make_provider("mock")
        self.assertIsInstance(provider, MockProvider)
        self.assertEqual("mock", provider.name)
        self.assertEqual("animatic-2", provider.model)
        self.assertEqual(MODEL, provider.model)

    def test_cache_salt_is_versioned_no_tts(self) -> None:
        # Audio left the clip (assembly owns voices), so the salt no longer
        # varies with a TTS mode — just a stable version bump.
        provider = make_provider("mock")
        self.assertEqual("v2", provider.cache_salt)
        self.assertEqual(CACHE_SALT, provider.cache_salt)

    def test_scene_palette_is_stable_and_scene_keyed(self) -> None:
        base = style_palette("photoreal-drama")
        self.assertEqual(base, scene_palette("photoreal-drama", None))
        self.assertEqual(base, scene_palette("photoreal-drama", ""))
        s1 = scene_palette("photoreal-drama", "s1")
        self.assertEqual(s1, scene_palette("photoreal-drama", "s1"))
        s2 = scene_palette("photoreal-drama", "s2")
        self.assertNotEqual(s1, s2, "scenes must look distinct")
        for c0, c1 in (s1, s2):
            for color in (c0, c1):
                self.assertRegex(color, r"^[0-9A-F]{6}$")

    def test_motion_filter_is_deterministic_and_kind_keyed(self) -> None:
        kwargs = dict(width=1080, height=1920, fps=24, duration_s=4.0)
        by_kind = {
            kind: motion_filter(kind, "s1_01", **kwargs)
            for kind in ("establish", "action", "dialogue", "insert")
        }
        for kind, expr in by_kind.items():
            self.assertEqual(expr, motion_filter(kind, "s1_01", **kwargs))
            self.assertIn("zoompan=", expr)
            self.assertIn("s=1080x1920", expr)
            self.assertIn("fps=24", expr)
        self.assertEqual(4, len(set(by_kind.values())), "kinds must differ")
        self.assertIn("0.15*in", by_kind["action"])  # faster push
        self.assertIn("sin(", by_kind["dialogue"])  # gentle drift
        self.assertIn("min(", by_kind["insert"])  # punch-in, then hold

    def test_dialogue_rows_fit_the_frame(self) -> None:
        line = "The board meets at nine. Decide before then, or I will."
        for width in (270, 540, 1080):
            rows, scale = dialogue_rows(line, width)
            self.assertLessEqual(len(rows), 3)
            self.assertTrue(all(rows))
            for row in rows:
                self.assertLessEqual(
                    len(row) * GLYPH_W * scale + 12,
                    width,
                    f"row overflows a {width}px frame at scale {scale}",
                )
        self.assertEqual(dialogue_rows(line, 1080), dialogue_rows(line, 1080))


class MockAnimaticRenderTests(unittest.TestCase):
    """Real renders at a tiny resolution — the animatic is SILENT video now
    (assembly owns audio), for every shot kind."""

    SERIES = ResolvedSeries(
        title="T", genre="revenge", style="photoreal-drama", aspect="9:16",
        resolution=(270, 480), fps=24, language="en",
    )
    CAST = (
        ResolvedCharacter(
            id="mei", name="Mei", look="sharp bob", voice="f_low_calm",
            ref_images=(),
        ),
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="dramapy-animatic-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _render(
        self, shot: ResolvedShot, *, scene_id: str = "s1", out_name: str | None = None
    ) -> Path:
        out = self.tmp / (out_name or f"shot_{shot.id}.mp4")
        MockProvider().render_shot(
            ShotContext(
                shot=shot, series=self.SERIES, characters=self.CAST,
                output_path=out, scene_id=scene_id,
            )
        )
        return out

    def test_dialogue_clip_is_silent_video(self) -> None:
        clip = self._render(
            ResolvedShot(
                id="d1", kind="dialogue", duration_s=2.0, prompt="close-up",
                cast=("mei",), line="You never told me he was alive.",
                emotion="dread",
            )
        )
        probe = media.probe_media(clip)
        self.assertTrue(probe.has_video)
        self.assertFalse(probe.has_audio, "assembly owns audio — clips are silent")
        self.assertAlmostEqual(2.0, probe.duration_s, delta=0.15)
        self.assertEqual((270, 480), (probe.width, probe.height))

    def test_non_dialogue_clip_is_silent_video(self) -> None:
        clip = self._render(
            ResolvedShot(
                id="e1", kind="establish", duration_s=2.0, prompt="wide",
                cast=(), line=None, emotion=None,
            )
        )
        probe = media.probe_media(clip)
        self.assertTrue(probe.has_video)
        self.assertFalse(probe.has_audio)
        self.assertAlmostEqual(2.0, probe.duration_s, delta=0.15)

    def test_render_is_deterministic_bytes(self) -> None:
        a = self._render(
            ResolvedShot(id="z1", kind="action", duration_s=2.0, prompt="run",
                         cast=(), line=None, emotion=None),
            out_name="a.mp4",
        )
        b = self._render(
            ResolvedShot(id="z1", kind="action", duration_s=2.0, prompt="run",
                         cast=(), line=None, emotion=None),
            out_name="b.mp4",
        )
        self.assertEqual(a.read_bytes(), b.read_bytes())


class DrawtextEscapingTests(unittest.TestCase):
    """Two-level escaping per the ffmpeg filtergraph docs — kept as the
    reference implementation for ffmpeg builds that have drawtext (the PATH
    build does not; dramapy composites rasterized PNGs instead)."""

    def test_plain_text_unchanged(self) -> None:
        self.assertEqual("hello world", escape_drawtext("hello world"))

    def test_colon(self) -> None:
        self.assertEqual("a\\\\:b", escape_drawtext("a:b"))

    def test_quote(self) -> None:
        self.assertEqual("it\\\\'s", escape_drawtext("it's"))

    def test_percent(self) -> None:
        self.assertEqual("100\\\\%", escape_drawtext("100%"))

    def test_backslash(self) -> None:
        self.assertEqual("a\\\\\\\\b", escape_drawtext("a\\b"))

    def test_graph_specials(self) -> None:
        self.assertEqual("\\[x\\]\\,y\\;z", escape_drawtext("[x],y;z"))

    def test_kitchen_sink(self) -> None:
        self.assertEqual(
            "a\\\\:b\\,c\\[d\\]\\\\'e\\\\%f\\\\\\\\g",
            escape_drawtext("a:b,c[d]'e%f\\g"),
        )


class PngRasterizerTests(unittest.TestCase):
    def test_renders_valid_png_with_expected_geometry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dramapy-png-") as tempdir:
            path = Path(tempdir) / "label.png"
            render_text_png(["ABC", "d"], path, scale=2, pad=6, line_gap=3)
            data = path.read_bytes()
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
            # IHDR: width at offset 16, height at 20 (big-endian uint32).
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
            self.assertEqual(3 * 8 * 2 + 12, width)   # 3 cols * 8px * scale + pads
            self.assertEqual(2 * 8 * 2 + 3 + 12, height)  # 2 rows + gap + pads
            # zlib stream in IDAT must decompress to RGBA rows + filter bytes.
            idat_start = data.index(b"IDAT") + 4
            idat_len = int.from_bytes(data[idat_start - 8 : idat_start - 4], "big")
            raw = zlib.decompress(data[idat_start : idat_start + idat_len])
            self.assertEqual(height * (1 + width * 4), len(raw))

    def test_non_ascii_folds_instead_of_crashing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dramapy-png-") as tempdir:
            path = Path(tempdir) / "label.png"
            render_text_png(["Chaebol 재벌 — héir"], path, scale=1)
            self.assertGreater(path.stat().st_size, 100)

    def test_wrap_subtitle_42_columns(self) -> None:
        wrapped = wrap_subtitle("word " * 20)
        self.assertTrue(all(len(line) <= 42 for line in wrapped))


if __name__ == "__main__":
    unittest.main()
