"""Tests for the pre-spend render plan."""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramapy.plan import format_plan, render_plan
from dramapy.spec import ResolvedEpisode, ResolvedScene, ResolvedSeries, ResolvedShot


def _ep():
    shots = (
        ResolvedShot(id="s1", kind="action", duration_s=5.0, prompt="x", cast=("mei",),
                     line=None, emotion=None),
        ResolvedShot(id="s2", kind="dialogue", duration_s=5.0, prompt="y", cast=("mei", "rex"),
                     line="You did this.", emotion=None),
    )
    return ResolvedEpisode(number=1, title="The Turn", hook_max_s=3.0,
                           scenes=(ResolvedScene(id="s1", location="loft", shots=shots),),
                           cliffhanger="freeze", bgm="tense", burn_subtitles=True)


def _ser():
    return ResolvedSeries(title="t", genre="revenge", style="photoreal-drama",
                          aspect="9:16", resolution=(2160, 3840), fps=24, language="en")


def test_plan_has_summary_shots_cost_and_gate():
    p = render_plan(episode=_ep(), series=_ser(), provider="cinematic")
    assert p["episode"]["shot_count"] == 2
    assert p["episode"]["duration_s"] == 10.0 and p["episode"]["cliffhanger"] is True
    assert len(p["shots"]) == 2
    assert p["shots"][1]["dialogue"] is True and p["shots"][1]["cast"] == ["mei", "rex"]
    assert p["cost"]["total_usd"] > 0 and all(s["est_usd"] is not None for s in p["shots"])
    assert "APPROVE_BEFORE_SPEND" in p["gate"]


def test_mock_plan_is_free():
    p = render_plan(episode=_ep(), series=_ser(), provider="mock")
    assert p["cost"]["total_usd"] == 0.0


def test_format_plan_is_human_readable():
    text = format_plan(render_plan(episode=_ep(), series=_ser()))
    assert "PLAN — The Turn" in text and "APPROVE_BEFORE_SPEND" in text
    assert "s1" in text and "s2" in text
