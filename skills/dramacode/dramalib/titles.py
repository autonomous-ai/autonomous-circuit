"""Title generator — the title IS the ad (a compressed plot promise, not a name).

From the mega-hit teardown (``docs/hit-teardowns.md``): winning titles follow
``[relationship/role] + [hidden reversal] + [possessive stakes]`` and lean on a
weighted trigger-word bank ("The Double Life of My Billionaire Husband", "Never
Divorce a Secret Billionaire Heiress", "Fated to My Forbidden Alpha"). This fills
proven per-genre patterns from the premise; the author picks/edits.
"""

from __future__ import annotations

from dramalib.tropes import trope_for_genre

# Weighted vocabulary — the words that reliably stop a scroll (highest-value first).
TRIGGER_WORDS: tuple[str, ...] = (
    "Secret", "Billionaire", "CEO", "Alpha", "Luna", "Rejected", "Reborn", "Revenge",
    "Contract", "Fake", "Substitute", "Hidden", "Divorced", "Pregnant", "Twins",
    "Heiress", "Bride", "Ruthless", "Cold", "Possessive", "Almighty", "Fated", "Cinderella",
)

# Per-genre title patterns. Slots: {role} {secret} {twist} {stakes}. Each genre
# carries defaults so a bare call still yields a full, on-genre title.
_PATTERNS: dict[str, dict] = {
    "bazong": {"pat": ["The CEO's {twist} {role}", "Married to the Billionaire's Secret {secret}",
                       "The Substitute {role} for the Ruthless CEO"],
               "role": "Wife", "secret": "Heir", "twist": "Contract", "stakes": "Love"},
    "werewolf": {"pat": ["Rejected by My Alpha {role}", "Fated to the {twist} Alpha King",
                         "The Alpha's Rejected Luna"],
                 "role": "Mate", "secret": "Luna", "twist": "Forbidden", "stakes": "Bond"},
    "billionaire": {"pat": ["The Double Life of My Billionaire {role}",
                            "Never Divorce a Secret Billionaire {secret}",
                            "The Billionaire's {twist} {role}"],
                    "role": "Husband", "secret": "Heiress", "twist": "Hidden", "stakes": "Empire"},
    "fuchou": {"pat": ["The Divorced {role}'s Comeback", "Reborn to Take Revenge",
                       "Back from the {twist}"],
               "role": "Heiress", "secret": "Heir", "twist": "Brink", "stakes": "Revenge"},
    "revenge": {"pat": ["The Divorced {role}'s Comeback", "Return of the {twist} {role}",
                        "Back from the {twist}"],
                "role": "Heiress", "secret": "Heir", "twist": "Ruthless", "stakes": "Revenge"},
    "chongsheng": {"pat": ["Reborn: The {role} Returns", "My Second Chance at {stakes}",
                           "Reborn to Save My {role}"],
                   "role": "Heiress", "secret": "Heir", "twist": "Reborn", "stakes": "Love"},
    "mafia": {"pat": ["The Mafia Boss's Innocent {role}", "Captive of the Ruthless Don",
                      "The {twist} Don's {role}"],
              "role": "Bride", "secret": "Don", "twist": "Ruthless", "stakes": "Vow"},
    "contract": {"pat": ["Fake Marriage to the {secret}", "Our Contract, His Real {stakes}",
                         "The {twist} {role} Contract"],
                 "role": "Wife", "secret": "CEO", "twist": "Fake", "stakes": "Love"},
    "inlaw": {"pat": ["The Underestimated {role}", "Mrs. {secret} in Disguise",
                      "The {twist} Daughter-in-Law"],
              "role": "Daughter-in-Law", "secret": "CEO", "twist": "Hidden", "stakes": "Family"},
    "riches": {"pat": ["From Rags to {secret}", "The Broke Heir's {twist}",
                       "The {twist} Billionaire Next Door"],
               "role": "Nobody", "secret": "Billionaire", "twist": "Inheritance", "stakes": "Fortune"},
    "flashmarry": {"pat": ["Flash-Married to a Secret {secret}", "My {twist} Flash Marriage",
                           "Flash Marriage to the {twist} {secret}"],
                   "role": "Wife", "secret": "Tycoon", "twist": "Secret", "stakes": "Dignity"},
    "zhuixu": {"pat": ["The Almighty {role}", "The {twist} Son-in-Law",
                       "The Son-in-Law Is Secretly the {secret}"],
               "role": "Son-in-Law", "secret": "King", "twist": "Hidden", "stakes": "Respect"},
    "zhanshen": {"pat": ["Return of the {twist} War God", "The War God Returns as a {role}",
                         "The {twist} War God's Comeback"],
                 "role": "Nobody", "secret": "War God", "twist": "Godly", "stakes": "Respect"},
}
_UNIVERSAL = {"pat": ["The {twist} {role}", "My Secret {secret}", "Reborn to Claim My {stakes}"],
              "role": "Heir", "secret": "Billionaire", "twist": "Hidden", "stakes": "Revenge"}


def title_candidates(*, genre: str, role: str = "", secret: str = "",
                     twist: str = "", stakes: str = "", n: int = 3) -> list[str]:
    """Fill the subject+secret+stakes formula into ``n`` candidate titles for a
    genre. Any slot you pass overrides the genre default; unknown genres fall back
    to universal patterns. Titles are 4-8 words, highest-value word first."""
    try:
        key = _resolve_key(genre)
    except ValueError:
        key = None
    spec = _PATTERNS.get(key, _UNIVERSAL) if key else _UNIVERSAL
    fill = {
        "role": role or spec["role"], "secret": secret or spec["secret"],
        "twist": twist or spec["twist"], "stakes": stakes or spec["stakes"],
    }
    out: list[str] = []
    for p in spec["pat"]:
        t = p.format(**fill)
        if t not in out:
            out.append(t)
        if len(out) >= n:
            break
    return out


def _resolve_key(genre: str) -> str | None:
    """Map a genre string to a _PATTERNS key via the trope table (aliases work)."""
    from dramalib.tables import GENRE_ALIASES, TROPE_TABLE
    for part in str(genre).strip().lower().replace("_", "-").split("-"):
        if part in _PATTERNS:
            return part
        if part in TROPE_TABLE:
            return part
        if part in GENRE_ALIASES:
            return GENRE_ALIASES[part]
    trope_for_genre(genre=genre)  # raises ValueError if truly unknown
    return None
