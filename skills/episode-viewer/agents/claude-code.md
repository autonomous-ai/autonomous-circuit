# Claude Code overrides

## Prefer the board for QC — the URL is for the user

When YOU need to judge an episode (composition, cast consistency,
caption placement), do not open the app URL — you can't watch video.
`Read` the `_review/_board.png` contact sheet instead (the dramacode
skill's review loop): Claude Code returns images as multimodal content,
so you actually see every shot's first frame. The episode-viewer URL is
a deliverable **for the user**, not an inspection tool for you.

## Registry read

`~/.autonomous-video/server.json` may not exist (app not running) — use a single
`Read` or `Bash cat` and treat a miss as the documented fallback (the
app shows artifacts live via `artifact_changed`; say so, don't start
servers). Never scan ports and never launch processes from this skill.
