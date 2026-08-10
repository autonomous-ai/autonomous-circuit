"""Tests for the taste loop (per-creator preference learning)."""

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from dramalib.taste import (
    TasteProfile,
    avoided,
    bias_scaffold,
    observe,
    preferred_genre,
)


def test_affinity_accumulates_and_picks_a_preferred_genre():
    p = TasteProfile(creator_id="mom")
    observe(p, event={"kind": "genre", "key": "revenge", "signal": "love"})
    observe(p, event={"kind": "genre", "key": "revenge", "signal": "accept"})
    observe(p, event={"kind": "genre", "key": "werewolf", "signal": "reject"})
    assert p.genre["revenge"] == 3 and p.genre["werewolf"] == -1
    assert preferred_genre(p) == "revenge"


def test_tie_does_not_guess_a_preferred_genre():
    p = TasteProfile()
    observe(p, event={"kind": "genre", "key": "revenge", "signal": "accept"})
    observe(p, event={"kind": "genre", "key": "ceo", "signal": "accept"})
    assert preferred_genre(p) is None


def test_kills_surface_after_threshold():
    p = TasteProfile()
    observe(p, event={"kind": "kill", "key": "love triangle"})
    assert avoided(p) == []                       # once isn't a pattern
    observe(p, event={"kind": "kill", "key": "love triangle"})
    assert avoided(p) == ["love triangle"]


def test_pace_tone_and_notes_accumulate_bounded():
    p = TasteProfile()
    observe(p, event={"kind": "pace", "delta": 1})
    observe(p, event={"kind": "tone", "delta": -1})
    for i in range(25):
        observe(p, event={"kind": "note", "text": f"n{i}"})
    assert p.pace == 1 and p.tone == -1
    assert len(p.notes) == 20 and p.notes[-1] == "n24"  # recent tail only


def test_bias_scaffold_is_nondestructive_and_roundtrips():
    p = TasteProfile()
    observe(p, event={"kind": "genre", "key": "revenge", "signal": "love"})
    biased = bias_scaffold(p, scaffold={"episodes": 50})
    assert biased["episodes"] == 50               # original kept
    assert biased["taste"]["preferred_genre"] == "revenge"
    assert TasteProfile.from_dict(p.to_dict()).genre == p.genre  # JSON round-trip
