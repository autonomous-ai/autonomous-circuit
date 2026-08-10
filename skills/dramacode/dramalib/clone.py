"""Clone-a-drama — the growth hook: name a show you love, swap in your people.

The fastest on-ramp of all: a fan types the name of a short drama they binged,
defines their characters, and we produce THEIR version. Steps: enter the name →
define the characters → done.

**The IP line (deliberate, not a limitation):** we do NOT fetch, store, or reuse
the named show's script or footage — that's its owner's copyright. What we clone
is the *shape*: genre, trope spine, archetype cast and beat structure, which are
not copyrightable. From that shape we generate an ORIGINAL series with the fan's
characters. The result is "in the style of", never a reproduction — and because
it's freshly generated, it's actually theirs to keep and share.

``infer_genre`` maps a title/premise to a genre key by trope keywords;
``clone_from_title`` builds the series bible in that shape and recasts it with the
user's people (via ``remix``). See docs/onboarding-ux.md and dramalib.remix.
"""

from __future__ import annotations

from dramalib.onboarding import series_bible
from dramalib.remix import remix_bible

# Ordered (keywords -> genre key). First match wins, so put the most specific
# signals first. Genre keys resolve through archetypes/tropes aliases. Keywords
# are trope signals, NOT titles — we key off the recognizable premise words fans
# use, never a copyrighted name.
_SIGNALS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("son-in-law", "war god", "god of war", "supreme", "dragon king", "strongest"), "zhanshen"),
    (("mother-in-law", "daughter-in-law", "in-law", "in law"), "inlaw"),
    (("alpha", "werewolf", "wolf", "luna", "fated mate", "mate", "moon"), "werewolf"),
    (("reborn", "rebirth", "reincarnat", "second life", "wakes up"), "chongsheng"),
    (("contract marriage", "fake marriage", "married to", "flash marr", "contract", "fake"), "contract"),
    (("mafia", "don ", "cartel", "underworld", "gang"), "mafia"),
    (("revenge", "vengeance", "betrayed", "framed", "discarded", "back for", "comes back"), "revenge"),
    (("rags", "riches", "poor", "overnight", "cinderella", "broke"), "riches"),
    (("ceo", "boss", "billionaire", "tycoon", "president", "heiress", "rich", "mr ", "mr."), "billionaire"),
)
DEFAULT_GENRE = "billionaire"  # the most common overseas hit shape


def infer_genre(*, title: str, premise: str = "") -> str:
    """Best-guess genre key for a named drama, from trope keywords in the title +
    premise. Never raises; falls back to the most common hit shape. Pure."""
    hay = f"{title} {premise}".lower()
    for keywords, genre in _SIGNALS:
        if any(k in hay for k in keywords):
            return genre
    return DEFAULT_GENRE


def clone_from_title(
    *,
    title: str,
    premise: str = "",
    hero_name: str = "",
    villain_name: str = "",
    market: str = "overseas",
    episodes: int | None = None,
) -> dict:
    """Produce an ORIGINAL series bible in the shape of the named drama, recast
    with the fan's characters.

    ``title`` is the show they name; ``premise`` is any one-line description they
    add (sharpens the genre guess). ``hero_name``/``villain_name`` are their
    character swaps. Returns a bible with ``inspired_by`` + a ``legal_note`` so
    the "in the style of, not a copy" framing travels with the data. Pure.

    Example::

        clone_from_title(title="that CEO revenge one", hero_name="Amara",
                         villain_name="my old boss")
    """
    if not str(title).strip():
        raise ValueError("clone_from_title needs a drama name")
    genre = infer_genre(title=title, premise=premise)
    bible = series_bible(genre=genre, market=market, episodes=episodes)
    bible = remix_bible(source=bible, hero_name=hero_name, villain_name=villain_name)
    bible["inspired_by"] = title.strip()
    bible["inferred_genre"] = genre
    bible["legal_note"] = (
        "Original series generated in the genre/trope style of the named show — "
        "not a reproduction of its script, dialogue, or footage."
    )
    # remix_bible already set notes/provenance; make the clone framing explicit.
    bible["notes"] = (
        f"Clone-inspired by \"{title.strip()}\" ({genre} shape). Original story, "
        "your characters. " + bible.get("notes", "")
    )
    return bible


def clone_summary(bible: dict) -> str:
    """One line for the confirm screen."""
    src = bible.get("inspired_by", "")
    who = bible.get("swaps", {}).get("hero", "you")
    return (f'Your "{bible.get("title","")}" — inspired by "{src}" '
            f'({bible.get("inferred_genre","")}), starring {who}. Original, not a copy.')
