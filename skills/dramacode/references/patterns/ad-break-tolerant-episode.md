# ad-break-tolerant-episode

**Trigger:** load when the series targets a free platform (红果 /
ad-supported), when the user says "no paywall", "free model", "ad
breaks", or when `gate_plan(market="free", ...)` returns
`ad_break_tolerant: True`.

## Why this exists

Free-platform economics replace paywall gates with mid-roll ads: an
~15s interruption (`AD_BREAK_TOLERANCE_S`) can land between any two
beats, and the platform — not you — picks the moment. An episode
survives this only if its structure is **resumable everywhere**: an
emotional beat every 20-30s (`BEAT_INTERVAL_S`), each beat
self-contained enough that "15 seconds of detergent, then the next
beat" still plays. Practically, this pushes free-market episodes toward
MORE reversals per minute and AWAY from long single-breath sequences —
tension must re-arm quickly after any cut point.

## Use the helper

```python
from dramalib.helpers import beat_sheet, gate_plan, validate_beat_law
from dramalib.tables import AD_BREAK_TOLERANCE_S, BEAT_INTERVAL_S

plan = gate_plan(market="free", total_episodes=60)
# {'gates': [], 'ad_break_tolerant': True}
beats = beat_sheet(genre="fuchou", episode_no=12, length_s=90.0, market="free")
```

`validate_beat_law`'s cadence check (no 30s window without a
dialogue/action beat) is exactly the ad-tolerance property — keep it at
zero warnings and the episode is break-safe. End every beat on a
micro-hook (a look, a ring, a door) so a break placed there still leaves
a reason to come back.

## Pitfalls

- **Importing the paid structure**: loading eps 9-10 with hooks for a
  gate that doesn't exist wastes the series' best material — spread
  reversals evenly instead.
- **One long virtuoso scene**: a 40s continuous confrontation is
  exactly what an ad will bisect. Split it into 20-30s beats with
  micro-hooks at the seams.
- **Cliffhanger discipline slipping** ("it's free anyway"): free
  platforms live on next-episode rate MORE than paid — the final 5-10s
  rule is unchanged.
- **Assuming you choose the break points**: you don't. Structure for
  "anywhere", not for "after the reversal".
