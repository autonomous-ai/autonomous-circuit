"""Composable episode-construction helpers.

Every helper is keyword-only, raises ``ValueError`` on an impossible spec,
and returns plain dataclasses / dicts that duck-type against the contract's
Episode / Scene / Shot shapes — they work identically with the real dramapy
and with a stub.

Drafting discipline: :func:`shots_from_beats` emits DRAFTS — every
``prompt`` / ``line`` it can't know carries a ``TODO:`` prefix. You (the
model) replace every TODO with real writing before generating;
:func:`validate_beat_law` warns on any TODO left behind.
"""

from __future__ import annotations

import math

from dramalib import tables
from dramalib.spec import Scene, Shot
from dramalib.tropes import trope_for_genre

__all__ = [
    "beat_sheet",
    "shots_from_beats",
    "validate_beat_law",
    "recap_flashback",
    "gate_plan",
    "episode_length_for",
    "clamp_duration",
    "emotional_arc",
    "sfx_for",
    "shot_rhythm",
]


# -- Duration plumbing ---------------------------------------------------------


def clamp_duration(*, kind: str, duration_s: float | None = None) -> float:
    """Return a contract-legal duration for ``kind``.

    Uses the midpoint of the craft range in ``tables.SHOT_DURATION_S`` when
    ``duration_s`` is None, then clamps into
    ``[contract floor (1.5), SHOT_HARD_CAP_S (10.0)]`` — research-true 1.5-2s
    inserts are now legal; the hard cap stays 10s.

    Example: ``clamp_duration(kind="insert")`` → ``1.5``.
    """
    table = tables.SHOT_DURATION_S
    if kind not in table:
        raise ValueError(
            f"unknown shot kind {kind!r} — table kinds: {', '.join(sorted(table))}"
        )
    lo, hi = table[kind]
    d = (lo + hi) / 2.0 if duration_s is None else float(duration_s)
    floor = tables.SHOT_CONTRACT_RANGE_S[0]
    return min(max(d, floor), tables.SHOT_HARD_CAP_S)


# -- Beat sheet ------------------------------------------------------------------


def beat_sheet(
    *,
    genre: str,
    episode_no: int,
    length_s: float,
    market: str = "overseas",
) -> list[dict]:
    """Return a timed beat list obeying the beat law for one episode.

    Each beat is ``{"t_start", "t_end", "beat", "purpose"}`` (seconds).
    The structure is fixed by the law — hook in the first
    ``HOOK_MAX_S``, world set by ``WORLD_BY_S``, first reversal by
    ``FIRST_REVERSAL_BY_S``, a beat every 20-30s, cliffhanger in the final
    5-10s — and the beat *names* cycle the genre's trope pattern.

    Example::

        beats = beat_sheet(genre="revenge", episode_no=1, length_s=75.0)
        [b["beat"] for b in beats]
        # ['hook', 'world', 'first_reversal', 'escalation', 'cliffhanger']

    Raises ``ValueError`` for an unknown genre/market, a non-positive
    episode number, or a length outside 30-600s.
    """
    if episode_no < 1:
        raise ValueError(f"episode_no must be >= 1, got {episode_no}")
    if not 30.0 <= float(length_s) <= 600.0:
        raise ValueError(
            f"length_s {length_s} outside the sane 30-600s window — "
            "check EPISODE_LENGTH_S for the format's range"
        )
    if market not in ("cn", "overseas", "free"):
        raise ValueError(f"market must be cn | overseas | free, got {market!r}")

    trope = trope_for_genre(genre=genre)
    pattern = list(trope["beats"])
    length_s = float(length_s)

    beats: list[dict] = [
        {
            "t_start": 0.0,
            "t_end": tables.HOOK_MAX_S,
            "beat": "hook",
            "purpose": f"cold open into conflict — {pattern[0]}",
        },
        {
            "t_start": tables.HOOK_MAX_S,
            "t_end": tables.WORLD_BY_S,
            "beat": "world",
            "purpose": "core conflict + character relations + protagonist goal",
        },
    ]

    # Cliffhanger claims the final window; everything else fits before it.
    cliff_lo, cliff_hi = tables.CLIFFHANGER_WINDOW_S
    cliff_len = min(cliff_hi, max(cliff_lo, length_s * 0.1))
    cliff_start = length_s - cliff_len

    # First reversal ends by FIRST_REVERSAL_BY_S (or earlier on short episodes).
    reversal_end = min(tables.FIRST_REVERSAL_BY_S, cliff_start)
    beats.append({
        "t_start": tables.WORLD_BY_S,
        "t_end": reversal_end,
        "beat": "first_reversal",
        "purpose": f"suppression → eruption — {pattern[1 % len(pattern)]}",
    })

    # Middle beats every BEAT_INTERVAL_S until the cliffhanger window,
    # cycling the trope pattern.
    lo_iv, hi_iv = tables.BEAT_INTERVAL_S
    t = reversal_end
    i = 2
    while cliff_start - t > hi_iv:
        n_left = max(1, math.ceil((cliff_start - t) / hi_iv))
        step = min(hi_iv, max(lo_iv, (cliff_start - t) / n_left))
        beats.append({
            "t_start": t,
            "t_end": t + step,
            "beat": "escalation",
            "purpose": f"emotional beat — {pattern[i % len(pattern)]}",
        })
        t += step
        i += 1
    if cliff_start - t > 1.0:
        beats.append({
            "t_start": t,
            "t_end": cliff_start,
            "beat": "escalation",
            "purpose": f"emotional beat — {pattern[i % len(pattern)]}",
        })

    beats.append({
        "t_start": cliff_start,
        "t_end": length_s,
        "beat": "cliffhanger",
        "purpose": "cut at the emotional peak, dead stop",
    })
    return beats


# -- Shots from beats --------------------------------------------------------------


def _cast_ids(cast) -> list[str]:
    ids = [c.id if hasattr(c, "id") else str(c) for c in (cast or [])]
    if not ids:
        raise ValueError("cast must name at least one character id")
    return ids


def shots_from_beats(*, beats: list[dict], cast) -> list[Shot]:
    """Expand a :func:`beat_sheet` into Shot DRAFTS with table durations.

    ``cast`` is a list of Character objects or plain id strings; dialogue
    drafts alternate between the first two. Every prompt/line the helper
    can't know arrives as ``TODO: …`` — replace them all
    (:func:`validate_beat_law` warns on leftovers). Draft ids are
    sequential ``s1_NN``; regroup and re-id when you split into scenes.

    Example::

        drafts = shots_from_beats(beats=beats, cast=["li_wei", "dorian"])
    """
    if not beats:
        raise ValueError("beats is empty — build it with beat_sheet() first")
    ids = _cast_ids(cast)
    lead, foil = ids[0], ids[1 % len(ids)]

    shots: list[Shot] = []
    n = 0

    def _add(kind: str, duration_s: float | None, *, prompt: str,
             cast_ids: list[str] | None = None, line: str | None = None,
             emotion: str | None = None) -> None:
        nonlocal n
        n += 1
        shots.append(Shot(
            id=f"s1_{n:02d}",
            kind=kind,
            duration_s=clamp_duration(
                kind=("dialogue_per_sentence" if kind == "dialogue" else
                      "peak_freeze" if kind == "freeze" else kind),
                duration_s=duration_s,
            ),
            prompt=prompt,
            cast=list(cast_ids or []),
            line=line,
            emotion=emotion,
        ))

    for beat in beats:
        name = beat.get("beat", "")
        purpose = beat.get("purpose", "")
        if name == "hook":
            _add("action", None, cast_ids=[lead], emotion="shock",
                 prompt=f"TODO: the hook image — {purpose}")
        elif name == "world":
            _add("establish", None,
                 prompt="TODO: establish the location and power relations")
            _add("dialogue", None, cast_ids=[lead], emotion="tense",
                 prompt="TODO: close-up on the lead",
                 line=f"TODO: one line that states the goal ({lead})")
        elif name in ("first_reversal", "reversal"):
            _add("dialogue", None, cast_ids=[foil], emotion="contempt",
                 prompt="TODO: the suppression before the turn",
                 line=f"TODO: the line that goes too far ({foil})")
            _add("action", None, cast_ids=[lead], emotion="eruption",
                 prompt=f"TODO: the eruption — {purpose}")
        elif name == "cliffhanger":
            _add("action", tables.SHOT_DURATION_S["peak_freeze"][0],
                 cast_ids=[lead], emotion="peak",
                 prompt=f"TODO: the freeze image — {purpose}")
        else:  # escalation and anything custom
            _add("dialogue", None, cast_ids=[lead], emotion="resolve",
                 prompt=f"TODO: {purpose}",
                 line=f"TODO: short sharp line ({lead})")
            _add("insert", None,
                 prompt="TODO: the prop/detail that lands the beat")
    return shots


# -- Beat-law validation -----------------------------------------------------------


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def validate_beat_law(*, episode) -> list[dict]:
    """Check an Episode (dataclass or dict) against the beat law.

    Returns the ``functional`` warning dicts the project declares in its
    envelope (``{"part", "kind": "functional", "detail", "severity"}``).
    Structural proxies only — it reads durations/kinds/text, it cannot
    judge whether a reversal actually reverses:

    - hook: leading establish footage must fit inside ``hook_max_s``
    - world: total establish in the open must not exceed ``WORLD_BY_S``
    - shot durations: inside the craft window for the kind, hard cap 10s
    - dialogue shots must carry cast + line (early, softer echo of the
      hard dramapy validator)
    - beat cadence: no 30s window without a dialogue/action shot
    - cliffhanger: set, and the final shot inside the 5-10s window rule
    - drafting: no ``TODO:`` left in any prompt/line

    Example::

        warnings = validate_beat_law(episode=ep)
        return {"episode": ep, "warnings": warnings}
    """
    warnings: list[dict] = []

    def warn(part: str, detail: str) -> None:
        warnings.append({
            "part": part, "kind": "functional",
            "detail": detail, "severity": "warning",
        })

    scenes = _get(episode, "scenes", []) or []
    shots = [s for sc in scenes for s in (_get(sc, "shots", []) or [])]
    if not shots:
        warn("episode", "episode has no shots")
        return warnings

    hook_max = float(_get(episode, "hook_max_s", tables.HOOK_MAX_S) or tables.HOOK_MAX_S)

    # Hook: time before the first non-establish shot.
    t = 0.0
    for s in shots:
        if _get(s, "kind") != "establish":
            break
        t += float(_get(s, "duration_s", 0.0) or 0.0)
    if t > hook_max:
        warn(_get(shots[0], "id", "s?"),
             f"hook_too_long: {t:.1f}s of establish before the first conflict "
             f"shot exceeds the {hook_max:.0f}s hook window")

    # World: total establish footage in the first WORLD_BY_S seconds.
    t = 0.0
    est = 0.0
    for s in shots:
        d = float(_get(s, "duration_s", 0.0) or 0.0)
        if t < tables.WORLD_BY_S and _get(s, "kind") == "establish":
            est += d
        t += d
    if est > tables.WORLD_BY_S:
        warn("episode",
             f"world_too_slow: {est:.1f}s of establish in the open — exposition "
             f"never exceeds {tables.WORLD_BY_S:.0f}s")

    # Per-shot durations + dialogue completeness + TODO drafts.
    last_beat_end = 0.0
    t = 0.0
    for s in shots:
        sid = _get(s, "id", "s?")
        kind = _get(s, "kind", "")
        d = float(_get(s, "duration_s", 0.0) or 0.0)
        if d > tables.SHOT_HARD_CAP_S:
            warn(sid, f"shot_too_long: {d:.1f}s exceeds the {tables.SHOT_HARD_CAP_S:.0f}s "
                      "generation cap — split the shot")
        key = "dialogue_per_sentence" if kind == "dialogue" else kind
        if key in tables.SHOT_DURATION_S:
            lo, hi = tables.SHOT_DURATION_S[key]
            hi_eff = max(hi, tables.SHOT_CONTRACT_RANGE_S[0])
            if kind == "dialogue":
                # dialogue scales per sentence — only cap the absurd.
                hi_eff = tables.SHOT_HARD_CAP_S
            if kind == "establish" and d > tables.ESTABLISH_ABS_MAX_S:
                warn(sid, f"establish_too_long: {d:.1f}s > "
                          f"{tables.ESTABLISH_ABS_MAX_S:.0f}s absolute max")
            elif d > hi_eff:
                warn(sid, f"duration_off_table: {kind} at {d:.1f}s exceeds the "
                          f"table range {lo:.0f}-{hi:.0f}s")
        if kind == "dialogue":
            if not (_get(s, "cast") and _get(s, "line")):
                warn(sid, "dialogue shot missing cast and/or line")
        for fieldname in ("prompt", "line"):
            v = _get(s, fieldname) or ""
            if isinstance(v, str) and "TODO" in v:
                warn(sid, f"draft placeholder left in {fieldname}: {v[:60]!r}")
        if kind in ("dialogue", "action"):
            gap = t - last_beat_end
            if gap > tables.BEAT_INTERVAL_S[1]:
                warn(sid, f"beat_gap: {gap:.1f}s since the last dialogue/action "
                          f"shot — the law wants a beat every "
                          f"{tables.BEAT_INTERVAL_S[0]:.0f}-{tables.BEAT_INTERVAL_S[1]:.0f}s")
            last_beat_end = t + d
        t += d

    # Cliffhanger.
    if not _get(episode, "cliffhanger"):
        warn("episode", "no_cliffhanger: set episode.cliffhanger — cut at the "
                        "peak, dead stop")
    last = shots[-1]
    last_d = float(_get(last, "duration_s", 0.0) or 0.0)
    if last_d > tables.CLIFFHANGER_WINDOW_S[1]:
        warn(_get(last, "id", "s?"),
             f"cliffhanger_window: final shot runs {last_d:.1f}s — the "
             f"cliffhanger lives in the final "
             f"{tables.CLIFFHANGER_WINDOW_S[0]:.0f}-{tables.CLIFFHANGER_WINDOW_S[1]:.0f}s")
    return warnings


# -- Recap / gates / lengths ----------------------------------------------------------


def recap_flashback(*, prev_cliffhanger: str, cast=None, duration_s: float = 8.0) -> Scene:
    """Build the optional 0:00-0:10 recap-as-flashback scene for ep N>1.

    Two shots: a flash-frames insert re-showing the previous cliffhanger,
    then a reaction beat. Total capped at ``RECAP_MAX_S`` (10s) — binge
    platforms often skip the recap entirely; keep it droppable.

    Example::

        recap = recap_flashback(prev_cliffhanger="freeze on the burning letter")
        ep = Episode(number=2, title="…", scenes=[recap, main_scene], …)
    """
    if not prev_cliffhanger:
        raise ValueError("prev_cliffhanger must name the previous episode's peak")
    if not 3.0 <= float(duration_s) <= tables.RECAP_MAX_S:
        raise ValueError(
            f"recap duration_s must be 3-{tables.RECAP_MAX_S:.0f}s, got {duration_s}"
        )
    ids = _cast_ids(cast) if cast else []
    d_flash = clamp_duration(kind="insert")
    d_react = max(3.0, float(duration_s) - d_flash)
    shots = [
        Shot(id="s0_01", kind="insert", duration_s=d_flash,
             prompt=f"rapid flash frames — {prev_cliffhanger}, desaturated, "
                    "quick cuts, memory texture"),
        Shot(id="s0_02", kind="action", duration_s=d_react,
             cast=ids[:1], emotion="haunted",
             prompt="snap back to the present, breath held, the memory lands"),
    ]
    return Scene(id="s0", location="flashback — previous episode peak", shots=shots)


def gate_plan(*, market: str, total_episodes: int) -> dict:
    """Where the paywall gates (卡点) land for a series.

    ``market``: ``cn`` (fixed 10/20/30), ``overseas`` (first gate defaults
    to the middle of the 5-12 app range, major to 28), ``free`` (红果: no
    gates, every episode ad-break tolerant).

    Example: ``gate_plan(market="cn", total_episodes=25)`` →
    ``{"gates": [10, 20], "ad_break_tolerant": False}``.
    """
    if total_episodes < 1:
        raise ValueError(f"total_episodes must be >= 1, got {total_episodes}")
    if market == "cn":
        return {"gates": [g for g in tables.PAYWALL_GATES["cn"] if g <= total_episodes],
                "ad_break_tolerant": False}
    if market == "overseas":
        lo, hi = tables.PAYWALL_GATES["overseas_first"]
        first = min(total_episodes, (lo + hi) // 2)
        mlo, mhi = tables.PAYWALL_GATES["overseas_major"]
        gates = [first]
        if total_episodes >= mlo:
            gates.append(min(total_episodes, (mlo + mhi) // 2))
        return {"gates": gates, "ad_break_tolerant": False}
    if market == "free":
        return {"gates": [], "ad_break_tolerant": True}
    raise ValueError(f"market must be cn | overseas | free, got {market!r}")


def episode_length_for(*, format: str) -> tuple[float, float]:
    """The (min_s, max_s) episode-length range for a format.

    Example: ``episode_length_for(format="ai-drama")`` → ``(45.0, 90.0)``.
    """
    if format not in tables.EPISODE_LENGTH_S:
        raise ValueError(
            f"unknown format {format!r} — formats: "
            f"{', '.join(sorted(tables.EPISODE_LENGTH_S))}"
        )
    return tables.EPISODE_LENGTH_S[format]


# -- The emotional layer (bond → gut-punch) -----------------------------------


def emotional_arc(*, length_s: float, gut_punch: str = "recognition") -> list[dict]:
    """Overlay a FEELING track on the episode, aimed at one gut-punch.

    :func:`beat_sheet` owns the STRUCTURE (hook / reversal / cliffhanger);
    this owns the FEELING. It partitions ``[0, length_s]`` into beats whose
    feeling turns roughly every ``FEELING_SHIFT_S`` (15-25s) — the "can't
    look away" cadence — along a fixed spine:

        bond → threat → escalation… → <gut_punch> → aftermath

    Each beat is ``{"t_start", "t_end", "function", "feeling", "purpose"}``.
    ``function`` is the dramatic job; ``feeling`` is what the frame should
    make the viewer feel (``bond`` = tenderness … the gut-punch feeling comes
    from :data:`tables.GUT_PUNCH_FEELING`). Plant the bond in the first beat;
    spend it on the gut-punch beat.

    Example::

        arc = emotional_arc(length_s=75.0, gut_punch="betrayal")
        [b["function"] for b in arc]
        # ['bond', 'threat', 'escalation', 'betrayal', 'aftermath']

    Raises ``ValueError`` for an unknown gut-punch or a length outside
    30-600s (mirrors :func:`beat_sheet`).
    """
    if gut_punch not in tables.EMOTIONAL_TURNS:
        raise ValueError(
            f"unknown gut_punch {gut_punch!r} — one of: "
            f"{', '.join(sorted(tables.EMOTIONAL_TURNS))}"
        )
    if not 30.0 <= float(length_s) <= 600.0:
        raise ValueError(
            f"length_s {length_s} outside the sane 30-600s window — "
            "check EPISODE_LENGTH_S for the format's range"
        )
    length_s = float(length_s)
    lo, hi = tables.FEELING_SHIFT_S

    # Aftermath rides the cliffhanger window; the gut-punch lands just before it.
    aftermath = min(tables.CLIFFHANGER_WINDOW_S[1],
                    max(tables.CLIFFHANGER_WINDOW_S[0], length_s * 0.1))
    before = length_s - aftermath

    # How many feeling beats before the aftermath, at a ~20s turn cadence,
    # never fewer than bond + threat + gut_punch.
    mid = (lo + hi) / 2.0
    n_before = max(3, math.ceil(before / mid))
    labels = ["bond", "threat"] + ["escalation"] * (n_before - 3) + [gut_punch]

    step = before / len(labels)
    beats: list[dict] = []
    t = 0.0
    for i, fn in enumerate(labels):
        t_end = before if i == len(labels) - 1 else t + step
        feeling = (tables.GUT_PUNCH_FEELING.get(fn)
                   if fn in tables.EMOTIONAL_TURNS
                   else tables.FEELING_BY_FUNCTION[fn])
        purpose = (tables.EMOTIONAL_TURNS[fn]
                   if fn in tables.EMOTIONAL_TURNS
                   else {
                       "bond": "show the thing the protagonist loves and can lose",
                       "threat": "put the bond in danger — name the stakes",
                       "escalation": "tighten the vise; each turn costs more",
                   }[fn])
        beats.append({"t_start": t, "t_end": t_end, "function": fn,
                      "feeling": feeling, "purpose": purpose})
        t = t_end
    beats.append({
        "t_start": before, "t_end": length_s, "function": "aftermath",
        "feeling": tables.FEELING_BY_FUNCTION["aftermath"],
        "purpose": "the choice the gut-punch forces — cut on it (cliffhanger)",
    })
    return beats


def sfx_for(*, moment: str) -> list[str]:
    """Return the SFX cue phrases for a ``moment`` — the per-shot ``sfx`` list.

    Reads :data:`tables.SFX_CUES`; use the return value directly as
    ``Shot(sfx=sfx_for(moment="slap"))``. Cue the peak, not the whole shot.

    Example: ``sfx_for(moment="reveal")`` → ``["low sub-bass swell",
    "single struck piano note"]``.

    Raises ``ValueError`` for an unknown moment (the spec is wrong — pick a
    known cue or extend the table).
    """
    cues = tables.SFX_CUES.get(moment)
    if cues is None:
        raise ValueError(
            f"unknown sfx moment {moment!r} — one of: "
            f"{', '.join(sorted(tables.SFX_CUES))}"
        )
    return list(cues)


def shot_rhythm(*, shots) -> dict:
    """Report on cut rhythm across a shot sequence (advisory, not a warning).

    Shooting every beat at the same size is the #1 flatness tell. This scans
    the shots' ``kind`` runs as a proxy and returns
    ``{"n", "longest_run", "longest_run_kind", "monotone"}`` — ``monotone``
    is True when more than three identical-kind shots run back to back. Vary
    the shot SCALE across the run (see references/cinematic-shots.md).

    Example::

        assert not shot_rhythm(shots=all_shots)["monotone"]

    Raises ``ValueError`` on an empty sequence.
    """
    seq = list(shots or [])
    if not seq:
        raise ValueError("shots is empty — nothing to measure")
    longest = 1
    longest_kind = _get(seq[0], "kind", "")
    run = 1
    for prev, cur in zip(seq, seq[1:]):
        if _get(cur, "kind") == _get(prev, "kind"):
            run += 1
        else:
            run = 1
        if run > longest:
            longest = run
            longest_kind = _get(cur, "kind", "")
    return {
        "n": len(seq),
        "longest_run": longest,
        "longest_run_kind": longest_kind,
        "monotone": longest > 3,
    }
