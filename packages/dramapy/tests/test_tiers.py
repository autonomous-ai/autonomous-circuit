"""Tests for render tiers (R2): the draft->premiere spend dial behind the aha
ladder, and its effect on the cost estimate + plan."""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramapy import tiers
from dramapy.costs import estimate_episode_cost
from dramapy.plan import render_plan
from dramapy.spec import (
    ResolvedEpisode,
    ResolvedScene,
    ResolvedSeries,
    ResolvedShot,
)


def _episode():
    shots = [
        ResolvedShot(id="s1", kind="establish", duration_s=5.0, prompt="x",
                     cast=("mei",), line=None, emotion=None),
        ResolvedShot(id="s2", kind="dialogue", duration_s=5.0, prompt="y",
                     cast=("mei",), line="Hello.", emotion=None),
    ]
    return ResolvedEpisode(
        number=1, title="Overnight", hook_max_s=3.0,
        scenes=(ResolvedScene(id="s1", location="loft", shots=tuple(shots)),),
        cliffhanger="freeze", bgm="tense", burn_subtitles=True,
    )


def _series(res=(1080, 1920)):
    return ResolvedSeries(title="t", genre="revenge", style="photoreal-drama",
                          aspect="9:16", resolution=res, fps=24, language="en")


def test_resolve_tier_falls_back_safely():
    assert tiers.resolve_tier("draft").name == "draft"
    assert tiers.resolve_tier("nonsense").name == "standard"   # never raises
    assert tiers.resolve_tier(None).name == "standard"


def test_standard_is_unchanged_default():
    ep, ser = _episode(), _series()
    default = estimate_episode_cost(episode=ep, series=ser, provider="cinematic")
    std = estimate_episode_cost(episode=ep, series=ser, provider="cinematic", tier="standard")
    assert default["total_usd"] == std["total_usd"]           # default == standard
    assert std["tier"] == "standard"


def test_draft_is_cheaper_and_drops_finishing_stages():
    ep, ser = _episode(), _series()
    draft = estimate_episode_cost(episode=ep, series=ser, provider="cinematic", tier="draft")
    std = estimate_episode_cost(episode=ep, series=ser, provider="cinematic", tier="standard")
    assert draft["total_usd"] < std["total_usd"]              # the aha trailer is cheap
    # draft skips the ceiling + turnaround
    assert draft["breakdown"]["upscale"] == 0
    assert draft["breakdown"]["lipsync"] == 0
    assert draft["breakdown"]["turnaround"] == 0
    assert draft["tier"] == "draft"
    # standard keeps them
    assert std["breakdown"]["upscale"] > 0 and std["breakdown"]["turnaround"] > 0


def test_draft_renders_at_lower_res_factor():
    ep, ser = _episode(), _series((2160, 3840))              # 4K series
    draft = estimate_episode_cost(episode=ep, series=ser, provider="cinematic", tier="draft")
    std = estimate_episode_cost(episode=ep, series=ser, provider="cinematic", tier="standard")
    assert draft["res_factor"] < std["res_factor"]


def test_plan_shows_the_tier():
    ep, ser = _episode(), _series()
    plan = render_plan(episode=ep, series=ser, provider="cinematic", tier="draft")
    assert plan["tier"] == "draft"
    assert "draft" in plan["summary"]
    assert plan["cost"]["total_usd"] < render_plan(
        episode=ep, series=ser, provider="cinematic", tier="premiere")["cost"]["total_usd"]


def test_apply_to_stages_only_removes():
    t = tiers.TIERS["draft"]
    survived = tiers.apply_to_stages({"keyframe", "i2v", "upscale", "lipsync"}, t)
    assert "i2v" in survived and "keyframe" in survived
    assert "upscale" not in survived and "lipsync" not in survived
