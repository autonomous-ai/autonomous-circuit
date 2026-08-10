"""Tests for the dramalib helper library.

Two layers, mirroring the donor's test_cadlib:

  1. Every helper runs through the REAL sandboxed runner (``scripts/drama``)
     — same path the agent uses — so allow-list regressions and helper bugs
     surface in one shot.
  2. In-process unit tests for the ValueError discipline and the beat-law
     math (fast, no subprocess).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def _run_drama(*args: str, timeout: int = 90) -> dict:
    cmd = [sys.executable, str(SCRIPTS / "drama"), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout or "").strip().splitlines()
    if not out:
        return {
            "ok": False,
            "error": {
                "code": "RUNTIME_ERROR",
                "message": f"no stdout (stderr: {proc.stderr[:300]!r})",
            },
        }
    return json.loads(out[-1])


# A minimal clean episode every probe can return after exercising a helper.
_CLEAN_EPISODE = """
EP = {
    'number': 1, 'title': 'probe', 'hook_max_s': 3.0,
    'scenes': [{'id': 's1', 'location': 'probe stage', 'shots': [
        {'id': 's1_01', 'kind': 'action', 'duration_s': 3.0,
         'prompt': 'probe shot', 'cast': []},
    ]}],
    'cliffhanger': 'freeze',
}
"""


# -- Helpers run inside the real sandbox --------------------------------------

HELPER_PROBES = {
    "tables.import": """
from dramalib import tables
assert tables.HOOK_MAX_S == 3.0
assert tables.BEAT_INTERVAL_S == (20.0, 30.0)
assert tables.PAYWALL_GATES['cn'] == [10, 20, 30]
assert tables.SUBTITLE['max_chars_zh'] == 14
assert tables.AUDIO['dialogue_over_bed_db'] == 10.0
""",
    "tropes.trope_for_genre": """
from dramalib.tropes import trope_for_genre
t = trope_for_genre(genre='revenge-romance')
assert 'face_slap_cascade' in t['beats']
assert trope_for_genre(genre='重生')['zh'] == '重生'
""",
    "helpers.beat_sheet": """
from dramalib.helpers import beat_sheet
beats = beat_sheet(genre='revenge', episode_no=1, length_s=75.0)
assert beats[0]['beat'] == 'hook' and beats[0]['t_end'] == 3.0
assert beats[-1]['beat'] == 'cliffhanger' and beats[-1]['t_end'] == 75.0
""",
    "helpers.shots_from_beats": """
from dramalib.helpers import beat_sheet, shots_from_beats
beats = beat_sheet(genre='zhuixu', episode_no=1, length_s=60.0)
drafts = shots_from_beats(beats=beats, cast=['a', 'b'])
assert all(1.5 <= s.duration_s <= 10.0 for s in drafts)
assert all(s.cast and s.line for s in drafts if s.kind == 'dialogue')
""",
    "helpers.validate_beat_law": """
from dramalib.helpers import validate_beat_law
slow = {
    'number': 1, 'title': 'slow', 'hook_max_s': 3.0,
    'scenes': [{'id': 's1', 'location': 'x', 'shots': [
        {'id': 's1_01', 'kind': 'establish', 'duration_s': 5.0, 'prompt': 'w'},
        {'id': 's1_02', 'kind': 'action', 'duration_s': 3.0, 'prompt': 'a'},
    ]}],
    'cliffhanger': 'freeze',
}
warns = validate_beat_law(episode=slow)
assert any('hook_too_long' in w['detail'] for w in warns)
assert all(w['kind'] == 'functional' for w in warns)
""",
    "helpers.recap_flashback": """
from dramalib.helpers import recap_flashback
scene = recap_flashback(prev_cliffhanger='freeze on the burning letter')
assert scene.id == 's0' and len(scene.shots) == 2
assert sum(s.duration_s for s in scene.shots) <= 10.0
""",
    "helpers.gate_plan": """
from dramalib.helpers import gate_plan
assert gate_plan(market='cn', total_episodes=25)['gates'] == [10, 20]
assert gate_plan(market='free', total_episodes=60)['ad_break_tolerant']
first = gate_plan(market='overseas', total_episodes=60)['gates'][0]
assert 5 <= first <= 12
""",
    "helpers.episode_length_and_clamp": """
from dramalib.helpers import clamp_duration, episode_length_for
assert episode_length_for(format='manju') == (60.0, 180.0)
assert clamp_duration(kind='insert') == 1.5        # research-true (floor 1.5)
assert clamp_duration(kind='establish', duration_s=4) == 4.0
assert clamp_duration(kind='action', duration_s=99) == 10.0  # hard cap
""",
    "spec.dataclasses": """
from dramalib.bible import Character, Series
from dramalib.spec import Episode, Scene, Shot
s = Series(title='t', genre='revenge')
assert s.fps == 24 and s.resolution == (1080, 1920)
c = Character(id='a', name='A', look='x', voice='v')
assert c.ref_images == []
ep = Episode(number=1, title='t', scenes=[Scene(id='s1', location='l',
     shots=[Shot(id='s1_01', kind='action', duration_s=3.0)])])
assert ep.burn_subtitles and ep.hook_max_s == 3.0
""",
}


@pytest.mark.parametrize("name,code", list(HELPER_PROBES.items()), ids=list(HELPER_PROBES))
def test_helper_runs_in_sandbox(name: str, code: str):
    """Each helper works inside the sandboxed runner with representative
    params — proving the allow-list admits dramalib and the helpers hold
    their contracts under restriction."""
    src = code + _CLEAN_EPISODE + (
        "def gen_episode():\n"
        "    return {'episode': EP}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / f"{name.replace('.', '_')}.py"
        probe.write_text(src)
        payload = _run_drama(str(probe), "--out-dir", tmp)
        assert payload.get("ok"), f"{name} failed: {payload}"
        assert not payload.get("warnings"), payload.get("warnings")


# -- ValueError discipline (in-process) ---------------------------------------


def test_value_errors_point_at_the_spec():
    from dramalib.helpers import (
        beat_sheet,
        clamp_duration,
        episode_length_for,
        gate_plan,
        recap_flashback,
        shots_from_beats,
    )
    from dramalib.tropes import trope_for_genre

    with pytest.raises(ValueError):
        clamp_duration(kind="jump-scare")
    with pytest.raises(ValueError):
        beat_sheet(genre="nonexistent-genre", episode_no=1, length_s=60)
    with pytest.raises(ValueError):
        beat_sheet(genre="revenge", episode_no=0, length_s=60)
    with pytest.raises(ValueError):
        beat_sheet(genre="revenge", episode_no=1, length_s=10)
    with pytest.raises(ValueError):
        beat_sheet(genre="revenge", episode_no=1, length_s=60, market="mars")
    with pytest.raises(ValueError):
        shots_from_beats(beats=[], cast=["a"])
    with pytest.raises(ValueError):
        shots_from_beats(beats=[{"beat": "hook", "purpose": "x"}], cast=[])
    with pytest.raises(ValueError):
        recap_flashback(prev_cliffhanger="")
    with pytest.raises(ValueError):
        recap_flashback(prev_cliffhanger="x", duration_s=20)
    with pytest.raises(ValueError):
        gate_plan(market="lunar", total_episodes=60)
    with pytest.raises(ValueError):
        gate_plan(market="cn", total_episodes=0)
    with pytest.raises(ValueError):
        episode_length_for(format="feature-film")
    with pytest.raises(ValueError):
        trope_for_genre(genre="cyberpunk-heist")


# -- Beat-law math (in-process) -------------------------------------------------


def test_beat_sheet_obeys_the_law():
    from dramalib import tables
    from dramalib.helpers import beat_sheet

    for length in (45.0, 75.0, 120.0, 180.0):
        beats = beat_sheet(genre="fuchou", episode_no=1, length_s=length)
        assert beats[0]["beat"] == "hook"
        assert beats[0]["t_end"] <= tables.HOOK_MAX_S
        assert beats[1]["beat"] == "world"
        assert beats[1]["t_end"] <= tables.WORLD_BY_S
        reversal = next(b for b in beats if b["beat"] == "first_reversal")
        assert reversal["t_end"] <= tables.FIRST_REVERSAL_BY_S
        assert beats[-1]["beat"] == "cliffhanger"
        assert beats[-1]["t_end"] == pytest.approx(length)
        cliff_len = beats[-1]["t_end"] - beats[-1]["t_start"]
        assert cliff_len <= tables.CLIFFHANGER_WINDOW_S[1] + 1e-9
        # Contiguous, monotonic, and no beat drags past the interval cap
        # (+tolerance for the remainder-absorbing tail beat).
        for prev, cur in zip(beats, beats[1:]):
            assert cur["t_start"] == pytest.approx(prev["t_end"])
        for b in beats:
            if b["beat"] == "escalation":
                assert (b["t_end"] - b["t_start"]) <= tables.BEAT_INTERVAL_S[1] + 1e-9


def test_shots_from_beats_drafts_are_honest():
    """Drafts must carry TODO markers — and validate_beat_law must call
    them out until the writer replaces them."""
    from dramalib.helpers import beat_sheet, shots_from_beats, validate_beat_law

    beats = beat_sheet(genre="billionaire", episode_no=1, length_s=60.0)
    drafts = shots_from_beats(beats=beats, cast=["lead", "foil"])
    assert any("TODO" in (s.prompt or "") for s in drafts)

    ep = {
        "number": 1, "title": "draft", "hook_max_s": 3.0,
        "scenes": [{"id": "s1", "location": "x",
                    "shots": [vars(s) if hasattr(s, "__dict__") else s
                              for s in drafts]}],
        "cliffhanger": "freeze",
    }
    warns = validate_beat_law(episode=ep)
    assert any("placeholder" in w["detail"] for w in warns)


def test_recap_flashback_respects_the_cap():
    from dramalib.helpers import recap_flashback

    scene = recap_flashback(prev_cliffhanger="the slap", duration_s=10.0)
    assert sum(s.duration_s for s in scene.shots) <= 10.0
    for s in scene.shots:
        assert 1.5 <= s.duration_s <= 10.0
