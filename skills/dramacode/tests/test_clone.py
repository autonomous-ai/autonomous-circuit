"""Tests for clone-a-drama (the growth hook): title -> genre shape -> recast,
generating an ORIGINAL in the named show's style (never a copy)."""

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from dramalib.clone import clone_from_title, clone_summary, infer_genre


def test_infer_genre_from_trope_keywords():
    assert infer_genre(title="The son-in-law who was a war god") == "zhanshen"
    assert infer_genre(title="Rejected by my alpha") == "werewolf"
    assert infer_genre(title="The CEO's secret wife") == "billionaire"
    assert infer_genre(title="Reborn to take my revenge") in ("chongsheng", "revenge")
    assert infer_genre(title="A fake marriage with the boss") == "contract"


def test_infer_genre_uses_premise_and_falls_back():
    assert infer_genre(title="Untitled", premise="she comes back for revenge") == "revenge"
    assert infer_genre(title="Something vague") == "billionaire"   # safe default, never raises


def test_clone_builds_original_in_shape_and_recasts():
    b = clone_from_title(title="that CEO revenge drama", hero_name="Amara",
                         villain_name="my old boss")
    assert b["inspired_by"] == "that CEO revenge drama"
    assert b["inferred_genre"] in ("billionaire", "revenge")
    # recast applied
    hero = next(c for c in b["cast"] if c["self_insert"])
    assert hero["cast_name"] == "Amara"
    # spine intact (real series)
    assert b["beats"] and b["gate_plan"] and b["episodes"]
    # the "not a copy" framing travels with the data
    assert "not a reproduction" in b["legal_note"]
    assert "Original story" in b["notes"]


def test_clone_requires_a_name():
    with pytest.raises(ValueError):
        clone_from_title(title="   ")


def test_clone_summary_reads_inspired_not_copied():
    b = clone_from_title(title="The Alpha's Rejected Mate", hero_name="Wei")
    s = clone_summary(b)
    assert "inspired by" in s and "not a copy" in s and "Wei" in s
