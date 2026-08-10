"""Recipe: the HIT-FORMULA episode 1 — the mega-hit secret sauce end to end.

Demonstrates, in one ep-1, the pieces tonight's craft work added:
  - DIGNITY-THEFT cold open (公开夺权, `cold-open-hook.md`): shot 1 publicly strips a
    named right from the self-insert.
  - The DRAMATIC-IRONY GAP (force #8, `binge-engine.md`): the audience learns the
    hidden truth in ep 1 (she owns the building); the tormentor does not — every
    later beat is loaded.
  - 虐→爽: the humiliation is stacked before the small first payoff; the BIG face-slap
    is withheld (that's the paywall's job later).
  - MASTER-SHOT-CRAFT (`master-shot-craft.md`): threat-in-the-keyframe, power via
    angle, z-axis blocking, reaction-shot, cut-on-the-wound — tagged [K]/[M] per shot.
  - The beat law (hook 0-3s → world → first reversal → beats → cliffhanger).

Genre: billionaire / hidden-identity (a ReelShort mega-hit shape). Standalone CAST
inline for readability; in a real project it lives in series.py.
"""

from __future__ import annotations

from dramalib.bible import Character
from dramalib.helpers import clamp_duration, validate_beat_law
from dramalib.spec import Episode, Scene, Shot
from dramalib.tables import HOOK_MAX_S
from dramalib.tropes import trope_for_genre

CAST = [
    # The self-insert: overlooked, secretly the most powerful person in the room.
    Character(id="lin", name="Lin Yue",
              look="woman, 27, plain grey temp uniform, hair in a low knot, "
                   "steady unreadable eyes",
              voice="f_low_calm"),
    # The tormentor who exists to be face-slapped.
    Character(id="marcus", name="Marcus Vantt",
              look="man, 34, tailored charcoal suit, silk tie, a smile built to "
                   "condescend",
              voice="m_cold_smooth"),
    Character(id="chair", name="Board Chair",
              look="woman, 60, silver bob, black suit, wire glasses",
              voice="f_grave"),
]

TROPE = trope_for_genre(genre="billionaire")  # mistaken_identity → wealth_reveal


def _dialogue_s(sentences: int) -> float:
    return clamp_duration(kind="dialogue_per_sentence", duration_s=2.5 * sentences)


def build_episode() -> Episode:
    return Episode(
        number=1,
        title="The Temp Who Owns the Tower",
        hook_max_s=HOOK_MAX_S,
        scenes=[
            Scene(
                id="s1",
                location="Vantt Tower lobby, first morning",
                shots=[
                    # HOOK 0-3s — DIGNITY-THEFT: a named right stripped, publicly.
                    # [K] low angle on Marcus (power), the coffee mid-air, staff frozen.
                    Shot(id="s1_01", kind="action",
                         duration_s=clamp_duration(kind="action", duration_s=3),
                         cast=["marcus", "lin"], emotion="humiliation",
                         prompt="low angle: Marcus flings a coffee cup, the arc "
                                "frozen mid-air toward Lin in her grey temp "
                                "uniform, lobby staff frozen watching"),
                    Shot(id="s1_02", kind="dialogue", duration_s=_dialogue_s(2),
                         cast=["marcus"], emotion="contempt",
                         line="You're fired. Clean it up before you crawl out.",
                         prompt="[K] tight on Marcus, eyes in shadow, the tower "
                                "logo hard-lit behind him"),
                    # 虐 STACK — pile a second strike on before any relief, so the
                    # debt the payoff must clear is real (references/face-slap-cascade).
                    Shot(id="s1_02b", kind="action",
                         duration_s=clamp_duration(kind="action", duration_s=3),
                         cast=["marcus", "lin"], emotion="humiliation",
                         prompt="Marcus drops her ID badge into the spilled coffee "
                                "and grinds it under his shoe, staff looking away"),
                    # WORLD by ~10s — where we are, who's dirt.
                    Shot(id="s1_03", kind="establish",
                         duration_s=clamp_duration(kind="establish", duration_s=4),
                         prompt="a slice of the VANTT lobby: marble, a wall of "
                                "glass, staff pretending not to watch"),
                    # IRONY GAP by ~30s — audience sees the truth Marcus can't.
                    # [K] threat-in-the-keyframe the character cannot see.
                    Shot(id="s1_04", kind="insert",
                         duration_s=clamp_duration(kind="insert"),
                         prompt="insert: the tablet in Lin's bag lit with an "
                                "ownership-transfer record — 'VANTT GROUP · 100% · "
                                "LIN YUE' — she alone sees it"),
                    # [M] slow push-in, hold ~1s past the line (realization beat).
                    Shot(id="s1_05", kind="dialogue", duration_s=_dialogue_s(1),
                         cast=["lin"], emotion="resolve",
                         line="As you wish. Today.",
                         prompt="[M] slow push-in on Lin as she rights the fallen "
                                "cup, calm, one brow lifting"),
                    # [M] z-axis: she walks TOWARD camera to the elevator = rising.
                    Shot(id="s1_06", kind="action",
                         duration_s=clamp_duration(kind="action", duration_s=3),
                         cast=["lin"], emotion="resolve",
                         prompt="Lin walks straight toward camera into the private "
                                "elevator, staff parting, Marcus small behind her"),
                ],
            ),
            Scene(
                id="s2",
                location="the executive floor boardroom, glass walls, morning",
                shots=[
                    Shot(id="s2_01", kind="establish",
                         duration_s=clamp_duration(kind="establish", duration_s=4),
                         prompt="glass boardroom high over the city, the board "
                                "seated, one empty chair at the head of the table"),
                    # [K] high-key, Marcus owning the room — before the turn.
                    Shot(id="s2_02", kind="dialogue", duration_s=_dialogue_s(2),
                         cast=["marcus"], emotion="smug",
                         line="The new owner's a ghost. Until they show, I run "
                              "this floor.",
                         prompt="[K] Marcus at the head chair, bright even light, "
                                "board deferring"),
                    # THE TURN — she enters; [M] z-axis, the room rotates to her.
                    Shot(id="s2_03", kind="action",
                         duration_s=clamp_duration(kind="action", duration_s=3),
                         cast=["lin", "marcus"], emotion="reversal",
                         prompt="the glass doors part; Lin enters in the owner's "
                                "black coat, walking toward camera; the board turns"),
                    Shot(id="s2_04", kind="dialogue", duration_s=_dialogue_s(1),
                         cast=["chair"], emotion="deference",
                         line="Ms. Lin. We've been waiting for you.",
                         prompt="the Board Chair rises, offering the head chair"),
                    # [M] reaction shot beats the thing — HOLD on the wound.
                    Shot(id="s2_05", kind="insert",
                         duration_s=clamp_duration(kind="insert"),
                         prompt="insert: Marcus's face, the smile cracking frame "
                                "by frame as it lands, held one beat too long"),
                    # Small first 爽 — the big face-slap is withheld (paywall's job).
                    Shot(id="s2_06", kind="dialogue", duration_s=_dialogue_s(1),
                         cast=["lin"], emotion="triumph",
                         line="Sit, Marcus. You work for me now.",
                         prompt="[K] low angle on Lin taking the head chair, "
                                "Marcus shrinking into a side seat"),
                    # PAYOFF of the scene-1 irony insert — the truth made physical.
                    Shot(id="s2_06b", kind="action",
                         duration_s=clamp_duration(kind="action", duration_s=3),
                         cast=["lin", "marcus"], emotion="triumph",
                         prompt="Lin slides the ownership-transfer document down the "
                                "long table; it stops dead in front of Marcus, her "
                                "name at the top"),
                    # CLIFFHANGER final beat — cut on a NEW threat, dead stop.
                    Shot(id="s2_07", kind="action",
                         duration_s=clamp_duration(kind="peak_freeze"),
                         cast=["marcus"], emotion="dread",
                         prompt="freeze on Marcus's phone lighting under the table: "
                                "a text — 'She knows about the fire.'"),
                ],
            ),
        ],
        cliffhanger="freeze on Marcus's phone: 'She knows about the fire.'",
        bgm="tense-strings",
        burn_subtitles=True,
    )


def gen_episode():
    ep = build_episode()
    return {"episode": ep, "warnings": validate_beat_law(episode=ep)}
