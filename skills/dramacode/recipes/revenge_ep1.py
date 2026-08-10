"""Recipe: revenge episode 1, photoreal-drama, ~50s — the 3s-hook / slap-beat opening.

Demonstrates composition of dramalib helpers:
  - tables.HOOK_MAX_S     — the beat law as constants, never retyped numbers
  - clamp_duration        — every duration derived from the kind table
                            (dialogue scales per sentence: 2.5s x sentences;
                            inserts run research-true at 1.5s)
  - trope_for_genre       — the revenge beat pattern names the beats
  - validate_beat_law     — the soft beat-law warnings ride the envelope
  - Episode/Scene/Shot    — the contract spec dataclasses

Beat skeleton (the law, applied to 49.5s — inside the 45-90s ai-drama range):
  0-3s hook (public humiliation frozen mid-slap) → 3-12s world (the will
  reading, who holds the money) → 12-23s first reversal (the found
  codicil) → 23-40s escalation (the library standoff) → 40-49.5s reveal
  and cliffhanger (the hospital band names her; freeze in the final 3s).

Standalone demo: CAST is inline for readability. In a real project the
cast lives in series.py (see templates/project_skeleton/) and every shot's
cast ids must exist in series.CAST.
"""

from __future__ import annotations

from dramalib.bible import Character
from dramalib.helpers import clamp_duration, validate_beat_law
from dramalib.spec import Episode, Scene, Shot
from dramalib.tables import HOOK_MAX_S
from dramalib.tropes import trope_for_genre

CAST = [
    Character(id="mira", name="Mira Chen",
              look="woman, 26, ink-black hair pinned up, mourning dress, "
                   "eyes that stopped crying years ago",
              voice="f_low_calm"),
    Character(id="vada", name="Vada Chen",
              look="woman, 33, champagne silk, pearl choker, a smile built "
                   "for cameras",
              voice="f_bright_sharp"),
]

TROPE = trope_for_genre(genre="revenge")  # betrayal → awakening → payback → 打脸


def _dialogue_s(sentences: int) -> float:
    """Table rule: 2-3s per spoken sentence — use the midpoint."""
    return clamp_duration(kind="dialogue_per_sentence", duration_s=2.5 * sentences)


def build_episode() -> Episode:
    return Episode(
        number=1,
        title="The Will",
        hook_max_s=HOOK_MAX_S,
        scenes=[
            Scene(
                id="s1",
                location="chen estate, great hall, morning of the funeral",
                shots=[
                    # HOOK (0-3s): extreme-contrast type — the slap lands
                    # inside the first beat, no ramp.
                    Shot(id="s1_01", kind="action",
                         duration_s=clamp_duration(kind="action", duration_s=3),
                         cast=["vada", "mira"], emotion="humiliation",
                         prompt="a slap frozen at impact, mourning guests "
                                "gasping, champagne glass mid-fall"),
                    # WORLD (by ~10s): who died, who inherits, who's dirt.
                    Shot(id="s1_02", kind="establish",
                         duration_s=clamp_duration(kind="establish", duration_s=4),
                         prompt="wide: black-clad guests ring a lawyer's "
                                "table, the will in a red folder"),
                    Shot(id="s1_03", kind="dialogue", duration_s=_dialogue_s(2),
                         cast=["vada"], emotion="contempt",
                         line="Father left you exactly what you're worth. "
                              "Nothing.",
                         prompt="close-up over the red folder, pearls "
                                "catching the light"),
                    # FIRST REVERSAL (by 30s): suppression → eruption.
                    Shot(id="s1_04", kind="insert",
                         duration_s=clamp_duration(kind="insert"),
                         prompt="detail: mira's thumb splitting the will's "
                                "back cover — a second envelope sewn inside"),
                    Shot(id="s1_05", kind="dialogue", duration_s=_dialogue_s(2),
                         cast=["mira"], emotion="eruption",
                         line="Then read the codicil. Out loud, sister.",
                         prompt="she rises, envelope high, the room turning"),
                    Shot(id="s1_06", kind="action",
                         duration_s=clamp_duration(kind="action", duration_s=3),
                         cast=["vada"], emotion="dread",
                         prompt="her smile cracks frame by frame, guests "
                                "stirring"),
                    Shot(id="s1_07", kind="insert",
                         duration_s=clamp_duration(kind="insert"),
                         prompt="detail: phones rising around the room, "
                                "red record dots blinking on"),
                ],
            ),
            Scene(
                id="s2",
                location="estate library, dusk, fire lit",
                shots=[
                    # ESCALATION (a beat every 20-30s): the counter-move.
                    Shot(id="s2_01", kind="establish",
                         duration_s=clamp_duration(kind="establish", duration_s=4),
                         prompt="firelight on ladder rails, the codicil "
                                "envelope on a reading desk"),
                    Shot(id="s2_02", kind="dialogue", duration_s=_dialogue_s(2),
                         cast=["vada"], emotion="threat",
                         line="Open it, and the family dies with him. "
                              "Your choice.",
                         prompt="she blocks the door, voice silk over glass"),
                    Shot(id="s2_03", kind="dialogue", duration_s=_dialogue_s(2),
                         cast=["mira"], emotion="resolve",
                         line="The family died years ago. The night you "
                              "locked the nursery.",
                         prompt="close-up, firelight, the first tear that "
                                "refuses to fall"),
                    Shot(id="s2_04", kind="action",
                         duration_s=clamp_duration(kind="action", duration_s=3),
                         cast=["mira"], emotion="fury",
                         prompt="she tears the envelope open; a photograph "
                                "and a hospital band spill out"),
                    Shot(id="s2_05", kind="insert",
                         duration_s=clamp_duration(kind="insert"),
                         prompt="detail: the photograph curling in "
                                "firelight — a woman holding twin girls"),
                    Shot(id="s2_06", kind="dialogue", duration_s=_dialogue_s(2),
                         cast=["vada"], emotion="panic",
                         line="Where did you get that? Answer me.",
                         prompt="she lunges off the door, composure gone, "
                                "hand outstretched"),
                    # CLIFFHANGER (final beat): cut at the peak, dead stop.
                    Shot(id="s2_07", kind="action",
                         duration_s=clamp_duration(kind="peak_freeze"),
                         cast=["mira"], emotion="peak",
                         prompt="freeze on her face as she reads the name "
                                "on the hospital band: her own"),
                ],
            ),
        ],
        cliffhanger="freeze on the hospital band — the 'dead' twin is Mira",
        bgm="tense-strings",
        burn_subtitles=True,
    )


def gen_episode():
    ep = build_episode()
    return {"episode": ep, "warnings": validate_beat_law(episode=ep)}
