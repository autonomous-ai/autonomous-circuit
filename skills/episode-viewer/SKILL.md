---
name: episode-viewer
description: Open a rendered drama episode in the Autonomous TV app. Use when the user asks to watch, preview, or open an episode mp4 — "open episode 3", "let me watch it", "show me the episode", "play it" — after dramacode has generated it. Resolves the app URL for the episode file from the running server's registry; when the app is already showing the project, no action is needed at all.
---

# Episode Viewer — hand the episode to the Video app

## Purpose

The Video app IS the viewer — a vertical `<video>` player with the
episode rail, storyboard strip, and cast panel. **This skill bundles no
server** (a deliberate divergence from its donor, whose viewer skill
shipped its own `serve:ensure` process): the web app is already running
and already watches the project via `artifact_changed` events, so
"viewing" is usually a no-op and at most a URL.

## Workflow

Given an episode file (e.g. `<project>/episodes/ep003.mp4`):

1. **Check the server registry**: `Read` (or `cat`) `~/.autonomous-video/server.json`.
   If present, it names the running app server, e.g.
   `{"port": 4180, "projects": {"<abs project root>": "<project id>"}}`.
2. **If the registry exists and lists the project**, print the URL:

   ```
   http://127.0.0.1:<port>/?project=<id>&file=episodes/ep003.mp4
   ```

   The `file` query value is workspace-relative (`episodes/epNNN.mp4`),
   never absolute.
3. **If the registry is missing or the project isn't listed**, do not
   start anything and do not invent a port. Tell the user: the Video
   app displays artifacts live as they render (`artifact_changed`
   fires when the mp4 lands) — open the project in the app and the
   episode is already there.

That's the whole skill. No render, no file writes, no server lifecycle.

## Use this skill when

- The user asks to watch/open/play a rendered episode.
- dramacode has just finished and the user wants the result on screen.

Do **not** use it to verify an episode's quality — that is dramacode's
review loop (`scripts/review` + the `_board.png`), which sees per-shot
frames the player doesn't surface. And never point the app at files
that don't exist yet; render first.

## Required final response

1. The URL (or the one-line "already live in the app" explanation).
2. The episode's absolute path, so the user can also open it directly.
