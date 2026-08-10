"""Every warning kind in the contract §1 table, each triggered deliberately.

E2E kinds ride real generation runs: drift/aspect/silence via doctored
render-cache entries (planting a wrong clip at the exact content address a
shot resolves to), hook_too_long via a late first non-establish beat,
no_cliffhanger via an unset cliffhanger, subtitle_overrun via a >84-char
line, functional via envelope warnings. The kinds generation cannot reach
with an honest mock provider (render_failed / missing_shot / stitch_failed /
check_failed) are triggered through the real ``checks`` API with real data —
still no mocking of dramapy."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramaproj import (  # noqa: E402
    DEFAULT_CAST,
    ProviderEnvGuard,
    episode_dict,
    make_clip,
    series_dict,
    shot,
    write_episode,
    write_series,
)
from dramapy import checks, render_cache  # noqa: E402
from dramapy.generation import generate_episode  # noqa: E402
from dramapy.providers.mock import MockProvider  # noqa: E402
from dramapy.spec import resolve_cast, resolve_episode, resolve_series  # noqa: E402

LONG_LINE = (
    "I have carried this secret through ten years of winters and I will not "
    "let you bury it again tonight, not for him."
)


def _index(warnings: list[dict]) -> dict[tuple[str, str], dict]:
    return {(w["kind"], w["part"]): w for w in warnings}


class DoctoredCacheWarningTests(ProviderEnvGuard, unittest.TestCase):
    """duration_drift (clip + episode), aspect_mismatch, silent_dialogue,
    subtitle_overrun, functional — one real run, three planted cache hits."""

    def test_planted_cache_clips_surface_the_check_table(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dramapy-warn-") as tempdir:
            root = Path(tempdir)
            series_raw = series_dict()  # 270x480 @ 24, photoreal-drama
            write_series(root, series=series_raw)

            shots = [
                shot("s_drift", "establish", 4, prompt="alley"),
                shot("s_aspect", "action", 4, prompt="chase"),
                shot("s_silent", "dialogue", 4, cast=["li_wei"], line="Quiet."),
                shot("s_ok", "dialogue", 3, cast=["li_wei"], line=LONG_LINE),
            ]
            envelope = {
                "episode": episode_dict(shots, cliffhanger="the letter burns"),
                "warnings": [{"detail": "recap beat exceeds 8s"}],
            }
            script = write_episode(root, envelope)

            # Plant wrong clips at the EXACT content addresses generation
            # will compute (same resolvers, same key function — real API).
            series = resolve_series(series_raw)
            cast = resolve_cast(list(DEFAULT_CAST))
            cast_by_id = {member.id: member for member in cast}
            episode = resolve_episode(envelope["episode"], cast_ids=frozenset(cast_by_id))
            resolved = {s.id: s for s in episode.shots}

            provider = MockProvider()

            def plant(shot_id: str, **clip_kwargs) -> None:
                target = resolved[shot_id]
                key = render_cache.shot_cache_key(
                    shot=target,
                    series=series,
                    provider_name=provider.name,
                    provider_model=provider.model,
                    characters=tuple(cast_by_id[c] for c in target.cast),
                    scene_id="s1",  # episode_dict default scene
                    location=episode.scenes[0].location,  # part of the address
                    provider_salt=provider.cache_salt,
                )
                make_clip(render_cache.cache_clip_path(root, key), **clip_kwargs)

            plant("s_drift", duration_s=6.0)                      # 50% > 15%
            plant("s_aspect", duration_s=4.0, width=480, height=480)
            # Voices are off in the test suite (conftest), so every dialogue
            # line is unvoiced → silent_dialogue. The clip's own audio no
            # longer matters (assembly owns audio); s_silent just needs a clip.
            plant("s_silent", duration_s=4.0, audio=False)

            result = generate_episode(script, root / "episodes" / "ep001.mp4")

            by_key = _index(result["warnings"])

            drift_clip = by_key[("duration_drift", "s_drift")]
            self.assertEqual("warning", drift_clip["severity"])
            self.assertIn("6.00s vs 4.00s", drift_clip["detail"])

            # Episode total ~17s vs 15s spec = 13% > 10% → episode-level too.
            drift_episode = by_key[("duration_drift", "episode")]
            self.assertEqual("warning", drift_episode["severity"])

            aspect = by_key[("aspect_mismatch", "s_aspect")]
            self.assertEqual("error", aspect["severity"])
            self.assertIn("480x480", aspect["detail"])

            silent = by_key[("silent_dialogue", "s_silent")]
            self.assertEqual("warning", silent["severity"])

            overrun = by_key[("subtitle_overrun", "s_ok")]
            self.assertEqual("info", overrun["severity"])

            functional = by_key[("functional", "episode")]
            self.assertEqual("warning", functional["severity"])
            self.assertEqual("recap beat exceeds 8s", functional["detail"])

            # Planted shots came from the cache; the honest one rendered.
            statuses = {entry["id"]: entry["status"] for entry in result["shots"]}
            self.assertEqual(
                {"s_drift": "cached", "s_aspect": "cached",
                 "s_silent": "cached", "s_ok": "rendered"},
                statuses,
            )

            # The same warnings land in the sidecar's validation block.
            sidecar = json.loads(
                (root / "episodes" / "ep001.episode.json").read_text(encoding="utf-8")
            )
            sidecar_kinds = {
                (w["kind"], w["part"]) for w in sidecar["validation"]["warnings"]
            }
            self.assertIn(("aspect_mismatch", "s_aspect"), sidecar_kinds)
            self.assertIn(("duration_drift", "episode"), sidecar_kinds)

            # shots[] carries measured durationS + status (2026-08-09 change):
            # the planted 6s clip reads ~6.0 with status "cached".
            sidecar_shots = {entry["id"]: entry for entry in sidecar["shots"]}
            self.assertEqual("cached", sidecar_shots["s_drift"]["status"])
            self.assertAlmostEqual(6.0, sidecar_shots["s_drift"]["durationS"], delta=0.25)
            self.assertEqual("rendered", sidecar_shots["s_ok"]["status"])


class HookAndCliffhangerTests(ProviderEnvGuard, unittest.TestCase):
    def test_late_hook_and_missing_cliffhanger(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dramapy-hook-") as tempdir:
            root = Path(tempdir)
            write_series(root)
            script = write_episode(
                root,
                {
                    "episode": episode_dict(
                        [
                            shot("cold_1", "establish", 4, prompt="skyline"),
                            shot("cold_2", "establish", 4, prompt="rain"),
                            shot("beat", "dialogue", 3, cast=["li_wei"], line="Now."),
                        ],
                        hook_max_s=5.0,
                        cliffhanger=None,
                    )
                },
            )
            result = generate_episode(script, root / "episodes" / "ep001.mp4")
            by_key = _index(result["warnings"])

            hook = by_key[("hook_too_long", "beat")]
            self.assertEqual("warning", hook["severity"])
            self.assertIn("8.0s", hook["detail"])

            cliff = by_key[("no_cliffhanger", "episode")]
            self.assertEqual("warning", cliff["severity"])


class ChecksUnitTests(unittest.TestCase):
    """The kinds an honest mock run cannot produce, via the real checks API."""

    def test_render_failed_and_missing_shot(self) -> None:
        shots = [
            {
                "id": "s_fail",
                "kind": "action",
                "spec_duration_s": 4.0,
                "status": "failed",
                "clip_path": None,
                "duration_s": None,
                "width": None,
                "height": None,
                "has_audio": False,
                "error": "ProviderError: quota exhausted",
            }
        ]
        warnings = checks.check_shot_outputs(shots)
        by_kind = {w["kind"]: w for w in warnings}
        self.assertEqual("error", by_kind["render_failed"]["severity"])
        self.assertIn("quota exhausted", by_kind["render_failed"]["detail"])
        self.assertEqual("error", by_kind["missing_shot"]["severity"])
        self.assertEqual("s_fail", by_kind["missing_shot"]["part"])

    def test_silent_dialogue_reflects_voiced_set(self) -> None:
        shots = [
            {"id": "d1", "kind": "dialogue", "clip_path": "x.mp4",
             "line": "Hello.", "has_audio": False},
            {"id": "d2", "kind": "dialogue", "clip_path": "y.mp4",
             "line": "There.", "has_audio": False},
            {"id": "e1", "kind": "establish", "clip_path": "z.mp4",
             "line": None, "has_audio": False},
        ]
        # No voice track at all → every dialogue line is silent.
        none_voiced = checks.check_silent_dialogue(shots)
        self.assertEqual(
            {("silent_dialogue", "d1"), ("silent_dialogue", "d2")},
            {(w["kind"], w["part"]) for w in none_voiced},
        )
        # Partial voicing → only the unvoiced line warns.
        partial = checks.check_silent_dialogue(shots, voiced_shot_ids={"d1"})
        self.assertEqual(
            [("silent_dialogue", "d2")],
            [(w["kind"], w["part"]) for w in partial],
        )
        # Fully voiced → zero warnings (the normal-render case).
        self.assertEqual(
            [], checks.check_silent_dialogue(shots, voiced_shot_ids={"d1", "d2"})
        )

    def test_stitch_failed_warning_shape(self) -> None:
        warning = checks.stitch_failed_warning("concat demuxer exploded")
        self.assertEqual(
            {"part": "episode", "kind": "stitch_failed",
             "detail": "concat demuxer exploded", "severity": "error"},
            warning,
        )

    def test_check_failed_never_raises(self) -> None:
        class _Hostile:
            hook_max_s = 5.0

            @property
            def shots(self):
                raise RuntimeError("verifier tripwire")

            cliffhanger = "x"

        poisoned = [
            {
                "id": "s_bad",
                "kind": "action",
                "spec_duration_s": "not-a-number",
                "status": "rendered",
                "clip_path": None,
                "duration_s": "also-not",
                "width": "x",
                "height": "y",
                "has_audio": False,
            }
        ]
        warnings = checks.collect_episode_warnings(
            episode=_Hostile(),
            shots=poisoned,
            aspect="9:16",
            episode_duration_s=10.0,
        )
        kinds = {w["kind"] for w in warnings}
        self.assertIn("check_failed", kinds)
        for warning in warnings:
            if warning["kind"] == "check_failed":
                self.assertEqual("warning", warning["severity"])

    def test_functional_coercion_defaults(self) -> None:
        coerced = checks.coerce_functional_warnings(
            [
                {"detail": "recap beat exceeds 8s"},
                {"detail": "b-plot missing", "part": "scene_2", "severity": "info"},
                {"detail": "bad severity", "severity": "catastrophic"},
                {"part": "scene_3"},  # no detail → dropped
                "not-a-mapping",  # dropped
            ]
        )
        self.assertEqual(3, len(coerced))
        self.assertEqual(
            {"part": "episode", "kind": "functional",
             "detail": "recap beat exceeds 8s", "severity": "warning"},
            coerced[0],
        )
        self.assertEqual("scene_2", coerced[1]["part"])
        self.assertEqual("info", coerced[1]["severity"])
        self.assertEqual("warning", coerced[2]["severity"])

    def test_hook_ok_when_first_beat_inside_window(self) -> None:
        class _Shot:
            def __init__(self, id, kind, duration_s):
                self.id, self.kind, self.duration_s = id, kind, duration_s

        class _Episode:
            hook_max_s = 5.0
            shots = (_Shot("a", "establish", 5.0), _Shot("b", "dialogue", 4.0))

        self.assertEqual([], checks.check_hook(_Episode()))


if __name__ == "__main__":
    unittest.main()
