"""The render plan — what a non-producer approves BEFORE any spend.

Claude Code shows a plan and waits for approval before it acts; this is the same
for real render money. Given an episode spec it produces an inspectable plan — the
shot list + the cost estimate — so the tool never spends 22 shots' worth of fal
before the creator has seen (and approved) the shape and the bill. Read-only: it
generates NOTHING, it describes what a render WOULD do.
"""

from __future__ import annotations

from dramapy.costs import estimate_episode_cost, format_estimate


def _shots(episode) -> list:
    return [s for sc in getattr(episode, "scenes", []) or []
            for s in getattr(sc, "shots", []) or []]


def render_plan(*, episode, series, provider: str = "cinematic",
                tier: str = "standard") -> dict:
    """The pre-spend plan: episode summary + per-shot list + cost estimate.

    ``tier`` (draft|standard|premiere) is the spend dial the aha ladder uses — a
    draft trailer plan costs a fraction of a premiere-season plan. Returns
    ``{"episode", "shots":[...], "cost", "tier", "summary", "gate"}``. The ``gate``
    is the approval contract — render happens only after the human approves this
    plan (the producer/orchestrator's spend gate; see docs/crew-orchestration.md).
    """
    shots = _shots(episode)
    cost = estimate_episode_cost(episode=episode, series=series,
                                 provider=provider, tier=tier)
    total_dur = sum(float(getattr(s, "duration_s", 0.0) or 0.0) for s in shots)
    cost_by_id = {c["id"]: c["usd"] for c in cost.get("per_shot", [])}
    shot_rows = [
        {
            "id": getattr(s, "id", "?"),
            "kind": getattr(s, "kind", ""),
            "duration_s": float(getattr(s, "duration_s", 0.0) or 0.0),
            "cast": list(getattr(s, "cast", []) or []),
            "dialogue": bool(getattr(s, "kind", "") == "dialogue" and getattr(s, "line", None)),
            "est_usd": cost_by_id.get(getattr(s, "id", "?")),
        }
        for s in shots
    ]
    return {
        "episode": {
            "number": getattr(episode, "number", 0),
            "title": getattr(episode, "title", ""),
            "shot_count": len(shots),
            "duration_s": round(total_dur, 1),
            "cliffhanger": bool(getattr(episode, "cliffhanger", None)),
        },
        "shots": shot_rows,
        "cost": cost,
        "tier": cost.get("tier", tier),
        "summary": (f'"{getattr(episode, "title", "")}" — {len(shots)} shots, '
                    f"{round(total_dur, 1)}s, [{cost.get('tier', tier)}] {format_estimate(cost)}"),
        "gate": "APPROVE_BEFORE_SPEND: render only after the human approves this plan",
    }


def format_plan(plan: dict) -> str:
    """A compact human-readable plan for the approve step."""
    ep = plan["episode"]
    lines = [
        f"PLAN — {ep['title']}  ({ep['shot_count']} shots · {ep['duration_s']}s"
        f"{' · cliffhanger set' if ep['cliffhanger'] else ' · NO cliffhanger'})",
        plan["summary"],
        "",
        "Shots:",
    ]
    for s in plan["shots"]:
        tag = "💬" if s["dialogue"] else s["kind"][:4]
        usd = f" ~${s['est_usd']:g}" if s.get("est_usd") else ""
        cast = f" [{', '.join(s['cast'])}]" if s["cast"] else ""
        lines.append(f"  {s['id']:>7}  {tag:<9} {s['duration_s']:>4.1f}s{cast}{usd}")
    lines += ["", plan["gate"]]
    return "\n".join(lines)
