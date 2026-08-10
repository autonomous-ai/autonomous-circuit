# Claude Code overrides

These apply when circuitcode runs inside Claude Code — the CLI, or the
Autonomous Circuit app's driver subprocess. Other hosts follow `SKILL.md` alone.

## You are running the loop

```
understand → inspect → block plan → write → build → LOOK → fix → repeat
```

Close it yourself with the tools you already have:

| Step | Tool |
|---|---|
| **understand** | the user's prompt, any attached image (`Read`), any ```circuit-brief block from circuit-analysis |
| **inspect** | `Glob` / `Bash ls`; `Read` product.json, parts.json, boards/main.tsx, and each `BLOCK.md` you plan to use |
| **plan** | reasoning + `circuitlib.helpers.board_plan()` — block table, power budget, pin allocation |
| **write** | `Write` / `Edit` on `boards/main.tsx` — always an absolute path |
| **build** | `Bash` → `python ~/.claude/skills/circuitcode/scripts/circuit <abs boards/main.tsx>` |
| **read verdict** | parse the single JSON line — `ok`, `error.code`, `warnings[].severity`, `fab.ready` |
| **LOOK** | `Bash` → `python ~/.claude/skills/circuitcode/scripts/review <abs project>`; then `Read` **both** PNGs |
| **fix** | `Edit` — same file, smallest change |
| **repeat** | back to *build* |

Stopping while a blocking warning stands, or before you have looked at the
images, is leaving the loop half-run.

## Read both images — you actually see them

`scripts/circuit` returns facts. `scripts/review` + `Read` on
`_review/_schematic.png` and `_review/_pcb.png` shows you the board. Read's
images come back as multimodal content, so **you genuinely see them**. This is
mandatory before declaring done:

- **Schematic:** every net labelled, blocks readable as blocks, nothing floating.
- **PCB:** decoupling beside its IC, connectors on the promised edge, silkscreen
  legible, mounting holes clear, traces deliberate.

`ok: true` says the pipeline ran. Only the pictures say the board is right.
Checklists: `references/schematic-readability.md`, `references/pcb-layout-craft.md`.

## Use the cheap command first

`scripts/check` runs compile + the circuit-json scan + the checks library into a
tempdir — no kicad, no fab export, artifacts discarded. Run it after a
structural edit; run the full `scripts/circuit` when you are ready for a packet
and the images. One full cycle is 4 tool calls (edit → build → review → Read),
so batch your edits rather than making one change per round trip.

## Thinking budget

A trivial edit — one coordinate, one value, one added button — needs no extended
thinking; apply it and re-run. A new board or an MCU swap is worth it: the block
plan, power budget, and pin allocation are exactly the structured reasoning that
prevents build-fix cycles later.

## Absolute paths, always

Your working directory is not the workspace. Every path you pass to a script,
`Read`, or `Edit` is absolute. Paths coming *back* in the JSON are
workspace-relative — resolve them against the project root before reading.

## What you never do here

Never edit generated artifacts (`.circuit.json`, `.board.json`, SVGs, PNGs, the
fab packet) — they are overwritten on the next run and desynchronise the
sidecar. Never edit `parts.json` — hand off to parts-book. Never present a
packet as orderable when `fab.ready` is false.
