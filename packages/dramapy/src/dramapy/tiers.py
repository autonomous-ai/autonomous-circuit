"""Render tiers — draft → premiere, the spend dial behind the aha ladder.

The onboarding aha ladder (docs/onboarding-ux.md) shows a cheap trailer before
asking for real money, then a premiere-quality season once the creator is in
love. Tiers are how the engine delivers that: one dial that toggles the
post-processing ceiling and scales resolution, so the SAME episode spec renders
cheap-and-fast or full-quality.

- **draft** — fast preview / the aha trailer. Skips the finishing ceiling
  (upscale, lip-sync) and character turnaround sheets, renders at half-res.
  Cheapest path to something watchable.
- **standard** — the default. The full pipeline at the series' native
  resolution. (Backwards-compatible: identical to pre-tier behavior.)
- **premiere** — the final. Full ceiling; reserved for the picture-locked cut a
  creator publishes.

A tier only *removes* stages and *scales* resolution — it never invents new
behavior — so an unknown/omitted tier safely resolves to ``standard`` and the
estimate/plan/provider all keep working. Per-shot-type MODEL choice is a separate
concern owned by ``dramalib.routing`` at the script stage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Tier:
    name: str
    label: str
    # Cost/render stages this tier turns OFF (names match costs.PROVIDER_STAGES).
    stages_off: frozenset[str] = field(default_factory=frozenset)
    # Multiplies the effective resolution factor (draft renders smaller/cheaper).
    res_scale: float = 1.0
    note: str = ""


TIERS: dict[str, Tier] = {
    "draft": Tier(
        name="draft", label="Draft / trailer",
        stages_off=frozenset({"upscale", "lipsync", "turnaround"}),
        res_scale=0.5,
        note="fast, cheapest — the aha trailer and quick previews",
    ),
    "standard": Tier(
        name="standard", label="Standard",
        stages_off=frozenset(), res_scale=1.0,
        note="the full pipeline at native resolution (default)",
    ),
    "premiere": Tier(
        name="premiere", label="Premiere",
        stages_off=frozenset(), res_scale=1.0,
        note="full ceiling for the picture-locked, published cut",
    ),
}

DEFAULT_TIER = "standard"
_ENV_KEY = "VIDEO_TIER"


def resolve_tier(name: str | None = None) -> Tier:
    """The Tier for ``name`` (or ``$VIDEO_TIER``, or the default). Never raises —
    an unknown name falls back to ``standard`` so the pipeline always renders."""
    key = (name or os.environ.get(_ENV_KEY) or DEFAULT_TIER).strip().lower()
    return TIERS.get(key, TIERS[DEFAULT_TIER])


def apply_to_stages(stages: set[str], tier: Tier) -> set[str]:
    """The cost/render stages that survive this tier."""
    return set(stages) - set(tier.stages_off)


def tier_summary(tier: Tier) -> str:
    off = ", ".join(sorted(tier.stages_off)) or "nothing"
    return f"{tier.label} — skips: {off}; {tier.res_scale:g}x res. {tier.note}"
