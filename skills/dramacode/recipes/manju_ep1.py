"""Recipe: manju (漫剧) episode 1, anime style, ~61s — 26 short shots, 1.5-3s cutting.

Demonstrates composition of dramalib helpers:
  - episode_length_for    — the manju range (60-180s) sets the target
  - clamp_duration        — 漫剧 cuts at table durations: 2.5s action/
                            dialogue beats, research-true 1.5s inserts
  - trope_for_genre       — the 战神 pattern names the beats
  - validate_beat_law     — the soft beat-law warnings ride the envelope
  - Episode/Scene/Shot    — the contract spec dataclasses

Manju grammar vs photoreal drama: 20-30 shots per minute, hard cuts,
V.O. carries exposition, inner monologue is a legal dialogue shot (cast +
line), and dub-after lip-sync is tolerated so dialogue can land on
profile/back shots. The opening executes the research doc's worked
storyboard literally: 3s爆点 → 10s立角色.

Standalone demo: CAST is inline for readability. In a real project the
cast lives in series.py (see templates/project_skeleton/) with
style="anime".
"""

from __future__ import annotations

from dramalib.bible import Character
from dramalib.helpers import clamp_duration, episode_length_for, validate_beat_law
from dramalib.spec import Episode, Scene, Shot
from dramalib.tables import HOOK_MAX_S
from dramalib.tropes import trope_for_genre

CAST = [
    Character(id="lin_ye", name="Lin Ye",
              look="anime, white-robed youth, 19, loose black hair, faint "
                   "gold seal glowing on his collarbone",
              voice="m_young_calm"),
    Character(id="tianjun", name="Celestial Sovereign",
              look="anime, towering armored god, crown of nine rings, face "
                   "in shadow",
              voice="m_vast_echo"),
]

TROPE = trope_for_genre(genre="zhanshen")   # return → dismissed → reveal → 打脸
LEN_LO, LEN_HI = episode_length_for(format="manju")  # (60.0, 180.0)

D = clamp_duration(kind="action")                       # 2.5 — the manju cut
D_LINE = clamp_duration(kind="dialogue_per_sentence")   # 2.5 per sentence
D_INS = clamp_duration(kind="insert")                   # 1.5 — flash detail


def build_episode() -> Episode:
    ep = Episode(
        number=1,
        title="The Execution Platform",
        hook_max_s=HOOK_MAX_S,
        scenes=[
            Scene(
                id="s1",
                location="Immortal-Execution Platform above a cloud sea, "
                         "starfield, cold palette",
                shots=[
                    # HOOK (0-3s): mystery type — the platform itself.
                    # Pinned to 3s so the establish open fits the hook
                    # window exactly (the research storyboard does this).
                    Shot(id="s1_01", kind="establish",
                         duration_s=clamp_duration(kind="establish",
                                                   duration_s=3),
                         prompt="extreme wide, slow push-in: the "
                                "Immortal-Execution Platform floats above "
                                "a cloud sea, ringed by celestial soldiers"),
                    Shot(id="s1_02", kind="dialogue", duration_s=D_LINE,
                         cast=["tianjun"], emotion="wrath",
                         line="Insolent mortal — you dared slaughter "
                              "sixteen sanctuaries!",
                         prompt="full shot, tilt down: white-robed youth "
                                "kneeling on the altar, gods encircling, "
                                "V.O. rolling like thunder"),
                    Shot(id="s1_03", kind="dialogue", duration_s=D_LINE,
                         cast=["lin_ye"], emotion="contempt",
                         line="You sanctimonious frauds.",
                         prompt="close-up, slight turn: sharp profile, "
                                "cold smirk, side backlight — inner "
                                "monologue"),
                    Shot(id="s1_04", kind="action", duration_s=D,
                         cast=["lin_ye"],
                         prompt="chains of light cinch his wrists; he "
                                "doesn't flinch"),
                    Shot(id="s1_05", kind="insert", duration_s=D_INS,
                         prompt="detail: the gold seal on his collarbone "
                                "pulses once, unseen"),
                    Shot(id="s1_06", kind="dialogue", duration_s=D_LINE,
                         cast=["tianjun"], emotion="verdict",
                         line="Nine lightning tribulations. Erase him.",
                         prompt="low angle: the sovereign raises one "
                                "gauntlet, rings igniting"),
                    Shot(id="s1_07", kind="action", duration_s=D,
                         prompt="the sky splits, a column of violet "
                                "lightning coiling above the platform"),
                    Shot(id="s1_08", kind="action", duration_s=D,
                         cast=["lin_ye"], emotion="defiance",
                         prompt="he stands up INTO the light, chains "
                                "straining"),
                    Shot(id="s1_09", kind="dialogue", duration_s=D_LINE,
                         cast=["lin_ye"], emotion="fury",
                         line="Sixteen sanctuaries of children, Sovereign.",
                         prompt="extreme close-up on his eyes, lightning "
                                "reflected, monologue turning to a shout"),
                    Shot(id="s1_10", kind="action", duration_s=D,
                         prompt="first tribulation bolt strikes; the altar "
                                "cracks in a spiderweb of gold"),
                    Shot(id="s1_11", kind="insert", duration_s=D_INS,
                         prompt="detail: a god's fingers tightening on a "
                                "jade decree, knuckles white"),
                    Shot(id="s1_12", kind="action", duration_s=D,
                         cast=["lin_ye"],
                         prompt="he walks forward through the second bolt, "
                                "robe burning off one shoulder"),
                    Shot(id="s1_13", kind="dialogue", duration_s=D_LINE,
                         cast=["tianjun"], emotion="cold",
                         line="Again.",
                         prompt="medium: the gauntlet clenches, rings "
                                "flaring brighter"),
                    Shot(id="s1_14", kind="action", duration_s=D,
                         prompt="third bolt; the platform's outer edge "
                                "shears away into the cloud sea"),
                ],
            ),
            Scene(
                id="s2",
                location="memory flash — burning mortal city at night, "
                         "warm palette against the cold",
                shots=[
                    Shot(id="s2_01", kind="insert", duration_s=D_INS,
                         prompt="hard cut, warm palette: a child's paper "
                                "lantern floating over a burning street"),
                    Shot(id="s2_02", kind="action", duration_s=D,
                         cast=["lin_ye"], emotion="grief",
                         prompt="younger lin ye digging through embers, "
                                "hands blistering"),
                    Shot(id="s2_03", kind="dialogue", duration_s=D_LINE,
                         cast=["lin_ye"], emotion="vow",
                         line="I will climb past every one of them.",
                         prompt="close-up in firelight, the vow whispered"),
                    Shot(id="s2_04", kind="action", duration_s=D,
                         prompt="smash back to cold palette: the fourth "
                                "bolt coiling above the shattered altar"),
                    Shot(id="s2_05", kind="dialogue", duration_s=D_LINE,
                         cast=["tianjun"], emotion="unease",
                         line="Why is the seal not breaking?",
                         prompt="medium: the sovereign's crown rings "
                                "stutter, first crack in the voice"),
                    Shot(id="s2_06", kind="action", duration_s=D,
                         cast=["lin_ye"],
                         prompt="the gold seal ignites, chains vaporizing "
                                "into petals of light"),
                    Shot(id="s2_07", kind="insert", duration_s=D_INS,
                         prompt="detail: celestial soldiers stepping back "
                                "in one motion, spears wavering"),
                    Shot(id="s2_08", kind="dialogue", duration_s=D_LINE,
                         cast=["lin_ye"], emotion="cold",
                         line="My turn.",
                         prompt="close-up, smirk returning, lightning "
                                "bending AROUND him"),
                    Shot(id="s2_09", kind="action", duration_s=D,
                         prompt="the bolt bends mid-air, arcing a full "
                                "circle around his body"),
                    Shot(id="s2_10", kind="action", duration_s=D,
                         cast=["lin_ye"],
                         prompt="he reaches up into the lightning, palm "
                                "open, unhurried"),
                    Shot(id="s2_11", kind="insert", duration_s=D_INS,
                         prompt="detail: one ring of the sovereign's "
                                "crown fracturing with a glass note"),
                    # CLIFFHANGER (final beat): cut at the peak, dead stop.
                    Shot(id="s2_12", kind="action",
                         duration_s=clamp_duration(kind="peak_freeze"),
                         cast=["lin_ye"], emotion="peak",
                         prompt="freeze mid-catch: the tribulation "
                                "lightning held in his fist like a spear, "
                                "sky gone white"),
                ],
            ),
        ],
        cliffhanger="freeze mid-catch: the tribulation lightning held in "
                    "his fist like a spear",
        bgm="low-strings-war-drums",
        burn_subtitles=True,
    )
    total = sum(s.duration_s for sc in ep.scenes for s in sc.shots)
    assert LEN_LO <= total <= LEN_HI, (
        f"manju episode {total:.1f}s outside {LEN_LO:.0f}-{LEN_HI:.0f}s"
    )
    return ep


def gen_episode():
    ep = build_episode()
    return {"episode": ep, "warnings": validate_beat_law(episode=ep)}
