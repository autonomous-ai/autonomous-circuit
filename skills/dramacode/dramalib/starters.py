"""The starter gallery — one-tap doors into a bingeable series (Doors 1 & 2).

The home screen is never a blank box; it's a feed of starters a non-producer can
make theirs with one tap. Each starter is a proven hit shape (seeded from the
genre playbook + hit teardowns) with a ready pitch and a default hero. This module
is the deterministic backbone: the curated set, locale-tuned ordering, ``surprise_me``,
and ``starter_bible`` (a starter → a full, ready-to-watch bible). See
``docs/onboarding-ux.md``.
"""

from __future__ import annotations

from dramalib.onboarding import series_bible
from dramalib.remix import remix_bible

# Curated one-tap starters. Each: genre (resolves via archetypes/tropes aliases),
# a punchy audience-facing pitch (the card copy), and a default hero handle used
# when the user taps "be the lead" without typing a name.
STARTERS: tuple[dict, ...] = (
    {"id": "ceo_secret", "genre": "billionaire",
     "pitch": "The temp everyone ignores secretly owns the whole company.",
     "hero_default": "you"},
    {"id": "revenge_return", "genre": "revenge",
     "pitch": "They framed her and threw her out. She comes back for everything.",
     "hero_default": "you"},
    {"id": "war_god_back", "genre": "zhanshen",
     "pitch": "The son-in-law they laughed at is the strongest man alive.",
     "hero_default": "you"},
    {"id": "second_chance", "genre": "riches",
     "pitch": "Broke, mocked, and counted out — then everything changes overnight.",
     "hero_default": "you"},
    {"id": "fake_marriage", "genre": "contract",
     "pitch": "A marriage on paper — until the feelings stop being fake.",
     "hero_default": "you"},
    {"id": "rejected_mate", "genre": "werewolf",
     "pitch": "Rejected by her alpha, she rises as the one he can't live without.",
     "hero_default": "you"},
    {"id": "reborn", "genre": "chongsheng",
     "pitch": "She dies betrayed — and wakes up the morning it all began.",
     "hero_default": "you"},
    {"id": "daughter_in_law", "genre": "inlaw",
     "pitch": "The family that bullied her has no idea who her real family is.",
     "hero_default": "you"},
)

_BY_ID = {s["id"]: s for s in STARTERS}

# locale/region key → the genres to surface FIRST. The frontend passes a locale;
# the feed just already looks like the user's taste (they never pick a region).
# Unknown locales fall back to DEFAULT_ORDER.
LOCALE_ORDER: dict[str, tuple[str, ...]] = {
    "cn": ("billionaire", "zhanshen", "chongsheng", "contract", "revenge"),
    "zh": ("billionaire", "zhanshen", "chongsheng", "contract", "revenge"),
    "us-rural": ("riches", "inlaw", "billionaire", "contract", "revenge"),
    "africa": ("riches", "revenge", "billionaire", "contract", "werewolf"),
    "br": ("contract", "revenge", "billionaire", "riches", "werewolf"),
    "latam": ("contract", "revenge", "billionaire", "riches", "werewolf"),
}
DEFAULT_ORDER: tuple[str, ...] = (
    "billionaire", "revenge", "werewolf", "riches", "contract",
)


def gallery(*, locale: str = "", limit: int = 0) -> list[dict]:
    """The starter cards, ordered for ``locale`` (falls back to DEFAULT_ORDER).

    Starters whose genre leads the locale order come first; the rest follow in
    their curated order, so the feed is always full even for a sparse locale map.
    ``limit`` (0 = all) caps how many cards to return.
    """
    order = LOCALE_ORDER.get(locale.lower().strip(), DEFAULT_ORDER)
    rank = {genre: i for i, genre in enumerate(order)}
    ordered = sorted(
        STARTERS,
        key=lambda s: (rank.get(s["genre"], len(order)), STARTERS.index(s)),
    )
    return list(ordered[:limit]) if limit else list(ordered)


def surprise_me(*, locale: str = "", seed: int = 0) -> dict:
    """One tap → one starter card. Deterministic in ``seed`` (the UI passes a
    rotating/random seed) so it's testable and never repeats twice in a row when
    the seed advances. Locale-tuned via ``gallery``."""
    cards = gallery(locale=locale)
    return cards[seed % len(cards)]


def starter_bible(*, starter_id: str, locale: str = "",
                  hero_name: str = "", episodes: int | None = None) -> dict:
    """A starter → a full, ready-to-watch bible. Builds the genre's series_bible,
    then (if the user typed/greeted a name) recasts them as the lead via
    ``remix_bible`` so the very first artifact is already about them.

    Raises ``ValueError`` on an unknown ``starter_id``.
    """
    starter = _BY_ID.get(starter_id)
    if starter is None:
        raise ValueError(f"unknown starter_id {starter_id!r}; see STARTERS")
    bible = series_bible(genre=starter["genre"], episodes=episodes)
    bible["starter_id"] = starter_id
    bible["pitch"] = starter["pitch"]
    if hero_name and hero_name != "you":
        bible = remix_bible(source=bible, hero_name=hero_name)
    return bible
