"""Episode 1 — "The Slap". One generation target: defines gen_episode().

Structure (mirrors spec.md):
  - validate() — HARD asserts; an impossible spec blocks the render
  - functional_warnings() — SOFT beat-law checks; the render still runs,
    the review loop drives them to zero
  - gen_episode() — returns the contract envelope {"episode", "warnings"}

Every duration comes from dramalib tables (never guessed); the beat
skeleton comes from beat_sheet(). Prompts and lines are written by hand —
that's the craft the helpers can't do.
"""

from __future__ import annotations

import series
from dramalib.helpers import clamp_duration, validate_beat_law
from dramalib.spec import Episode, Scene, Shot
from dramalib.tables import HOOK_MAX_S, SHOT_CONTRACT_RANGE_S, SHOT_HARD_CAP_S

CAST_IDS = {c.id for c in series.CAST}


def build_episode() -> Episode:
    # Beat sheet (45s target, revenge pattern):
    #   0-3s hook (slap mid-air, frozen) → 3-14s world (penthouse lobby,
    #   the contract, the debt) → 14-21.5s first reversal (she signs,
    #   then turns the clause on him) → 21.5-42s escalation (clause ten,
    #   the ring) → 42-45s cliffhanger (the "dead" first wife's name
    #   lights up his phone).
    return Episode(
        number=1,
        title="The Slap",
        hook_max_s=HOOK_MAX_S,
        scenes=[
            Scene(
                id="s1",
                location="penthouse lobby, night, rain",
                shots=[
                    Shot(id="s1_01", kind="action", duration_s=3,
                         cast=["li_wei"], emotion="fury",
                         prompt="a slap frozen mid-swing, rain-slick lobby, "
                                "her wrist caught in his grip, faces inches "
                                "apart"),
                    Shot(id="s1_02", kind="establish", duration_s=4,
                         prompt="wide: glass lobby doors, neon reflections, "
                                "a contract folder scattered on wet marble"),
                    Shot(id="s1_03", kind="dialogue", duration_s=7,
                         cast=["dorian"], emotion="cold",
                         line="Sign, and your father's debt disappears "
                              "tonight.",
                         prompt="close-up, he releases her wrist, offers a "
                                "pen like a knife"),
                    Shot(id="s1_04", kind="dialogue", duration_s=6,
                         cast=["li_wei"], emotion="dread",
                         line="Then buy yourself a bride.",
                         prompt="close-up, trembling hand takes the pen"),
                    Shot(id="s1_05", kind="insert",
                         duration_s=clamp_duration(kind="insert"),
                         prompt="detail: the signature line, ink bleeding "
                                "into rain drops on the page"),
                ],
            ),
            Scene(
                id="s2",
                location="dorian's penthouse, study, low lamplight",
                shots=[
                    Shot(id="s2_01", kind="dialogue", duration_s=7,
                         cast=["li_wei"], emotion="defiance",
                         line="Clause nine. You never touch me. I never "
                              "love you.",
                         prompt="she reads the contract aloud, chin up, "
                                "backlit by the city"),
                    Shot(id="s2_02", kind="action", duration_s=3,
                         cast=["dorian"], emotion="amused",
                         prompt="he turns from the window, slow, the "
                                "half-smile appearing"),
                    Shot(id="s2_03", kind="dialogue", duration_s=6,
                         cast=["dorian"], emotion="threat",
                         line="Clause ten. Nobody ever finds out why.",
                         prompt="two-shot, he closes the distance, voice "
                                "dropping"),
                    Shot(id="s2_04", kind="action", duration_s=3,
                         cast=["li_wei"], emotion="resolve",
                         prompt="she slides the ring on herself, refusing "
                                "his hand"),
                    Shot(id="s2_05", kind="insert",
                         duration_s=clamp_duration(kind="insert"),
                         prompt="detail: his phone face-down on the desk, "
                                "buzzing"),
                    Shot(id="s2_06", kind="action", duration_s=3,
                         cast=["dorian"], emotion="shock",
                         prompt="he flips the phone; the caller name reads "
                                "SERAPHINE — freeze on his face draining"),
                ],
            ),
        ],
        cliffhanger="freeze on the phone: the dead first wife is calling",
        bgm="tense-strings",
        burn_subtitles=True,
    )


def validate(ep: Episode) -> None:
    """Hard asserts — impossible specs fail here, before paying a render."""
    ids = {s.id for sc in ep.scenes for s in sc.shots}
    assert len(ids) == sum(len(sc.shots) for sc in ep.scenes), "duplicate shot ids"
    for sc in ep.scenes:
        for s in sc.shots:
            assert SHOT_CONTRACT_RANGE_S[0] <= s.duration_s <= SHOT_HARD_CAP_S, (
                f"{s.id}: {s.duration_s}s outside the "
                f"{SHOT_CONTRACT_RANGE_S[0]:g}-{SHOT_HARD_CAP_S:g}s generation window"
            )
            if s.kind == "dialogue":
                assert s.cast and s.line, f"{s.id}: dialogue needs cast + line"
            for cid in s.cast:
                assert cid in CAST_IDS, f"{s.id}: unknown cast id {cid!r}"
    assert ep.cliffhanger, "episode needs a cliffhanger"


def functional_warnings(ep: Episode) -> list[dict]:
    """Soft beat-law checks — dramalib owns the law; project may add more."""
    return validate_beat_law(episode=ep)


def gen_episode():
    ep = build_episode()
    validate(ep)
    return {"episode": ep, "warnings": functional_warnings(ep)}
