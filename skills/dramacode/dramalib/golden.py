"""The binge golden set — flywheel loop #5's CI backbone.

A fixed, representative set of episodes the binge reward runs on every material
change, so no change silently regresses the binge machine. The check is
*invariant-based*, not exact-number-based (robust to proxy-scoring tweaks, but it
still catches a broken reward function): strong episodes must out-reward weak ones
by a margin, strong episodes must have nothing to rework, weak ones must.

Used by the platform CI (`evals/run.py`) and a dramacode unit test.
"""

from __future__ import annotations

from dramalib.evals import binge_reward, binge_rework
from dramalib.spec import Episode, Scene, Shot

_EMOS = ["shock", "rage", "grief", "dread", "triumph", "longing"]


def _strong(title: str, opener_prompt: str) -> Episode:
    """A healthy ~104s episode: opens on action, varied emotion, tight cliffhanger."""
    shots = [
        Shot(id="s1_01", kind="action", duration_s=8.0, prompt=opener_prompt, emotion="shock"),
        Shot(id="s1_02", kind="dialogue", duration_s=6.0, cast=["a"], line="You did this.",
             emotion="rage"),
    ]
    shots += [
        Shot(id=f"s1_{i:02d}", kind="action", duration_s=8.0, prompt="beat",
             emotion=_EMOS[i % len(_EMOS)])
        for i in range(3, 15)
    ]
    return Episode(number=1, title=title,
                   scenes=[Scene(id="s1", location="penthouse", shots=shots)],
                   cliffhanger="freeze on the reveal")


def _weak(title: str) -> Episode:
    """A deliberately un-bingeable floor sentinel: slow establish open, too short,
    flat emotion, no cliffhanger."""
    shots = [Shot(id="s1_01", kind="establish", duration_s=6.0, prompt="wide city", emotion="calm")]
    shots += [Shot(id=f"s1_0{i}", kind="action", duration_s=6.0, prompt="x", emotion="calm")
              for i in range(2, 5)]
    return Episode(number=1, title=title,
                   scenes=[Scene(id="s1", location="street", shots=shots)],
                   cliffhanger=None)


def golden_set() -> list[tuple[str, Episode, str]]:
    """``[(name, episode, expectation)]`` — expectation in {"strong", "weak"}."""
    return [
        ("revenge_strong", _strong("Revenge", "a slap lands, the crowd gasps"), "strong"),
        ("ceo_strong", _strong("CEO", "she signs the divorce papers, unflinching"), "strong"),
        ("weak_floor", _weak("Flat"), "weak"),
    ]


def golden_rewards() -> dict[str, float]:
    """Name → binge reward for the whole golden set (the tracked signal)."""
    return {name: binge_reward(episode=ep) for name, ep, _ in golden_set()}


def check_golden(*, margin: float = 5.0) -> list[str]:
    """Regression check. Returns a list of failure messages; empty = pass.

    Invariants: every ``strong`` has no rework (clean), every ``weak`` has rework,
    and the weakest strong out-rewards the strongest weak by ``margin``.
    """
    problems: list[str] = []
    rewards = golden_rewards()
    strong = [n for n, _e, k in golden_set() if k == "strong"]
    weak = [n for n, _e, k in golden_set() if k == "weak"]
    for name, ep, kind in golden_set():
        rework = binge_rework(episode=ep)
        if kind == "strong" and rework:
            problems.append(f"{name}: strong episode has rework it shouldn't: "
                            f"{[a.get('code') or a.get('dimension') for a in rework]}")
        if kind == "weak" and not rework:
            problems.append(f"{name}: weak floor sentinel has no rework — the eval went blind")
    if strong and weak:
        lo_strong = min(rewards[n] for n in strong)
        hi_weak = max(rewards[n] for n in weak)
        if lo_strong <= hi_weak + margin:
            problems.append(
                f"binge reward doesn't separate strong from weak: "
                f"min(strong)={lo_strong:.1f} vs max(weak)={hi_weak:.1f} (need +{margin})")
    return problems
