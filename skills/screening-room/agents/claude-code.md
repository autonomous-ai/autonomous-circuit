# Claude Code overrides

These instructions apply when the screening-room skill runs inside Claude Code
(the CLI or the Video app's driver subprocess, in the SCREENING review phase).
Other hosts follow the main `SKILL.md` only.

## You are the eyes of the loop

`SKILL.md`'s loop —

```
run bundle → Read every frame + board + poster + source → score → emit ```screening-report
```

— is the whole job. Close it with the tools Claude Code already gives you:

| Step | Claude Code tool |
|---|---|
| **build bundle** | `Bash` → `python ~/.claude/skills/screening-room/scripts/bundle <abs epNNN.mp4>` |
| **read manifest** | parse the single JSON line from stdout |
| **WATCH** | `Read` every `frames[].path`, then the `board` and `poster` — Read returns images multimodally, so you actually see them |
| **read intent** | `Read` `source.episode_source` + `source.series_py` — the script says what each shot was meant to be |
| **judge** | reason: score the 8 rubric dimensions 1-10, honestly and stingily |
| **verdict** | emit exactly one ` ```screening-report ` fenced JSON block |

If you scored without `Read`ing the frames, you did not screen the episode —
you read a spreadsheet. Don't.

## Read the frames — you actually see them

The bundle samples ~3 frames per shot (early/mid/late) into
`<stem>_review/frames/`. `Read` all of them. The board shows only first-frames;
the sampled frames are where mid-shot face drift, motion artifacts, and the
rotation bug become visible. This is mandatory before emitting a verdict.

## Silent phase

In the Video app you run inside the automatic post-build SCREENING phase. Work
SILENTLY: no greeting, no summary, no questions. Your entire output is the one
` ```screening-report ` block. The driver reads it, and if you failed the cut it
resumes the session with your blocker/major notes as fix instructions.

## Thinking budget

Screening a cut is judgment-heavy — extended thinking is worth it. Weigh the
frames against the script's intent (did the gut-punch land? is the face the same
person?) before scoring. A snap 7 across the board is the failure mode; think,
then commit to specific numbers and specific shot ids.

## Tool budget

Hard limit: 6 model turns. One clean pass is: bundle (1 Bash) → Read frames
(batch the `Read`s) → Read source → emit report. Batch the frame reads; don't
spend a turn per image.
