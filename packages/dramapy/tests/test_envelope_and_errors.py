"""Envelope resolution + the GenerationError hierarchy (contract §1).

Every failure mode uses a real project on disk and the real entry point —
no dramapy internals are mocked. ``SyntaxError``/``ImportError`` propagate
untouched; an ``AssertionError`` from a project ``validate()`` surfaces as
``ProjectShapeError`` carrying the assert message; unknown envelope keys
raise a ``ProjectShapeError`` that is *also* a ``TypeError`` (both contract
clauses at once)."""

from __future__ import annotations

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
from dramapy.errors import (  # noqa: E402
    GenerationError,
    GeneratorRuntimeError,
    ProjectShapeError,
    ProviderError,
    SpecValidationError,
)


class EnvelopeAndErrorTests(ProviderEnvGuard, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tempdir = tempfile.TemporaryDirectory(prefix="dramapy-err-")
        self.root = Path(self._tempdir.name)
        write_series(self.root)
        self.out = self.root / "episodes" / "ep001.mp4"

    def tearDown(self) -> None:
        self._tempdir.cleanup()
        super().tearDown()

    def _write_episode_source(self, body: str) -> Path:
        episodes = self.root / "episodes"
        episodes.mkdir(exist_ok=True)
        path = episodes / "ep001.py"
        path.write_text(body, encoding="utf-8")
        return path

    # -- error hierarchy shape (contract, verbatim) --------------------------

    def test_hierarchy_is_verbatim(self) -> None:
        for subclass in (
            ProjectShapeError,
            GeneratorRuntimeError,
            SpecValidationError,
            ProviderError,
        ):
            self.assertTrue(issubclass(subclass, GenerationError))
        from dramapy.errors import ExportError

        self.assertTrue(issubclass(ExportError, GenerationError))

    # -- input polymorphism + output suffix ----------------------------------

    def test_bad_output_suffix(self) -> None:
        script = write_episode(self.root, {"episode": episode_dict([shot("a", "establish", 3)])})
        with self.assertRaises(ProjectShapeError) as caught:
            generate_episode(script, self.root / "episodes" / "ep001.mov")
        self.assertIn(".mp4", str(caught.exception))

    def test_missing_source(self) -> None:
        with self.assertRaises(ProjectShapeError):
            generate_episode(self.root / "episodes" / "nope.py", self.out)

    def test_directory_without_main_py(self) -> None:
        (self.root / "episodes").mkdir(exist_ok=True)
        with self.assertRaises(ProjectShapeError) as caught:
            generate_episode(self.root / "episodes", self.out)
        self.assertIn("main.py", str(caught.exception))

    def test_directory_with_main_py_renders(self) -> None:
        # Polymorphic input: a directory containing main.py (donor rule).
        envelope = {
            "episode": episode_dict(
                [shot("d1", "establish", 3, prompt="tiny")],
                cliffhanger="the door opens",
            )
        }
        write_series(self.root, series={
            "title": "T", "genre": "g", "style": "anime",
            "aspect": "9:16", "resolution": (180, 320), "fps": 12, "language": "en",
        })
        (self.root / "main.py").write_text(
            "import series\n\ndef gen_episode():\n    return " + repr(envelope) + "\n",
            encoding="utf-8",
        )
        out = self.root / "pilot.mp4"
        result = generate_episode(self.root, out)
        self.assertTrue(out.is_file())
        self.assertEqual(1, result["shot_count"])

    def test_no_series_py_anywhere(self) -> None:
        # A fresh temp tree whose ancestors carry no series.py.
        with tempfile.TemporaryDirectory(prefix="dramapy-orphan-") as isolated:
            isolated_p = Path(isolated) / "episodes"
            isolated_p.mkdir()
            isolated_script = isolated_p / "ep001.py"
            isolated_script.write_text("def gen_episode():\n    return {}\n", encoding="utf-8")
            with self.assertRaises(ProjectShapeError) as caught:
                generate_episode(isolated_script, isolated_p / "ep001.mp4")
            self.assertIn("series.py", str(caught.exception))

    # -- gen_episode presence + behavior --------------------------------------

    def test_missing_gen_episode(self) -> None:
        script = self._write_episode_source("import series\nX = 1\n")
        with self.assertRaises(ProjectShapeError) as caught:
            generate_episode(script, self.out)
        self.assertIn("gen_episode", str(caught.exception))

    def test_gen_episode_raising_maps_to_generator_runtime_error(self) -> None:
        script = self._write_episode_source(
            "def gen_episode():\n    raise RuntimeError('boom in project code')\n"
        )
        with self.assertRaises(GeneratorRuntimeError) as caught:
            generate_episode(script, self.out)
        self.assertIn("boom in project code", str(caught.exception))

    def test_assertion_error_becomes_project_shape_error_with_message(self) -> None:
        script = self._write_episode_source(
            "def validate():\n"
            "    assert False, 'hook beat 7.5s exceeds hook_max_s 5.0'\n\n"
            "def gen_episode():\n"
            "    validate()\n"
        )
        with self.assertRaises(ProjectShapeError) as caught:
            generate_episode(script, self.out)
        self.assertIn("hook beat 7.5s exceeds hook_max_s 5.0", str(caught.exception))
        self.assertNotIsInstance(caught.exception, GeneratorRuntimeError)

    def test_syntax_error_propagates_untouched(self) -> None:
        script = self._write_episode_source("def gen_episode(:\n    pass\n")
        with self.assertRaises(SyntaxError):
            generate_episode(script, self.out)

    def test_import_error_propagates_untouched(self) -> None:
        script = self._write_episode_source(
            "import module_that_definitely_does_not_exist_42\n\n"
            "def gen_episode():\n    return {}\n"
        )
        with self.assertRaises(ImportError):
            generate_episode(script, self.out)

    # -- envelope shape (the only accepted return form) ----------------------

    def test_return_42_rejected(self) -> None:
        script = self._write_episode_source("def gen_episode():\n    return 42\n")
        with self.assertRaises(ProjectShapeError) as caught:
            generate_episode(script, self.out)
        self.assertIn("envelope", str(caught.exception))
        self.assertIn("int", str(caught.exception))

    def test_unknown_envelope_key_raises_typeerror_and_project_shape_error(self) -> None:
        script = self._write_episode_source(
            "def gen_episode():\n"
            "    return {'episode': None, 'scenes': [], 'extra': 1}\n"
        )
        with self.assertRaises(ProjectShapeError) as caught:
            generate_episode(script, self.out)
        self.assertIsInstance(caught.exception, TypeError)
        self.assertIn("extra", str(caught.exception))
        self.assertIn("scenes", str(caught.exception))

    def test_envelope_missing_episode_key(self) -> None:
        script = self._write_episode_source(
            "def gen_episode():\n    return {'warnings': []}\n"
        )
        with self.assertRaises(ProjectShapeError) as caught:
            generate_episode(script, self.out)
        self.assertIn("'episode'", str(caught.exception))

    # -- provider selection ----------------------------------------------------

    def test_unknown_provider(self) -> None:
        script = write_episode(self.root, {"episode": episode_dict([shot("a", "establish", 3)])})
        with self.assertRaises(ProviderError) as caught:
            generate_episode(script, self.out, provider="sora")
        self.assertIn("unknown provider", str(caught.exception))

    def test_hosted_providers_require_keys_and_never_touch_network(self) -> None:
        script = write_episode(self.root, {"episode": episode_dict([shot("a", "establish", 3)])})
        for name, env_var in (
            ("fal", "FAL_KEY"),
            ("dashscope", "DASHSCOPE_API_KEY"),
            ("minimax", "MINIMAX_API_KEY"),
        ):
            with self.assertRaises(ProviderError) as caught:
                generate_episode(script, self.out, provider=name)
            self.assertIn(env_var, str(caught.exception))

    def test_provider_env_fallback_selects_mock_by_default(self) -> None:
        # VIDEO_PROVIDER is popped by the guard; the default is mock and the
        # spec validation error below proves we got past provider selection.
        script = write_episode(
            self.root, {"episode": episode_dict([shot("a", "establish", 20)])}
        )
        with self.assertRaises(SpecValidationError):
            generate_episode(script, self.out)

    def test_per_shot_timeout_surfaces_as_provider_error(self) -> None:
        script = write_episode(
            self.root, {"episode": episode_dict([shot("slow", "establish", 3)])}
        )
        with self.assertRaises(ProviderError) as caught:
            generate_episode(script, self.out, max_render_s=0.005)
        self.assertIn("timed out", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
