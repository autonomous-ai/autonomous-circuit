# The taste loop — the tool learns your eye

**Load when:** a returning creator starts a project, or when deciding defaults for
someone the platform has seen before. Flywheel loop #3, and the #1 adoption driver:
the tool that adapts to *you* is the one you keep using. Backbone:
`dramalib.taste`.

## The claim: every human choice is signal

A non-producer can't specify their taste — but they *reveal* it, in every accept,
reject, kill, reroll, and note. Capture it into a persistent `TasteProfile` and
feed it back as biased defaults, and the platform stops being generic: it proposes
the genres you love, avoids the tropes you always kill, and matches your pace and
tone before you ask. This is why Claude Code feels personal (it adapts to your
codebase); ours adapts to your eye.

## What we capture (and how)

`observe(profile, event=…)` folds one signal in:
- **genre / trope affinity** — `{"kind": "genre", "key": "revenge", "signal": "love"}`;
  accept/keep/love raise it, reroll/reject/kill lower it (`SIGNAL_WEIGHTS`).
- **kills** — `{"kind": "kill", "key": "love triangle"}`; a thing they keep rejecting,
  remembered so we stop proposing it (`avoided()` surfaces it past a threshold).
- **pace / tone** — `{"kind": "pace", "delta": +1}` (tighter) / `{"kind": "tone", "delta": -1}`
  (darker); directional nudges from notes like "too slow" / "make it darker".
- **notes** — their own words, recent tail kept, echoed back so the story stays theirs.

## How it feeds back

`bias_scaffold(profile, scaffold=…)` attaches a `taste` block to the onboarding
scaffold (`dramalib.onboarding.series_scaffold`): the preferred genre, the avoid
list, pace/tone, recent notes. Authoring reads it and leans that way in its
defaults — **taste biases, it never overrides an explicit ask.** A returning
creator gets a first proposal already shaped like their last hit; a new creator
gets the platform defaults until signal accumulates.

## Why per-creator, not global

Global taste would regress everyone to the mean — the opposite of what makes each
creator's series feel like *theirs* (and what lets the long tail of niche/community
tastes exist, `docs/short-drama-playbook.md` Part 4). Keep profiles per creator
(persist `to_dict()` as one JSON file per `creator_id`); aggregate only into the
craft-learning loop (flywheel #7), never back into an individual's defaults.

## Pitfalls

- **Overriding an explicit choice.** If they *ask* for a werewolf drama, give them
  one even if their history skews revenge. Bias defaults, honor asks.
- **Guessing on thin signal.** `preferred_genre` returns `None` on a tie or no data
  — don't force a lean the evidence doesn't support (the anti-goal-seek discipline).
- **Learning noise as taste.** One reject isn't a kill; require a threshold
  (`avoided(threshold=2)`) before you stop proposing something.
- **A stale profile.** Recent notes are a tail; weight recent behavior over ancient
  choices so the tool tracks a creator whose taste evolves.
