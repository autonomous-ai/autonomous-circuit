"""Tests for the title generator."""

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from dramalib.titles import TRIGGER_WORDS, title_candidates


def test_trigger_bank_nonempty():
    assert "Billionaire" in TRIGGER_WORDS and "Alpha" in TRIGGER_WORDS


def test_genre_titles_fill_and_respect_overrides():
    t = title_candidates(genre="ceo", role="Nurse")   # ceo -> bazong via alias
    assert t and any("Nurse" in x for x in t)
    assert all(x and "{" not in x for x in t)          # every slot filled, no braces left


def test_aliases_resolve():
    for g in ("fated-mates", "闪婚", "rags-to-riches", "revenge"):
        titles = title_candidates(genre=g)
        assert titles and all("{" not in x for x in titles)


def test_unknown_genre_falls_back_not_crash():
    t = title_candidates(genre="cyberpunk-heist")
    assert t and all("{" not in x for x in t)


def test_n_caps_count():
    assert len(title_candidates(genre="werewolf", n=2)) <= 2
