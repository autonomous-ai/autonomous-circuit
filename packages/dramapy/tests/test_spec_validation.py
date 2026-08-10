"""Shot rules (contract §1 + CHANGES 2026-08-09): duration_s ∈ [1.5, 15],
dialogue requires cast+line, cast ids must exist in the series bible — plus
the duck-typed resolution rule (dicts with the same keys are accepted).

Pure resolution-level tests call the real ``resolve_*`` API directly; one
case goes through ``generate_episode`` to prove validation fires before any
provider work."""

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
from dramapy.errors import SpecValidationError  # noqa: E402
from dramapy.generation import generate_episode  # noqa: E402
from dramapy.spec import (  # noqa: E402
    Episode,
    Scene,
    Shot,
    resolve_episode,
    resolve_shot,
)

CAST_IDS = frozenset({"li_wei", "dorian"})


class ShotRuleTests(unittest.TestCase):
    def _resolve(self, raw_shot: dict):
        return resolve_shot(raw_shot, scene_id="s1", cast_ids=CAST_IDS)

    def test_valid_shot_resolves(self) -> None:
        resolved = self._resolve(shot("s1_01", "establish", 5, prompt="lobby"))
        self.assertEqual("s1_01", resolved.id)
        self.assertEqual(5.0, resolved.duration_s)

    def test_duration_below_range(self) -> None:
        with self.assertRaises(SpecValidationError) as caught:
            self._resolve(shot("s1_01", "establish", 1.4))
        self.assertIn("duration_s", str(caught.exception))
        self.assertIn("s1_01", str(caught.exception))

    def test_duration_above_range(self) -> None:
        with self.assertRaises(SpecValidationError):
            self._resolve(shot("s1_01", "establish", 15.1))

    def test_duration_bounds_inclusive(self) -> None:
        self.assertEqual(3.0, self._resolve(shot("a", "insert", 3)).duration_s)
        self.assertEqual(15.0, self._resolve(shot("b", "insert", 15)).duration_s)

    def test_unknown_kind(self) -> None:
        with self.assertRaises(SpecValidationError) as caught:
            self._resolve(shot("s1_01", "montage", 5))
        self.assertIn("kind", str(caught.exception))

    def test_dialogue_requires_line(self) -> None:
        with self.assertRaises(SpecValidationError) as caught:
            self._resolve(shot("s1_02", "dialogue", 5, cast=["li_wei"]))
        self.assertIn("line", str(caught.exception))

    def test_dialogue_requires_cast(self) -> None:
        with self.assertRaises(SpecValidationError) as caught:
            self._resolve(shot("s1_02", "dialogue", 5, line="Hello."))
        self.assertIn("cast", str(caught.exception))

    def test_cast_id_must_exist_in_bible(self) -> None:
        with self.assertRaises(SpecValidationError) as caught:
            self._resolve(shot("s1_02", "dialogue", 5, cast=["ghost"], line="Hi."))
        self.assertIn("ghost", str(caught.exception))
        self.assertIn("li_wei", str(caught.exception))

    def test_unsafe_shot_id_rejected(self) -> None:
        with self.assertRaises(SpecValidationError):
            self._resolve(shot("s1/../evil", "establish", 5))


class EpisodeResolutionTests(unittest.TestCase):
    def test_dataclasses_and_dicts_resolve_identically(self) -> None:
        # The donor .wrapped duck-typing rule: attribute-based, so a plain
        # dict with the same keys works wherever a dataclass does.
        as_dataclasses = Episode(
            number=1,
            title="T",
            scenes=[
                Scene(
                    id="s1",
                    location="lobby",
                    shots=[
                        Shot(id="a", kind="establish", duration_s=4, prompt="p"),
                        Shot(
                            id="b",
                            kind="dialogue",
                            duration_s=5,
                            cast=["li_wei"],
                            line="Hi.",
                        ),
                    ],
                )
            ],
            cliffhanger="cliff",
            bgm="tense-strings",
        )
        as_dicts = episode_dict(
            [
                shot("a", "establish", 4, prompt="p"),
                shot("b", "dialogue", 5, cast=["li_wei"], line="Hi.", prompt=""),
            ],
            title="T",
            cliffhanger="cliff",
            bgm="tense-strings",
        )
        resolved_dc = resolve_episode(as_dataclasses, cast_ids=CAST_IDS)
        resolved_dict = resolve_episode(as_dicts, cast_ids=CAST_IDS)
        self.assertEqual(resolved_dc.shots, resolved_dict.shots)
        self.assertEqual(resolved_dc.cliffhanger, resolved_dict.cliffhanger)
        self.assertEqual(9.0, resolved_dc.spec_duration_s)

    def test_duplicate_shot_ids_rejected(self) -> None:
        raw = episode_dict(
            [shot("dup", "establish", 4), shot("dup", "action", 4)]
        )
        with self.assertRaises(SpecValidationError) as caught:
            resolve_episode(raw, cast_ids=CAST_IDS)
        self.assertIn("dup", str(caught.exception))

    def test_empty_scenes_rejected(self) -> None:
        with self.assertRaises(SpecValidationError):
            resolve_episode(
                {"number": 1, "title": "T", "scenes": []}, cast_ids=CAST_IDS
            )

    def test_missing_required_field(self) -> None:
        with self.assertRaises(SpecValidationError) as caught:
            resolve_episode({"number": 1, "scenes": [1]}, cast_ids=CAST_IDS)
        self.assertIn("title", str(caught.exception))


class ValidationThroughEntryPointTests(ProviderEnvGuard, unittest.TestCase):
    def test_duration_20_raises_spec_validation_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dramapy-spec-") as tempdir:
            root = Path(tempdir)
            write_series(root)
            script = write_episode(
                root, {"episode": episode_dict([shot("long", "action", 20)])}
            )
            with self.assertRaises(SpecValidationError) as caught:
                generate_episode(script, root / "episodes" / "ep001.mp4")
            self.assertIn("[1.5, 15]", str(caught.exception))
            self.assertIn("20", str(caught.exception))
            # Validation fires before any rendering: no artifacts at all.
            self.assertFalse((root / "episodes" / "ep001_shots").exists())


if __name__ == "__main__":
    unittest.main()
