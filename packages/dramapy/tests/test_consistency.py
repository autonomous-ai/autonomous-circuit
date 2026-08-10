"""Tests for the pre-i2v consistency gate (checker + reroll logic)."""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramapy import consistency  # noqa: E402
from dramapy.errors import ProviderError  # noqa: E402
from dramapy.providers.base import ShotContext  # noqa: E402
from dramapy.providers.cinematic import CinematicProvider  # noqa: E402
from dramapy.spec import ResolvedCharacter, ResolvedSeries, ResolvedShot  # noqa: E402


class FakeVlm:
    """Client whose .run returns a scripted YES/NO per call (or raises)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def run(self, model, payload, *, budget_s=None, label=None):
        a = self.answers[self.calls] if self.calls < len(self.answers) else "YES"
        self.calls += 1
        if a == "ERR":
            raise ProviderError("boom")
        return {"output": a}


def _char(name="Mei"):
    return ResolvedCharacter(id=name.lower(), name=name, look="a woman", voice="f", ref_images=())


# -- make_checker gating -------------------------------------------------------

def test_gate_off_by_default():
    assert consistency.make_checker(object(), env={}) is None
    assert consistency.gate_enabled({}) is False


def test_gate_on_with_env():
    c = consistency.make_checker(object(), env={"VIDEO_CONSISTENCY_GATE": "on"})
    assert isinstance(c, consistency.VlmConsistencyChecker)


# -- checker: which characters are missing -------------------------------------

def test_confident_no_is_missing_yes_and_error_are_not():
    chk = consistency.VlmConsistencyChecker(FakeVlm(["NO", "YES", "ERR"]))
    missing = chk.missing_characters("url", [_char("A"), _char("B"), _char("C")])
    assert missing == ["A"]          # only the confident NO counts; YES + error kept


def test_answer_text_shapes():
    assert consistency._answer_text({"answer": "yes"}) == "yes"
    assert consistency._answer_text("no") == "no"
    assert consistency._answer_text({}) == ""


# -- the reroll gate on the provider ------------------------------------------

def _ctx(nchars=1):
    chars = tuple(_char(f"C{i}") for i in range(nchars))
    shot = ResolvedShot(id="s1", kind="action", duration_s=5.0, prompt="x",
                        cast=tuple(c.id for c in chars), line=None, emotion=None)
    series = ResolvedSeries(title="t", genre="revenge", style="photoreal-drama",
                            aspect="9:16", resolution=(1080, 1920), fps=24, language="en")
    return ShotContext(shot=shot, series=series, characters=chars,
                       output_path=Path("/tmp/p/episodes/e_shots/s.mp4"))


def _provider_with(checker, rerolls=2):
    prov = CinematicProvider.__new__(CinematicProvider)  # skip __init__ (no FAL_KEY needed)
    prov._consistency = checker
    prov._gate_rerolls = rerolls
    calls = {"n": 0}
    def fake_build(ctx, stack, budget):
        calls["n"] += 1
        return f"kf{calls['n']}"
    prov._build_keyframe = fake_build  # type: ignore
    return prov, calls


class _Stub:
    """Reports the scripted missing-list per check call."""
    def __init__(self, seq): self.seq = list(seq); self.i = 0
    def missing_characters(self, url, chars, *, budget_s=None):
        m = self.seq[self.i] if self.i < len(self.seq) else []
        self.i += 1
        return m


def test_no_gate_builds_keyframe_once():
    prov, calls = _provider_with(None)
    prov._keyframe_with_gate(_ctx(), [], 60.0)
    assert calls["n"] == 1            # checker None → no gate


def test_gate_rerolls_until_present():
    prov, calls = _provider_with(_Stub([["C0"], []]))   # missing, then present
    url = prov._keyframe_with_gate(_ctx(), [], 60.0)
    assert calls["n"] == 2 and url == "kf2"             # initial + 1 reroll


def test_gate_gives_up_after_max_rerolls():
    prov, calls = _provider_with(_Stub([["C0"], ["C0"], ["C0"]]), rerolls=2)
    prov._keyframe_with_gate(_ctx(), [], 60.0)
    assert calls["n"] == 3            # initial + 2 rerolls, then proceed (never fails)
