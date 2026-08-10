"""Tests for the binge eval (the eval team's script-stage pre-check)."""

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from dramalib.evals import (
    BINGE_DIMENSIONS,
    JUDGMENT_DIMENSIONS,
    binge_flags,
    binge_reward,
    binge_rework,
    binge_scorecard,
    best_variant,
    combine_binge,
    rank_variants,
    series_binge_flags,
)
from dramalib.spec import Episode, Scene, Shot


def _episode(shots, *, cliffhanger="the letter burns"):
    return Episode(number=1, title="t", scenes=[Scene(id="s1", location="loft", shots=shots)],
                   cliffhanger=cliffhanger)


def _healthy_shots():
    # ~100s, varied emotions, opens on action (no establish), tight cliff.
    return [
        Shot(id="s1_01", kind="action", duration_s=8.0, prompt="slap", emotion="shock"),
        Shot(id="s1_02", kind="dialogue", duration_s=6.0, cast=["a"], line="You did this.",
             emotion="rage"),
        Shot(id="s1_03", kind="action", duration_s=9.0, prompt="reveal", emotion="triumph"),
        Shot(id="s1_04", kind="dialogue", duration_s=7.0, cast=["a"], line="I remember.",
             emotion="grief"),
    ] + [Shot(id=f"s1_1{i}", kind="action", duration_s=8.0, prompt="beat", emotion="dread")
         for i in range(9)]  # pad toward the 90-120s band


def test_healthy_episode_scores_high_and_no_flags():
    ep = _episode(_healthy_shots())
    card = binge_scorecard(episode=ep)
    assert card["scores"]["hook_strength"] == 9      # opens on action
    assert card["scores"]["cliffhanger_pull"] == 9   # cliff set, final shot tight
    assert card["scores"]["pacing_fit"] == 9         # in the 90-120s band
    # judgment dimensions are honestly deferred, not faked
    assert card["scores"]["wish_fulfillment"] is None
    assert BINGE_DIMENSIONS["bingeability"] == "judgment"
    assert card["flags"] == []


def test_too_short_episode_flagged():
    shots = [Shot(id="s1_01", kind="action", duration_s=8.0, prompt="x", emotion="shock"),
             Shot(id="s1_02", kind="dialogue", duration_s=6.0, cast=["a"], line="Hi", emotion="calm")]
    flags = binge_flags(episode=_episode(shots))
    assert any("episode_too_short" in f["detail"] for f in flags)
    assert all(f["kind"] == "binge" for f in flags)


def test_flat_emotion_flagged_only_when_authored():
    flat = [Shot(id=f"s1_0{i}", kind="action", duration_s=8.0, prompt="x", emotion="sad")
            for i in range(1, 6)]
    assert any("flat_emotion" in f["detail"] for f in binge_flags(episode=_episode(flat)))
    # unset emotions are NOT a flatness signal
    unset = [Shot(id=f"s1_0{i}", kind="action", duration_s=8.0, prompt="x") for i in range(1, 6)]
    assert not any("flat_emotion" in f["detail"] for f in binge_flags(episode=_episode(unset)))


def test_missing_cliffhanger_tanks_pull():
    ep = _episode(_healthy_shots(), cliffhanger=None)
    assert binge_scorecard(episode=ep)["scores"]["cliffhanger_pull"] == 1


def _weak_shots():
    # slow establish open, too short, all one emotion -> low reward + flags
    return [Shot(id="s1_01", kind="establish", duration_s=6.0, prompt="wide city", emotion="calm"),
            Shot(id="s1_02", kind="action", duration_s=6.0, prompt="x", emotion="calm"),
            Shot(id="s1_03", kind="dialogue", duration_s=6.0, cast=["a"], line="Hm", emotion="calm"),
            Shot(id="s1_04", kind="action", duration_s=6.0, prompt="x", emotion="calm")]


def test_reward_prefers_the_bingeable_variant():
    strong = binge_reward(episode=_episode(_healthy_shots()))
    weak = binge_reward(episode=_episode(_weak_shots(), cliffhanger=None))
    assert strong > weak


def test_rank_variants_orders_best_first_and_is_stable():
    good = ("good", _episode(_healthy_shots()))
    bad = ("bad", _episode(_weak_shots(), cliffhanger=None))
    ranked = rank_variants(variants=[bad, good])
    assert ranked[0]["label"] == "good" and ranked[0]["rank"] == 0
    assert ranked[0]["reward"] >= ranked[1]["reward"]
    assert best_variant(variants=[bad, good]) is good[1]


def test_rank_variants_accepts_bare_episodes_and_rejects_empty():
    ranked = rank_variants(variants=[_episode(_healthy_shots())])
    assert ranked[0]["label"] == "variant_0"
    import pytest as _pytest
    with _pytest.raises(ValueError):
        rank_variants(variants=[])


def test_rework_is_empty_for_a_healthy_episode():
    assert binge_rework(episode=_episode(_healthy_shots())) == []


def test_rework_lists_flags_first_then_weak_dimensions_with_fixes():
    actions = binge_rework(episode=_episode(_weak_shots(), cliffhanger=None))
    assert actions[0]["kind"] == "flag"                      # flags cap the verdict → first
    dims = [a for a in actions if a["kind"] == "dimension"]
    assert any(a["dimension"] == "cliffhanger_pull" for a in dims)
    cliff = next(a for a in dims if a["dimension"] == "cliffhanger_pull")
    assert "peak" in cliff["fix"]                            # act-on-able, not just a number
    assert all("fix" in a and a["fix"] for a in actions)


def test_combine_fills_all_seven_dims_and_passes_when_strong():
    v = combine_binge(episode=_episode(_healthy_shots()),
                          judgment={"wish_fulfillment": 8, "bingeability": 8, "clip_ability": 7})
    assert all(v["scores"][d] is not None for d in BINGE_DIMENSIONS)  # 7/7 filled
    assert set(JUDGMENT_DIMENSIONS) == {"wish_fulfillment", "bingeability", "clip_ability"}
    assert v["pass"] is True and v["overall"] >= 6.0


def test_combine_hard_fails_on_a_killer_regardless_of_good_judgment():
    # a weak episode (flags + dead hook) can't be rescued by high judgment scores
    v = combine_binge(episode=_episode(_weak_shots(), cliffhanger=None),
                          judgment={"wish_fulfillment": 10, "bingeability": 10, "clip_ability": 10})
    assert v["pass"] is False
    assert v["flags"] or any(s is not None and s <= 2 for s in v["scores"].values())


def test_series_flags_length_and_gate():
    assert any("series_too_short" in f["detail"] for f in series_binge_flags(episode_count=12, gates=[5]))
    assert any("no_gate_plan" in f["detail"] for f in series_binge_flags(episode_count=50, gates=None))
    # a healthy 50-ep series with a gate plan is clean
    assert series_binge_flags(episode_count=50, gates=[10, 20, 30]) == []
