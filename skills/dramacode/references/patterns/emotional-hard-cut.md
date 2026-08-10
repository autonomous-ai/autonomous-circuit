# emotional-hard-cut

**Trigger:** load when writing the moment right AFTER a gut-punch or
reveal, when the user says "it drags at the end", "the ending fizzles",
"cut it tighter", or when a scene keeps playing past its peak. The "cut on
the wound" discipline behind `references/patterns/cliffhanger-beat.md`.

## Why this exists

The instinct after an emotional peak is to let it breathe — a reaction, a
comforting beat, a slow fade. That instinct kills the feeling. The peak's
power is the audience *sitting in it unresolved*; a cooldown shot hands them
the resolution and they exhale and stop caring. So: **land the turn, hold
one beat, hard cut.** No reaction montage, no music tail-out, no dialogue
that explains what just happened.

This is why 80% of short-drama transitions are hard cuts
(`references/assembly-conventions.md`): the cut itself is emotional
punctuation. A hard cut to black, or to an unrelated cold next scene, leaves
the wound open. A dissolve closes it.

Where it applies: after the recognition beat, after a betrayal line, on the
cliffhanger freeze, and between a memory flash and the present (smash back,
never dissolve).

## Use the helper

```python
from dramalib.helpers import clamp_duration, sfx_for
from dramalib.spec import Shot

# The gut-punch line, then ONE freeze, then the episode ends. No beat after.
freeze = Shot(id="s3_04", kind="action",
              duration_s=clamp_duration(kind="peak_freeze"),   # 3s, then cut
              cast=["cass"], emotion="peak",
              sfx=sfx_for(moment="loss"),        # a note decaying into silence
              prompt="MS freeze: Cass between the copy that loves her and the "
                     "sister at the door — everything held still")
```

The freeze shot IS the last frame; `Episode(cliffhanger=...)` names it.
Cue `loss`/`reveal` and let the score fall out (`references/sound-design.md`).

## Pitfalls

- **The comfort shot**: one reassuring reaction after the reveal and the
  click-through is gone. Delete it.
- **A music tail-out / fade to black slow**: a fade says "resolved". Dead
  stop, hard cut.
- **Explaining the turn in dialogue after it lands**: the picture already
  said it. Silence.
- **Holding the freeze too long**: 3s reads as intent; 6s+ reads as a
  render bug (`cliffhanger-beat.md`).
- **Dissolving the memory→present cut**: smash-cut between the flashback and
  now — the collision is the point (see `recap-flashback.md`).
