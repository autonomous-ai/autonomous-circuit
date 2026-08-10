# sfx-cue-cheatsheet

**Trigger:** load when adding the per-shot `sfx` list, when the user says
"add sound effects", "which sound for this beat", "the peak doesn't hit",
or whenever a reversal / reveal / loss beat feels weak on the board. The
quick-reference companion to `references/sound-design.md`.

## Why this exists

A peak without its sound is half a peak (slap + glass-shatter SFX:
completion **+172%** in the research). The mistake isn't forgetting SFX —
it's not knowing *which* cue for *which* beat, and putting SFX on every
shot until it's mush. The rule: **one cue that IS the peak + one texture**,
1-3 total, and only on beats that need a hit. The connective tissue stays
ambience.

Map beat → moment (each is a key into `dramalib.tables.SFX_CUES`):

| Beat on screen | `moment` |
|---|---|
| a slap / a struck face | `slap` |
| shattering glass, a dropped vase | `glass_shatter` |
| a door / gate / lid slamming | `door_slam` |
| a blow, a body hitting the floor | `punch` |
| a shot fired | `gunshot` |
| something cutting through metal/hull | `tearing_metal` |
| a tense wait, indecision, a hovering hand | `heartbeat`, `clock` |
| storm, night, exterior grief | `rain`, `wind` |
| a burning building, embers | `fire` |
| underwater, drowning memory | `water` |
| an emergency, a countdown | `alarm` |
| an incoming call, a text landing | `phone_buzz` |
| someone approaching offscreen | `footsteps` |
| **the reveal lands** | `reveal` |
| **the trusted one turns** | `betrayal` |
| **the enemy-was-ours moment** | `recognition` |
| **the bond is spent / cut on the wound** | `loss` |
| **a sacrifice** | `sacrifice` |
| **a reunion** | `reunion` |
| a bond object (song, memory) | `lullaby` |
| a death on a monitor | `monitor_flatline` |

## Use the helper

```python
from dramalib.helpers import sfx_for
from dramalib.spec import Shot

Shot(id="s1_01", kind="action", duration_s=3, cast=["mira"], emotion="fury",
     sfx=sfx_for(moment="slap"),        # ['sharp skin-crack slap', 'crowd gasp']
     prompt="ECU: a slap frozen at impact, guests recoiling")
```

Across one episode, pull at least one from each family: an **impact peak**
on every reversal, a **tension texture** under the escalation, an
**emotional sting** on the gut-punch. Never retype cue strings — the table
owns the vocab so a series stays sonically consistent.

## Pitfalls

- **SFX on every shot**: goes to mush. Cue the moments; leave the rest to
  ambience.
- **No cue on the peaks**: the flatness you can't see on a still.
- **Wrong family on the gut-punch**: an impact cue where a sting belongs —
  a betrayal wants `betrayal`/`loss`, not `punch`.
- **Score fighting the sting**: at the gut-punch, drop the bed and let the
  sting carry it (`references/sound-design.md`).
- **Unknown moment string**: `sfx_for` raises `ValueError` with the known
  keys — pick one or extend `SFX_CUES` in `dramalib/tables.py`.
