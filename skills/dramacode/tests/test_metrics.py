"""Tests for the binge-metrics contract + diagnose_metrics."""

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from dramalib.metrics import BINGE_METRICS, diagnose_metrics


def test_every_metric_maps_to_a_dimension_and_a_band():
    for name, spec in BINGE_METRICS.items():
        assert spec["dimension"], name
        lo, hi = spec["target"]
        assert 0.0 <= lo <= hi


def test_healthy_metrics_diagnose_clean():
    healthy = {"hook_retention_3s": 0.7, "next_episode_start": 0.75, "d7_return": 0.10}
    assert diagnose_metrics(observed=healthy) == []


def test_low_next_episode_start_routes_to_cliffhanger_fix():
    diag = diagnose_metrics(observed={"next_episode_start": 0.2})
    assert len(diag) == 1
    assert diag[0]["dimension"] == "cliffhanger_pull"
    assert "peak" in diag[0]["fix"]


def test_worst_shortfall_ranked_first_and_unknowns_ignored():
    diag = diagnose_metrics(observed={
        "hook_retention_3s": 0.50,   # shortfall 0.05
        "next_episode_start": 0.10,  # shortfall 0.50 (worst)
        "not_a_metric": 0.0,         # ignored
    })
    assert [d["metric"] for d in diag] == ["next_episode_start", "hook_retention_3s"]
