"""Tests for the zero-friction on-ramps: remix (character swap) + the starter
gallery (one-tap create + locale ordering + surprise_me)."""

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from dramalib import starters
from dramalib.onboarding import series_bible
from dramalib.remix import recast, remix_bible, remix_summary


# -- remix (character swap) ---------------------------------------------------


def test_remix_recasts_hero_and_preserves_spine():
    src = series_bible(genre="revenge", market="overseas")
    mine = remix_bible(source=src, hero_name="Amara", setting="a Lagos law firm")
    # hero recast on the self-insert role, function untouched
    hero = next(c for c in mine["cast"] if c["self_insert"])
    assert hero["cast_name"] == "Amara"
    assert hero["function"] == next(c for c in src["cast"] if c["self_insert"])["function"]
    # spine carried over unchanged
    assert mine["beats"] == src["beats"]
    assert mine["gate_plan"] == src["gate_plan"]
    assert mine["genre"] == src["genre"]
    # provenance + setting recorded
    assert mine["remixed_from"] == src["title"]
    assert mine["setting"] == "a Lagos law firm"
    assert mine["swaps"]["hero"] == "Amara"
    # source is not mutated (pure)
    assert "cast_name" not in src["cast"][0]


def test_remix_recasts_villain():
    src = series_bible(genre="billionaire")
    mine = remix_bible(source=src, villain_name="my ex")
    villain = next(c for c in mine["cast"] if not c["self_insert"])
    assert villain["cast_name"] == "my ex"
    assert mine["swaps"]["villain"] == "my ex"


def test_remix_generates_title_featuring_hero():
    src = series_bible(genre="revenge")
    mine = remix_bible(source=src, hero_name="Wei")
    assert mine["title"] and mine["title_options"]
    # explicit title wins over generation
    titled = remix_bible(source=src, hero_name="Wei", title="Back For Blood")
    assert titled["title"] == "Back For Blood"


def test_recast_out_of_range_raises():
    src = series_bible(genre="revenge")
    with pytest.raises(ValueError):
        recast(bible=src, role_index=99, name="X")


def test_remix_summary_reads_as_your_version():
    src = series_bible(genre="revenge")
    mine = remix_bible(source=src, hero_name="Amara", villain_name="the boss")
    s = remix_summary(mine)
    assert "your version of" in s and "Amara" in s
    # an un-remixed bible summarizes as original
    assert "original" in remix_summary(src)


# -- starter gallery ----------------------------------------------------------


def test_gallery_orders_by_locale():
    cn = starters.gallery(locale="cn")
    assert cn[0]["genre"] == "billionaire"        # cn leads with 霸总
    us = starters.gallery(locale="us-rural")
    assert us[0]["genre"] == "riches"             # rural US leads with second-chance
    # unknown locale falls back but still returns a full feed
    other = starters.gallery(locale="zz")
    assert len(other) == len(starters.STARTERS)


def test_gallery_limit():
    assert len(starters.gallery(limit=3)) == 3


def test_surprise_me_deterministic_in_seed():
    a = starters.surprise_me(seed=0)
    b = starters.surprise_me(seed=1)
    assert a["id"] != b["id"]                      # advancing the seed changes the pick
    assert starters.surprise_me(seed=0)["id"] == a["id"]  # same seed → same pick


def test_starter_bible_is_ready_and_recasts_hero():
    b = starters.starter_bible(starter_id="ceo_secret", hero_name="Mei")
    assert b["starter_id"] == "ceo_secret" and b["pitch"]
    assert b["cast"] and b["beats"] and b["gate_plan"]
    hero = next(c for c in b["cast"] if c["self_insert"])
    assert hero["cast_name"] == "Mei"             # first artifact is already about them
    # 'you' default doesn't force a recast
    plain = starters.starter_bible(starter_id="ceo_secret", hero_name="you")
    assert "remixed_from" not in plain


def test_starter_bible_unknown_id_raises():
    with pytest.raises(ValueError):
        starters.starter_bible(starter_id="does_not_exist")
