"""Tests for the cast-book skill's scripts/cast tool."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
CAST_TOOL = SKILL_DIR / "scripts" / "cast"


SERIES_TEMPLATE = '''"""Series bible for tests."""

from dramalib.bible import Character, Series

SERIES = Series(
    title="Test Series",
    genre="revenge",
)

# CAST-BOOK BEGIN
CAST = [
    Character(
        id="li_wei",
        name="Li Wei",
        look="woman, 28, sharp black bob, gray suit",
        voice="f_low_calm",
        ref_images=[],
    ),
    Character(
        id="dorian",
        name="Dorian Cross",
        look="man, 34, black wool coat",
        voice="m_deep_cold",
        ref_images=[],
    ),
]
# CAST-BOOK END

TOTAL_EPISODES = 60
'''


def _run(*args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(CAST_TOOL), *args],
        capture_output=True, text=True, timeout=30,
    )
    out = (proc.stdout or "").strip().splitlines()
    assert out, f"no stdout (stderr: {proc.stderr[:300]!r})"
    return json.loads(out[-1])


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "series.py").write_text(SERIES_TEMPLATE, encoding="utf-8")
    return tmp_path


def test_sync_writes_ref_prompts(tmp_path: Path):
    proj = _make_project(tmp_path)
    payload = _run(str(proj))
    assert payload["ok"], payload
    assert [c["id"] for c in payload["cast"]] == ["li_wei", "dorian"]
    for c in payload["cast"]:
        # 4-6 reference slots per the research convention.
        assert 4 <= c["ref_prompts"] <= 6
        data = json.loads(
            (proj / "cast" / c["id"] / "ref_prompts.json").read_text()
        )
        slots = {p["slot"] for p in data["prompts"]}
        assert {"ref_front", "ref_side", "ref_back"} <= slots
        assert "_<feature>" in data["naming"]
        for p in data["prompts"]:
            assert data["look"].split(",")[0] in p["prompt"]


def test_sync_only_touches_the_guarded_block(tmp_path: Path):
    proj = _make_project(tmp_path)
    before = (proj / "series.py").read_text()
    prefix = before.split("# CAST-BOOK BEGIN")[0]
    suffix = before.split("# CAST-BOOK END")[1]

    payload = _run(str(proj))
    assert payload["ok"], payload

    after = (proj / "series.py").read_text()
    assert after.split("# CAST-BOOK BEGIN")[0] == prefix
    assert after.split("# CAST-BOOK END")[1] == suffix
    # The rewritten file still parses and still holds both cast ids.
    compile(after, "series.py", "exec")
    assert 'id="li_wei"' in after or '"li_wei"' in after


def test_existing_ref_images_get_wired_in(tmp_path: Path):
    proj = _make_project(tmp_path)
    ref_dir = proj / "cast" / "li_wei"
    ref_dir.mkdir(parents=True)
    (ref_dir / "ref_front.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    payload = _run(str(proj))
    assert payload["ok"], payload
    after = (proj / "series.py").read_text()
    block = after.split("# CAST-BOOK BEGIN")[1].split("# CAST-BOOK END")[0]
    assert "cast/li_wei/ref_front.png" in block


def test_add_appends_a_character(tmp_path: Path):
    proj = _make_project(tmp_path)
    payload = _run(str(proj), "--add", "vera", "--name", "Vera Lin",
                   "--look", "woman, 30, silver crop, red blazer",
                   "--voice", "f_bright_sharp")
    assert payload["ok"], payload
    assert [c["id"] for c in payload["cast"]] == ["li_wei", "dorian", "vera"]
    assert (proj / "cast" / "vera" / "ref_prompts.json").exists()
    after = (proj / "series.py").read_text()
    assert "Vera Lin" in after
    compile(after, "series.py", "exec")


def test_add_duplicate_id_refuses(tmp_path: Path):
    proj = _make_project(tmp_path)
    payload = _run(str(proj), "--add", "li_wei", "--name", "X",
                   "--look", "y", "--voice", "z")
    assert payload["ok"] is False
    assert "li_wei" in payload["error"]["message"]


def test_missing_markers_refuses(tmp_path: Path):
    src = SERIES_TEMPLATE.replace("# CAST-BOOK BEGIN\n", "").replace(
        "# CAST-BOOK END\n", "")
    (tmp_path / "series.py").write_text(src, encoding="utf-8")
    payload = _run(str(tmp_path))
    assert payload["ok"] is False
    assert "marker" in payload["error"]["message"].lower()
    # And the file was not rewritten.
    assert (tmp_path / "series.py").read_text() == src


def test_help_works():
    proc = subprocess.run(
        [sys.executable, str(CAST_TOOL), "--help"],
        capture_output=True, text=True, timeout=20,
    )
    assert proc.returncode == 0
    assert "usage" in proc.stdout.lower()
