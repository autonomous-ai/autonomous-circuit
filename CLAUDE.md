# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Architecture at a glance

Autonomous TV ("Video") is a web app: chat → screenplay → shots → stitched vertical episode. Three layers,
wired through one frozen interface document.

- **`viewer/`** — Vite + React frontend (chat surface + vertical episode player +
  storyboard strip) **and** the Node server (`viewer/src/server/video/`): HTTP commands
  (`POST /api/<command>`), SSE events (`GET /api/events`), the `claude` CLI subprocess
  driver, the artifact mtime snapshotter, the project/catalog/settings stores, and the
  silent post-build review loop. In dev everything mounts as Vite middleware; in prod
  `viewer/src/server/server.mjs` serves the built app and the same API.
- **`packages/dramapy/`** — Python pipeline that turns an episode project
  (`series.py` + `episodes/epNNN.py` defining `gen_episode()`) into artifacts:
  stitched `.mp4`, `.srt`, `.episode.json` sidecar, per-shot clips in `<stem>_shots/`,
  poster + contact-sheet board in `<stem>_review/`. Providers: `mock` (ffmpeg-synthesized,
  no network) and `fal` (Wan 2.2 hosted); shots are content-addressed in
  `.video/render-cache/` so edits re-render only changed shots. Vendored into the skill
  runtime by `scripts/build/build-skill-runtimes.sh` — the copy under
  `skills/dramacode/scripts/packages/dramapy/` is **generated; never hand-edit**.
- **`skills/`** — Claude Code skills the app installs to `~/.claude/skills/`:
  `dramacode` (the loop: write episode source → run generator → read verdict → Read the
  `_board.png` → fix), `story-analysis` (vague ask → `drama-brief`), `cast-book`,
  `episode-viewer`. Each is self-contained at runtime; `dramalib/` inside dramacode is
  the single owner of the domain numbers (beat law, shot-duration tables, trope tables).

### The contract document

**`docs/video-interfaces.md` is frozen and is the contract every track codes against.**
Changes go through `docs/video-interfaces-CHANGES.md` (append-only). The critical split:
the skill's **stdout JSON line** (snake_case) is what the model sees; the
**`.episode.json` sidecar** (camelCase) is what the server's review loop reads —
it walks `*.episode.json` and gates on `validation.warnings[].severity`
(`error` blocks / `warning` reviews / `info` advises; the driver never switches on `kind`).

### Data flow per chat turn

User message → `POST /api/chat_start_turn` → Node driver spawns `claude` with
stream-json → CLI invokes `dramacode` as a tool call → skill runs sandboxed, calls
`dramapy.generation.generate_episode()`, writes artifacts, prints one JSON line →
driver's mtime snapshotter diffs the workspace → `artifact_changed` over SSE →
viewer reloads catalog, player/storyboard update.

### Two-phase chat: plan → approve → build (+ silent review)

- **Plan** (`--permission-mode plan`): Video proposes the beat sheet + shot list as a
  plan block; preference questions arrive as a fenced ` ```video-questions ` JSON block.
  Driver intercepts `ExitPlanMode` → emits `plan_proposed`, ends the turn.
- **Build** (`chat_approve_plan` → `--permission-mode bypassPermissions`): resumes the
  same session (deterministic uuidv5 per project). Autopilot (`autoBuild !== false`)
  chains the build server-side.
- **Review** (automatic, silent, best-effort, inside the build turn): phase 1 *structure*
  (severity `error`, ≤2 rounds) → phase 2 *dramatic function* (`kind == "functional"`,
  ≤3 rounds) → phase 3 *craft* (always one round: re-render + Read `_board.png` /
  `_poster.png`, break when nothing changes). A review failure never fails the build.

## Common commands

Three independent gates — run only what your change touches:

```bash
# dramapy (Python pipeline)
cd packages/dramapy && /Users/d/miniconda/bin/python3.12 -m pytest -q

# viewer (client + Node server)
npm --prefix viewer test
npm --prefix viewer run build

# skills
cd skills/dramacode && /Users/d/miniconda/bin/python3.12 -m pytest tests/ -q
```

Dev / build:

```bash
npm --prefix viewer run dev        # the whole app (Vite + API middleware) on :4178
scripts/build/build-skill-runtimes.sh   # re-vendor dramapy into the skill runtime
```

## Conventions worth knowing

- **Session-dir encoding footgun (inherited, real):** the encoded Claude session dir
  replaces **every** non-alphanumeric char with `-` (matching
  `cwd.replace(/[^a-zA-Z0-9]/g, '-')`). Project workspaces live under
  `~/.autonomous-video/projects/<uuid>` — a `/`-only encoding mismatches and the driver dies with
  "Session ID already in use".
- **Cache-bust or suffer:** every media URL carries `?v=<mtime_nanos>-<size>`.
  `<video>` caches harder than the donor's STL viewer ever did; a re-rendered episode
  at the same path WILL replay stale frames without it.
- **One JSON line, last line wins:** skill runners print exactly one JSON line;
  parents parse `stdout.splitlines()[-1]` so stray prints don't kill a turn.
- **Errors** from `generate_episode()` subclass `dramapy.generation.GenerationError`
  (`ProjectShapeError` / `GeneratorRuntimeError` / `SpecValidationError` /
  `ProviderError` / `ExportError`); `SyntaxError`/`ImportError` propagate untouched.
- **Artifact order:** sidecar before final `.mp4` (the snapshotter has 1s granularity;
  metadata must be readable when the episode event fires).
- **Mock provider is the default** (`VIDEO_PROVIDER=mock`): the entire loop — including
  CI and the review phases — must work with zero network and zero GPUs.
- **Domain numbers live in `dramalib/tables.py`** (beat law, shot durations, trope
  tables, subtitle/audio constants). Docs point at helpers; never transcribe numbers
  into prompts or references.
- **Out of scope for v1:** the Tauri desktop shell (`desktop/` is donor residue until
  deleted), slicing/printing/social donor code paths, real TTS voices (interface exists;
  mock uses a synthesized bed), LoRA training, the `comfyui` provider.
