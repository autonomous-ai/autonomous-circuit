"""Institutional memory — flywheel loop #7, the slow compounding one.

Every production run is evidence: a pattern used, in a context, with an outcome
(reward / critic-score delta). Fold those into **craft cards**, and when a pattern
proves itself across enough runs, **promote** it to a rubric candidate — how the
platform's craft compounds instead of staying static. This is the offline
backbone (the card store + the promotion logic); it needs real runs to mine, so
until we're producing at volume it's a contract waiting for data — but the
promotion discipline (support + consistent outcome, not one anecdote) is testable
now and is what keeps institutional memory honest.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CraftCard:
    """One observation from a run. ``outcome`` is a signed delta (positive =
    it helped): a reward gain, a critic-score lift, a retention bump."""

    pattern: str          # e.g. "cold_open_slap", "grovel_after_reveal"
    context: str          # e.g. "revenge", "hook_strength", "us_market"
    outcome: float        # signed: +good / -bad
    note: str = ""        # evidence / where it came from


@dataclass
class InstitutionalMemory:
    cards: list[CraftCard] = field(default_factory=list)

    def add_card(self, card: CraftCard) -> "InstitutionalMemory":
        self.cards.append(card)
        return self

    def to_dict(self) -> dict:
        return {"cards": [vars(c) for c in self.cards]}

    @classmethod
    def from_dict(cls, d: dict) -> "InstitutionalMemory":
        return cls(cards=[CraftCard(**c) for c in (d or {}).get("cards", [])])


def _grouped(mem: InstitutionalMemory) -> dict:
    """(pattern, context) -> list of outcomes."""
    out: dict[tuple[str, str], list[float]] = {}
    for c in mem.cards:
        out.setdefault((c.pattern, c.context), []).append(float(c.outcome))
    return out


def promote(mem: InstitutionalMemory, *, min_support: int = 3,
            min_mean_outcome: float = 1.0) -> list[dict]:
    """Patterns proven enough to become rubric candidates.

    A pattern promotes only with **support** (≥ min_support cards in that context)
    AND a **consistent positive** mean outcome (≥ min_mean_outcome) — one lucky
    run never promotes (the anti-anecdote discipline). Best-supported, then
    strongest-mean, first.
    """
    promoted = []
    for (pattern, context), outcomes in _grouped(mem).items():
        n = len(outcomes)
        mean = sum(outcomes) / n
        if n >= min_support and mean >= min_mean_outcome:
            promoted.append({"pattern": pattern, "context": context,
                             "support": n, "mean_outcome": round(mean, 2)})
    promoted.sort(key=lambda p: (p["support"], p["mean_outcome"]), reverse=True)
    return promoted


def retire(mem: InstitutionalMemory, *, min_support: int = 3,
           max_mean_outcome: float = -1.0) -> list[dict]:
    """The inverse: patterns that consistently HURT — candidates to stop using.
    Same support discipline, negative mean."""
    out = []
    for (pattern, context), outcomes in _grouped(mem).items():
        n = len(outcomes)
        mean = sum(outcomes) / n
        if n >= min_support and mean <= max_mean_outcome:
            out.append({"pattern": pattern, "context": context,
                        "support": n, "mean_outcome": round(mean, 2)})
    out.sort(key=lambda p: (p["support"], -p["mean_outcome"]), reverse=True)
    return out


def coverage(mem: InstitutionalMemory) -> dict[str, int]:
    """Cards per context — the coverage map that shows where we lack evidence
    (a gap to go run experiments in, per the knowhow-flywheel discipline)."""
    out: dict[str, int] = {}
    for c in mem.cards:
        out[c.context] = out.get(c.context, 0) + 1
    return out
