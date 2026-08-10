#!/usr/bin/env python3
"""Autonomous TV structural evals.

End-to-end quality gates over representative creator projects, run through
the real dramapy pipeline with the mock provider (zero network, zero GPUs).
Unit tests assert code behavior; these evals assert *creator-visible
outcomes*: a well-formed episode produces zero warnings and a complete
artifact set, the beat-law verifiers actually fire on bad dramaturgy, and
the render cache makes iteration fast.

Usage: python evals/run.py [case-name ...]     (default: all)
Exit: non-zero if any case fails. Output: one scorecard line per case.

Extending: add a Case to CASES. Hosted-provider smoke cases belong here
too later, gated on env keys (never in CI).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "dramapy" / "src"))
sys.path.insert(0, str(REPO / "skills" / "dramacode"))  # dramalib (binge eval)

from dramalib.golden import check_golden, golden_rewards  # noqa: E402
from dramapy.generation import generate_episode  # noqa: E402

FPS = 24


def _series_py(style: str = "photoreal-drama") -> str:
    return textwrap.dedent(
        f"""
        SERIES = {{
            "title": "Eval Drama",
            "genre": "revenge",
            "style": "{style}",
            "aspect": "9:16",
            "resolution": (1080, 1920),
            "fps": {FPS},
            "language": "en",
        }}
        CAST = [
            {{"id": "mei", "name": "Mei", "look": "woman, 27, sharp bob", "voice": "f_low", "ref_images": []}},
            {{"id": "rex", "name": "Rex", "look": "man, 33, black coat", "voice": "m_cold", "ref_images": []}},
        ]
        """
    ).strip()


def _episode_py(shots: list[dict], cliffhanger: str | None = "freeze on the letter") -> str:
    lines = ["def gen_episode():", "    return {"]
    lines.append("        \"episode\": {")
    lines.append("            \"number\": 1, \"title\": \"Eval Ep\", \"hook_max_s\": 5.0,")
    lines.append("            \"scenes\": [{\"id\": \"s1\", \"location\": \"lobby, night\", \"shots\": [")
    for s in shots:
        lines.append(f"                {s!r},")
    lines.append("            ]}],")
    cl = f"\"{cliffhanger}\"" if cliffhanger else "None"
    lines.append(f"            \"cliffhanger\": {cl},")
    lines.append("            \"bgm\": \"tense-strings\", \"burn_subtitles\": True,")
    lines.append("        },")
    lines.append("    }")
    return "\n".join(lines)


GOOD_SHOTS = [
    {"id": "s1_01", "kind": "establish", "duration_s": 3, "prompt": "rain lobby doors"},
    {"id": "s1_02", "kind": "dialogue", "duration_s": 4, "cast": ["mei"],
     "line": "You never told me he was alive.", "emotion": "dread", "prompt": "close-up, letter"},
    {"id": "s1_03", "kind": "action", "duration_s": 3, "cast": ["rex"],
     "prompt": "he turns from the window, slow"},
]


def build_project(tmp: Path, shots: list[dict], cliffhanger="freeze on the letter",
                  style: str = "photoreal-drama") -> Path:
    proj = tmp / "proj"
    (proj / "episodes").mkdir(parents=True)
    (proj / "series.py").write_text(_series_py(style))
    (proj / "episodes" / "ep001.py").write_text(_episode_py(shots, cliffhanger))
    return proj


def warning_kinds(meta_path: Path) -> list[str]:
    meta = json.loads(meta_path.read_text())
    return [w["kind"] for w in meta.get("validation", {}).get("warnings", [])]


class Case:
    def __init__(self, name, fn):
        self.name, self.fn = name, fn


def eval_clean_episode(tmp: Path):
    """A well-formed episode → zero warnings, full artifact set, sane timing."""
    proj = build_project(tmp, GOOD_SHOTS)
    out = proj / "episodes" / "ep001.mp4"
    result = generate_episode(proj / "episodes" / "ep001.py", out)
    kinds = warning_kinds(proj / "episodes" / "ep001.episode.json")
    assert kinds == [], f"expected clean, got warnings: {kinds}"
    for rel in ["ep001.mp4", "ep001.episode.json", "ep001.srt",
                "ep001_review/_poster.png", "ep001_review/_board.png"]:
        assert (proj / "episodes" / rel).exists(), f"missing artifact {rel}"
    assert (proj / "series.json").exists(), "missing series.json"
    assert abs(result["duration_s"] - 10.0) < 1.5, f"duration {result['duration_s']}"
    assert result["shot_count"] == 3


def eval_hook_verifier_fires(tmp: Path):
    """Late first beat (long establish run) → hook_too_long warning."""
    shots = [
        {"id": "a", "kind": "establish", "duration_s": 4, "prompt": "wide city"},
        {"id": "b", "kind": "establish", "duration_s": 4, "prompt": "wide lobby"},
        {"id": "c", "kind": "dialogue", "duration_s": 4, "cast": ["mei"],
         "line": "Finally.", "prompt": "close"},
    ]
    proj = build_project(tmp, shots)
    generate_episode(proj / "episodes" / "ep001.py", proj / "episodes" / "ep001.mp4")
    kinds = warning_kinds(proj / "episodes" / "ep001.episode.json")
    assert "hook_too_long" in kinds, f"hook verifier silent: {kinds}"


def eval_cliffhanger_verifier_fires(tmp: Path):
    proj = build_project(tmp, GOOD_SHOTS, cliffhanger=None)
    generate_episode(proj / "episodes" / "ep001.py", proj / "episodes" / "ep001.mp4")
    kinds = warning_kinds(proj / "episodes" / "ep001.episode.json")
    assert "no_cliffhanger" in kinds, f"cliffhanger verifier silent: {kinds}"


def eval_manju_density(tmp: Path):
    """Anime-style density: 12 short shots stitch clean and in budget."""
    shots = [{"id": f"m{i:02d}", "kind": "action" if i % 3 else "establish",
              "duration_s": 3, "prompt": f"panel {i}"} for i in range(12)]
    proj = build_project(tmp, shots, style="manhwa")
    t0 = time.monotonic()
    result = generate_episode(proj / "episodes" / "ep001.py", proj / "episodes" / "ep001.mp4")
    elapsed = time.monotonic() - t0
    assert result["shot_count"] == 12
    assert elapsed < 120, f"12-shot manju took {elapsed:.0f}s"


def eval_cache_iteration_speed(tmp: Path):
    """Second run (no edits) must be all-cached and ≥3x faster."""
    proj = build_project(tmp, GOOD_SHOTS)
    src, out = proj / "episodes" / "ep001.py", proj / "episodes" / "ep001.mp4"
    t0 = time.monotonic(); generate_episode(src, out); cold = time.monotonic() - t0
    t0 = time.monotonic(); result = generate_episode(src, out); warm = time.monotonic() - t0
    statuses = {s["status"] for s in result["shots"]}
    assert statuses == {"cached"}, f"expected all cached, got {statuses}"
    assert warm * 3 < cold or warm < 3.0, f"cache too slow: cold={cold:.1f}s warm={warm:.1f}s"


def eval_binge_golden(tmp: Path):
    """Flywheel #5: the binge reward must keep separating strong from weak
    episodes on the golden set — no platform change silently regresses the
    binge machine. Invariant-based (see dramalib.golden.check_golden)."""
    problems = check_golden()
    assert problems == [], "; ".join(problems)
    rewards = golden_rewards()
    assert rewards, "golden set produced no rewards"


CASES = [
    Case("clean-episode", eval_clean_episode),
    Case("hook-verifier-fires", eval_hook_verifier_fires),
    Case("cliffhanger-verifier-fires", eval_cliffhanger_verifier_fires),
    Case("manju-density", eval_manju_density),
    Case("cache-iteration-speed", eval_cache_iteration_speed),
    Case("binge-golden", eval_binge_golden),
]


def main(argv: list[str]) -> int:
    wanted = set(argv) or {c.name for c in CASES}
    unknown = wanted - {c.name for c in CASES}
    if unknown:
        print(f"unknown case(s): {sorted(unknown)}"); return 2
    failures = 0
    for case in CASES:
        if case.name not in wanted:
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"video-eval-{case.name}-"))
        t0 = time.monotonic()
        try:
            case.fn(tmp)
        except Exception as exc:  # noqa: BLE001 — scorecard, not crash
            failures += 1
            print(f"FAIL  {case.name:28s} {time.monotonic()-t0:6.1f}s  {exc}")
        else:
            print(f"ok    {case.name:28s} {time.monotonic()-t0:6.1f}s")
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(wanted)-failures}/{len(wanted)} evals passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
