"""Binge eval — the eval team's deterministic pre-check (script stage).

Two axes are scored, at two stages, by two evaluators:

- **Craft** — the screening-room film rubric, at the RENDER stage, over the
  actual pixels (needs a rendered cut). "Is it well made?"
- **Binge** — THIS module + ``references/binge-eval.md``, at the SCRIPT
  stage, over the ``.py`` spec (no render, no spend). "Is it compulsive?"

Binge is evaluated *before* spending render money, because a beautifully
rendered episode that nobody binges is wasted spend. The full 7-dimension
binge score (``references/binge-engine.md``) is ultimately a judgment
call — this module supplies the **deterministic backbone**: coarse proxy scores
for the dimensions structure can estimate, honest ``judgment`` placeholders for
the rest, plus flags for the structural binge-killers ``validate_beat_law``
does not check (episode-duration band, emotional flatness, series length).

Duck-types on attributes (contract §1): an Episode dataclass or a plain dict
both work.
"""

from __future__ import annotations

from dramalib import tables


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _shots(episode) -> list:
    scenes = _get(episode, "scenes", []) or []
    return [s for sc in scenes for s in (_get(sc, "shots", []) or [])]


def _episode_length_s(shots) -> float:
    return sum(float(_get(s, "duration_s", 0.0) or 0.0) for s in shots)


def binge_flags(*, episode) -> list[dict]:
    """Structural binge-killers ``validate_beat_law`` doesn't cover.

    Same warning shape as ``validate_beat_law`` (``{"part", "kind":
    "binge", "detail", "severity"}``) so the two merge in one envelope.
    """
    warnings: list[dict] = []

    def warn(part: str, detail: str, severity: str = "warning") -> None:
        warnings.append(
            {"part": part, "kind": "binge", "detail": detail, "severity": severity}
        )

    shots = _shots(episode)
    if not shots:
        return warnings  # validate_beat_law owns the empty-episode warning

    # 1. Episode-duration band. The series unit is 90-120s; too short is
    #    under-developed, too long drags for the format (kills completion).
    lo, hi = tables.DEFAULT_EPISODE_LENGTH_S
    total = _episode_length_s(shots)
    if total < lo:
        warn("episode",
             f"episode_too_short: {total:.0f}s is under the {lo:.0f}-{hi:.0f}s "
             f"series band — an under-developed episode has no room for a payoff")
    elif total > hi + 30.0:  # allow slack; only flag a real drag
        warn("episode",
             f"episode_too_long: {total:.0f}s exceeds the {lo:.0f}-{hi:.0f}s band "
             f"by a wide margin — the format rewards tight; trim to the spine")

    # 2. Emotional flatness — no 虐→爽 contrast. Only meaningful when emotions
    #    are actually authored; unset emotions are not a flatness signal.
    emos = [e for e in (_get(s, "emotion") for s in shots) if e]
    if len(emos) >= 4 and len(set(emos)) == 1:
        warn("episode",
             f"flat_emotion: every authored beat is '{emos[0]}' — the payoff "
             f"engine needs a swing (torment→relief), not one register")

    return warnings


def series_binge_flags(*, episode_count: int, gates=None) -> list[dict]:
    """Series-level binge structure: bingeable length + a pay/retention gate."""
    warnings: list[dict] = []

    def warn(detail: str) -> None:
        warnings.append(
            {"part": "series", "kind": "binge", "detail": detail, "severity": "warning"}
        )

    lo, hi = tables.SERIES_EPISODES_RANGE
    if episode_count < lo:
        warn(f"series_too_short: {episode_count} eps is under the {lo}-{hi} range "
             f"— a bingeable series needs the arc length to build the parasocial bond")
    elif episode_count > hi:
        warn(f"series_too_long: {episode_count} eps exceeds {hi} — padding past the "
             f"arc dilutes the payoff ladder")
    # gates is the output of gate_plan(); [] is valid for the free/ad model.
    if gates is None:
        warn("no_gate_plan: run gate_plan() and place the pay/retention gate at a "
             "cliffhanger — the money loop IS the compulsion loop")
    return warnings


# Dimensions the eval scores. ``proxy`` = estimable from structure here;
# ``judgment`` = the LLM eval role scores it (references/binge-eval.md).
BINGE_DIMENSIONS: dict[str, str] = {
    "hook_strength": "proxy",
    "payoff_cadence": "proxy",
    "cliffhanger_pull": "proxy",
    "pacing_fit": "proxy",
    "wish_fulfillment": "judgment",
    "bingeability": "judgment",
    "clip_ability": "judgment",
}


def binge_scorecard(*, episode) -> dict:
    """Coarse proxy scores (0-10) for the structural dimensions + honest
    ``None`` for the judgment ones. Returns
    ``{"scores": {dim: int|None}, "basis": {dim: "proxy"|"judgment"}, "flags": [...]}``.

    Coarse on purpose (bands, not false precision): the deterministic layer
    catches "obviously weak", the judgment layer (LLM) sets the real number.
    """
    shots = _shots(episode)
    scores: dict[str, int | None] = {d: None for d in BINGE_DIMENSIONS}
    if not shots:
        return {"scores": scores, "basis": dict(BINGE_DIMENSIONS), "flags": []}

    # hook_strength — establish footage before the first conflict shot.
    hook_max = float(_get(episode, "hook_max_s", tables.HOOK_MAX_S) or tables.HOOK_MAX_S)
    pre = 0.0
    for s in shots:
        if _get(s, "kind") != "establish":
            break
        pre += float(_get(s, "duration_s", 0.0) or 0.0)
    scores["hook_strength"] = 9 if pre == 0.0 else (6 if pre <= hook_max else 2)

    # payoff_cadence — the largest gap between dialogue/action beats.
    lo_gap, hi_gap = tables.BEAT_INTERVAL_S
    t = 0.0
    last = 0.0
    max_gap = 0.0
    for s in shots:
        d = float(_get(s, "duration_s", 0.0) or 0.0)
        if _get(s, "kind") in ("dialogue", "action"):
            max_gap = max(max_gap, t - last)
            last = t + d
        t += d
    scores["payoff_cadence"] = 9 if max_gap <= hi_gap else (5 if max_gap <= hi_gap * 2 else 2)

    # cliffhanger_pull — set, and the final shot inside the cut window.
    has_cliff = bool(_get(episode, "cliffhanger"))
    last_d = float(_get(shots[-1], "duration_s", 0.0) or 0.0)
    in_window = last_d <= tables.CLIFFHANGER_WINDOW_S[1]
    scores["cliffhanger_pull"] = 9 if (has_cliff and in_window) else (5 if has_cliff else 1)

    # pacing_fit — episode length in the series band.
    lo, hi = tables.DEFAULT_EPISODE_LENGTH_S
    total = _episode_length_s(shots)
    scores["pacing_fit"] = 9 if lo <= total <= hi else (5 if lo * 0.7 <= total <= hi + 30 else 2)

    return {
        "scores": scores,
        "basis": dict(BINGE_DIMENSIONS),
        "flags": binge_flags(episode=episode),
    }


# -- The reward signal + variant-select (flywheel loops #2 and #5) -------------
#
# A flywheel needs a real reward. Until the network gives us real audience data
# (completion / next-episode / shares — flywheel.md loop #8), the deterministic
# binge reward is the best offline proxy: the sum of the structural proxy
# scores, penalized by each structural binge-killer flag. Higher = more
# bingeable. It's coarse (the judgment dims aren't in it), so it ranks *variants*
# of the same element well but is not an absolute grade.


def binge_reward(*, episode) -> float:
    """Scalar reward for variant selection (higher = more bingeable).

    Reduces ``binge_scorecard`` to one number: the proxy scores summed, minus
    a penalty per structural killer flag, floored at 0. Relative only — use it to
    *rank* variants, not as an absolute score (that's the judgment layer's job).
    """
    card = binge_scorecard(episode=episode)
    base = sum(v for v in card["scores"].values() if v is not None)  # 0..40
    return max(0.0, float(base) - 3.0 * len(card["flags"]))


def rank_variants(*, variants, reward=None) -> list[dict]:
    """Produce→score→pick: rank candidate episodes by an binge reward.

    This is flywheel loop #2 (variant-and-select) made real — generate N variants
    of a high-leverage element (a cold open, a whole episode), score each, keep the
    best. Mirrors the screening loop's keep-the-best, at the *creative* level.

    ``variants``: a list of episodes, or of ``(label, episode)`` pairs.
    ``reward``: ``episode -> float`` (defaults to ``binge_reward``).
    Returns ``[{"rank", "index", "label", "reward", "episode"}]``, best first;
    ties keep input order (stable).
    """
    if not variants:
        raise ValueError("variants is empty — generate candidates first")
    scorer = reward or (lambda ep: binge_reward(episode=ep))
    scored = []
    for i, v in enumerate(variants):
        if isinstance(v, tuple) and len(v) == 2:
            label, ep = v
        else:
            label, ep = f"variant_{i}", v
        scored.append({"index": i, "label": label, "reward": float(scorer(ep)), "episode": ep})
    scored.sort(key=lambda r: (-r["reward"], r["index"]))  # stable: index breaks ties
    for rank, r in enumerate(scored):
        r["rank"] = rank
    return scored


def best_variant(*, variants, reward=None):
    """The single highest-reward variant's episode (see ``rank_variants``)."""
    return rank_variants(variants=variants, reward=reward)[0]["episode"]


# -- The auto-rework loop (close eval → fix) ----------------------------------
#
# One source of truth for "a weak dimension → its mechanical fix", shared by the
# eval doctrine (references/binge-eval.md), the metrics diagnosis
# (dramalib.metrics), and binge_rework below. Each fix names the pattern/lever
# that fixes it, so the loop is act-on-able, not just diagnostic.

REWORK_FIXES: dict[str, str] = {
    "hook_strength": "regenerate the cold open mid-conflict (patterns/cold-open-hook.md)",
    "payoff_cadence": "insert a face-slap / reversal (patterns/face-slap-cascade.md)",
    "cliffhanger_pull": "cut on the peak, delete the resolution (patterns/cliffhanger-beat.md)",
    "pacing_fit": "tighten to the 90-120s spine; cut connective tissue",
    "wish_fulfillment": "sharpen the ep-1 wound and pay it off on the satisfaction ladder",
    "bingeability": "deepen the parasocial bond + hold cast consistency across episodes",
    "clip_ability": "cut stronger 15-30s ad-hooks (patterns/ad-cut-sheet.md)",
}

# Structural-flag code → its fix (flags carry a "code:" prefix in their detail).
FLAG_FIXES: dict[str, str] = {
    "episode_too_short": "expand toward the 90-120s band — add a beat, don't pad",
    "episode_too_long": "trim to the 90-120s spine; cut connective tissue",
    "flat_emotion": "add the 虐 setup before the 爽 payoff — give the feeling a swing",
    "series_too_short": "grow toward ~50 episodes so the arc builds the bond",
    "series_too_long": "tighten toward ~50; don't dilute the payoff ladder",
    "no_gate_plan": "run gate_plan() and place the gate at a cliffhanger",
}


def binge_rework(*, episode, weak_below: int = 6) -> list[dict]:
    """The auto-rework step: a scorecard → a prioritized list of fixes.

    Structural killer flags first (they cap the verdict), then any proxy dimension
    scoring below ``weak_below``, worst first. Each item carries the specific fix
    (the pattern/lever), so a caller — the authoring agent or the driver — can act,
    not just read a number. Empty list = nothing to rework at the proxy level (the
    judgment layer still has the final say). Closes eval → reward → fix.
    """
    card = binge_scorecard(episode=episode)
    actions: list[dict] = []
    for f in card["flags"]:
        code = str(f.get("detail", "")).split(":", 1)[0].strip()
        actions.append({
            "kind": "flag", "code": code,
            "severity": f.get("severity", "warning"),
            "fix": FLAG_FIXES.get(code, "review this structural flag"),
        })
    for dim, score in card["scores"].items():
        if score is not None and score < weak_below:
            actions.append({
                "kind": "dimension", "dimension": dim, "score": score,
                "fix": REWORK_FIXES.get(dim, "review this dimension"),
            })
    # flags first (kind ordering), then lowest score first
    actions.sort(key=lambda a: (a["kind"] != "flag", a.get("score", 0)))
    return actions


# -- Combine proxy + judgment into one verdict (the eval team's two halves) ----

JUDGMENT_DIMENSIONS = tuple(d for d, b in BINGE_DIMENSIONS.items() if b == "judgment")


def combine_binge(*, episode, judgment: dict) -> dict:
    """Merge the deterministic proxy scorecard with the LLM's judgment scores into
    one 7-dimension binge verdict.

    ``judgment``: ``{wish_fulfillment, bingeability, clip_ability: 0-10}`` — the
    dimensions structure can't compute (see references/binge-eval.md for the
    rubric + the LLM output contract). Returns
    ``{"scores"(all 7), "flags", "overall", "pass", "rework"}``.

    The gate is judgment-shaped, not an average: a structural killer flag or ANY
    dimension ≤ 2 hard-fails it regardless of the mean (a dead hook or a self-
    resolving episode isn't rescued by good vibes elsewhere). ``overall`` is the
    mean of the filled dimensions — a summary, not the gate.
    """
    card = binge_scorecard(episode=episode)
    scores = dict(card["scores"])
    for dim in JUDGMENT_DIMENSIONS:
        if judgment.get(dim) is not None:
            scores[dim] = int(judgment[dim])
    filled = [v for v in scores.values() if v is not None]
    flags = card["flags"]
    hard_fail = bool(flags) or any(v is not None and v <= 2 for v in scores.values())
    overall = round(sum(filled) / len(filled), 1) if filled else 0.0
    passed = (not hard_fail) and overall >= 6.0 and all(v is None or v >= 4 for v in scores.values())
    return {
        "scores": scores,
        "flags": flags,
        "overall": overall,
        "pass": passed,
        "rework": binge_rework(episode=episode),
    }
