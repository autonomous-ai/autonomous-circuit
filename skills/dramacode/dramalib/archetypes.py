"""Archetype casting kit — the stock roles each genre runs on.

Short drama casts to *function*, not novelty: the invisible/wronged self-insert,
the arrogant lead who cherishes only them, the scheming rival, the villain who
exists to be face-slapped. Pre-building these per genre lets the tool cast a
series the moment the genre is known — the "TV casts the characters" step. Each
role carries whether it's the **self-insert** (the viewer's seat) and its dramatic
function, so the bible + the beats can wire it correctly.
"""

from __future__ import annotations

from dramalib.tropes import trope_for_genre

# genre-key → ordered roster. Each role: (id, function, self_insert?).
ARCHETYPES: dict[str, list[dict]] = {
    "bazong": [
        {"role": "heroine", "function": "ordinary, kind Cinderella lead", "self_insert": True},
        {"role": "ceo", "function": "cold, domineering, obscenely rich — soft only for her", "self_insert": False},
        {"role": "white_lotus", "function": "the scheming other-woman (白莲花)", "self_insert": False},
        {"role": "mother_in_law", "function": "the overbearing gatekeeper", "self_insert": False},
    ],
    "werewolf": [
        {"role": "rejected_mate", "function": "the rejected she-wolf/omega with hidden lineage", "self_insert": True},
        {"role": "alpha", "function": "arrogant-then-obsessed alpha mate", "self_insert": False},
        {"role": "luna_rival", "function": "the rival for the alpha / Luna seat", "self_insert": False},
        {"role": "second_chance_mate", "function": "the second mate who values her", "self_insert": False},
    ],
    "billionaire": [
        {"role": "heroine", "function": "overlooked woman, secretly destined/an heiress", "self_insert": True},
        {"role": "billionaire", "function": "the secret-billionaire love interest", "self_insert": False},
        {"role": "rival", "function": "the scheming social rival", "self_insert": False},
        {"role": "the_family", "function": "the family that underestimates her", "self_insert": False},
    ],
    "fuchou": [
        {"role": "wronged", "function": "the betrayed/framed/discarded protagonist", "self_insert": True},
        {"role": "backstabber", "function": "the one who wronged them", "self_insert": False},
        {"role": "hidden_ally", "function": "the secret benefactor/mentor", "self_insert": False},
        {"role": "big_bad", "function": "the true mastermind, revealed late", "self_insert": False},
    ],
    "revenge": [
        {"role": "wronged", "function": "the betrayed/framed/discarded protagonist", "self_insert": True},
        {"role": "backstabber", "function": "the one who wronged them", "self_insert": False},
        {"role": "hidden_ally", "function": "the secret benefactor/mentor", "self_insert": False},
        {"role": "big_bad", "function": "the true mastermind, revealed late", "self_insert": False},
    ],
    "chongsheng": [
        {"role": "reborn", "function": "protagonist reborn with foreknowledge", "self_insert": True},
        {"role": "future_betrayer", "function": "the one who ruined the past life, now unaware", "self_insert": False},
        {"role": "to_save", "function": "the person they failed and now save", "self_insert": False},
    ],
    "zhuixu": [
        {"role": "son_in_law", "function": "the despised nobody with hidden power", "self_insert": True},
        {"role": "mocking_family", "function": "the in-laws who look down on him", "self_insert": False},
        {"role": "believer", "function": "the few who saw his worth", "self_insert": False},
    ],
    "zhanshen": [
        {"role": "war_god", "function": "the dismissed returnee, secretly the strongest", "self_insert": True},
        {"role": "dismissers", "function": "those who wrote him off", "self_insert": False},
        {"role": "old_enemy", "function": "the rival worthy of the final reckoning", "self_insert": False},
    ],
    "mafia": [
        {"role": "heroine", "function": "the innocent pulled into his world", "self_insert": True},
        {"role": "don", "function": "the dangerous, devoted mafia lead", "self_insert": False},
        {"role": "rival_family", "function": "the external threat", "self_insert": False},
        {"role": "enforcer", "function": "the loyal right hand", "self_insert": False},
    ],
    "contract": [
        {"role": "partner_a", "function": "the reluctant party entering the deal (self-insert)", "self_insert": True},
        {"role": "partner_b", "function": "the other half of the contract, hiding real feeling", "self_insert": False},
        {"role": "jealous_rival", "function": "the rival who makes the fake feelings real", "self_insert": False},
    ],
    "inlaw": [
        {"role": "wife", "function": "the mistreated, virtuous daughter-in-law", "self_insert": True},
        {"role": "mother_in_law", "function": "the tyrannical matriarch", "self_insert": False},
        {"role": "husband", "function": "the useless/cheating husband", "self_insert": False},
        {"role": "hidden_backer", "function": "her secret powerful family", "self_insert": False},
    ],
    "riches": [
        {"role": "broke_lead", "function": "the humiliated poor protagonist", "self_insert": True},
        {"role": "windfall", "function": "the backer/system/inheritance that changes everything", "self_insert": False},
        {"role": "doubters", "function": "those who looked down on them", "self_insert": False},
    ],
    "flashmarry": [
        {"role": "overlooked_lead", "function": "the overlooked older self-insert", "self_insert": True},
        {"role": "secret_tycoon", "function": "the flash-marriage spouse, secretly powerful", "self_insert": False},
        {"role": "dismissive_family", "function": "the family that dismissed them", "self_insert": False},
    ],
}


def _resolve_key(genre: str) -> str:
    from dramalib.tables import GENRE_ALIASES, TROPE_TABLE
    for part in str(genre).strip().lower().replace("_", "-").split("-"):
        if part in ARCHETYPES:
            return part
        if part in TROPE_TABLE and part in ARCHETYPES:
            return part
        if part in GENRE_ALIASES and GENRE_ALIASES[part] in ARCHETYPES:
            return GENRE_ALIASES[part]
    trope_for_genre(genre=genre)  # raises ValueError on a truly unknown genre
    return ""  # known genre, no bespoke roster → caller falls back


def archetypes_for(*, genre: str) -> list[dict]:
    """The stock cast roster for a genre (self-insert first). Empty list for a
    known genre with no bespoke roster (caller supplies a generic lead+rival)."""
    key = _resolve_key(genre)
    roster = list(ARCHETYPES.get(key, []))
    roster.sort(key=lambda r: (not r["self_insert"]))  # self-insert first
    return roster
