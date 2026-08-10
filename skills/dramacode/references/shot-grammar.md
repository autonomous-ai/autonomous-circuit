# Shot grammar — durations, scales, cuts, and prompts

**Load when:** writing or fixing any shot list, deciding where to cut,
writing the 画面提示词 (AI prompt) column, or diagnosing "too choppy /
too slow / the shot doesn't match the script".

## Durations by shot kind

Owned by `dramalib.tables.SHOT_DURATION_S`; emit via `clamp_duration()`:

| Kind | Craft range | Note |
|---|---|---|
| establish / wide | 3-4s | never exceed 5s (`ESTABLISH_ABS_MAX_S`) |
| action / emotional expression | 2-3s | |
| dialogue | 2-3s **per sentence** | a 2-sentence line earns ~5s |
| insert (prop/detail close-up) | 1-2s | clamps to the 1.5s contract floor |
| emotional-peak freeze | 3s | the cliffhanger's last image |

Two ceilings to keep straight: the contract validates
`duration_s ∈ [1.5, 15]` (floor lowered 2026-08-09); **this skill caps generation at 10s**
(`SHOT_HARD_CAP_S`) because generated video degrades past 10s. When a
moment wants 12 seconds, it is two shots.

Volume: **8-15 shots for a 60-90s episode** (`SHOTS_PER_EPISODE`); manju
runs 20-30 shots/min at 2-4s (contract floor 1.5s). Scenes per
episode: 1-3 (`SCENES_PER_EPISODE`) — few locations, reused assets.

## The five scales

Restrict 景别 to five: **ECU / CU / MCU / MS / WS**
(大特写/特写/近景/中景/全景). Name the scale in the prompt; providers
follow named scales far better than descriptions of distance.

## When to cut (new-shot triggers)

Start a new shot on: location change · time jump · cast change · key
prop's first appearance · emotional turn · action start/end · speaker
switch · needed scale change.

Do **NOT** split: continuous action, sustained emotion, ongoing dialogue
within one speaker's breath. A cut inside a continuous gesture reads as
an error, not pace.

## The I2V prompt formula

```
[subject] + [action/emotion] + [camera] + [light/atmosphere] + [style]
```

Video prompts need **explicit action chains**: "she reaches for the
letter, then freezes, immediately looks up" — sequence words drive
motion. A prompt that only describes a state produces a slideshow.

For the director-grade version of this formula — size + move + light +
action + atmosphere, the flat→cinematic rewrite ladder, and why to vary
the shot size across the cut — see `references/cinematic-shots.md`.

## Candidates and rerolls

- Generate 2-3 candidates per shot, pick the best; **hero shots (the
  hook, the cliffhanger) expect 5+ draws**.
- The cheapest QC gate in the whole pipeline: **verify storyboard STILLS
  against the script BEFORE any video spend.** A wrong first frame never
  becomes a right clip. This is why the loop Reads `_board.png`.
- Reroll mechanics: `references/patterns/reroll-a-shot.md` — the render
  cache re-renders only shots whose spec changed.

## The 14-field 分镜表

The shot list is the production source of truth. Canonical industrial
field set:

镜号 · 时间轴 · 时长 · 场景 · 人物 · 道具 · 剧本原文 · **画面提示词**
(the AI prompt — a first-class column, never an afterthought) · 景别 ·
镜头运动 · 人物动作 · 对白 · BGM/音效 · 备注

In the Plan phase, present it as a markdown table (SKILL.md shows the
header). In code, each row becomes one `Shot(...)`: 镜号→`id`,
时长→`duration_s`, 人物→`cast`, 画面提示词→`prompt`, 对白→`line`, and
景别/镜头运动/人物动作 fold INTO the prompt via the formula above.

## Using the helpers

```python
from dramalib.helpers import clamp_duration, shots_from_beats
from dramalib.tables import SHOT_DURATION_S, SHOT_HARD_CAP_S

d = clamp_duration(kind="dialogue_per_sentence", duration_s=2.5 * 2)  # 2 sentences
```

`shots_from_beats` drafts the skeleton with table durations; the prompt
craft in this doc is the part you write yourself.
