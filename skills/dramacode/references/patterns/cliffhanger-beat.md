# cliffhanger-beat

**Trigger:** load when writing any episode's ending, when the user says
"cliffhanger", "make them click next", "the ending is weak", or when the
`no_cliffhanger` warning fires.

## Why this exists

The final 5-10 seconds (`CLIFFHANGER_WINDOW_S`) decide whether the next
episode gets played. The rule is mechanical: **cut at the emotional
peak, dead stop** — no resolution, no cooldown shot, no music tail-out.
Three cliffhanger shapes: **suspense** (the door opens on…), **crisis**
(the brakes are cut), **reversal hook** (the ally's face in the enemy's
war room). The last shot is typically a 3s peak-freeze on the image that
carries the question.

## Use the helper

```python
from dramalib.helpers import clamp_duration, validate_beat_law
from dramalib.spec import Shot
from dramalib.tables import CLIFFHANGER_WINDOW_S

final = Shot(id="s2_06", kind="action",
             duration_s=clamp_duration(kind="peak_freeze"),
             cast=["mira"], emotion="peak",
             prompt="freeze on her face as she reads her own name")
```

Always set `Episode(cliffhanger="…")` — it names the peak for the
verifier AND becomes the `prev_cliffhanger` input to the next episode's
`recap_flashback()`. The final *beat* (insert + freeze) should span the
5-10s window even though the freeze shot itself is 3s.

## Pitfalls

- **Resolving before cutting**: one comforting reaction shot after the
  reveal kills the click-through. The freeze IS the last frame.
- **Cliffhanger nobody was primed for**: the peak must pay off a thread
  this episode planted, or it reads as random.
- **`cliffhanger` field unset**: the verifier warns (`no_cliffhanger`)
  and the next episode's recap has nothing to flash back to.
- **Freeze too long**: a 6s+ freeze reads as a render bug. 3s, cut.
- **Same shape every episode**: rotate suspense / crisis / reversal —
  three identical cliffhangers in a row train viewers to stop caring.
