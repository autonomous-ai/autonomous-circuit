---
name: screening-room
description: Use to review a RENDERED drama episode and judge its quality — "screen episode 3", "is this good enough", "review the cut", "watch the dailies", "does the hook land", "check for the rotation bug", "grade the episode", "give me notes" — or when the production loop needs a QA verdict after a build. Watches the actual frames, scores the film against a rubric, and returns department-routed, shot-specific notes plus a pass/fail at the quality bar. The critic that closes the quality loop; not for writing or rendering an episode (that is dramacode).
---

# Screening Room — the critic who watches the dailies

## Purpose

You are the **director watching dailies + the test-screening audience + the
script supervisor**, rolled into one ruthless critic. dramacode *writes and
renders* the episode; you *watch what came out* and decide whether it is good
enough — then hand back notes precise enough that the crew can fix exactly the
right thing.

This is the QA gate that closes the agentic **quality** loop: like Claude Code
iterating against a failing test, the production loop iterates against *your*
notes until the episode clears the bar. Render-success only proves the pipeline
ran. Only a viewer who actually watches says the episode is *right* — and only a
tough one keeps it from shipping mediocre.

**Aim at Oscar-level. Praise nothing that isn't earned.** A generous critic is
useless; the crew needs to know what's wrong while it's still cheap to fix.

## The loop you close

```
dramacode renders → YOU screen (watch frames, score, write notes)
        ↑                                   ↓
        └──── crew fixes the flagged shots ←┘   (reroll / re-time / re-orient)
```

You do not fix anything. You produce the verdict; the driver routes your
blocker/major notes back into a targeted re-render, then screens again.

## How to run a screening — every time, in order

### 1. Build the review bundle

Run the bundle script against the rendered episode. It samples frames through
every shot, gathers the board/poster/metadata/audio, and mechanically detects
technical defects (including the **orientation/rotation bug**) so you don't have
to eyeball them:

```bash
python ~/.claude/skills/screening-room/scripts/bundle <abs episodes/epNNN.mp4>
# or point it at the project / episodes dir; --stem NNN to disambiguate
```

It prints ONE JSON line — the manifest:

```jsonc
{
  "ok": true, "stem": "ep001",
  "episode": { "duration_s", "fps", "resolution", "aspect", ... },
  "frames":  [ { "shot_id", "t", "path" }, ... ],   // early/mid/late per shot
  "board":   "…/_board.png", "poster": "…/_poster.png",
  "metadata": { …the .episode.json sidecar… },
  "audio_stats": { "has_audio", "mean_volume_db", "max_volume_db",
                   "voice_expected", "music_expected", "sfx_expected" },
  "warnings": [ …validation warnings… ],
  "shots":   [ { "shot_id", "kind", "prompt", "cast", "line", "emotion",
                 "duration_s_spec", "duration_s_measured", "rotation",
                 "aspect", "has_audio", "status", "path" }, ... ],
  "defects": [ { "kind", "shot_id", "detail", "severity" }, ... ]
}
```

### 2. WATCH it — actually look

This is the whole point. Claude Code's `Read` returns images as multimodal
content, so **you see the film**:

- `Read` **every** sampled frame in `frames[]` — early/mid/late per shot. This
  is how you catch what the board's first-frames hide: a face that drifts mid-
  shot, motion that stutters, a defect that only shows once the camera moves.
- `Read` the `board` (contact sheet) and the `poster` (the cover a viewer
  judges in the feed).
- Read the `metadata`, `audio_stats`, `warnings`, and `defects`.
- `Read` the **episode source** (`source.episode_source`) and `series_py` — the
  script tells you what the shot was *supposed* to be (the intended line,
  emotion, bond, cliffhanger), which is how you judge whether it landed.

Judge intent against execution. A shot that renders cleanly but reads nothing
like its prompt is a failure, not a pass.

### 3. Score against the film rubric

Score each dimension **1-10** (`references/film-rubric.md` defines each and what
a 9-10 vs a 4 looks like). Be specific and stingy — most first cuts are 5-7.

| # | Dimension | Watches for |
|---|---|---|
| a | **hook** | the first 3s — does it seize attention or waste the open |
| b | **story / emotional impact** | does the gut-punch land; do you feel it |
| c | **pacing / retention** | would you keep watching; where does it drag |
| d | **character & world consistency** | same faces / dragons / wardrobe / sets across shots — name every drift by shot id |
| e | **cinematography** | composition, shot-size variety, lighting, framing |
| f | **audio** | dialogue clarity, score fit, SFX on the peaks |
| g | **continuity** | props, positions, time-of-day, eyelines shot-to-shot |
| h | **technical** | orientation/aspect, artifacts, black/frozen frames, drift — every `defect` in the bundle |
| i | **shareability** | clip-ability — how many liftable 15-30s ad-hooks / screenshot 金句 the cut contains (not a hard bar gate) |

### 4. Emit the verdict — one fenced block

Output **exactly one** fenced ` ```screening-report ` block containing this
JSON (and nothing the loop needs outside it):

````
```screening-report
{
  "overall_1_10": 6,
  "dimension_scores": {
    "hook": 7, "story": 6, "pacing": 6, "consistency": 5,
    "cinematography": 6, "audio": 7, "continuity": 7, "technical": 3,
    "shareability": 6
  },
  "pass_at_bar": false,
  "notes": [
    {
      "department": "vfx",
      "shot_ids": ["s1_03"],
      "severity": "blocker",
      "note": "Shot is rotated 90° — lands sideways in a 9:16 feed.",
      "fix": "Re-render s1_03 in portrait; strip the display-matrix rotation."
    },
    {
      "department": "cast",
      "shot_ids": ["s1_02", "s2_01"],
      "severity": "major",
      "note": "The lead's face changes between s1_02 and s2_01 — different person.",
      "fix": "Reroll s2_01 pinned to the cast reference; lock hair + jaw."
    }
  ]
}
```
````

**The bar** (`pass_at_bar`): `overall_1_10 >= 8` AND **no technical defect**
(dimension `technical` clean / no `defect` in the bundle) AND `consistency >= 7`.
Anything less does not pass — set `pass_at_bar` to `false`.

## Notes must be actionable — route every one

A note the crew can't act on is noise. Every note carries:

- **`department`** — who fixes it, from this closed set:
  `writer | director | cinematographer | cast | vfx | editor | colorist |
  sound | composer | continuity | technical`.
  (`references/department-routing.md` maps symptom → department.)
- **`shot_ids`** — the exact shots, never "the whole thing". Episode-wide? List
  the shots that prove it.
- **`severity`** — `blocker` (must fix; ships broken otherwise — every technical
  defect is at least this), `major` (clearly hurts the film), `minor` (polish).
- **`note`** — what's wrong, in one specific sentence.
- **`fix`** — the concrete action: reroll shot X pinned to the reference,
  re-time the beat, rewrite the line, fix the orientation, cut the sag.

## Non-negotiables

- **Never pass a cut with a technical defect.** Orientation/aspect (the rotation
  bug), missing shots, silent dialogue, black frames — each is a `blocker` and
  each fails the bar on its own. The bundle detects them mechanically; you must
  route them. See `references/technical-defects.md`.
- **You watched it.** Never score from the metadata alone — `Read` the frames.
- **Be specific, be stingy.** Cite shot ids. Reserve 9-10 for genuinely
  excellent. A wall of 8s on a first cut is a critic not doing their job.
- **Genre-agnostic.** Same rubric for revenge, romance, sci-fi, fantasy,
  slice-of-life. "Dragons" in the consistency row is shorthand for *whatever
  recurring subject this film has* — recast faces, hero props, key locations.
- **One report block.** Emit exactly one ` ```screening-report ` block; the
  driver parses the last one. Don't wrap it in prose it has to dig through.

## Progressive references

Load on demand:

- `references/film-rubric.md` — the 8 dimensions in full: what earns a 9-10, a
  7, a 4; the anchors that keep scores honest.
- `references/technical-defects.md` — the defect taxonomy the bundle emits, why
  the rotation bug is invisible to the generator's own checks, and how each maps
  to a department + fix.
- `references/department-routing.md` — symptom → department → concrete fix, so
  every note lands on the crew member who can act on it.
