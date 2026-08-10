"""Sales package — the one-screen pitch that ships with every series.

The strategy study's conclusion: the deliverable is not just the episode, it's the
episode + the ad-hooks + a *sellable pitch*. Distribution/monetization is where the
value lives, so a series must hand a distributor (or the algorithm, or a licensing
buyer) exactly what they need to say yes: what it is, who it's for, why it hooks,
and where it charges. This composes that from the genre + the author's premise.
"""

from __future__ import annotations

from dramalib.helpers import gate_plan
from dramalib.titles import title_candidates
from dramalib.tropes import trope_for_genre

# genre → the fantasy it sells (the emotional promise a buyer is buying).
_FANTASY = {
    "bazong": "being chosen and cherished by an all-powerful man",
    "billionaire": "the overlooked woman revealed as secretly destined/wealthy",
    "werewolf": "being fated, special, and fought over by a powerful alpha",
    "mafia": "a dangerous man who will burn the world for you",
    "fuchou": "the wronged rise from ruin; those who wronged you grovel",
    "revenge": "the wronged rise from ruin; those who wronged you grovel",
    "chongsheng": "a do-over — foreknowledge to win and punish this time",
    "zhuixu": "the dismissed nobody is secretly the most powerful in the room",
    "zhanshen": "respect reclaimed — the dismissed returns as the strongest",
    "riches": "sudden wealth and the respect and revenge it buys",
    "contract": "forced closeness becomes real, devoted love",
    "inlaw": "the mistreated is vindicated; the cruel family eats their words",
    "flashmarry": "the overlooked older self-insert, flash-married into wealth and dignity",
}


def sales_package(*, genre: str, logline: str = "", hooks=(), comparables=(),
                  market: str = "overseas", episodes: int = 50) -> dict:
    """The one-screen pitch. ``genre`` resolves via the trope table; ``logline``,
    ``hooks`` (2-4 ad-hook one-liners), ``comparables`` (2-3 hit titles) are the
    author's to supply — the rest is composed. Returns a dict; ``format_sales_
    package`` renders the markdown."""
    trope = trope_for_genre(genre=genre)  # raises on unknown genre
    key = _resolve_key(genre)
    gates = gate_plan(market=market, total_episodes=episodes)
    return {
        "title_options": title_candidates(genre=genre, n=3),
        "genre": key or genre,
        "audience": trope["audience"],        # male / female / all frequency
        "market": market,
        "fantasy": _FANTASY.get(key, "emotional restitution for the overlooked"),
        "logline": logline or f"[one-line premise — {_FANTASY.get(key, 'the core promise')}]",
        "beats": list(trope["beats"]),
        "hooks": list(hooks) or ["[cold-open dignity-theft hook]",
                                 "[the first big face-slap]", "[the mid-series reveal]"],
        "comparables": list(comparables),
        "episodes": episodes,
        "gate_plan": gates,
    }


def format_sales_package(pkg: dict) -> str:
    title = pkg["title_options"][0] if pkg["title_options"] else "[title]"
    lines = [
        f"# {title}",
        f"*{pkg['logline']}*",
        "",
        f"- **Genre / audience / market:** {pkg['genre']} · {pkg['audience']}-freq · {pkg['market']}",
        f"- **The fantasy it sells:** {pkg['fantasy']}",
        f"- **Length:** {pkg['episodes']} episodes · pay-gate at ep "
        + (", ".join(str(g) for g in pkg['gate_plan'].get('gates', [])) or "free/ad-model"),
        "",
        "**Strongest hooks (for paid UA):**",
    ]
    lines += [f"  {i+1}. {h}" for i, h in enumerate(pkg["hooks"][:4])]
    if pkg["comparables"]:
        lines += ["", f"**Comparable hits:** {', '.join(pkg['comparables'])}"]
    lines += ["", f"**Alt titles:** {' · '.join(pkg['title_options'])}"]
    return "\n".join(lines)


def _resolve_key(genre: str) -> str | None:
    from dramalib.tables import GENRE_ALIASES, TROPE_TABLE
    for part in str(genre).strip().lower().replace("_", "-").split("-"):
        if part in TROPE_TABLE:
            return part
        if part in GENRE_ALIASES:
            return GENRE_ALIASES[part]
    return None
