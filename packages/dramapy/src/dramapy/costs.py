"""Render-cost estimation — the number the plan-and-approve gate shows BEFORE
spending (the "Claude Code plan mode" for real money).

A non-producer should see "~$X to render this episode" and approve, not discover
the bill after 22 shots. This module estimates that from the episode spec +
provider + resolution, with a per-shot breakdown.

**Honesty:** the prices below are DIRECTIONAL ESTIMATES sourced from the roadmap's
model study (nano-banana-pro keyframe ~$0.15, Kling/Veo i2v per-second, Topaz
upscale, ElevenLabs music/tts, mmaudio sfx). They are structured as editable data
and MUST be reconciled against live fal invoices at wire-time — treat the output
as an order-of-magnitude plan number, not an invoice. Rounded UP where unsure so
the plan never under-promises.
"""

from __future__ import annotations

from dramapy import tiers
from dramapy.spec import ResolvedEpisode, ResolvedSeries  # type: ignore

# -- Price table (USD; ESTIMATES — reconcile with live invoices) --------------
PRICES = {
    "keyframe_image": 0.15,      # nano-banana-pro edit, one per shot
    "turnaround_per_char": 0.60,  # ~4 identity images per character, ONE-TIME (cached)
    "i2v_per_second": 0.06,      # Kling 2.5 turbo pro i2v (Veo Lite $0.05 / Std $0.40 bracket)
    "upscale_image": 0.10,       # Topaz/Clarity finishing pass
    "lipsync_per_shot": 0.10,    # sync-lipsync on a dialogue shot
    "music_per_episode": 0.20,   # ElevenLabs Music, one score
    "tts_per_line": 0.05,        # ElevenLabs voice, per spoken line
    "sfx_per_second": 0.001,     # mmaudio, over the episode
}
BASE_PIXELS = 1080 * 1920       # the price baseline resolution
RES_CAP = 4.0                   # don't let a 4K factor run away past 4x

# Which cost stages each provider actually incurs.
PROVIDER_STAGES = {
    "mock": set(),                                              # free (ffmpeg placeholders)
    "cinematic": {"keyframe", "turnaround", "i2v", "upscale", "lipsync", "audio"},
    "fal": {"i2v", "audio"},
    "minimax": {"i2v", "audio"},
    "dashscope": {"i2v", "audio"},
}
_DEFAULT_STAGES = {"i2v", "audio"}


def _res_factor(series) -> float:
    w, h = getattr(series, "resolution", (1080, 1920)) or (1080, 1920)
    return max(1.0, min(RES_CAP, (float(w) * float(h)) / BASE_PIXELS))


def _shots(episode) -> list:
    return [s for sc in getattr(episode, "scenes", []) or []
            for s in getattr(sc, "shots", []) or []]


def estimate_episode_cost(*, episode, series, provider: str = "cinematic",
                          tier: str = "standard") -> dict:
    """Estimate the USD cost to render one episode on ``provider`` at ``tier``.

    ``tier`` (draft|standard|premiere, see ``dramapy.tiers``) is the spend dial:
    draft drops the finishing ceiling and renders at half-res, so the aha-trailer
    estimate is a fraction of the premiere-season estimate. ``standard`` (default)
    is the full pipeline — identical to the pre-tier behavior.

    Returns ``{"provider", "tier", "total_usd", "breakdown"{stage: usd},
    "per_shot"[...], "res_factor", "basis": "estimate", "caveat"}``. ``mock`` is
    always $0.
    """
    t = tiers.resolve_tier(tier)
    stages = tiers.apply_to_stages(PROVIDER_STAGES.get(provider, _DEFAULT_STAGES), t)
    rf = max(0.25, _res_factor(series) * t.res_scale)
    img_rf = rf                     # image gen scales ~with pixels
    vid_rf = min(rf, 2.0)           # video pricing is gentler than linear pixels
    shots = _shots(episode)

    breakdown = {k: 0.0 for k in
                 ("keyframe", "turnaround", "i2v", "upscale", "lipsync", "audio")}
    per_shot: list[dict] = []

    for s in shots:
        sid = getattr(s, "id", "?")
        dur = float(getattr(s, "duration_s", 0.0) or 0.0)
        is_dialogue = getattr(s, "kind", "") == "dialogue" and bool(getattr(s, "line", None))
        c = 0.0
        if "keyframe" in stages:
            k = PRICES["keyframe_image"] * img_rf
            breakdown["keyframe"] += k
            c += k
        if "i2v" in stages:
            v = PRICES["i2v_per_second"] * dur * vid_rf
            breakdown["i2v"] += v
            c += v
        if "upscale" in stages:
            u = PRICES["upscale_image"] * img_rf
            breakdown["upscale"] += u
            c += u
        if "lipsync" in stages and is_dialogue:
            breakdown["lipsync"] += PRICES["lipsync_per_shot"]
            c += PRICES["lipsync_per_shot"]
        per_shot.append({"id": sid, "usd": round(c, 3)})

    if "turnaround" in stages:
        n_chars = len({cid for s in shots for cid in (getattr(s, "cast", []) or [])})
        breakdown["turnaround"] = PRICES["turnaround_per_char"] * n_chars * img_rf
    if "audio" in stages:
        total_dur = sum(float(getattr(s, "duration_s", 0.0) or 0.0) for s in shots)
        voiced = sum(1 for s in shots
                     if getattr(s, "kind", "") == "dialogue" and getattr(s, "line", None))
        breakdown["audio"] = (PRICES["music_per_episode"]
                              + PRICES["tts_per_line"] * voiced
                              + PRICES["sfx_per_second"] * total_dur)

    total = round(sum(breakdown.values()), 2)
    return {
        "provider": provider,
        "tier": t.name,
        "total_usd": total,
        "breakdown": {k: round(v, 3) for k, v in breakdown.items()},
        "per_shot": per_shot,
        "res_factor": round(rf, 2),
        "basis": "estimate",
        "caveat": "directional estimate from the model study; reconcile with live "
                  "fal invoices — an order-of-magnitude plan number, not a bill",
    }


def format_estimate(est: dict) -> str:
    """One-line human summary for the plan gate."""
    if est["total_usd"] == 0:
        return f"~$0 ({est['provider']} — free placeholder renders)"
    parts = ", ".join(f"{k} ${v:g}" for k, v in est["breakdown"].items() if v)
    return (f"~${est['total_usd']:g} to render on {est['provider']} "
            f"({len(est['per_shot'])} shots, {est['res_factor']:g}x res) — {parts} "
            f"[estimate]")
