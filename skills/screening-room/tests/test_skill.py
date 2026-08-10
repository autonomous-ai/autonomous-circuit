"""Self-contained tests for the screening-room skill.

Run from inside the skill directory:

    python -m pytest tests/

Covers: the skill layout + 2-field frontmatter, the SKILL.md report/rubric
contract, the pure ``parse_screening_report`` used to self-check a critique,
and the bundle CLI end-to-end against a REAL rendered episode (frames written,
manifest shape, and a transposed shot flagged as the orientation defect).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"

sys.path.insert(0, str(SCRIPTS))
from bundle.cli import (  # noqa: E402
    NOTE_DEPARTMENTS,
    RUBRIC_DIMENSIONS,
    actionable_notes,
    parse_screening_report,
)


# -- Layout -----------------------------------------------------------------


def test_layout_intact():
    assert (SKILL_DIR / "SKILL.md").exists()
    assert (SKILL_DIR / "agents" / "claude-code.md").exists()
    for name in ("film-rubric.md", "technical-defects.md", "department-routing.md"):
        assert (SKILL_DIR / "references" / name).exists(), f"missing references/{name}"
    assert (SCRIPTS / "bundle" / "__main__.py").exists()
    assert (SCRIPTS / "bundle" / "cli.py").exists()
    # Vendor target ships tracked docs even before build populates the tree.
    assert (SCRIPTS / "packages" / "dramapy" / "README.md").exists()


def test_skill_frontmatter_has_exactly_two_fields():
    """Contract: SKILL.md frontmatter carries exactly `name` + `description`."""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    end = text.index("\n---", 4)
    front = text[4:end]
    keys = [
        line.split(":", 1)[0].strip()
        for line in front.splitlines()
        if line.strip() and not line.startswith((" ", "\t"))
    ]
    assert keys == ["name", "description"], keys
    assert "name: screening-room" in front


# -- SKILL.md defines the report + rubric contract --------------------------


def test_skill_md_defines_rubric_and_report_contract():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "```screening-report" in text
    for key in ("overall_1_10", "dimension_scores", "pass_at_bar", "notes"):
        assert key in text, f"SKILL.md missing report key {key}"
    # The pass bar is stated explicitly.
    assert "overall_1_10 >= 8" in text or "overall_1_10 >=8" in text
    # All eight rubric dimensions are named.
    rubric = (SKILL_DIR / "references" / "film-rubric.md").read_text(encoding="utf-8")
    for dim in ("hook", "consistency", "cinematography", "audio", "continuity", "technical"):
        assert dim in text and dim in rubric, dim
    # The rotation bug is called out as the safety-net target.
    defects = (SKILL_DIR / "references" / "technical-defects.md").read_text(encoding="utf-8")
    assert "rotation" in defects.lower() and "orientation_aspect" in defects


# -- parse_screening_report (pure, no dramapy) ------------------------------


GOOD_REPORT = textwrap.dedent(
    """
    Here is my verdict.

    ```screening-report
    {
      "overall_1_10": 6,
      "dimension_scores": {"hook": 7, "story": 6, "pacing": 6, "consistency": 5,
        "cinematography": 6, "audio": 7, "continuity": 7, "technical": 3,
        "shareability": 6},
      "pass_at_bar": false,
      "notes": [
        {"department": "vfx", "shot_ids": ["s1_03"], "severity": "blocker",
         "note": "Rotated 90 degrees.", "fix": "Re-render s1_03 in portrait."},
        {"department": "editor", "shot_ids": ["s2_01"], "severity": "minor",
         "note": "Slightly long.", "fix": "Trim 0.5s."}
      ]
    }
    ```
    """
)


def test_parse_good_report_and_actionable_filter():
    report = parse_screening_report(GOOD_REPORT)
    assert report is not None
    assert report["overall_1_10"] == 6
    assert report["pass_at_bar"] is False
    assert set(report["dimension_scores"]) == set(RUBRIC_DIMENSIONS)
    for note in report["notes"]:
        assert note["department"] in NOTE_DEPARTMENTS
    # Only the blocker is actionable; the minor is filtered out.
    act = actionable_notes(report)
    assert len(act) == 1 and act[0]["department"] == "vfx"


def test_parse_picks_last_block_and_rejects_bad_input():
    assert parse_screening_report("no fence here") is None
    assert parse_screening_report("```screening-report\n{not json}\n```") is None
    # Missing a required key → rejected.
    assert (
        parse_screening_report(
            '```screening-report\n{"overall_1_10": 8, "notes": []}\n```'
        )
        is None
    )
    # Two blocks → the last one wins (mirrors the driver's last-line discipline).
    two = (
        '```screening-report\n{"overall_1_10": 1, "dimension_scores": {},'
        ' "pass_at_bar": false, "notes": []}\n```\n'
        '```screening-report\n{"overall_1_10": 9, "dimension_scores": {},'
        ' "pass_at_bar": true, "notes": []}\n```'
    )
    report = parse_screening_report(two)
    assert report["overall_1_10"] == 9 and report["pass_at_bar"] is True


# -- Bundle CLI -------------------------------------------------------------


def test_bundle_help_works():
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "bundle"), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "usage" in proc.stdout.lower()


def _render_episode(root: Path) -> Path:
    """Render a tiny real episode with dramapy's mock provider (repo src on
    path via conftest). Returns the .mp4."""
    from dramapy.generation import generate_episode

    (root).mkdir(parents=True, exist_ok=True)
    (root / "series.py").write_text(
        textwrap.dedent(
            """
            from dramapy.bible import Series, Character
            SERIES = Series(title="T", genre="revenge", style="photoreal-drama",
                            aspect="9:16", resolution=(270, 480), fps=24, language="en")
            CAST = [Character(id="a", name="A", look="woman", voice="f_low_calm", ref_images=[])]
            """
        ),
        encoding="utf-8",
    )
    eps = root / "episodes"
    eps.mkdir(exist_ok=True)
    (eps / "ep001.py").write_text(
        textwrap.dedent(
            """
            import series
            from dramapy.spec import Episode, Scene, Shot
            def gen_episode():
                return {"episode": Episode(number=1, title="T", hook_max_s=5.0, scenes=[
                    Scene(id="s1", location="lobby", shots=[
                        Shot(id="s1_01", kind="establish", duration_s=3, prompt="wide lobby"),
                        Shot(id="s1_02", kind="action", duration_s=3, prompt="he turns"),
                    ])], cliffhanger="freeze", burn_subtitles=False)}
            """
        ),
        encoding="utf-8",
    )
    out = eps / "ep001.mp4"
    generate_episode(eps / "ep001.py", out, provider="mock")
    return out


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_bundle_cli_end_to_end_flags_rotation():
    from dramapy import media

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "proj"
        mp4 = _render_episode(root)

        # Transpose one shot → a landscape clip in a portrait series.
        clip = root / "episodes" / "ep001_shots" / "shot_s1_02.mp4"
        swapped = clip.with_suffix(".swap.mp4")
        media.run_ffmpeg(
            ["-y", "-i", str(clip), "-vf", "transpose=1", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", str(swapped)]
        )
        swapped.replace(clip)

        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "bundle"), str(mp4), "--frames-per-shot", "2"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode in (0, 2), proc.stderr
        manifest = json.loads(proc.stdout.strip().splitlines()[-1])

        assert manifest["ok"] is True, manifest
        assert manifest["frames"], "frames were sampled"
        assert all(Path(f["path"]).is_file() for f in manifest["frames"])
        orient = [d for d in manifest["defects"] if d["kind"] == "orientation_aspect"]
        assert any(d["shot_id"] == "s1_02" for d in orient), manifest["defects"]
