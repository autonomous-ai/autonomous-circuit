"""Recipe: sci-fi love story, ~50s — the emotional core done cinematically.

The upgraded craft in one file. Same envelope + beat law as the other
recipes, plus the three things that turn "a competent episode" into one
that makes people ache:

  - emotional_arc     — the FEELING track under the beat law: a bond planted
                        early, threatened, then spent on a recognition
                        gut-punch ("the enemy was once ours")
  - sfx_for + Shot.sfx — a peak without its sound is half a peak; every
                        reversal carries a cued SFX, every quiet beat a texture
  - shot_rhythm       — the shots vary in size, so a cut means something
  - a scored arc      — bgm builds, then DROPS OUT for the heartbreak
                        (see SCORE_ARC; silence is the loudest score)

Genre-agnostic on purpose: no trope table is consulted. The same machine
(plant → threaten → pay off) drives revenge, romance, thriller or this —
a salvage engineer, a drone she rebuilt from her drowned sister, and the
hunter at the door that carries the real sister's voice.

Beat skeleton (the law + the feeling, applied to 50.0s):
  0-3s hook (the breach) → 3-6s world (the failing bay) → 6-16s BOND
  (Cass and Wick, the lullaby) → 16-30s THREAT (the hunter cuts in;
  power routes to one) → 30-37.5s memory (why the bond) → 37.5-47s
  RECOGNITION gut-punch (the hunter is the real sister) → 47-50s
  cliffhanger (the impossible choice, frozen).

Standalone demo: CAST is inline for readability. In a real project the
cast lives in series.py (see templates/project_skeleton/).
"""

from __future__ import annotations

from dramalib.bible import Character
from dramalib.helpers import (
    clamp_duration,
    emotional_arc,
    sfx_for,
    shot_rhythm,
    validate_beat_law,
)
from dramalib.spec import Episode, Scene, Shot
from dramalib.tables import HOOK_MAX_S

CAST = [
    Character(id="cass", name="Cass",
              look="woman, 30, buzzed hair, thermal jacket over a pressure "
                   "suit, salvage grease on her jaw, exhausted eyes",
              voice="f_low_calm"),
    Character(id="wick", name="Wick",
              look="a small rebuilt repair drone, one cracked camera-eye lit "
                   "warm amber, a child's sticker on its shell",
              voice="m_soft_synth"),
    Character(id="mara", name="Mara",
              look="woman, 28, the drowned sister — seen only as a distorted "
                   "signal-ghost on a cracked screen, blue and pixel-torn",
              voice="f_bright_warm"),
]

# The feeling track this episode is aimed at — a recognition gut-punch.
# emotional_arc owns the FEELING; the beat law owns the STRUCTURE.
ARC = emotional_arc(length_s=50.0, gut_punch="recognition")


def _line(sentences: int) -> float:
    """Table rule: 2-3s per spoken sentence — use the midpoint."""
    return clamp_duration(kind="dialogue_per_sentence", duration_s=2.5 * sentences)


def build_episode() -> Episode:
    ep = Episode(
        number=1,
        title="Salvage",
        hook_max_s=HOOK_MAX_S,
        scenes=[
            Scene(
                id="s1",
                location="orbital salvage bay, red alarm strobe, hull frost",
                shots=[
                    # HOOK (0-3s): direct-confrontation type — the breach,
                    # already happening. ECU, kinetic, no ramp.
                    Shot(id="s1_01", kind="action",
                         duration_s=clamp_duration(kind="action", duration_s=3),
                         cast=["cass"], emotion="panic",
                         sfx=sfx_for(moment="alarm"),
                         prompt="ECU, handheld: Cass's grease-black hands clamp "
                                "a sparking console, red strobe raking her face, "
                                "frost creeping across the glass"),
                    # WORLD (by 10s): one wide, the stakes made visible.
                    Shot(id="s1_02", kind="establish",
                         duration_s=clamp_duration(kind="establish", duration_s=3),
                         sfx=sfx_for(moment="wind"),
                         prompt="WS, slow push-in: a cramped salvage bay adrift, "
                                "the oxygen gauge sliding into red, one amber "
                                "drone-eye glowing in the dark"),
                    # BOND (plant it early — the loss only lands if we loved it):
                    Shot(id="s1_03", kind="dialogue", duration_s=_line(2),
                         cast=["cass"], emotion="tender",
                         sfx=sfx_for(moment="lullaby"),
                         line="Stay with me, Wick. Keep humming.",
                         prompt="MCU, soft single practical: Cass kneels to the "
                                "little drone, forehead almost touching its shell"),
                    Shot(id="s1_04", kind="dialogue", duration_s=_line(2),
                         cast=["wick"], emotion="tender",
                         sfx=sfx_for(moment="lullaby"),
                         line="I have your sister's song. I won't stop.",
                         prompt="CU on the cracked amber eye, warm glow pulsing "
                                "in time with a hummed tune"),
                    Shot(id="s1_05", kind="insert",
                         duration_s=clamp_duration(kind="insert"),
                         prompt="ECU macro: a child's lullaby waveform scrolling "
                                "across the drone's tiny screen, a name half-worn "
                                "off the sticker"),
                    # THREAT (put the bond in danger — name the stakes):
                    Shot(id="s1_06", kind="action",
                         duration_s=clamp_duration(kind="action", duration_s=3),
                         emotion="dread",
                         sfx=sfx_for(moment="tearing_metal"),
                         prompt="MS, silhouette backlight: a hunter-drone's black "
                                "cutting arm punches through the aft hull, molten "
                                "line crawling down the bulkhead"),
                    Shot(id="s1_07", kind="dialogue", duration_s=_line(2),
                         cast=["cass"], emotion="fear",
                         sfx=sfx_for(moment="clock"),
                         line="Something's in the aft lock. It's cutting through.",
                         prompt="MCU whip-pan to Cass, strobe flashing across "
                                "wide eyes"),
                    Shot(id="s1_08", kind="dialogue", duration_s=_line(2),
                         cast=["wick"], emotion="urgent",
                         sfx=sfx_for(moment="heartbeat"),
                         line="Power routes to me or the lock. Choose, Cass.",
                         prompt="CU on the amber eye dimming, the score thinning "
                                "to a single held note"),
                ],
            ),
            Scene(
                id="s2",
                location="memory flash — a flooded dock years ago, cold blue",
                shots=[
                    # Why the bond exists — the grief the episode is really about.
                    Shot(id="s2_01", kind="insert",
                         duration_s=clamp_duration(kind="insert"),
                         sfx=sfx_for(moment="water"),
                         prompt="hard cut, cold blue: a hand slipping beneath "
                                "black water, fingers opening"),
                    Shot(id="s2_02", kind="action",
                         duration_s=clamp_duration(kind="action", duration_s=3),
                         cast=["cass"], emotion="grief",
                         sfx=sfx_for(moment="water"),
                         prompt="MS underwater: younger Cass thrashing downward, "
                                "reaching, the surface light shrinking above her"),
                    Shot(id="s2_03", kind="dialogue", duration_s=_line(1),
                         cast=["cass"], emotion="guilt",
                         sfx=sfx_for(moment="loss"),
                         line="I couldn't reach you.",
                         prompt="ECU on Cass's face against the dark water, the "
                                "line barely a breath"),
                ],
            ),
            Scene(
                id="s3",
                location="salvage bay — smash back, red strobe",
                shots=[
                    # ESCALATION → the choice that spends the bond.
                    Shot(id="s3_01", kind="action",
                         duration_s=clamp_duration(kind="action", duration_s=3),
                         cast=["cass"], emotion="torn",
                         sfx=sfx_for(moment="heartbeat"),
                         prompt="CU high angle: Cass's hand trembling between two "
                                "power switches, WICK and AFT LOCK stencilled "
                                "beneath them"),
                    # RECOGNITION gut-punch — the enemy was once ours.
                    Shot(id="s3_02", kind="insert",
                         duration_s=clamp_duration(kind="insert"),
                         sfx=sfx_for(moment="reveal"),
                         prompt="ECU macro: the hunter-drone's transponder ID "
                                "resolving on the cracked screen — a name, then a "
                                "familiar lullaby waveform underneath it"),
                    Shot(id="s3_03", kind="dialogue", duration_s=_line(2),
                         cast=["mara"], emotion="recognition",
                         sfx=sfx_for(moment="recognition"),
                         line="Cass. You kept humming my song.",
                         prompt="MCU of a blue signal-ghost forming on the "
                                "screen — the drowned sister's face, pixel-torn, "
                                "the room gone silent around it"),
                    # CLIFFHANGER (final beat): cut at the peak, dead stop.
                    Shot(id="s3_04", kind="action",
                         duration_s=clamp_duration(kind="peak_freeze"),
                         cast=["cass"], emotion="peak",
                         sfx=sfx_for(moment="loss"),
                         prompt="MS freeze: Cass between them — the amber drone "
                                "that loves her, and the real sister's face at "
                                "the breached door — plasma cutter half-raised, "
                                "everything held still"),
                ],
            ),
        ],
        # Score mood matches the dominant feeling; the arc (SCORE_ARC) builds
        # through the threat and CUTS on the recognition beat — the gut-punch
        # is carried by SFX and one held breath, not by music.
        cliffhanger="freeze on Cass between the copy that loves her and the "
                    "sister she let drown",
        bgm="cold-synth",
        burn_subtitles=True,
    )
    # The shots vary in size (ECU/CU/MCU/MS/WS across the cut) — never a wall
    # of same-scale shots. shot_rhythm proves it before we pay a render.
    all_shots = [s for sc in ep.scenes for s in sc.shots]
    assert not shot_rhythm(shots=all_shots)["monotone"]
    # The episode is aimed at a real gut-punch, not just a plot turn.
    assert any(b["function"] == "recognition" for b in ARC)
    return ep


def gen_episode():
    ep = build_episode()
    return {"episode": ep, "warnings": validate_beat_law(episode=ep)}
