# Claude Code overrides

These instructions apply when this skill runs inside Claude Code (the
CLI or the Video app's driver subprocess). Other hosts should follow the
main `SKILL.md` only.

## You are running the loop

The loop in `SKILL.md` —

```
understand → inspect → beat sheet + shot list → write → render → look → fix → repeat
```

— is the entire point of this skill. Close the loop yourself with the
tools Claude Code already gives you:

| Step in the loop | Claude Code tool |
|---|---|
| **understand** | the user's prompt + any attached reference image (`Read`), any ```drama-brief block from story-analysis |
| **inspect** | `Glob` / `Bash ls` on the project; `Read` spec.md, series.py, prior episode `.py` |
| **plan** | reasoning — the beat sheet + 分镜表 shot list |
| **write** | `Write` / `Edit` — always an absolute path |
| **render** | `Bash` → `python ~/.claude/skills/dramacode/scripts/drama <abs episodes/epNNN.py>` |
| **read verdict** | parse the JSON line from stdout — check `ok`, `warnings`, `shots[].status` |
| **look** | `Bash` → `python ~/.claude/skills/dramacode/scripts/review <abs episodes dir>`; then `Read` the `_board.png` |
| **fix** | `Edit` — same `.py`, smallest change (one shot's prompt for a reroll) |
| **repeat** | back to *render* — the cache re-renders only changed shots |

If you stop before `warnings` is empty and the board tiles look right,
you are leaving the loop half-run. Don't.

## Read the `_board.png` — you actually see it

`scripts/drama` returns JSON facts; only `scripts/review` +
`Read` on `_review/_board.png` shows you the episode. Claude Code's Read
tool returns images as multimodal content, so **you actually see the
contact sheet** — first frame of every shot. This is mandatory before
declaring done: check cast consistency tile-to-tile, prompt/frame match,
caption clearance in the lower fifth, and that the cliffhanger tile
reads as a peak. Render-success says the pipeline ran; only the board
says the episode is right.

## Thinking budget

For a trivial edit (one line, one prompt, one duration), no extended
thinking — apply the edit and re-run; latency drops ~5x. For a new
episode or series plan, extended thinking is worth its cost: the beat
sheet + shot list is exactly the kind of structured planning it
improves, and it reduces render-fix iterations that cost real provider
money.

## File writes

Always pass an **absolute path** to `Write`/`Edit`. Claude Code's cwd is
the session workspace — correct for project files — but `Bash` calls
into the skill scripts shell out and may have different cwds; absolute
paths on both sides.

## Tool budget

Hard limit: 6 model turns per user message. One full cycle (write →
drama → review → Read board) is 4 tool calls, so the SKILL.md soft cap
of 4 fix iterations keeps you under it only if fixes stay small — batch
edits (all line rewrites at once) instead of one render per tweak. On
real providers this is also the money discipline: every un-batched edit
pass re-renders shots.
