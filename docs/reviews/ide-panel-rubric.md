# The IDE panel — a standing board of judges, and the number they produce

Autonomous Circuit is Cursor for electronics: agents do ~99% of a board, and a
human EE finishes the 1% that is NP-hard or is taste. That only pays off if the
editor feels like the tool they already own. This file is how we find out, on a
schedule, with a number that moves.

**The metric.** Seven lenses, 1–10 each, 70 points. A round is one panel run
against one commit. The score is only comparable across rounds if the rubric
does not move, so **this file changes by appending a dated note, never by
rewriting a lens**.

## The rule that makes a score worth having

A judge may not score a feature they did not use. Every number carries evidence
of one of three kinds and says which:

- **drove it** — a command run against the live app, or a headless render that
  mounts the real component and dispatches real events. Quote the output.
- **read it** — `file:line`, for a claim about how something is built.
- **could not** — the feature could not be reached at all. That is a finding,
  not a missing score: write what you tried.

"Looks fine" is not evidence. A lens scored without evidence is recorded as
**unscored**, which is worse for the total than a low score, because it is.

## The seven lenses

| # | Lens | The question it answers |
|---|---|---|
| 1 | **Familiarity** | Does an Altium/KiCad hand find the keys, buttons and gestures where they expect them, and do they mean the same thing? |
| 2 | **Navigation & sight** | Can they see the board — pan, zoom, fit, layers, dimming, cross-probe schematic ↔ PCB, measure, coordinates? |
| 3 | **Editing** | Can they actually change the board — select, move, rotate, nudge, type an exact coordinate, undo — and does the change land in the source? |
| 4 | **Verdict** | After an edit, do they know whether the board is still legal, how fast, and with what honesty about the checks that did not run? |
| 5 | **Integrity** | Can the tool corrupt or lose work — a bad splice, a lost undo, an agent and a human writing at once, a refusal with no reason? |
| 6 | **Discoverability** | Can they find a feature without being told: menus, right-click, the shortcut sheet, empty states, and the words used in errors? |
| 7 | **Board readiness** | Open the three shipped example boards. Is each one "a couple of small human edits from done", and what exactly are those edits? |

## The ship bar

The board is *ready to put in front of an EE* when **all** hold:

- every lens ≥ 7
- Familiarity ≥ 8 and Editing ≥ 8 (the two the premise rests on)
- 0 must-fix findings open
- the three example boards each need ≤ 3 named human edits, each one a
  judgement call rather than a defect

Below that bar the panel's job is a **ranked list of what raises the score
most per unit of work**, which is the input to the next build round.

## How a round runs

1. The app is up on `:4179` (`scripts/dev.sh`), with the three example boards
   installed as projects.
2. One judge per lens, in parallel. Each drives the app, scores their lens,
   and returns: score, evidence per claim, ≤5 must-fix, ≤5 should-fix, and one
   sentence answering *would an EE use this instead of exporting to KiCad?*
3. The findings are merged into `docs/reviews/ide-panel-<date>.md`, must-fix
   items go into the build queue, and the round's total lands in the table
   below.
4. Fix, then re-run. A lens may not be re-scored without a re-run.

### What a judge may touch

- **The live app**: `POST http://127.0.0.1:4179/api/<command>` with a JSON
  body. Commands are listed in `viewer/src/server/circuit/http.mjs`.
- **The real components, headless**: mount them and dispatch real pointer and
  key events through `viewer/src/client/test/render.js`. From `viewer/`:

  ```bash
  node --test --experimental-strip-types --no-warnings=ExperimentalWarning \
       --import ./scripts/testHooks.mjs <file>
  ```

- **The source**, for `file:line` claims.

Two standing cautions, both learned the expensive way. **Never read a whole
`circuit.json` or `.board.json`** — they run to megabytes and have killed five
review agents on context; count them with a one-line Python script. And **a
finding is a claim**: measure the two numbers against each other before
reporting a defect. Most of a day went into a via-in-pad that was never real.

## Scoreboard

| Round | Date | Commit | 1 Fam | 2 Nav | 3 Edit | 4 Verdict | 5 Integrity | 6 Disc | 7 Boards | Total |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-08-16 | `80ad1c0` | 6 | 8 | 6 | 2 | 6 | 6 | 6 | **40** |
| 2 | 2026-08-16 | `dc9b3ea` | — | — | — | — | — | — | — | running |

Round 1 is written up in `docs/reviews/ide-panel-2026-08-16.md`; the judges'
own reports are under `work/ide-panel/round<N>/`.
