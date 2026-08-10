# cold-open-hook

**Trigger:** load when writing any episode's opening, when the user says
"hook", "boring start", "viewers drop off", "grab them faster", or when
the `hook_too_long` warning fires.

## Why this exists

80% of viewers decide within 6 seconds; the format's answer is a hook
inside the first 3 (`HOOK_MAX_S`). A cold open is not "start earlier in
the scene" — it is starting at the frame where the conflict is already
undeniable. Four types, pick one deliberately:

1. **Direct confrontation** (正面冲突): the slap mid-swing, the
   accusation mid-sentence.
2. **Mystery** (悬念): an image that demands explanation — the execution
   platform above the cloud sea.
3. **Extreme contrast** (极致反差): the janitor keyed into the CEO
   elevator.
4. **Dignity theft** (公开夺权) — *the mega-hit opener*: the lead is
   *publicly stripped of a specific, named identity or right* by a named
   antagonist, in the first shot. Not a generic slap — a *status* being
   taken: the divorce papers shoved across the table, the mate-rejection,
   the firing, the disinheritance at the will reading. It plants the exact
   wound the whole series will pay back, so the debt starts accruing at t=0.

## Canonical opening lines (each a stakes-question generator)

Reusable first-line templates — the line IS the hook:

- "Sign the divorce papers." / "I want a divorce — and full custody."
- "I, Alpha [X], reject you as my mate."
- "You're fired. Effective today, you work for [rival]."
- "Get out. You were never really our daughter."
- "The baby isn't yours." / "I'm marrying your sister."
- "You have thirty days to pay, or the company — and your father — are mine."

Localize per market (`references/genre-playbook.md`); the shape is always
*a named person takes a named thing from the lead, on camera, now.*

The hook shot is a hero shot: budget 5+ candidate draws for it, and
verify its first frame on the `_board.png` before anything else.

## Use the helper

```python
from dramalib.helpers import beat_sheet, clamp_duration
from dramalib.tables import HOOK_MAX_S

beats = beat_sheet(genre="revenge", episode_no=1, length_s=60.0)
# beats[0] == {"t_start": 0.0, "t_end": 3.0, "beat": "hook", ...}
```

Set `Episode(hook_max_s=HOOK_MAX_S)` so the verifier holds the episode
to the law. Open on an `action` (or a 3s-pinned `establish` that IS the
mystery image, as `recipes/manju_ep1.py` does), never a leisurely wide.

## Pitfalls

- **Establish creep**: two 4s establishing shots before anyone acts —
  `hook_too_long` fires. The world can be established DURING the
  conflict; a slap in a penthouse lobby establishes the lobby.
- **Hook without stakes**: an explosion nobody is in. The hook must
  implicate a lead — put a cast id in the first shot.
- **Cold open that spoils the reversal**: hook with the ep's *question*,
  not its *answer*.
- **Logo/title cards first**: never. The head amplifies the hook
  (`references/assembly-conventions.md`); branding lives at the tail.
