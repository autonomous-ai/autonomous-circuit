# Sound as storytelling — SFX cues and the scored arc

**Load when:** writing the `sfx` cues on shots, choosing an episode's
`bgm` mood, when a peak "doesn't land", when the user says "add sound
effects", "the music", "make the ending hit", or when a slap/reveal/loss
beat feels weak on the `_board.png`. Pairs with
`references/assembly-conventions.md` (levels, ducking, lip-sync order).

## The claim: a peak without its sound is half a peak

The single strongest, best-measured lever in the research: slap +
glass-shatter SFX lifted episode completion **+172%**. Sound is not
post-production polish — it is where the emotion lands. Two tools:
per-shot **SFX cues** (the impact) and the **scored arc** (the swell and,
crucially, the silence).

## Per-shot SFX — cue the peak and one texture

The engine consumes an optional per-shot `sfx` list (`Shot.sfx`). Keep it
to **1-3 cues**: one that IS the peak (the crack, the swell, the breath)
and one texture (the room, the rain). Build it from the vocabulary, never
by retyping cue strings:

```python
from dramalib.helpers import sfx_for
from dramalib.spec import Shot

Shot(id="s2_04", kind="action", duration_s=3, cast=["mira"], emotion="fury",
     sfx=sfx_for(moment="glass_shatter"),      # ['glass shatter', 'shards ...']
     prompt="ECU: the champagne glass bursts against the marble")
```

`dramalib.tables.SFX_CUES` groups moments three ways — cue at least one
from each family across an episode:

- **impact peaks** (every reversal gets one): `slap`, `glass_shatter`,
  `door_slam`, `punch`, `gunshot`, `tearing_metal`.
- **tension textures** (build under escalation): `heartbeat`, `clock`,
  `rain`, `wind`, `fire`, `water`, `alarm`, `phone_buzz`, `footsteps`.
- **emotional stings** (land ON the gut-punch): `reveal`, `betrayal`,
  `recognition`, `loss`, `sacrifice`, `reunion`, `lullaby`,
  `monitor_flatline`.

The full cheat sheet — which moment for which beat — is
`references/patterns/sfx-cue-cheatsheet.md`.

## The score is an arc, not a volume

`Episode.bgm` is ONE mood key for the bed (`SCORE_MOODS`); the engine
turns it into a generated instrumental score or a synth drone. Pick the
mood that matches the episode's **dominant** feeling:

```python
from dramalib.tables import SCORE_MOODS
# 'tense-strings' revenge · 'warm-piano' romance · 'cold-synth' sci-fi
# 'aching-cello' grief · 'low-strings-war-drums' epic · 'bright-pulse' triumph
Episode(bgm="cold-synth", ...)
```

But the mood is only half of it. The score has a **shape** across the
episode (`SCORE_ARC`), and writing that shape into the shot prompts /
notes is what makes an ending hit:

| Phase | Intent |
|---|---|
| open | state the mood quietly *under* the hook, never over it |
| build | add a layer at each escalation beat |
| climax | peak intensity at the reversal / spectacle |
| **gut_punch** | **CUT the score** — SFX + one breath carry the heartbreak |
| tail | silence into the cliffhanger; no music tail-out |

## Silence is the loudest score

The counter-intuitive one, and the mark of a real drama: at the gut-punch,
**drop the music**. A held breath, a single struck note, a room tone — and
then the cut. Music swelling *over* a heartbreak is how amateur edits
telegraph "feel now"; pulling it *away* is how the feeling actually lands.
Cue `sfx_for(moment="loss")` (a note decaying into silence) on that shot
and let the bed fall out.

## Match TTS delivery to the feeling

Speed keys off the shot's `emotion` (`AUDIO`, `assembly-conventions.md`):
conflict lines 1.1-1.15x, tender lines 0.85-0.9x. A whispered vow at
tender speed under a dropped-out score is worth more than any line read at
normal pace over strings.

## Pitfalls

- **No SFX on the peaks**: the flatness you can't see on a still. Every
  slap/reveal/loss beat carries a cue.
- **SFX on every shot**: constant effects go to mush. Cue the moments,
  leave the connective tissue to ambience.
- **Score over the gut-punch**: pull it out. Silence, then cut.
- **A music tail-out on the cliffhanger**: a fade says "it's over". Dead
  stop — no tail (`cliffhanger-beat.md`).
- **Retyping cue strings**: use `sfx_for()`; the table owns the vocab so
  cues stay consistent across an episode and a series.
- **Wrong mood for the arc**: a bright pulse under a grief episode fights
  every frame. Pick the mood for the dominant feeling, then shape it.
