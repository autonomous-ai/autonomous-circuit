---
name: board-viewer
description: Open a generated board in the Autonomous Circuit app. Use when the user asks to see, preview, or open the board — "show me the board", "open the schematic", "let me see the PCB", "show the gerbers", "open it in the app" — after circuitcode has generated it. Resolves the app URL for the board from the running server's registry; when the app is already showing the project, no action is needed at all.
---

# Board Viewer — hand the board to the Circuit app

## Purpose

The Circuit app IS the viewer — the board rail with status dots and the
Schematic / PCB / BOM / Fab tabs, plus the parts panel and the warnings
strip. **This skill bundles no server**: the web app is already running
and already watches the project via `artifact_changed` events, so
"viewing" is usually a no-op and at most a URL.

## Workflow

Given a board (e.g. `<project>/boards/main.tsx` and its generated
artifacts):

1. **Check the server registry**: `Read` (or `cat`)
   `~/.autonomous-circuit/server.json`. If present, it names the running
   app server, e.g.
   `{"port": 4179, "projects": {"<abs project root>": "<project id>"}}`.
2. **If the registry exists and lists the project**, print the URL:

   ```
   http://127.0.0.1:<port>/?project=<id>&file=boards/main.tsx
   ```

   The `file` query value is workspace-relative (`boards/<stem>.tsx`),
   never absolute. Point it at the board **source**, not at a generated
   artifact — the app groups the schematic, PCB, BOM, and fab packet
   under that board entry and opens on the tab the user last used.
3. **If the registry is missing or the project isn't listed**, do not
   start anything and do not invent a port. Tell the user: the Circuit
   app displays artifacts live as they land (`artifact_changed` fires
   when the review PNGs, the sidecar, and the fab packet are written) —
   open the project in the app and the board is already there.

That's the whole skill. No build, no file writes, no server lifecycle.

## Use this skill when

- The user asks to see/open/preview a board, schematic, PCB, BOM, or fab
  packet.
- circuitcode has just finished and the user wants the result on screen.

Do **not** use it to judge a board's quality — that is circuitcode's
review loop (`scripts/review` plus `Read`ing `_review/_schematic.png`
and `_review/_pcb.png`), which surfaces the warnings and the images the
app's tabs summarize. And never point the app at a board that hasn't
been generated yet; build first.

## Required final response

1. The URL (or the one-line "already live in the app" explanation).
2. The board's absolute path, so the user can also open the files
   directly.
