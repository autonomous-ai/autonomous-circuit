"""Tests for institutional memory (flywheel #7)."""

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from dramalib.institutional import (
    CraftCard,
    InstitutionalMemory,
    coverage,
    promote,
    retire,
)


def _mem(cards):
    m = InstitutionalMemory()
    for c in cards:
        m.add_card(c)
    return m


def test_promotes_only_well_supported_positive_patterns():
    m = _mem([
        CraftCard("cold_open_slap", "revenge", 2.0),
        CraftCard("cold_open_slap", "revenge", 3.0),
        CraftCard("cold_open_slap", "revenge", 2.5),   # 3 cards, mean +2.5 → promote
        CraftCard("slow_pan_open", "revenge", 4.0),    # only 1 card → not enough support
    ])
    promoted = promote(m)
    assert [p["pattern"] for p in promoted] == ["cold_open_slap"]
    assert promoted[0]["support"] == 3


def test_one_lucky_run_never_promotes():
    m = _mem([CraftCard("gimmick", "ceo", 9.0)])
    assert promote(m) == []


def test_retire_surfaces_consistently_harmful_patterns():
    m = _mem([
        CraftCard("monologue_open", "revenge", -2.0),
        CraftCard("monologue_open", "revenge", -1.5),
        CraftCard("monologue_open", "revenge", -1.0),
    ])
    r = retire(m)
    assert r and r[0]["pattern"] == "monologue_open" and r[0]["mean_outcome"] < 0


def test_coverage_and_roundtrip():
    m = _mem([CraftCard("a", "revenge", 1.0), CraftCard("b", "revenge", 1.0),
              CraftCard("c", "ceo", 1.0)])
    assert coverage(m) == {"revenge": 2, "ceo": 1}
    assert InstitutionalMemory.from_dict(m.to_dict()).cards == m.cards
