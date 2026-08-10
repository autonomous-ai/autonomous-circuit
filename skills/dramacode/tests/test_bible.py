"""Tests for the archetype casting kit + the series-bible generator."""

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from dramalib.archetypes import archetypes_for
from dramalib.onboarding import format_bible, series_bible


def test_archetypes_self_insert_first_and_unique():
    roster = archetypes_for(genre="ceo")           # -> bazong via alias
    assert roster and roster[0]["self_insert"] is True
    assert sum(1 for r in roster if r["self_insert"]) == 1   # exactly one self-insert


def test_archetypes_resolve_aliases():
    for g in ("fated-mates", "闪婚", "rags-to-riches", "revenge"):
        assert archetypes_for(genre=g)             # non-empty roster


def test_archetypes_unknown_genre_raises():
    with pytest.raises(ValueError):
        archetypes_for(genre="cyberpunk-heist")


def test_bible_composes_title_cast_gate_arc():
    b = series_bible(genre="billionaire", market="overseas", episodes=60)
    assert b["title"] and b["title_options"]
    assert b["audience"] == "female"
    assert b["cast"][0]["self_insert"] is True
    assert b["episodes"] == 60 and b["episode_length_s"] == (90.0, 120.0)
    assert b["gate_plan"]["gates"] and b["arc"]
    assert "wound" in b["ep1_wound"] or b["ep1_wound"]


def test_bible_title_and_wound_overrides():
    b = series_bible(genre="revenge", title="Back From Nothing", wound="stripped of her company on live TV")
    assert b["title"] == "Back From Nothing" and "live TV" in b["ep1_wound"]


def test_format_bible_is_readable():
    text = format_bible(series_bible(genre="werewolf"))
    assert "SERIES BIBLE" in text and "self-insert" in text and "Pay-gate" in text
