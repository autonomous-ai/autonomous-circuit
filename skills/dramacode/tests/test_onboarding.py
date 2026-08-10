"""Tests for the chat-first onboarding backbone."""

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from dramalib import tables
from dramalib.onboarding import INTAKE_QUESTIONS, series_scaffold


def test_intake_is_three_jargon_free_feeling_questions():
    assert len(INTAKE_QUESTIONS) == 3
    blob = " ".join(INTAKE_QUESTIONS).lower()
    # no craft jargon leaks to the non-producer
    for jargon in ("cliffhanger", "beat sheet", "act ", "reversal", "paywall", "shot"):
        assert jargon not in blob


def test_scaffold_fills_the_blank_page_with_defaults():
    s = series_scaffold(genre="revenge", market="overseas")
    assert s["episodes"] == tables.DEFAULT_SERIES_EPISODES          # ~50
    assert s["episode_length_s"] == tables.DEFAULT_EPISODE_LENGTH_S  # 90-120
    assert s["audience"] == "all"                                    # from the trope
    assert s["beats"]                                                # a real spine
    assert s["gate_plan"]["gates"]                                   # a paywall plan
    assert s["intake_questions"] == INTAKE_QUESTIONS


def test_scaffold_resolves_natural_genre_strings():
    # aliases added with the genre playbook must resolve through onboarding
    for g in ("ceo", "fated-mates", "rags-to-riches", "contract"):
        assert series_scaffold(genre=g)["beats"]


def test_scaffold_rejects_unknown_genre_and_bad_count():
    with pytest.raises(ValueError):
        series_scaffold(genre="cyberpunk-heist")
    with pytest.raises(ValueError):
        series_scaffold(genre="revenge", episodes=0)


def test_free_market_scaffold_has_no_gates():
    s = series_scaffold(genre="revenge", market="free")
    assert s["gate_plan"]["gates"] == []
    assert s["gate_plan"]["ad_break_tolerant"] is True
