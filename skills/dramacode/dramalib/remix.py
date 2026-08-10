"""Remix an existing series into your own — the zero-friction on-ramp (Door 1).

The strongest cold-start cure: don't author from a blank box, twist a hit you
already love. ``remix_bible`` takes a source bible (from ``onboarding.series_bible``
or a published series) and applies a *swap* — recast the lead as you, rename the
antagonist, move the setting, retitle — returning a new personalized bible.

The story SPINE is preserved by construction: genre, trope, beats, pay-gates and
the season arc all carry over untouched. A remix changes *who it happens to*, not
*whether it works* — so a non-producer can make it theirs without breaking the
thing that made the original bingeable. See ``docs/onboarding-ux.md``.
"""

from __future__ import annotations

import copy

from dramalib.titles import title_candidates


def _self_insert_index(cast: list[dict]) -> int:
    for i, role in enumerate(cast):
        if role.get("self_insert"):
            return i
    raise ValueError("source bible has no self-insert role to recast")


def _antagonist_index(cast: list[dict]) -> int:
    """The lead antagonist/co-lead — the first non-self-insert role, which in
    every archetype roster is the CEO / alpha / backstabber the story turns on."""
    for i, role in enumerate(cast):
        if not role.get("self_insert"):
            return i
    raise ValueError("source bible has only a self-insert role; nothing to recast")


def recast(*, bible: dict, role_index: int, name: str = "", look: str = "") -> dict:
    """Return a copy of ``bible`` with one cast slot recast. Sets ``cast_name`` /
    ``cast_look`` overrides on the role (the archetype ``function`` is preserved,
    so the dramatic job is unchanged — only the face and name move). Pure."""
    out = copy.deepcopy(bible)
    cast = out.get("cast") or []
    if not 0 <= role_index < len(cast):
        raise ValueError(f"role_index {role_index} out of range (0..{len(cast)-1})")
    if name:
        cast[role_index]["cast_name"] = name
    if look:
        cast[role_index]["cast_look"] = look
    return out


def remix_bible(
    *,
    source: dict,
    hero_name: str = "",
    hero_look: str = "",
    villain_name: str = "",
    setting: str = "",
    title: str = "",
    wound: str = "",
) -> dict:
    """Personalize a source series into a new bible.

    - ``hero_name`` / ``hero_look`` recast the self-insert (the "put yourself in
      it" move). ``villain_name`` recasts the lead antagonist.
    - ``setting`` overrides where it plays; ``title`` names the remix (falls back
      to a generated title featuring the hero); ``wound`` overrides the ep-1
      injustice.
    - Everything else — genre, trope beats, gate plan, arc, episode count/length,
      audience — is carried over UNCHANGED (the spine that makes it bingeable).

    Records provenance (``remixed_from``, ``swaps``) so the UI can show "your
    version of X" and the credit chain for remix-of-a-remix. Pure; raises
    ``ValueError`` if the source has no recastable roles.

    Example::

        mine = remix_bible(source=hit, hero_name="Amara",
                           villain_name="my ex", setting="a Lagos law firm")
    """
    out = copy.deepcopy(source)
    cast = out.get("cast") or []
    swaps: dict[str, str] = {}

    if hero_name or hero_look:
        hi = _self_insert_index(cast)
        if hero_name:
            cast[hi]["cast_name"] = hero_name
            swaps["hero"] = hero_name
        if hero_look:
            cast[hi]["cast_look"] = hero_look
    if villain_name:
        vi = _antagonist_index(cast)
        cast[vi]["cast_name"] = villain_name
        swaps["villain"] = villain_name
    if setting:
        out["setting"] = setting
        swaps["setting"] = setting
    if wound:
        out["ep1_wound"] = wound

    # Title: explicit → generated-with-hero → keep source, but always mark it a
    # remix so two users' versions never collide in the feed.
    if title:
        out["title"] = title
    elif hero_name:
        cands = title_candidates(genre=out.get("genre", ""), role=hero_name, n=3)
        if cands:
            out["title"] = cands[0]
            out["title_options"] = cands

    out["remixed_from"] = source.get("title", "")
    out["swaps"] = swaps
    out["notes"] = (
        f"Remix of \"{source.get('title', '')}\": {_swap_phrase(swaps) or 'no swaps'}. "
        "Spine (trope/beats/gates/arc) preserved from the source."
    )
    return out


def _swap_phrase(swaps: dict[str, str]) -> str:
    bits = []
    if "hero" in swaps:
        bits.append(f"you play {swaps['hero']}")
    if "villain" in swaps:
        bits.append(f"{swaps['villain']} is the one who wronged you")
    if "setting" in swaps:
        bits.append(f"set in {swaps['setting']}")
    return "; ".join(bits)


def remix_summary(bible: dict) -> str:
    """One line for the confirm screen: what this remix changed."""
    src = bible.get("remixed_from", "")
    if not src:
        return f'"{bible.get("title", "")}" (original)'
    return f'"{bible.get("title", "")}" — your version of "{src}" ({_swap_phrase(bible.get("swaps", {})) or "recast"})'
