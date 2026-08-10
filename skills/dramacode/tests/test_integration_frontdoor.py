"""Integration: the front-door → craft → eval chain composes end to end.

Unit tests cover each module; this proves they fit together — a genre becomes a
bible, the bible's spine becomes beats, beats become shots, the shots form an
episode, and the binge eval + beat-law both run on it. If any module's shape
drifts from another's expectation, this breaks first.
"""

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from dramalib.evals import BINGE_DIMENSIONS, binge_scorecard
from dramalib.helpers import beat_sheet, shots_from_beats, validate_beat_law
from dramalib.onboarding import series_bible
from dramalib.spec import Episode, Scene


def test_genre_to_bible_to_episode_to_eval():
    # 1. front door: a genre → a cast-complete bible
    bible = series_bible(genre="revenge", market="overseas")
    assert bible["title"] and bible["cast"][0]["self_insert"] is True
    cast_ids = [c["role"] for c in bible["cast"][:2]]

    # 2. craft: the genre spine → beats → shot drafts
    beats = beat_sheet(genre="revenge", episode_no=1, length_s=95.0)
    assert beats[0]["beat"] == "hook" and beats[-1]["beat"] == "cliffhanger"
    shots = shots_from_beats(beats=beats, cast=cast_ids)
    assert shots  # drafted a shot per beat

    # 3. assemble an episode from the drafted shots
    ep = Episode(number=1, title=bible["title"],
                 scenes=[Scene(id="s1", location="the reckoning", shots=list(shots))],
                 cliffhanger="freeze on the summons", hook_max_s=3.0)

    # 4. both evaluators run on the composed episode
    warnings = validate_beat_law(episode=ep)
    assert isinstance(warnings, list)                     # beat law runs
    card = binge_scorecard(episode=ep)
    assert set(card["scores"]) == set(BINGE_DIMENSIONS)  # eval runs, all dims present
    assert card["scores"]["cliffhanger_pull"] is not None    # a proxy dimension scored


def test_bible_gate_matches_market():
    cn = series_bible(genre="revenge", market="cn")
    free = series_bible(genre="revenge", market="free")
    assert cn["gate_plan"]["gates"]                       # cn has fixed gates
    assert free["gate_plan"]["gates"] == []               # free/ad-model has none
