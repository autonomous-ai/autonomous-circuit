# reroll-a-shot

**Trigger:** load when the user says "reroll shot X", "that shot looks
wrong", "regenerate", "doesn't match the prompt", "try again on the
close-up", or when one `_board.png` tile is off while the rest are fine.

## Why this exists

Generation is stochastic — the industry term is 抽卡 (card-pulling).
Normal shots get 2-3 candidate draws; hero shots (hook, cliffhanger)
expect 5+. The render cache makes rerolls cheap: shots are
content-addressed by spec + style + provider + cast fingerprints, so
re-running the episode re-renders **only shots whose spec changed** and
re-stitches. A reroll is therefore an EDIT to that one shot's spec —
usually its `prompt` — not a pipeline restart.

Diagnose from the board tile first: wrong subject → prompt's [subject]
segment; dead motion → missing action chain ("then… immediately…");
wrong framing → name the scale (CU/MS/WS); wrong person → cast refs
(`references/patterns/cast-consistency.md`), not the prompt.

## Use the helper

```python
from dramalib.tables import SHOT_DURATION_S  # durations don't change on reroll
```

Reroll = edit the one `Shot(...)` in the episode `.py` (sharpen the
prompt with the formula `[subject] + [action/emotion] + [camera] +
[light] + [style]`), then:

```bash
python ~/.claude/skills/dramacode/scripts/drama /abs/episodes/ep001.py
python ~/.claude/skills/dramacode/scripts/review /abs/episodes --stem ep001
```

Then `Read` the new `_board.png` and compare that tile. An UNCHANGED
spec returns the cached clip — to force a re-draw of a shot you're happy
with structurally, change the prompt's wording (even slightly): same
meaning, new cache key.

## Pitfalls

- **Rerolling by re-running without an edit**: identical spec = cache
  hit = the same clip back. Change the shot's spec.
- **Rewriting the whole scene to fix one tile**: every touched shot
  re-renders and costs money on real providers. Smallest responsible
  edit — one shot.
- **Fixing cast drift with prompt words**: "the SAME woman as before"
  does nothing — identity lives in `series.CAST` ref images.
- **Chasing a hero shot past ~5 draws with the same prompt**: after 3
  misses, the prompt is the problem. Restructure it (subject first,
  explicit action chain, named scale) instead of pulling again.
- **Editing the clip file**: never touch `_shots/*.mp4` — regenerated
  output overwrites it silently.
