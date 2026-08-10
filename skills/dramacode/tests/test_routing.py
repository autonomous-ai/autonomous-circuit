"""Tests for the model-routing memory (flywheel #4)."""

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from dramalib.routing import (
    COLD_START_ROUTES,
    RoutingMemory,
    best_model,
    record_outcome,
    route,
)


def test_cold_start_before_any_evidence():
    mem = RoutingMemory()
    assert route(mem, shot_type="dialogue") == COLD_START_ROUTES["dialogue"]
    assert best_model(mem, shot_type="dialogue") is None
    # unknown shot-type falls back to a floor default, never crashes
    assert route(mem, shot_type="montage") == COLD_START_ROUTES["establish"]


def test_learns_the_winner_after_enough_trials():
    mem = RoutingMemory()
    for _ in range(3):
        record_outcome(mem, shot_type="action", model="veo-3.1", score=9.0)
        record_outcome(mem, shot_type="action", model="kling-2.5", score=6.0)
    assert best_model(mem, shot_type="action") == "veo-3.1"
    assert route(mem, shot_type="action") == "veo-3.1"   # overrides the cold-start


def test_min_trials_guards_against_one_lucky_render():
    mem = RoutingMemory()
    record_outcome(mem, shot_type="hero", model="wan-2.2", score=10.0)  # only 1 trial
    assert best_model(mem, shot_type="hero") is None
    assert route(mem, shot_type="hero") == COLD_START_ROUTES["hero"]     # still cold-start


def test_roundtrips_json():
    mem = RoutingMemory()
    record_outcome(mem, shot_type="action", model="kling-2.5", score=7.0)
    assert RoutingMemory.from_dict(mem.to_dict()).stats == mem.stats
