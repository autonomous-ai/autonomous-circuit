"""Model-routing memory — flywheel loop #4.

No single video model wins every shot type. This learns *which model wins for
which shot type* from critic scores and routes accordingly — the "AI
cinematographer / R&D" brain that keeps us on the best-of-the-best without manual
chasing. Cold-starts with research-based defaults (the OSS/model study + roadmap
R2); once real renders are scored it routes on the learned mean, not the default.

The concrete model ids are the current best-per-type from the study and are
*configurable* — the real provider owns the exact fal ids; verify at wire-time.
This module is the memory + the routing decision, testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# shot-type → cold-start default model (from the model study; roadmap R2).
# Verify exact fal ids at wire-time; these are the families that win each type.
COLD_START_ROUTES: dict[str, str] = {
    "dialogue": "kling-2.5",       # multi-shot dialogue + native lip-sync
    "action": "kling-2.5",         # coherent motion
    "establish": "seedance-2.5",   # long-take / world consistency, native 30s
    "insert": "seedance-2.5",      # cheap, controllable detail
    "hero": "veo-3.1",             # prompt-precise + 4K + native audio (reserve)
}


@dataclass
class RoutingMemory:
    """Per (shot_type, model) running stats. JSON-round-trippable."""

    stats: dict[str, dict[str, dict]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"stats": {st: {m: dict(v) for m, v in models.items()}
                          for st, models in self.stats.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "RoutingMemory":
        d = d or {}
        return cls(stats={st: {m: dict(v) for m, v in models.items()}
                          for st, models in d.get("stats", {}).items()})


def record_outcome(mem: RoutingMemory, *, shot_type: str, model: str, score: float) -> RoutingMemory:
    """Fold one critic score for a (shot_type, model) into the memory."""
    st = mem.stats.setdefault(shot_type, {})
    cell = st.setdefault(model, {"n": 0, "sum": 0.0})
    cell["n"] += 1
    cell["sum"] += float(score)
    return mem


def _mean(cell: dict) -> float:
    return cell["sum"] / cell["n"] if cell.get("n") else 0.0


def best_model(mem: RoutingMemory, *, shot_type: str, min_trials: int = 3) -> str | None:
    """The highest mean-score model for a shot-type with enough trials, else None.

    ``min_trials`` guards against routing on one lucky render (anti-noise, the same
    discipline as the taste loop). Ties → None (don't guess; the caller falls back
    to the cold-start default via ``route``)."""
    models = mem.stats.get(shot_type, {})
    eligible = {m: _mean(c) for m, c in models.items() if c.get("n", 0) >= min_trials}
    if not eligible:
        return None
    top = max(eligible.values())
    winners = [m for m, v in eligible.items() if v == top]
    return winners[0] if len(winners) == 1 else None


def route(mem: RoutingMemory, *, shot_type: str, min_trials: int = 3) -> str:
    """The model to use for a shot-type: the learned best if we have evidence,
    else the research cold-start default (else the establish default as a floor)."""
    learned = best_model(mem, shot_type=shot_type, min_trials=min_trials)
    if learned:
        return learned
    return COLD_START_ROUTES.get(shot_type, COLD_START_ROUTES["establish"])
