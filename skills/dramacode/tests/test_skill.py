"""Self-contained tests for the dramacode skill.

Run from inside the skill directory:

    python -m pytest tests/

The suite talks to the scripts exactly like the agent does — subprocess in,
one JSON line out — with a stub dramapy injected via
``DRAMACODE_TEST_DRAMAPY_PATH`` (see conftest.py).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
RECIPES = SKILL_DIR / "recipes"
TEMPLATE = SKILL_DIR / "templates" / "project_skeleton"


def _run_tool(tool: str, *args: str, timeout: int = 90) -> dict:
    """Invoke ``python scripts/<tool> ...`` and parse the last JSON line."""
    cmd = [sys.executable, str(SCRIPTS / tool), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout or "").strip().splitlines()
    if not out:
        return {
            "ok": False,
            "error": {
                "code": "RUNTIME_ERROR",
                "message": f"no stdout (stderr: {proc.stderr[:300]!r})",
            },
        }
    return json.loads(out[-1])


def _run_drama(*args: str, timeout: int = 90) -> dict:
    return _run_tool("drama", *args, timeout=timeout)


def _err_message(payload: dict) -> str:
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("message", ""))
    return str(err or "")


# -- Smoke ------------------------------------------------------------------


def test_layout_intact():
    """SKILL.md + tier-1 references + script entrypoints + dramalib exist."""
    assert (SKILL_DIR / "SKILL.md").exists()
    assert (SKILL_DIR / "agents" / "claude-code.md").exists()
    for name in (
        "beat-structure.md",
        "shot-grammar.md",
        "assembly-conventions.md",
        "series-architecture.md",
    ):
        assert (SKILL_DIR / "references" / name).exists(), f"missing references/{name}"
    for name in ("drama", "check", "review"):
        assert (SCRIPTS / name / "__main__.py").exists(), (
            f"missing scripts/{name}/__main__.py"
        )
    for name in ("bible.py", "spec.py", "tables.py", "tropes.py", "helpers.py"):
        assert (SKILL_DIR / "dramalib" / name).exists(), f"missing dramalib/{name}"
    assert (TEMPLATE / "series.py").exists()
    assert (TEMPLATE / "episodes" / "ep001.py").exists()
    assert (TEMPLATE / "spec.md").exists()
    # The vendor target ships empty but tracked.
    assert (SCRIPTS / "packages" / "dramapy" / "README.md").exists()


def test_help_works():
    """The CLI tools support ``--help``."""
    for tool in ("drama", "check", "review"):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / tool), "--help"],
            capture_output=True, text=True, timeout=20,
        )
        assert proc.returncode == 0, f"{tool} --help failed: {proc.stderr}"
        assert "usage" in proc.stdout.lower()


# -- Recipes compile through the runner ---------------------------------------


RECIPE_FILES = sorted(RECIPES.glob("*.py")) if RECIPES.exists() else []


@pytest.mark.parametrize("recipe", RECIPE_FILES, ids=lambda p: p.stem)
def test_recipe_produces_artifact_set(recipe: Path):
    """Every recipe must run clean through the sandboxed runner and produce
    the contract §1 artifact set with a well-formed sidecar."""
    with tempfile.TemporaryDirectory() as tmp:
        payload = _run_drama(str(recipe), "--out-dir", tmp)
        assert payload.get("ok"), f"{recipe.name} failed: {payload}"
        # Recipes are FINISHED demos — zero warnings, functional or verifier.
        assert not payload.get("warnings"), payload.get("warnings")
        assert payload.get("shot_count", 0) >= 8
        assert payload.get("duration_s", 0) >= 45

        out = Path(tmp)
        stem = recipe.stem
        assert (out / f"{stem}.mp4").exists()
        assert (out / f"{stem}.episode.json").exists()
        # Both recipes carry dialogue, so the srt must exist.
        assert (out / f"{stem}.srt").exists()
        assert (out / f"{stem}_review" / "_board.png").exists()
        assert (out / f"{stem}_review" / "_poster.png").exists()

        meta = json.loads((out / f"{stem}.episode.json").read_text())
        assert meta["generator"] == "dramapy"
        assert meta["entryKind"] == "episode"
        assert meta["episode"]["durationS"] == pytest.approx(payload["duration_s"])
        assert meta["validation"]["shotCount"] == payload["shot_count"]
        assert len(meta["shots"]) == payload["shot_count"]
        for s in meta["shots"]:
            assert (out / s["path"]).exists(), f"missing shot clip {s['path']}"
            assert (out / s["jsonPath"]).exists(), f"missing shot json {s['jsonPath']}"

        # The stdout shots echo status per contract §3.
        assert {s["status"] for s in payload["shots"]} <= {"rendered", "cached"}


def test_template_project_compiles():
    """The bundled project skeleton must render end-to-end — including the
    `import series` path (project root on sys.path)."""
    ep = TEMPLATE / "episodes" / "ep001.py"
    with tempfile.TemporaryDirectory() as tmp:
        payload = _run_drama(str(ep), "--out-dir", tmp)
        assert payload.get("ok"), f"skeleton failed: {payload}"
        # The template ships warning-free — it is the canonical example.
        assert not payload.get("warnings"), payload.get("warnings")
        out = Path(tmp)
        assert (out / "ep001.mp4").exists()
        assert (out / "ep001.episode.json").exists()
        assert (out / "ep001.srt").exists()


# -- Error paths ---------------------------------------------------------------


def test_sandbox_rejects_os_import():
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "bad.py"
        bad.write_text(
            "import os\n"
            "def gen_episode():\n"
            "    return {'episode': {'scenes': []}}\n"
        )
        payload = _run_drama(str(bad), "--out-dir", tmp)
    assert not payload["ok"]
    msg = _err_message(payload).lower()
    assert "os" in msg
    assert "not allowed" in msg


def test_sandbox_rejects_subprocess_in_user_code():
    """dramapy runs ffmpeg via subprocess — USER code still must not reach
    it (the attribution trick, not a blanket allow)."""
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "bad.py"
        bad.write_text(
            "import subprocess\n"
            "def gen_episode():\n"
            "    return {'episode': {'scenes': []}}\n"
        )
        payload = _run_drama(str(bad), "--out-dir", tmp)
    assert not payload["ok"]
    msg = _err_message(payload).lower()
    assert "subprocess" in msg and "not allowed" in msg


def test_missing_gen_episode():
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "noentry.py"
        bad.write_text("EPISODE = {'number': 1}\n")
        payload = _run_drama(str(bad), "--out-dir", tmp)
    assert not payload["ok"]
    assert "gen_episode" in _err_message(payload)
    assert payload["error"]["code"] == "VALIDATION_FAILED"


def test_envelope_unknown_key_raises():
    """Contract §1: unknown envelope keys raise TypeError → VALIDATION_FAILED."""
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "extra.py"
        bad.write_text(
            "def gen_episode():\n"
            "    return {'episode': {'scenes': []}, 'seed': 7}\n"
        )
        payload = _run_drama(str(bad), "--out-dir", tmp)
    assert not payload["ok"]
    assert payload["error"]["code"] == "VALIDATION_FAILED"
    assert "seed" in _err_message(payload)


def test_syntax_error_surfaces():
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "broken.py"
        bad.write_text("def gen_episode(:\n")
        payload = _run_drama(str(bad), "--out-dir", tmp)
    assert not payload["ok"]
    assert payload["error"]["code"] == "SYNTAX_ERROR"


def test_infinite_loop_killed_by_timeout():
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "spin.py"
        bad.write_text("while True:\n    pass\n")
        payload = _run_drama(str(bad), "--out-dir", tmp, "--wall-clock-s", "8",
                             timeout=30)
    assert not payload["ok"]
    assert (
        payload.get("timed_out") is True
        or payload["error"]["code"] == "RENDER_TIMEOUT"
    )


# -- Verifiers fire -------------------------------------------------------------


def test_hook_too_long_fires_on_slow_open():
    """A 5s establish open against hook_max_s=3 must produce the §1
    ``hook_too_long`` warning — and prove plain dicts duck-type as Episode."""
    src = (
        "def gen_episode():\n"
        "    return {'episode': {\n"
        "        'number': 1, 'title': 'slow', 'hook_max_s': 3.0,\n"
        "        'scenes': [{'id': 's1', 'location': 'lobby', 'shots': [\n"
        "            {'id': 's1_01', 'kind': 'establish', 'duration_s': 5.0,\n"
        "             'prompt': 'wide'},\n"
        "            {'id': 's1_02', 'kind': 'action', 'duration_s': 3.0,\n"
        "             'prompt': 'turn'},\n"
        "        ]}],\n"
        "        'cliffhanger': 'freeze',\n"
        "    }}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        ep = Path(tmp) / "slow.py"
        ep.write_text(src)
        payload = _run_drama(str(ep), "--out-dir", tmp)
    assert payload.get("ok"), payload
    kinds = {w["kind"] for w in payload.get("warnings", [])}
    assert "hook_too_long" in kinds, payload.get("warnings")


def test_functional_warnings_ride_the_envelope():
    """Project-declared warnings surface with kind=functional (§1)."""
    src = (
        "def gen_episode():\n"
        "    return {\n"
        "        'episode': {'number': 1, 'title': 'w', 'scenes': [\n"
        "            {'id': 's1', 'location': 'x', 'shots': [\n"
        "                {'id': 's1_01', 'kind': 'action', 'duration_s': 3.0,\n"
        "                 'prompt': 'p'}]}],\n"
        "         'cliffhanger': 'freeze'},\n"
        "        'warnings': [{'part': 'scene_1', 'kind': 'functional',\n"
        "                      'detail': 'recap beat exceeds 8s',\n"
        "                      'severity': 'warning'}],\n"
        "    }\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        ep = Path(tmp) / "warned.py"
        ep.write_text(src)
        payload = _run_drama(str(ep), "--out-dir", tmp)
    assert payload.get("ok"), payload
    fns = [w for w in payload.get("warnings", []) if w["kind"] == "functional"]
    assert fns and fns[0]["detail"] == "recap beat exceeds 8s"


# -- scripts/check ---------------------------------------------------------------


def test_check_validates_and_strips_paths():
    recipe = RECIPES / "revenge_ep1.py"
    payload = _run_tool("check", str(recipe))
    assert payload.get("ok"), payload
    assert payload.get("shot_count", 0) >= 8
    for key in ("episode_path", "srt_path", "metadata_path", "shots"):
        assert key not in payload, f"{key} should be stripped (tempdir path)"


# -- scripts/review ----------------------------------------------------------------


def test_review_surfaces_warnings_and_regenerates_board():
    recipe = RECIPES / "revenge_ep1.py"
    with tempfile.TemporaryDirectory() as tmp:
        gen = _run_drama(str(recipe), "--out-dir", tmp)
        assert gen.get("ok"), gen
        board = Path(tmp) / "revenge_ep1_review" / "_board.png"
        poster = Path(tmp) / "revenge_ep1_review" / "_poster.png"
        board.unlink()
        poster.unlink()

        payload = _run_tool("review", tmp)
        assert payload.get("ok"), payload
        assert payload["stem"] == "revenge_ep1"
        assert payload["warnings"] == []
        # Regenerated from the shot clips / episode via ffmpeg fallback.
        assert payload["board_png"] and Path(payload["board_png"]).is_file()
        assert payload["poster_png"] and Path(payload["poster_png"]).is_file()
        assert len(payload["shots"]) == gen["shot_count"]
        for s in payload["shots"]:
            assert s["id"] and Path(s["path"]).is_file()


def test_review_multiple_sidecars_requires_stem(tmp_path: Path):
    for stem in ("alpha", "beta"):
        (tmp_path / f"{stem}.episode.json").write_text("{}", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "review"), str(tmp_path)],
        capture_output=True, text=True, timeout=20,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False
    msg = payload["error"]["message"]
    assert "--stem" in msg and "alpha" in msg and "beta" in msg


# -- Pattern library (references/patterns/*.md) -----------------------------


PATTERN_NAMES = [
    "cold-open-hook",
    "cliffhanger-beat",
    "face-slap-cascade",
    "paywall-gate-episode",
    "recap-flashback",
    "dialogue-shot-writing",
    "reroll-a-shot",
    "cast-consistency",
    "ad-break-tolerant-episode",
    "next-episode-teaser",
]


@pytest.mark.parametrize("pattern", PATTERN_NAMES)
def test_pattern_doc_exists_and_has_required_sections(pattern: str):
    """Each pattern doc lives at the canonical path and carries the donor
    skeleton: H1 slug, **Trigger:**, Why this exists, Pitfalls, and a
    'Use the helper' section pointing at dramalib (the package is the
    source of truth — no copy-paste numbers)."""
    path = SKILL_DIR / "references" / "patterns" / f"{pattern}.md"
    assert path.exists(), f"missing pattern doc: {path}"
    text = path.read_text()
    assert f"# {pattern}" in text, f"{pattern}.md missing H1 heading"
    assert "**Trigger:**" in text, f"{pattern}.md missing **Trigger:** line"
    assert "## Why this exists" in text, f"{pattern}.md missing Why section"
    assert "## Pitfalls" in text, f"{pattern}.md missing Pitfalls section"
    assert "## Use the helper" in text, f"{pattern}.md missing helper section"
    assert "from dramalib" in text, f"{pattern}.md does not point at dramalib"


def test_skill_md_lists_every_pattern():
    """SKILL.md's pattern-library trigger table must reference each pattern
    file by name — otherwise the agent can't find them on demand."""
    skill_md = (SKILL_DIR / "SKILL.md").read_text()
    for pattern in PATTERN_NAMES:
        ref = f"references/patterns/{pattern}.md"
        assert ref in skill_md, f"SKILL.md missing pointer to {ref}"


def test_skill_md_names_the_tier1_references():
    skill_md = (SKILL_DIR / "SKILL.md").read_text()
    for name in (
        "references/beat-structure.md",
        "references/shot-grammar.md",
        "references/assembly-conventions.md",
        "references/series-architecture.md",
    ):
        assert name in skill_md, f"SKILL.md missing pointer to {name}"
