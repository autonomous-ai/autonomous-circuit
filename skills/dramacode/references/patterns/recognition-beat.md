# recognition-beat

**Trigger:** load when the user wants "a twist that hurts", "the enemy
was someone they loved", "she was one of us", a mask-off reveal that
re-prices the whole story, or when an episode has a villain but no ache.
The recognition gut-punch from `references/emotional-core.md`.

## Why this exists

The cheapest reveal is "the villain is powerful". The one that breaks
hearts is "the villain is *ours*" — the enemy the protagonist has been
fighting is someone they loved, trusted, or once were. It works because it
weaponizes the bond you planted: every blow the protagonist landed earlier
now lands on someone they love, retroactively. The audience re-feels the
whole episode in one shot.

Four shapes:
- **The mask-off**: the hunter/rival is literally the lost sibling, mentor,
  first love.
- **The mirror**: the enemy is who the protagonist is becoming (the revenge
  that made her the thing she hated).
- **The inheritance**: the enemy is acting on a promise the protagonist
  made and forgot.
- **The double**: a copy/AI/impostor that loves them, standing beside the
  original that doesn't.

The beat is structural (a reversal) AND emotional (the gut-punch) — land
them on the same shot, then cut (`references/patterns/emotional-hard-cut.md`).

## Use the helper

```python
from dramalib.helpers import emotional_arc, sfx_for
from dramalib.spec import Shot

arc = emotional_arc(length_s=50.0, gut_punch="recognition")
# functions: ['bond', 'threat', 'recognition', 'aftermath']

# The reveal insert, then the recognition on a face — one held breath, cut.
reveal = Shot(id="s3_02", kind="insert", duration_s=1.5,
              sfx=sfx_for(moment="reveal"),
              prompt="ECU macro: the transponder ID resolves — a familiar name")
turn   = Shot(id="s3_03", kind="dialogue", duration_s=5, cast=["mara"],
              emotion="recognition", sfx=sfx_for(moment="recognition"),
              line="You kept humming my song.",
              prompt="MCU: the lost sister's face forms on the cracked screen")
```

`recipes/recognition_ep1.py` is a full worked example (sci-fi love story,
the hunter carries the drowned sister's voice).

## Pitfalls

- **No bond to pay off**: the reveal only hurts if the person was loved on
  screen first. Plant the bond by 10s
  (`references/patterns/the-bond-and-the-loss.md`).
- **Explaining the reveal**: don't narrate the connection. Show the tell
  (the lullaby, the scar, the ring) and let the face do it.
- **Recognizing, then reacting for 20s**: the aftermath is the cliffhanger.
  Cut on the recognition, don't process it.
- **A recognition nobody could have felt coming AND nothing pointed to**:
  plant one quiet tell earlier so the reveal is a re-read, not a cheat.
- **Two reveals in one episode**: dilutes both. One gut-punch per episode.
