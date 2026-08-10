"""The taste loop — learn a creator's eye and bias the tool's defaults to it.

Flywheel loop #3 (adoption driver #1). Every human accept / reject / kill / reroll
/ note is signal. We fold it into a per-creator ``TasteProfile`` that biases the
onboarding scaffold and the crew's defaults — the reason the tool feels *personal*
(Claude Code adapts to your codebase; this adapts to your eye). Pure, JSON-round-
trippable dicts so persistence is a trivial file write later; the learning logic
lives here and is testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# How much each human signal moves an affinity counter.
SIGNAL_WEIGHTS: dict[str, int] = {
    "accept": 1, "keep": 1, "love": 2,
    "reroll": -1, "reject": -1, "kill": -2,
}


@dataclass
class TasteProfile:
    """A creator's accumulated preferences. All fields JSON-serializable."""

    creator_id: str = ""
    genre: dict[str, int] = field(default_factory=dict)   # genre -> net affinity
    trope: dict[str, int] = field(default_factory=dict)   # trope -> net affinity
    kills: dict[str, int] = field(default_factory=dict)   # element -> reject count
    pace: int = 0     # <0 prefers slower/breathier, >0 prefers tighter/faster
    tone: int = 0     # <0 prefers darker/angstier, >0 prefers sweeter
    notes: list[str] = field(default_factory=list)        # recent steering notes

    def to_dict(self) -> dict:
        return {
            "creator_id": self.creator_id, "genre": dict(self.genre),
            "trope": dict(self.trope), "kills": dict(self.kills),
            "pace": self.pace, "tone": self.tone, "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TasteProfile":
        d = d or {}
        return cls(
            creator_id=d.get("creator_id", ""), genre=dict(d.get("genre", {})),
            trope=dict(d.get("trope", {})), kills=dict(d.get("kills", {})),
            pace=int(d.get("pace", 0)), tone=int(d.get("tone", 0)),
            notes=list(d.get("notes", [])),
        )


def observe(profile: TasteProfile, *, event: dict) -> TasteProfile:
    """Fold one human signal into the profile (mutates and returns it).

    ``event`` kinds:
      - ``{"kind": "genre"|"trope", "key": <name>, "signal": accept|keep|love|reroll|reject|kill}``
      - ``{"kind": "kill", "key": <element>}`` — a hard reject to remember
      - ``{"kind": "pace"|"tone", "delta": +1|-1}`` — a directional nudge
      - ``{"kind": "note", "text": "<their words>"}``
    Unknown kinds/signals are ignored (forward-compatible with new UI events).
    """
    kind = event.get("kind")
    if kind in ("genre", "trope"):
        w = SIGNAL_WEIGHTS.get(event.get("signal", "accept"))
        if w is not None and event.get("key"):
            table = getattr(profile, kind)
            table[event["key"]] = table.get(event["key"], 0) + w
    elif kind == "kill" and event.get("key"):
        profile.kills[event["key"]] = profile.kills.get(event["key"], 0) + 1
    elif kind == "pace":
        profile.pace += int(event.get("delta", 0))
    elif kind == "tone":
        profile.tone += int(event.get("delta", 0))
    elif kind == "note" and event.get("text"):
        profile.notes.append(str(event["text"]))
        profile.notes[:] = profile.notes[-20:]  # keep the recent tail
    return profile


def preferred_genre(profile: TasteProfile) -> str | None:
    """The creator's highest-affinity genre (ties → None, don't guess)."""
    if not profile.genre:
        return None
    top = max(profile.genre.values())
    winners = [g for g, v in profile.genre.items() if v == top and v > 0]
    return winners[0] if len(winners) == 1 else None


def avoided(profile: TasteProfile, *, threshold: int = 2) -> list[str]:
    """Elements the creator kills often enough to stop proposing."""
    return sorted(k for k, n in profile.kills.items() if n >= threshold)


def bias_scaffold(profile: TasteProfile, *, scaffold: dict) -> dict:
    """Attach a taste block to an onboarding scaffold so authoring defaults lean
    the creator's way. Non-destructive — adds a ``"taste"`` key; the caller keeps
    final say (taste biases, it doesn't override an explicit ask)."""
    out = dict(scaffold)
    out["taste"] = {
        "preferred_genre": preferred_genre(profile),
        "avoid": avoided(profile),
        "pace": profile.pace,
        "tone": profile.tone,
        "recent_notes": profile.notes[-3:],
    }
    return out
