# Claude Code overrides

## Prefer the review images for QC — the URL is for the user

When YOU need to judge a board (net labels present? decoupling next to
its IC? connector on the right edge? silkscreen legible? mounting
holes?), do not open the app URL — you cannot interact with the app's
pan-zoom tabs. `Read` the generated review images instead:
`<stem>_review/_schematic.png` and `<stem>_review/_pcb.png` (plus
`_pcb_bottom.png` when parts sit on both sides), regenerated on demand
by circuitcode's `scripts/review`. Claude Code returns images as
multimodal content, so you actually see the board. The board-viewer URL
is a deliverable **for the user**, not an inspection tool for you.

## Registry read

`~/.autonomous-circuit/server.json` may not exist (app not running) —
use a single `Read` or `Bash cat` and treat a miss as the documented
fallback (the app shows artifacts live via `artifact_changed`; say so,
don't start servers). Never scan ports and never launch processes from
this skill.
