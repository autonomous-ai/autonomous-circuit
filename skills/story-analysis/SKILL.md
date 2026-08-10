---
name: story-analysis
description: Enrich a short or vague drama request into a production-ready series brief before any episode is written. Use when the user asks for a drama in a few words — "make me a revenge drama", "a werewolf romance", "CEO falls for the cleaner", "something like ReelShort", "60 episodes, make it bingeable" — and the request lacks genre mechanics, market, cast, season arc, or an episode-1 beat sheet. Returns one structured drama-brief with proven trope defaults so the series comes out bingeable rather than generic. Not for a full spec, an existing project, or an edit to a written episode.
---

# Story Analysis — prompt enrichment for short dramas

## Purpose

A short prompt like "make me a revenge drama" underspecifies the series:
it says nothing about market (which decides gates and episode length),
cast, the season's reversals, or what happens in the first 3 seconds of
episode 1. Building it literally yields a generic soap. This skill turns
a terse request into a **production-ready series brief** — the genre
mechanics, the market plan, the cast sketches, the arc, and an
episode-1 beat sheet that obeys the beat law.

This is a **read-only, no-artifact** skill. It writes no files and
renders no video. Its only output is one enriched drama brief, which the
user (or the `dramacode` skill) then turns into a project.

## When to use

- The user names a drama in a few words with no market, cast, arc, or
  beat detail.
- The prompt is ambiguous about audience ("revenge" — CN female-lead
  复仇 or overseas universal revenge?) or platform economics (paid
  gates vs free ad-supported).
- The user explicitly asks to "make it bingeable / bingeable / like the
  hits".

Do **not** use it when the user already gave a complete spec (premise +
market + cast + arc), is editing an existing episode or `series.py`, or
asked for a single episode inside an existing project — that's
`dramacode` directly.

## Workflow

1. **Classify the genre.** Map the prompt onto the trope table —
   `zhuixu` (赘婿), `zhanshen` (战神), `chongsheng` (重生), `fuchou`
   (复仇), `bazong` (霸总), `werewolf`, `billionaire`, `revenge` — the
   names in `dramalib.tables.TROPE_TABLE` (the dramacode skill).
   Compound genres resolve on their first component ("revenge-romance"
   → revenge). If the reading is unclear, pick the most common one and
   state the assumption.

2. **Pick the market.** CN paid mini-program / 红果-free /
   overseas app — this single choice changes the gate plan AND episode
   length: cn gates [10, 20, 30] at 45-90s eps; free has no gates but
   every episode must tolerate ~15s ad breaks; overseas gates first at
   ep 5-12 (default 8), major at 26-30, 1-3 min eps. Infer from
   language/genre (werewolf → overseas) and state the assumption.

3. **Cast 2-3 leads.** Each with an id, a name, a one-line **look**
   sketch (age, costume, one signature detail — this becomes the
   consistency anchor) and a **voice** key (e.g. `f_low_calm`,
   `m_deep_cold`). Two leads and one antagonist is the workhorse shape;
   never more than 3.

4. **Season arc.** Total episodes for the market; the golden window
   (eps 1-10: protagonist + core conflict + reason to watch); minor
   reversal every 5-10 eps, major every 20-30; **2-3 core reversals**
   placed at or just before gate episodes; gate plan per the market.

5. **Episode-1 beat sheet, obeying the beat law.** Hook ≤3s into
   conflict (name which hook type: direct confrontation / mystery /
   extreme contrast) → world by 10s → first reversal by 30s → a beat
   every 20-30s → cliffhanger in the final 5-10s. Timed rows, each with
   the beat and what happens.

6. **Style/format block.** Aspect 9:16, 1080×1920, 24fps, style preset
   (`photoreal-drama` / `manhwa` / `anime`), language, episode length
   in the format's range (ai-drama 45-90s; manju 60-180s).

7. **Emit one brief** in the format below and stop. Do not create a
   project, write any `.py`, or render anything.

## Output format

Return exactly one fenced ```drama-brief block containing the enriched
spec as JSON, followed by a 2-3 sentence plain-language summary. Times
in seconds.

```drama-brief
{
  "premise": "A dying heiress marries her family's enemy to buy her revenge — then discovers he signed for a reason worse than love.",
  "genre": "revenge-romance",
  "market": "overseas",
  "format": { "episodes": 60, "minutes_per_ep": 1.0, "aspect": "9:16",
              "fps": 24, "style": "photoreal-drama" },
  "cast": [
    { "id": "li_wei", "name": "Li Wei",
      "look": "woman, 28, sharp black bob, gray tailored suit, cold poise hiding grief",
      "voice": "f_low_calm" },
    { "id": "dorian", "name": "Dorian Cross",
      "look": "man, 34, black wool coat, silver watch, unreadable half-smile",
      "voice": "m_deep_cold" }
  ],
  "arc": {
    "gates": [8, 28],
    "reversals": [
      { "ep": 8,  "turn": "the contract's hidden clause surfaces — he needs her alive" },
      { "ep": 28, "turn": "the 'dead' first wife is running the family that ruined hers" },
      { "ep": 52, "turn": "her revenge target was her protector all along" }
    ]
  },
  "episode_1_beats": [
    { "t": "0-3",   "beat": "hook",           "what": "direct confrontation: the slap frozen mid-swing at the funeral" },
    { "t": "3-10",  "beat": "world",          "what": "the debt, the contract, who owns whom" },
    { "t": "10-28", "beat": "first_reversal", "what": "she signs — then turns clause nine on him" },
    { "t": "28-52", "beat": "escalation",     "what": "the ring she puts on herself; the buzzing phone" },
    { "t": "52-60", "beat": "cliffhanger",    "what": "freeze: the dead first wife's name on his screen" }
  ]
}
```

Then: a short summary the user can approve or redirect, e.g. *"A 60-ep
overseas revenge-romance: contract marriage as a revenge vehicle, gates
at 8 and 28 sitting on the two biggest reversals, episode 1 opening on a
funeral slap and freezing on a call from a dead woman. Say the word and
I'll build the project and write episode 1."*

## Handoff

This skill is standalone: it produces a brief, not a project. When the
user approves, hand the brief to the **`dramacode`** skill as the design
input — its `premise`/`cast`/`format` map onto `series.py`, `arc` onto
`spec.md`'s gate plan, and `episode_1_beats` is exactly the beat sheet
dramacode's episode 1 is written against. The `cast` entries later feed
the `cast-book` skill for reference images. If the user only asked for
the analysis, stop after the brief.

## Guidance

- Default to **proven, market-tested** choices — the trope table exists
  because originals underperform adapted formulas; call out every
  assumption so the user can correct it in one message.
- Never invent a market rule (gate episode, length cap) from memory —
  they are dramalib table values; if a number isn't in the tables, mark
  it as an estimate.
- Keep briefs tight. One brief, 2-3 leads, 3 reversals, 5 beat rows —
  the handful of decisions that most change whether episode 1 hooks.
