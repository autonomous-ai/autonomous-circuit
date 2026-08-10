"""Binge metrics — the ground-truth that defines "bingeable", and the map
from each real audience metric back to the eval dimension it grades and the fix.

Today the reward signal is the deterministic proxy (``dramalib.evals``). The
*real* reward is audience behavior — completion, next-episode starts, returns,
payment (flywheel.md loop #8). We don't have a network yet, so this module is the
**contract**: the canonical metric names + definitions + healthy target bands
(from the market study — directional, to be calibrated on our own data, NOT our
measurements) + the mapping metric → binge dimension → rework note. When the
network ships, it emits these events and ``diagnose_metrics`` turns them straight
into targeted fixes, superseding the proxy.

Honesty note: the target bands are industry ranges from the research
(`docs/short-drama-playbook.md` Part 3), flagged as targets, not observed values.
"""

from __future__ import annotations

# metric -> (what it measures, healthy target band, the eval dimension it grades)
# Target bands are directional research ranges to calibrate against, not measured.
BINGE_METRICS: dict[str, dict] = {
    "hook_retention_3s": {
        "measures": "share still watching at 3s (the golden-3-seconds test)",
        "target": (0.55, 1.0),          # most decide by ~6s; 3s is the scroll gate
        "dimension": "hook_strength",
    },
    "episode_completion": {
        "measures": "share who finish the episode",
        "target": (0.60, 1.0),
        "dimension": "pacing_fit",
    },
    "next_episode_start": {
        "measures": "share who start the NEXT episode — the binge pull",
        "target": (0.60, 1.0),          # the single most important binge metric
        "dimension": "cliffhanger_pull",
    },
    "binge_depth": {
        "measures": "avg consecutive episodes per session",
        "target": (3.0, 100.0),
        "dimension": "bingeability",
    },
    "d1_return": {
        "measures": "day-1 return rate",
        "target": (0.20, 1.0),          # research: D1 ~20-30%
        "dimension": "bingeability",
    },
    "d7_return": {
        "measures": "day-7 return rate",
        "target": (0.08, 1.0),          # research: D7 ~8-12%
        "dimension": "bingeability",
    },
    "paywall_conversion": {
        "measures": "share of viewers who pay at the gate",
        "target": (0.02, 1.0),          # research: ~2-5% install→payer
        "dimension": "wish_fulfillment",
    },
    "ad_hook_ctr": {
        "measures": "click-through on the ad-cut that feeds paid UA",
        "target": (0.01, 1.0),
        "dimension": "clip_ability",
    },
}

# dimension -> the mechanical rework note. One source of truth, shared with the
# eval + the auto-rework loop (dramalib.evals.REWORK_FIXES).
from dramalib.evals import REWORK_FIXES as _REWORK


def diagnose_metrics(*, observed: dict) -> list[dict]:
    """Turn observed audience metrics into targeted rework.

    ``observed``: ``{metric_name: value}`` (unknown names ignored; missing ones
    skipped). For each metric below its healthy target band, returns
    ``{"metric", "value", "target", "dimension", "fix"}`` — the underperforming
    binge dimension and the specific fix. Empty list = everything healthy.

    This is the audience-reward loop's action step (flywheel #8): real signal →
    which binge dimension failed → the fix that changes the next series.
    """
    out: list[dict] = []
    for name, value in (observed or {}).items():
        spec = BINGE_METRICS.get(name)
        if spec is None:
            continue
        lo, _hi = spec["target"]
        if float(value) < lo:
            dim = spec["dimension"]
            out.append({
                "metric": name,
                "value": float(value),
                "target": spec["target"],
                "dimension": dim,
                "fix": _REWORK.get(dim, "review this dimension"),
            })
    # worst (largest shortfall vs the target floor) first
    out.sort(key=lambda r: (r["target"][0] - r["value"]), reverse=True)
    return out
