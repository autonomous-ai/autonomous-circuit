---
name: dramacode
description: Use when the user wants to create, edit, or render an AI short drama from a natural-language description — "make a short drama", "a 60-episode revenge series", "write episode 3", "reroll shot s2_04", "make her angrier in the confession scene", "add a cliffhanger", "tighten the hook" — or to tweak, re-render, or fix an existing episode `.py` in an Autonomous TV drama project (series.py + episodes/).
---

# DramaCode — AI short dramas via episode programs

## Purpose

Turn natural-language drama requests into rendered, bingeable vertical
episodes. The source of truth is **Python** — a series bible
(`series.py`) plus one small episode program per episode
(`episodes/epNNN.py`). Every generated `.py` file is an editable
screenplay-as-code. The user owns the project; tweak a line, reroll a
shot, re-render.

Optimised for the **short-drama format** (短剧 / ReelShort-class): 9:16
vertical, 45-120 second episodes, a hook in the first 3 seconds, a
cliffhanger in the last 5-10. The deliverable is a stitched `.mp4` with
burned subtitles, plus the per-shot clips and review images the loop
inspects.

## Make it cinematic and heartbreaking — by default

The engine (frontier video, character consistency, lip-sync, voices,
score, SFX) is being upgraded to render whatever you write beautifully.
**Your job is the craft it renders.** A plain prompt — "make a revenge
drama", "a sci-fi love story" — must come out *thrilling and aching*, not
generic. That is not luck; it is four habits, applied every time. Default
to them; don't wait to be asked.

1. **Lead with a bond, aim at a gut-punch.** Plot is watchable; heartbreak
   is memorable. Every episode plants a **bond** early (someone/something
   the protagonist loves and can lose) and spends it on one irreversible
   turn — betrayal, sacrifice, recognition ("the enemy was once ours"), an
   irreversible loss, or an aching reunion. **The gut-punch has the same
   status as the hook.** → `references/emotional-core.md`.
2. **Write shots a director would recognize.** Every `prompt` carries a
   shot size + camera move + lighting + one action + atmosphere — not a
   caption ("she is sad in a room"). And vary the size: a wall of the same
   scale reads flat. → `references/cinematic-shots.md`.
3. **Sound is where the emotion lands.** Cue per-shot `sfx` on every peak
   (a slap without its crack is half a slap), pick a score mood, and shape
   it — build to the climax, **cut the score for the gut-punch** (silence
   is the loudest score). → `references/sound-design.md`.
4. **Keep the feeling turning.** The beat law bans dead air; the emotional
   layer bans *flatness* — a new feeling every 15-25s, open on the
   strongest image, end on the hardest cliffhanger. → `emotional_arc()`.

Genre-agnostic on purpose: the same machine drives romance, revenge,
thriller, sci-fi, fantasy and slice-of-life. `recipes/recognition_ep1.py`
is all four habits built end to end. Read `references/emotional-core.md`
before the beat sheet — it is tier-1, not optional polish.

## Treat the series as a project

**A drama is a small software project, not a single script.** One project
= one series; one file = one episode. The layout (contract §1):

```
my_series/
├── spec.md              series intent: premise, genre, cast, arc, gates
├── series.py            the series bible — SERIES constants + CAST
│                        (the cast-book skill owns the marked CAST block)
├── episodes/
│   ├── ep001.py         one generation target; defines gen_episode()
│   ├── ep001.mp4        ┐
│   ├── ep001.episode.json │ artifacts land next to the source
│   ├── ep001.srt        │
│   ├── ep001_shots/     │  shot_<id>.mp4 + shot_<id>.json per shot
│   └── ep001_review/    ┘  _poster.png, _board.png (the contact sheet)
├── cast/<id>/           reference images + ref_prompts.json (cast-book)
├── inputs/              chat reference images (excluded from catalog)
└── .video/render-cache/ content-addressed shot clips — a re-render after
                         an edit re-renders only changed shots
```

Rules of the project format:

- **Series-wide facts live in `series.py` only** — title, genre, style
  preset, aspect/resolution/fps, language, CAST. Editing the bible
  invalidates every rendered episode (the sidecar fingerprint folds it
  in), so change it deliberately.
- **One file per episode.** Each `epNNN.py` defines `gen_episode()` at
  module scope and does `import series` for cast ids and pacing constants
  (the runner puts the project root on `sys.path`).
- **Every number comes from `dramalib` tables** — shot durations, beat
  timings, gate placements, subtitle limits. Geometry code never
  hardcoded millimeters in the donor; episode code never hardcodes
  seconds here. See "Where the numbers come from".
- **Validation is folded into the episode file**: a `validate(ep)` with
  hard `assert`s (impossible specs fail before paying a render) and a
  `functional_warnings(ep)` returning `validate_beat_law(episode=ep)` —
  soft beat-law warnings that ride the envelope. Copy the pattern from
  `templates/project_skeleton/episodes/ep001.py`.
- **Copy `templates/project_skeleton/` when starting a new series** —
  it is the canonical layout. Fill in `spec.md` first.

### The envelope contract

`gen_episode()` returns exactly one shape — the envelope dict. No legacy
forms; unknown keys raise `TypeError`:

```python
def gen_episode():
    ep = build_episode()
    validate(ep)                                  # hard asserts
    return {
        "episode": ep,                            # dramalib.spec.Episode
        "warnings": validate_beat_law(episode=ep) # optional, project-declared
    }
```

`Episode` / `Scene` / `Shot` come from `dramalib.spec` (duck-typed on
attributes — a plain dict with the same keys also works):

```python
Episode(
  number=1, title="The Slap",
  hook_max_s=3.0,                       # cold-open rule; the verifier reads this
  scenes=[
    Scene(id="s1", location="penthouse lobby, night, rain",
      shots=[
        Shot(id="s1_01", kind="establish", duration_s=3,
             prompt="rain-slick lobby doors, neon reflections, she enters"),
        Shot(id="s1_02", kind="dialogue", duration_s=5, cast=["li_wei"],
             line="You never told me he was alive.", emotion="dread",
             prompt="close-up, trembling, letter in hand"),
      ]),
  ],
  cliffhanger="freeze on the burning letter",     # verifier warns when unset
  bgm="tense-strings",
  burn_subtitles=True,
)
```

Shot rules (enforced by dramapy validators, not convention):
`kind ∈ {establish, action, dialogue, insert}`; `dialogue` requires
`cast` **and** `line`; every cast id must exist in `series.CAST`;
`duration_s ∈ [1.5, 15]` per the contract (floor lowered from 3.0 in the
2026-08-09 CHANGES entry) — **but this skill's own cap is 10s** (generated video degrades past 10s; see Non-negotiables).

## The loop

The dramacode skill turns you into a self-correcting showrunner. **You
close the feedback loop yourself** — do not hand a possibly-broken
episode to the user for verification.

```
understand task → inspect project → beat sheet + shot list → edit .py
      ↑                                                          ↓
      └── fix ← Read _board.png ← read the JSON verdict ← run scripts/drama
```

What "fix" means in practice:

- `ok=false`: read `error.code` + `message`, change the smallest
  responsible thing, re-run. `SYNTAX_ERROR` / `RUNTIME_ERROR` → the
  episode `.py`; `VALIDATION_FAILED` → the spec shape (envelope keys,
  shot rules, a failed `validate()` assert); `PROVIDER_ERROR` /
  `RENDER_TIMEOUT` → retry once, then reduce shot count or duration;
  `EXPORT_ERROR` → the stitch — check aspect and fps in `series.py`.
- `warnings` non-empty: severity `error` kinds (`missing_shot`,
  `render_failed`, `aspect_mismatch`, `stitch_failed`) are **blocking**.
  `warning` kinds (`duration_drift`, `silent_dialogue`, `hook_too_long`,
  `no_cliffhanger`, `functional`) mean the episode renders but fails the
  craft bar — fix and re-run until the list is empty. `info` advises.
- The `_board.png` looks wrong (cast drift between shots, an empty
  frame, captions colliding with the platform UI, a shot that reads
  nothing like its prompt): edit that shot's `prompt` (or the cast refs)
  and re-run — the render cache re-renders **only the changed shots**.

You have everything you need to close the loop on your own: the user's
prompt and prior chat, the project files (`spec.md`, `series.py`, prior
episodes), `scripts/drama` to render, `scripts/check` for a cheap
structure pass, `scripts/review` + `Read` on `_board.png` to actually
look, and this SKILL.md + references for the craft.

**Iterate until the episode is right.** Soft cap of 4 iterations before
you ask the user a clarifying question — past that, you're probably
guessing about taste (tone, casting, plot) rather than fixing a
structural bug.

## Plan-phase design discipline

When the Video app runs you in its **Plan phase** (enforced by the phase system
prompt: no writing `.py`, no running the generator), you write no episode
code — you produce the plan the user approves. That plan is an **emotional
spine + beat sheet + shot list**, not a synopsis. Three artifacts, in this
order:

**1. The emotional spine** — three lines that decide whether anyone cares
(from `references/emotional-core.md`; the plan is not done without them):

- **Bond**: the one concrete thing the protagonist loves and can lose —
  an image you can plant by 10s, not a fact ("they were close").
- **Stakes**: the single specific way that bond is lost.
- **Gut-punch**: which of the five turns it ends on (betrayal / sacrifice
  / recognition / irreversible_loss / reunion) and the shot it lands on.

Everything downstream — the hook, the escalation, the cliffhanger — points
at that gut-punch. Derive the feeling track with
`emotional_arc(length_s=…, gut_punch=…)` and show it beside the beat sheet.

**2. The beat sheet** — the episode's timed spine. Hard rules (the beat
law, from `dramalib.tables` — violating any of these is a defect, not a
style choice):

- **0-3s: the hook.** Into conflict immediately — direct confrontation,
  mystery, or extreme contrast. 80% of viewers decide within 6 seconds.
- **By 10s: the world.** Core conflict + character relations +
  protagonist's goal all established. Exposition never exceeds 10s.
- **By 30s: the first reversal** (suppression → eruption).
- **Every 20-30s: an emotional beat** (3-4 beats per minute) — and the
  *feeling* itself should turn every 15-25s (`FEELING_SHIFT_S`): tenderness
  → unease → dread → the turn. Cadence is the beat law; a feeling that
  never changes is flat even when the plot moves.
- **Final 5-10s: the cliffhanger.** Cut at the emotional peak. Dead stop.
- **Open on the strongest image, end on the hardest cliffhanger.** The
  first frame and the last frame are the two you get to choose deliberately
  — spend the hero-shot budget there.

Derive it with `beat_sheet(genre=…, episode_no=…, length_s=…)` and show
it as `t_start-t_end · beat · what happens`.

**3. The shot list** — the production source of truth, as the 14-field
分镜表 the industry actually uses. Fill in a markdown table with these
columns (compress to the fields that matter for the episode, but never
drop 画面提示词 — the AI prompt is a first-class column):

| 镜号 shot | 时间轴 timeline | 时长 dur | 场景 scene | 人物 cast | 道具 props | 剧本原文 script | 画面提示词 AI prompt | 景别 scale | 镜头运动 camera | 人物动作 action | 对白 line | BGM/音效 audio | 备注 notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

Prompt formula for the 画面提示词 column: `[shot size] + [camera move] +
[lighting] + [blocking/one action] + [atmosphere]` — a director's shot
order, never a caption. Video prompts need explicit action chains ("then…
immediately… next…"). Scales restricted to five: ECU/CU/MCU/MS/WS
(大特写/特写/近景/中景/全景), and **vary the size across the cut** — a wall of
the same scale reads flat (`shot_rhythm()` flags a run over three). The
full method, with the flat→cinematic rewrite ladder, is
`references/cinematic-shots.md`. Cue per-shot `sfx` on every peak
(`references/sound-design.md`).

**Scale to the request.** A trivial edit ("make her angrier in s2_03",
"cut the recap") needs only the exact before→after shots — one to three
lines. A new episode or a series plan gets the full treatment.

### Where the numbers come from — the tables own them, never guess

Every duration, gate, and limit in an episode comes from exactly one
place: **`dramalib.tables`**. Don't carry pacing numbers from memory and
never invent one — retyped numbers drift, and drift is how a 6-second
establishing shot sneaks into a cold open.

| Number | Owner |
|---|---|
| Shot duration by kind (establish 3-4s, action 2-3s, dialogue 2-3s/sentence, insert 1-2s, peak-freeze 3s) | `SHOT_DURATION_S` + `clamp_duration()` |
| The beat law (hook 3s, world 10s, reversal 30s, beat every 20-30s, cliffhanger 5-10s) | `HOOK_MAX_S`, `WORLD_BY_S`, `FIRST_REVERSAL_BY_S`, `BEAT_INTERVAL_S`, `CLIFFHANGER_WINDOW_S` |
| Episode length per format (ai-drama 45-90s, manju 60-180s) | `EPISODE_LENGTH_S` + `episode_length_for()` |
| Paywall gates (cn 10/20/30; overseas first 5-12, major 26-30; free = none) | `PAYWALL_GATES` + `gate_plan()` |
| Subtitle limits (14 zh / 42 en chars, lower-fifth, 2px stroke) | `SUBTITLE` |
| Audio levels (dialogue +10dB over bed, BGM −5dB, TTS speeds) | `AUDIO` |
| Genre beat patterns (赘婿/战神/重生/复仇/霸总/werewolf/billionaire/revenge) | `TROPE_TABLE` + `trope_for_genre()` |
| The five gut-punches + bond types + the feeling-shift cadence (15-25s) | `EMOTIONAL_TURNS`, `BOND_TYPES`, `FEELING_SHIFT_S` + `emotional_arc()` |
| Per-shot SFX cue vocabulary (slap, glass_shatter, reveal, loss, …) | `SFX_CUES` + `sfx_for()` |
| Score moods + the score's arc (build → climax → cut for the gut-punch) | `SCORE_MOODS`, `SCORE_ARC` |
| Cinematic vocabulary (the five scales, camera moves, lighting keys) | `SHOT_SCALES`, `CAMERA_MOVES`, `LIGHTING_KEYS` |

One clamp to know: the contract validates `duration_s ∈ [1.5, 15]`, so
`clamp_duration()` emits research-true 1.5s inserts and caps everything
at the 10s generation limit. Use the helper; don't re-derive the clamp.

## Use this skill when

The user asks for any of:

- A new drama, series, or episode: "make me a revenge short drama",
  "60-episode werewolf romance", "write episode 3", "the next episode".
- An edit to an existing episode `.py`: "make her angrier in the
  confession scene", "tighten the hook", "swap scene 2's location",
  "cut the recap", "add a face-slap beat".
- A reroll: "reroll shot s2_04", "that shot doesn't match the prompt",
  "the cast looks different in shot 5".
- A render/check: "render episode 2", "why does it warn about the hook".

Do **not** use this skill for: long-form/landscape video, music videos,
documentary or ad content, generating standalone images, or managing cast
reference images (that's the `cast-book` skill; `story-analysis` handles
the pre-project brief). If a sibling skill fits, use it; otherwise say
this skill is not the right tool.

## Default assumptions

Use these defaults unless the user or `series.py` specifies otherwise:

- **Format**: 9:16 vertical, 1080×1920, **24fps** (the contract default;
  match `series.py` once it exists — never mix fps within a series).
- **Episode length**: 45-120s (ai-drama sweet spot 45-90s; manju up to
  180s).
- **Shots per episode**: 8-15 (manju cuts faster: 20-30 shots/min at
  1.5-3s).
- **Scenes per episode**: 1-3 locations — asset reuse is the doctrine.
- **Cast**: 2-3 leads. Two-handers beat ensembles.
- **Subtitles**: burned in, always (`burn_subtitles=True`), lower fifth.
- **Provider**: `mock` (free, offline placeholder clips) until the user
  asks for a real render; `fal` needs `FAL_KEY`.
- **Language**: follow `series.language`; dialogue register is short,
  fragmented, sharp — a single line ≤ 15 Chinese chars / one breath in
  English.

### Ask only about preferences; decide all craft silently

Split every open decision into two buckets and treat them oppositely.
The user verifies **taste**, never **shot mechanics**.

- **Personal preferences — only the user can know these. Ask, but
  sparingly.** Genre and tone; market (CN paid / free / overseas — it
  changes gate plan and length); cast names and looks; how dark, how
  spicy (within compliance); series length. Ask via question chips, the
  fewest, highest-leverage questions — ideally one, never a quiz.
- **Craft choices — there is a best answer. Never ask; pick it.** Shot
  durations, shot scales, when to cut, beat placement, cliffhanger
  mechanics, subtitle position, audio levels, recap length, gate
  episodes for a known market. Decide these from the tables and the
  references — silently. Default-and-state: make the call, note it in
  one line only if it changes the story, move on.

The test: *could a competent short-drama director pick this correctly
without knowing the user?* If yes, it's craft — decide it.

## Available tools

The skill lives at `~/.claude/skills/dramacode/` (or wherever installed).
From the workspace:

```bash
# Render an episode (the normal case: pass the episode file).
python ~/.claude/skills/dramacode/scripts/drama episodes/ep001.py \
       [--out-dir DIR] [--stem NAME] [--provider mock|fal] [--wall-clock-s S]

# Cheap structure check — mock provider, artifacts discarded.
python ~/.claude/skills/dramacode/scripts/check episodes/ep001.py

# Review pass — warnings + the _board.png / _poster.png paths
# (regenerates them if missing).
python ~/.claude/skills/dramacode/scripts/review episodes/ [--stem ep001]
```

Always pass **absolute paths** — the agent's cwd may not be the user's
workspace.

**`scripts/drama`** — primary tool. Runs the episode in an isolated
subprocess (rlimits + restricted imports + wall-clock kill; the project
root goes on `sys.path` so `import series` works) and writes the full
artifact set next to the source. Prints a single JSON line:
`{ok, episode_path, srt_path, metadata_path, duration_s, shot_count,
shots:[{id, path, duration_s, status}], warnings, error?}`.

**`scripts/check`** — quick validator. Same pipeline on the mock
provider into a tempdir; reports `{ok, duration_s, shot_count, warnings,
error?}` without keeping artifacts. Use it before paying for a real
render.

**`scripts/review`** — the eyes. Reads the `.episode.json` sidecar and
prints `{ok, stem, warnings, board_png, poster_png, shots}`. Then `Read`
the `_board.png` — the contact sheet shows the first frame of every shot;
it is where cast drift, dead frames, and caption collisions become
visible.

## Running the loop

Each phase in concrete terms:

### 1. Understand the task

Classify it: **new series**, **new episode**, **edit of an existing
episode**, **reroll of specific shots**, or **review/check only**. If the
user attached reference images, `Read` them — they seed cast looks and
style. If a ```drama-brief block exists in the conversation (from
`story-analysis`), it is the approved spec — build from it, don't
re-litigate it.

### 2. Inspect the project

List the workspace. Read `spec.md` and `series.py` first — genre, cast
ids, market, format bound every decision downstream. For an edit, `Read`
the episode `.py` before writing; minimal diffs respect prior tweaks and
maximize the render cache (only changed shots re-render). For a new
series, copy `templates/project_skeleton/` and fill in `spec.md`.

### 3. Beat sheet + shot list

Derive the spine with `beat_sheet()`, expand with `shots_from_beats()`,
then do the writing the helpers can't: replace every `TODO:` prompt and
line with real craft. (In the Video app's Plan phase this becomes the user-facing
plan — see Plan-phase discipline.)

### 4. Edit the `.py`

Write the episode file with: a 1-line docstring, the beat skeleton as a
comment, `build_episode()` returning the `Episode`, `validate()` with
hard asserts, `functional_warnings()` calling `validate_beat_law`, and
`gen_episode()` returning the envelope. Mimic
`templates/project_skeleton/episodes/ep001.py`. Use absolute paths with
the Write tool.

### 5. Run `scripts/drama`

```bash
python ~/.claude/skills/dramacode/scripts/drama /abs/path/episodes/ep001.py
```

Renders every shot (cache-aware), stitches, burns subtitles, writes the
sidecar, prints the JSON verdict.

### 6. Read the verdict — then LOOK

Don't skip this step. `ok=true` says the pipeline ran; only your eyes say
the episode is *right*.

- **Resolve `warnings` first.** Anything severity `error` is blocking.
  `hook_too_long` / `no_cliffhanger` / `functional` beat-law warnings:
  fix the structure, not the wording of the warning.
- **Run `scripts/review` and `Read` the `_board.png`.** Check, tile by
  tile: does each first frame match its shot's prompt? Is the cast
  visually consistent across tiles (the #1 dropout driver)? Do captions
  sit in the lower fifth, clear of platform UI? Does the cliffhanger
  tile actually read as a peak?
- **Check the numbers in the JSON**: `duration_s` inside the format
  range, `shot_count` in 8-15, `shots[].status` — a `failed` shot means
  a missing clip even if the stitch survived.
- Compare against the beat sheet: hook by 3s, first reversal by 30s,
  cliffhanger in the final window.

### 7. Fix

Apply the **smallest responsible** change: a prompt reword for a bad
frame (reroll — only that shot re-renders), a duration from
`clamp_duration()` for a pacing warning, a scene restructure only when
the beat sheet itself was wrong. Then back to step 5. **Soft cap: 4
iterations** before asking the user a clarifying question.

### 8. Hand off

Final reply per "Required final response" below: the mp4 path, duration
and shot count, what to tweak, and the assumptions you made.

## Non-negotiables

- The agent **never** edits generated artifacts — `.mp4`, `.srt`,
  `.episode.json`, `_shots/`, `_review/`. Edit the `.py`, re-generate.
- Every episode `.py` defines exactly one `gen_episode()` at module scope
  returning the envelope dict. Nothing else is accepted.
- **Never exceed 10s per shot** — generated video degrades past 10s even
  though the contract ceiling is 15. Split the shot instead
  (`clamp_duration` enforces this).
- **Dialogue shots always carry `cast` + `line`.** No orphan dialogue;
  every cast id must exist in `series.CAST`.
- **Subtitles are always burned** unless `series.py` explicitly says
  otherwise — platform UI eats un-burned captions.
- Run `scripts/drama` (or at minimum `scripts/check`) before declaring
  done. Never claim an episode renders from reading code alone.
- Never declare done with a severity-`error` warning, a `functional`
  beat-law warning, or a `failed` shot in the JSON — and never without
  having `Read` the `_board.png`.
- **Every episode plants a bond and aims at a gut-punch.** A hook with no
  heartbreak is a trick with no payoff — decide the bond/stakes/turn before
  the beat sheet (`references/emotional-core.md`).
- **Prompts are director's shot orders, not captions** — size + move +
  light + one action + atmosphere, and vary the size across the cut.
- **Peaks carry sound.** Cue per-shot `sfx` on every slap/reveal/loss beat
  via `sfx_for()`; drop the score for the gut-punch. A silent peak is the
  flatness you can't see on a still.
- Ask the user only about *preferences* (genre, tone, cast, market) —
  decide every *craft* choice silently from the tables and references.

## Reference examples

Working files to study (do NOT load eagerly — read on demand when you
need to mimic a pattern):

| File | Demonstrates |
|---|---|
| `templates/project_skeleton/` | The canonical project: spec.md, series.py with the cast-book markers, ep001.py with validate() + functional_warnings() folded in |
| `recipes/revenge_ep1.py` | Photoreal drama, ~50s, 14 shots — the 3s-hook slap-beat opening, dialogue-per-sentence durations, clean beat law end to end |
| `recipes/manju_ep1.py` | Anime 漫剧, ~61s, 26 short shots — fast cutting at 1.5-3s, V.O. exposition, memory-flash scene, format length check |
| `recipes/recognition_ep1.py` | Sci-fi love story, ~50s, 15 shots — the upgraded craft in one file: `emotional_arc` + a recognition gut-punch, `sfx_for` cues on every peak, varied shot sizes, a scored arc that cuts for the heartbreak |

These are the canonical patterns. Mimic the file shape: docstring with
the beat skeleton, table-derived durations, a single `gen_episode()`
returning the envelope.

## Progressive references

Load these only when their trigger applies (saves the host agent's
context):

- `references/binge-engine.md` — the secret sauce as ONE measurable
  system: the compulsion loop (hook → 爽点 cadence → 虐→爽 → cliffhanger →
  variable reward → parasocial), the seven forces, and the binge score.
  **Tier-0 — load FIRST for any new series.** Craft makes it good; this makes
  it *compulsive*. The platform's whole job is to supply this machine so a
  non-producer gets an bingeable series by chatting.
- `references/ideal-users.md` — who's creating (non-producer personas — the
  binge-watcher mom, the fanfic writer, the student in Brazil) and who they
  create *for* (viewer personas the tool designs to). How to talk to them (no
  jargon, ask 2-3 feeling questions, default aggressively, steer by feel).
  **Tier-0 — load when starting a project from a vague prompt.**
- `references/onboarding.md` — the chat-first flow: three feeling questions →
  propose a full series scaffold (`dramalib.onboarding.series_scaffold`) →
  first draft fast → steer by feel. Never a blank page, never an interrogation.
  **Tier-0 — load when onboarding a non-producer from a one-line pitch.**
- `../../docs/onboarding-ux.md` — the zero-friction cold-start design: three
  doors (remix a hit / surprise me / feeling-wizard), character-swap, the aha
  ladder. On-ramps: `dramalib.starters` (one-tap gallery + `surprise_me` +
  `starter_bible`) and `dramalib.remix` (recast a hit as yours, spine preserved).
  **Tier-0 — load when the user says "make it like X", "put me in it", "I don't
  know where to start", or taps a starter.**
- `references/taste-loop.md` — the tool learns a returning creator's eye:
  `dramalib.taste` folds their accept/reject/kill/note into a TasteProfile that
  biases defaults (preferred genre, avoid list, pace/tone). Bias, never override.
  **Load when a returning creator starts a project.**
- `references/emotional-core.md` — the bond, the stakes, and the five
  gut-punches; plant-early/pay-off-late; the feeling-shift cadence.
  **Tier-1 — load before the beat sheet for any new episode/series.** This
  is what makes it ache.
- `references/cinematic-shots.md` — director-grade shot prompts: size +
  move + light + action + atmosphere, the flat→cinematic rewrite ladder,
  and varying the size. **Load when writing/fixing any shot prompt or when
  the board looks flat.**
- `references/master-shot-craft.md` — great-film technique that survives 90s
  vertical, tagged [K]eyframe/[M]otion/[S]cene: the threat-in-the-keyframe,
  land the turn on one gesture, reveal-to-the-audience-first, Z-axis blocking,
  power via angle, one-motivated-light + one-grade-per-chapter, cut-on-the-wound,
  the anti-rules. **Load when a turn/reveal/gut-punch doesn't land or the board
  reads flat/"AI".**
- `references/sound-design.md` — per-shot `sfx` cues and the scored arc
  (build to the climax, cut for the gut-punch). **Load when cueing sound,
  choosing `bgm`, or a peak doesn't land.**
- `references/beat-structure.md` — the episode beat law in full: hook
  types, the 2-minute template, dialogue register, series-level pacing.
  **Load before writing any new episode's beat sheet.**
- `references/shot-grammar.md` — shot durations by type, the five
  scales, new-shot triggers vs do-not-split, the I2V prompt formula,
  candidates/reroll economics, the 14-field 分镜表. **Load before
  writing or fixing any shot list.**
- `references/assembly-conventions.md` — transitions (80% hard cuts),
  subtitles, the 4 audio layers and levels, lip-sync ordering
  (audio-first for realistic dialogue), head/tail conventions. **Load
  when a warning or the board points at captions, audio, pacing feel, or
  transitions.**
- `references/series-architecture.md` — episode counts per market,
  paywall-gate placement, reversal cadence across a season, the
  golden-window rule. **Load before planning any multi-episode series or
  choosing gate episodes.**
- `references/genre-playbook.md` — the nine genre engines (CEO, revenge,
  werewolf, mafia, rebirth, hidden-identity, rags-to-riches, contract,
  in-law), each with its core fantasy, archetypes, mandatory beats, tropes
  and title shape; the 男频/女频 track switch; localization-is-rewrite.
  **Load when the user names or implies a genre, when picking a trope
  skeleton, or when a draft is competent but generic.**
- `references/emotion-to-action.md` — convert a feeling into observable
  body tells (the model can't render an adjective); the "turn off the
  sound" mute test; the emotion→action lookup and relational tells. **Load
  when a shot prompt names a feeling or the board renders blank/generic
  faces.**
- `references/binge-eval.md` — the eval team: score the BINGE axis
  at the script stage (before render spend), distinct from the screening-room
  craft critic. Deterministic pre-check (`dramalib.evals.binge_scorecard`
  / `series_binge_flags`) + the judgment score + the gate + the auto-rework
  map. **Load before locking a series draft / advancing to render.**
- `references/retention-metrics.md` — the audience-behavior ground-truth that
  defines "bingeable" (hook-retention, next-episode-start, returns, paywall
  conversion), the target bands, and `dramalib.metrics.diagnose_metrics` (real
  signal → dimension → fix). **Load when reasoning about whether a series will
  actually hook an audience, or wiring analytics/the reward.**
- `references/safety-gate.md` — pre-publish safety at UGC scale: the likeness
  gate (screener-not-judge, `dramalib.safety.likeness_gate`, 3 checkpoints,
  never a false pass) + the compliance scan + provenance. **Load when locking a
  character sheet, before publish/export, or reasoning about legal risk.**

## Helper library (`dramalib`)

The skill ships a Python package at `~/.claude/skills/dramacode/dramalib/`
with composable, tested helpers. **Prefer importing these over retyping
numbers from the reference docs.** The runner adds the skill root to
`sys.path`, so `from dramalib.X import Y` resolves inside the sandbox.

```python
from dramalib.bible import Series, Character
from dramalib.spec import Episode, Scene, Shot
from dramalib.helpers import (
    beat_sheet,            # genre + length → timed beat list obeying the law
    shots_from_beats,      # beats + cast → Shot drafts with table durations
    validate_beat_law,     # episode → functional warning dicts
    recap_flashback,       # prev cliffhanger → the optional <=10s recap scene
    gate_plan,             # market + total episodes → paywall gates
    episode_length_for,    # format → (min_s, max_s)
    clamp_duration,        # kind → contract-legal duration from the table
    emotional_arc,         # length + gut_punch → the FEELING track (bond→turn)
    sfx_for,               # moment → the per-shot sfx cue list (Shot.sfx)
    shot_rhythm,           # shots → cut-rhythm report (flags same-size runs)
)
from dramalib.tropes import trope_for_genre
from dramalib.tables import (
    SHOT_DURATION_S, BEAT_LAW, EPISODE_LENGTH_S, PAYWALL_GATES,
    SUBTITLE, AUDIO, TROPE_TABLE,
    EMOTIONAL_TURNS, BOND_TYPES, FEELING_SHIFT_S,   # the emotional core
    SFX_CUES, SCORE_MOODS, SCORE_ARC,               # sound as storytelling
    SHOT_SCALES, CAMERA_MOVES, LIGHTING_KEYS,       # cinematic vocabulary
)
```

Every helper is **keyword-only**, returns plain dataclasses/dicts that
duck-type against the contract shapes, and **raises `ValueError`** on
impossible params (so failures point at the spec, not at ffmpeg five
frames deep). `shots_from_beats` emits `TODO:`-marked drafts on purpose —
`validate_beat_law` warns until you've replaced every placeholder with
real writing.

When no helper fits, write the structure inline in the episode `.py` —
that's a signal the library is missing a helper, worth promoting later.
**Do not copy numbers out of the reference docs** when a table owns them.

### Recipes — worked examples to mimic

Two complete episodes live at `~/.claude/skills/dramacode/recipes/`. Read
them when writing a similar episode:

| Recipe | Demonstrates |
|---|---|
| `revenge_ep1.py` | clamp_duration + trope_for_genre + validate_beat_law; photoreal pacing, dialogue-per-sentence math, reveal→question→freeze cliffhanger |
| `manju_ep1.py` | episode_length_for + the manju cut (26 shots at 1.5-3s); V.O. dialogue shots, palette-swap memory scene, format assert |
| `recognition_ep1.py` | emotional_arc + sfx_for + shot_rhythm; a planted bond spent on a recognition gut-punch, cued SFX on every peak, varied shot sizes, a scored arc that drops out for the heartbreak (genre-agnostic — no trope table) |

All three return the envelope from `gen_episode()` and render warning-free
— that's the bar.

## Pattern library

Atomic dramaturgy patterns, each in its own file under
`references/patterns/`. **Load only the patterns you actually need** —
they are small but adding 10 to every turn blows context. Match the
user's language to the trigger column and `Read` the corresponding file.

| Trigger phrases the user might say | Pattern file |
|---|---|
| "make me feel something", stakes, "I don't care about them", the bond, plant/payoff | `references/patterns/the-bond-and-the-loss.md` |
| "a twist that hurts", "the enemy was one of us", mask-off, she-was-ours, a reveal that re-prices everything | `references/patterns/recognition-beat.md` |
| epic, "bigger", wow, breathtaking, battle/chase/power-reveal, "the middle sags" | `references/patterns/escalating-spectacle.md` |
| "the ending fizzles", "it drags", cut it tighter, after the reveal, no cooldown | `references/patterns/emotional-hard-cut.md` |
| "add sound effects", which SFX for this beat, "the peak doesn't hit", the music | `references/patterns/sfx-cue-cheatsheet.md` |
| cold open, hook, "viewers drop off", "boring start", first 3 seconds | `references/patterns/cold-open-hook.md` |
| cliffhanger, ending, "make them click next", final shot, freeze frame | `references/patterns/cliffhanger-beat.md` |
| face-slap, 打脸, comeuppance, humiliation payback, "she shows them all" | `references/patterns/face-slap-cascade.md` |
| paywall, gate episode, 卡点, "episode 10", monetization, unlock | `references/patterns/paywall-gate-episode.md` |
| ad, trailer, promo, hook clips, 投流, marketing cuts, "clips for TikTok", packaging a series | `references/patterns/ad-cut-sheet.md` |
| recap, "previously on", flashback opening, episode 2+, catch-up | `references/patterns/recap-flashback.md` |
| dialogue, lines, "sounds stiff", monologue, subtitles overflow, TTS | `references/patterns/dialogue-shot-writing.md` |
| reroll, "shot looks wrong", regenerate a shot, "doesn't match the prompt", retry | `references/patterns/reroll-a-shot.md` |
| cast looks different, face drift, consistency, "who is that", same actor | `references/patterns/cast-consistency.md` |
| free platform, 红果, ad breaks, "no paywall", ad-supported | `references/patterns/ad-break-tolerant-episode.md` |
| teaser, next-episode preview, tail card, "coming up", end card | `references/patterns/next-episode-teaser.md` |

Each file has the same shape: **Trigger**, **Why this exists**,
**Use the helper** (`from dramalib …` — the package is the source of
truth), and **Pitfalls**. Adapt the helper call to the user's episode
rather than re-deriving the numbers.

When an episode needs **multiple patterns** (e.g. a gate episode with a
recap and a teaser), load all the relevant pattern files at the start of
the design phase, then weave them into one `.py`.

## Required final response

Your final reply to the user MUST contain, in order:

1. **One sentence** stating what you made — including the emotional core,
   not just the plot (e.g. "Made episode 1 of the revenge series — 46s, 12
   shots, slap-frozen cold open; the bond is the sister who raised her, the
   gut-punch is the twin-reveal that she's the 'dead' one, frozen on the
   cliffhanger.").
2. **Output path** — the episode `.mp4` (absolute), plus the `.srt` and
   the `_review/_board.png` contact sheet.
3. **Duration + shot count** against the format range, and the warning
   state (must be: none).
4. **Tweakables** — the 2-4 levers the user most likely wants: a line,
   a shot prompt, the cliffhanger, episode length.
5. **Assumptions** — one or two bullets for anything story-changing you
   defaulted (genre reading, market, cast look, tone).

Skip anything else. The user wants an episode, not a thesis.
