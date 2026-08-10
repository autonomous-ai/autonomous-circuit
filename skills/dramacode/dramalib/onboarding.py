"""Chat-first onboarding — turn a non-producer's one-line pitch into a series
scaffold, so they never face a blank page.

The doctrine (how to run the conversation) is ``references/onboarding.md`` and
``references/ideal-users.md``. This module is the deterministic backbone: the
minimal intake questions (feeling-words, no jargon) and ``series_scaffold`` — a
ready-to-edit default series the tool *proposes* and the creator reacts to.
Everything here composes existing helpers (``gate_plan``, ``trope_for_genre``,
the ``tables`` canon) so the scaffold is always consistent with the platform.
"""

from __future__ import annotations

from dramalib import tables
from dramalib.archetypes import archetypes_for
from dramalib.helpers import gate_plan
from dramalib.titles import title_candidates
from dramalib.tropes import trope_for_genre

# The whole intake — three FEELING questions, no craft jargon. A non-producer
# can answer all three; from them the tool infers genre, the ep-1 wound, and the
# want that drives the arc (see references/ideal-users.md "the bridge").
INTAKE_QUESTIONS: tuple[str, ...] = (
    "Who do you want us to root for — and what do they secretly wish would happen to them?",
    "What got taken from them, or done to them? (the wound / the injustice we open on)",
    "What do they want more than anything — and who's standing in the way?",
)


def series_scaffold(
    *,
    genre: str,
    market: str = "overseas",
    episodes: int | None = None,
) -> dict:
    """A ready-to-edit default series scaffold from a one-line pitch.

    Fills the blanks a non-producer can't (episode count/length, the trope
    spine, the pay/retention gate) with platform defaults — the ~50-episode
    short-drama-series unit — so onboarding *proposes* a whole series the
    creator edits by reacting, instead of interrogating them.

    ``genre`` resolves through ``trope_for_genre`` (natural strings + aliases
    work: "ceo", "fated-mates", "rags-to-riches"). ``market`` is cn|overseas|
    free. Returns a dict duck-typed toward the series bible + a suggested arc.

    Example::

        series_scaffold(genre="revenge", market="overseas")
        # {"episodes": 50, "episode_length_s": (90.0, 120.0), "genre": "revenge",
        #  "audience": "all", "beats": [...], "gate_plan": {...},
        #  "intake_questions": (...)}
    """
    n = int(episodes if episodes is not None else tables.DEFAULT_SERIES_EPISODES)
    if n < 1:
        raise ValueError(f"episodes must be >= 1, got {n}")
    trope = trope_for_genre(genre=genre)  # raises ValueError on an unknown genre
    return {
        "episodes": n,
        "episode_length_s": tables.DEFAULT_EPISODE_LENGTH_S,
        "genre": genre,
        "audience": trope["audience"],
        "beats": list(trope["beats"]),
        "gate_plan": gate_plan(market=market, total_episodes=n),
        "intake_questions": INTAKE_QUESTIONS,
    }


def series_bible(*, genre: str, title: str = "", market: str = "overseas",
                 episodes: int | None = None, wound: str = "") -> dict:
    """The starter series bible — the "TV writes the series bible + casts the
    characters" step. Composes the genre spine (`series_scaffold`), the archetype
    cast (`archetypes_for`), and title options into a series.py-shaped scaffold the
    creator names + edits. The self-insert role is listed first; the ep-1 wound and
    the dramatic-irony gap are called out so the binge engine wires correctly.
    """
    s = series_scaffold(genre=genre, market=market, episodes=episodes)
    titles = title_candidates(genre=genre, n=3)
    return {
        "title": title or (titles[0] if titles else "[title]"),
        "title_options": titles,
        "genre": s["genre"],
        "audience": s["audience"],
        "market": market,
        "episodes": s["episodes"],
        "episode_length_s": s["episode_length_s"],
        "cast": archetypes_for(genre=genre),          # self-insert first
        "beats": s["beats"],
        "gate_plan": s["gate_plan"],
        "arc": dict(tables.SERIES_PACING),
        "ep1_wound": wound or "[the named identity/right stripped from the self-insert in shot 1]",
        "notes": ("Name + cast each role via cast-book; plant the self-insert's ep-1 wound; "
                  "reveal the hidden truth to the audience by ~ep 2 (the irony gap)."),
    }


def format_bible(bible: dict) -> str:
    """A compact human-readable bible for the approve step."""
    lines = [
        f"SERIES BIBLE — {bible['title']}",
        f"  {bible['genre']} · {bible['audience']}-freq · {bible['market']} · "
        f"{bible['episodes']} eps @ {int(bible['episode_length_s'][0])}-{int(bible['episode_length_s'][1])}s",
        f"  ep-1 wound: {bible['ep1_wound']}",
        "  Cast:",
    ]
    for c in bible["cast"]:
        star = " ★self-insert" if c["self_insert"] else ""
        lines.append(f"    - {c['role']}: {c['function']}{star}")
    gates = ", ".join(str(g) for g in bible["gate_plan"].get("gates", [])) or "free/ad-model"
    lines.append(f"  Pay-gate: ep {gates}")
    lines.append(f"  Alt titles: {' · '.join(bible['title_options'])}")
    return "\n".join(lines)
