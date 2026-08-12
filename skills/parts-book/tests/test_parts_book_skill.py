"""Skill-layout and docs-drift checks for parts-book."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL_MD = SKILL_DIR / "SKILL.md"
PARTS_TOOL = SKILL_DIR / "scripts" / "parts"


def _frontmatter(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---", f"{path.name} must open with a frontmatter fence"
    end = lines.index("---", 1)
    return [
        line.split(":", 1)[0]
        for line in lines[1:end]
        if line and not line.startswith((" ", "\t"))
    ]


def test_layout_intact():
    assert SKILL_MD.is_file()
    for name in ("__init__.py", "__main__.py", "cli.py"):
        assert (PARTS_TOOL / name).is_file(), f"scripts/parts/{name} missing"


def test_frontmatter_has_exactly_two_fields():
    assert _frontmatter(SKILL_MD) == ["name", "description"]


def test_help_runs():
    proc = subprocess.run(
        [sys.executable, str(PARTS_TOOL), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[:400]
    assert "parts.json" in proc.stdout


def test_skill_md_documents_every_flag():
    """Docs drift: a flag the CLI accepts but the SKILL.md never names is a
    flag the agent will never use."""
    import re

    cli_src = (PARTS_TOOL / "cli.py").read_text(encoding="utf-8")
    doc = SKILL_MD.read_text(encoding="utf-8")
    flags = sorted(set(re.findall(r"add_argument\(\s*\"(--[\w-]+)\"", cli_src)))
    assert "--lookup" in flags, "flag scan found nothing — the test went blind"
    missing = [f for f in flags if f not in doc]
    assert not missing, f"SKILL.md does not document: {missing}"


def test_skill_md_states_the_non_negotiables():
    doc = SKILL_MD.read_text(encoding="utf-8")
    for phrase in (
        "wholly",
        "Basic",
        "47–90s",
        "one exact orderable `C` number",
        "FOOTPRINT CHANGE",
        "golden-blocks.lock.json",
        "unresolved parametric",
        "Do not write a `version`/`summary`/`parts` wrapper",
    ):
        assert phrase in doc, f"SKILL.md is missing the rule about {phrase!r}"
