"""Tests for shot-to-shot continuity (#34): the chain planner (pure) and the
tail-frame extractor (real ffmpeg on a synthesized clip)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramapy import continuity, media


def _shot(id, kind="action", cast=("lin",), emotion=""):
    return SimpleNamespace(id=id, kind=kind, cast=list(cast), emotion=emotion)


def _episode(*scenes):
    return SimpleNamespace(scenes=list(scenes))


def _scene(id, shots):
    return SimpleNamespace(id=id, shots=list(shots))


# -- chain_plan ---------------------------------------------------------------


def test_chains_continuous_same_subject_action():
    ep = _episode(_scene("s1", [
        _shot("a", cast=("lin",)),
        _shot("b", cast=("lin",)),
        _shot("c", cast=("lin",)),
    ]))
    links = continuity.chain_plan(episode=ep)
    assert [(l.from_id, l.to_id) for l in links] == [("a", "b"), ("b", "c")]
    # collapses into one continuous run
    assert continuity.chains(episode=ep) == [["a", "b", "c"]]


def test_scene_boundary_breaks_chain():
    ep = _episode(
        _scene("s1", [_shot("a"), _shot("b")]),
        _scene("s2", [_shot("c")]),
    )
    links = continuity.chain_plan(episode=ep)
    assert [(l.from_id, l.to_id) for l in links] == [("a", "b")]  # no a→c / b→c
    assert continuity.chains(episode=ep) == [["a", "b"], ["c"]]


def test_insert_and_establish_break_the_chain_both_sides():
    ep = _episode(_scene("s1", [
        _shot("a"),
        _shot("ins", kind="insert"),   # cutaway detail
        _shot("b"),
        _shot("est", kind="establish"),
        _shot("c"),
    ]))
    # nothing chains: every adjacent pair straddles a cutaway/establish
    assert continuity.chain_plan(episode=ep) == []
    assert continuity.chains(episode=ep) == [["a"], ["ins"], ["b"], ["est"], ["c"]]


def test_different_lead_subject_breaks_chain():
    ep = _episode(_scene("s1", [
        _shot("a", cast=("lin",)),
        _shot("b", cast=("marcus",)),
    ]))
    assert continuity.chain_plan(episode=ep) == []


def test_emotion_flip_breaks_chain():
    ep = _episode(_scene("s1", [
        _shot("a", emotion="contempt"),
        _shot("b", emotion="triumph"),
    ]))
    assert continuity.chain_plan(episode=ep) == []
    # but a held emotion chains
    ep2 = _episode(_scene("s1", [
        _shot("a", emotion="dread"),
        _shot("b", emotion="dread"),
    ]))
    assert len(continuity.chain_plan(episode=ep2)) == 1


def test_castless_shots_never_chain():
    ep = _episode(_scene("s1", [_shot("a", cast=()), _shot("b", cast=())]))
    assert continuity.chain_plan(episode=ep) == []


def test_chains_cover_every_shot_in_order():
    ep = _episode(_scene("s1", [
        _shot("a"), _shot("b"),
        _shot("ins", kind="insert"),
        _shot("c"), _shot("d"),
    ]))
    flat = [s for run in continuity.chains(episode=ep) for s in run]
    assert flat == ["a", "b", "ins", "c", "d"]  # union == full list, order kept


# -- tail_frame ---------------------------------------------------------------


def test_tail_frame_extracts_a_png(tmp_path):
    clip = tmp_path / "clip.mp4"
    # a 1s synthetic clip — no network, no provider
    media.run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=24",
        "-pix_fmt", "yuv420p", "-y", str(clip),
    ])
    out = continuity.tail_frame(clip=clip, out=tmp_path / "frames" / "seed.png")
    assert out.is_file() and out.suffix == ".png"
    info = media.probe_media(out)
    assert info.width == 64 and info.height == 64
