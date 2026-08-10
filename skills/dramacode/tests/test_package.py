"""Tests for the sales-package generator."""

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from dramalib.package import format_sales_package, sales_package


def test_package_composes_title_fantasy_gate():
    pkg = sales_package(genre="billionaire", market="overseas", episodes=60)
    assert pkg["title_options"]                       # from the title generator
    assert pkg["audience"] == "female"                # from the trope table
    assert "overlooked" in pkg["fantasy"] or pkg["fantasy"]
    assert pkg["gate_plan"]["gates"]                  # from gate_plan
    assert pkg["beats"]


def test_author_inputs_override_placeholders():
    pkg = sales_package(genre="revenge", logline="She buried him. He came back richer.",
                        hooks=["The will names the maid.", "He owns the company now."],
                        comparables=["Back from the Brink"])
    assert pkg["logline"].startswith("She buried")
    assert len(pkg["hooks"]) == 2 and pkg["comparables"] == ["Back from the Brink"]


def test_format_is_one_screen_markdown():
    text = format_sales_package(sales_package(genre="werewolf"))
    assert text.startswith("# ") and "fantasy it sells" in text and "hooks" in text.lower()


def test_unknown_genre_raises():
    with pytest.raises(ValueError):
        sales_package(genre="cyberpunk-heist")
