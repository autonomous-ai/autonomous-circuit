"""Tests for the render-cost estimator (plan-and-approve number)."""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramapy.costs import estimate_episode_cost, format_estimate
from dramapy.spec import (
    ResolvedEpisode,
    ResolvedScene,
    ResolvedSeries,
    ResolvedShot,
)


def _episode():
    shots = [
        ResolvedShot(id="s1", kind="establish", duration_s=5.0, prompt="x", cast=("mei",), line=None, emotion=None),
        ResolvedShot(id="s2", kind="dialogue", duration_s=5.0, prompt="y", cast=("mei",),
                     line="Hello.", emotion=None),
    ]
    return ResolvedEpisode(
        number=1, title="t", hook_max_s=3.0,
        scenes=(ResolvedScene(id="s1", location="loft", shots=tuple(shots)),),
        cliffhanger="freeze", bgm="tense", burn_subtitles=True,
    )


def _series(res=(1080, 1920)):
    return ResolvedSeries(title="t", genre="revenge", style="photoreal-drama",
                          aspect="9:16", resolution=res, fps=24, language="en")


def test_mock_is_free():
    est = estimate_episode_cost(episode=_episode(), series=_series(), provider="mock")
    assert est["total_usd"] == 0.0
    assert "0" in format_estimate(est)


def test_cinematic_costs_more_than_fal():
    ep, ser = _episode(), _series()
    cine = estimate_episode_cost(episode=ep, series=ser, provider="cinematic")
    fal = estimate_episode_cost(episode=ep, series=ser, provider="fal")
    assert cine["total_usd"] > fal["total_usd"] > 0        # cinematic adds keyframe/upscale/turnaround
    assert cine["breakdown"]["keyframe"] > 0 and fal["breakdown"]["keyframe"] == 0


def test_4k_costs_more_than_1080():
    ep = _episode()
    hd = estimate_episode_cost(episode=ep, series=_series((1080, 1920)), provider="cinematic")
    k4 = estimate_episode_cost(episode=ep, series=_series((2160, 3840)), provider="cinematic")
    assert k4["total_usd"] > hd["total_usd"]
    assert k4["res_factor"] > hd["res_factor"]


def test_breakdown_sums_to_total_and_has_per_shot():
    est = estimate_episode_cost(episode=_episode(), series=_series(), provider="cinematic")
    assert abs(sum(est["breakdown"].values()) - est["total_usd"]) < 0.02
    assert len(est["per_shot"]) == 2
    # dialogue shot carries lipsync cost on cinematic
    assert est["breakdown"]["lipsync"] > 0
