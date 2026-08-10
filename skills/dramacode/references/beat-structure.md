# Beat structure — the episode formula every hit follows

**Load when:** writing any new episode's beat sheet, diagnosing "it feels
slow / flat / confusing", or planning where an episode's emotional turns
land. The constants live in `dramalib.tables` — this doc is the why;
`beat_sheet()` is the how.

## The beat law

Every number here is a `dramalib.tables` constant — import it, never
retype it.

| Window | Rule | Constant |
|---|---|---|
| 0-3s | **The hook.** Into conflict immediately. 80% of viewers decide within 6s. | `HOOK_MAX_S` |
| 0-10s | **The world.** Core conflict + character relations + protagonist's goal all established. Exposition never exceeds 10s. | `WORLD_BY_S` |
| 0-30s | **First emotional reversal** (爽点): suppression → eruption. | `FIRST_REVERSAL_BY_S` |
| every 20-30s | **An emotional beat** — 3-4 beats per minute, no dead air. | `BEAT_INTERVAL_S` |
| final 5-10s | **The cliffhanger.** Cut at the emotional peak. Dead stop. | `CLIFFHANGER_WINDOW_S` |

## The three hook types

Pick one deliberately; a hook that is none of these is not a hook.

1. **Direct confrontation** (正面冲突) — the slap, the accusation, the
   contract shoved across the table. Opens mid-conflict.
2. **Mystery** (悬念) — an image that demands explanation: the execution
   platform, the coffin with a heartbeat monitor.
3. **Extreme contrast** (极致反差) — janitor keyed into the CEO elevator;
   a bride in handcuffs.

The research's worked case executes it literally: **3s hook → 2s conflict
statement → 2s attitude** — three shots, seven seconds, and the audience
knows who to watch and why.

## The 2-minute episode template

For longer (manju-length) episodes, the consensus internal clock:

```
0:00-0:10  recap-as-flashback (optional — binge platforms skip it;
           see references/patterns/recap-flashback.md)
0:10-0:30  new scene: today's conflict opens
0:30-1:20  core event
1:20-1:50  reversal / climax
1:50-2:00  cliffhanger hook
```

Shorter episodes compress the middle, never the ends: the hook and the
cliffhanger are fixed-size; the core event is what flexes.

## Dialogue register

Short, fragmented, sharp (短碎锋利):

- A single spoken line ≤ 15 Chinese characters / one breath in English
  (`DIALOGUE_LINE_MAX_ZH`).
- 200-300 characters of dialogue TOTAL per 1.5-2 min episode
  (`DIALOGUE_CHARS_PER_EPISODE`) — the picture carries the rest.
- Two-handers preferred. No monologues — break them into exchanges or
  intercut with reaction shots.
- **"Kneeling beats crying, a slap beats a speech."** When a beat can be
  an action instead of a line, make it an action.

## Series-level pacing (红果 official)

- **Eps 1-10 — the golden window**: protagonist, core conflict, and the
  reason to keep watching must all land here. A series that "starts slow
  and gets good at ep 15" is dead at ep 3.
- **Eps 11-30 — main development**; **31-80 — escalating climaxes and
  payoffs**.
- Minor reversal every 5-10 eps; major reversal every 20-30; **2-3 core
  reversals per series** (`SERIES_PACING`). More than that reads as
  churn, fewer as stall.

## Using the helpers

```python
from dramalib.helpers import beat_sheet, shots_from_beats, validate_beat_law

beats  = beat_sheet(genre="revenge", episode_no=1, length_s=75.0)
drafts = shots_from_beats(beats=beats, cast=["li_wei", "dorian"])
# ...replace every TODO with real writing, group into scenes...
warnings = validate_beat_law(episode=ep)   # ship at zero
```

`validate_beat_law` checks structural proxies (hook timing, establish
budget, cadence gaps, cliffhanger window, leftover TODOs). It cannot
judge whether a reversal actually reverses — that judgment is yours, per
this doc.
