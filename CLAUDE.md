# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Architecture at a glance

Autonomous Circuit is a web app: chat → engineering spec → board source → verified,
fab-ready PCB/PCBA packet. Three layers, wired through one frozen interface document.

- **`viewer/`** — Vite + React frontend (chat surface + board workspace) **and**
  the Node server (`viewer/src/server/circuit/`): HTTP commands
  (`POST /api/<command>`), SSE events (`GET /api/events`), the `claude` CLI subprocess
  driver, the artifact mtime snapshotter, the project/catalog/settings stores, and the
  silent post-build review loop. In dev everything mounts as Vite middleware; in prod
  `viewer/src/server/server.mjs` serves the built app and the same API. Port **4179**
  (`VIEWER_PORT` overrides); projects live under `~/.autonomous-circuit/projects/<uuid>`.
- **`packages/circuitpy/`** — the Python pipeline. `build_board()` turns a board project
  (`product.json` + `parts.json` + `boards/<stem>.tsx` composed from `blocks/`) into
  artifacts via the staged gauntlet: compile → circuit.json error scan → `@tscircuit/checks`
  re-check → kicad-cli ERC/DRC on the converted board → DFM + BOM gate → fab export →
  render + sidecar. Artifacts: `<stem>.circuit.json` (IR of record, lands LAST),
  `<stem>.board.json` sidecar (lands first), `<stem>_review/` schematic+PCB PNGs,
  `<stem>_fab/` packet (gerbers.zip, bom.csv, cpl.csv, ORDER.md, board.glb). All Node
  tooling is invoked out-of-process from the exact-pinned `toolchain/` (the ffmpeg
  posture); `toolchain.py` is the only module that names binaries. Vendored into the
  skill runtime by `scripts/build/build-skill-runtimes.sh` (run by dev.sh) — the copy
  under `skills/circuitcode/scripts/packages/circuitpy/` is **generated; never hand-edit**.
- **`skills/`** — Claude Code skills the app installs to `~/.claude/skills/`:
  `circuitcode` (the loop: edit TSX → run generator → read verdict → `Read` BOTH review
  PNGs → smallest responsible fix), `circuit-analysis` (vague ask → one fenced
  ` ```circuit-brief ` block, read-only), `parts-book` (owns `parts.json` wholly; locks
  LCSC numbers), `board-viewer` (prints the app URL from `~/.autonomous-circuit/server.json`,
  never starts anything). `circuitlib/` inside circuitcode is the single owner of the
  domain numbers (rails, trace-width-per-amp, JLC DFM/cost tables) and the golden-block
  registry; blocks ship BLOCK.md + a graded testbench.

### The contract document

**`docs/circuit-interfaces.md` is frozen and is the contract every track codes against.**
Changes go through `docs/circuit-interfaces-CHANGES.md` (append-only). The critical
split: the skill's **stdout JSON line** (snake_case, exactly one line; parent parses
`stdout.splitlines()[-1]`) is what the model sees; the **`.board.json` sidecar**
(camelCase, canonical JSON) is what the server's review loop reads — it walks
`*.board.json` and gates on `validation.warnings[].severity` (`error` blocks /
`warning` reviews / `info` advises; the driver never switches on `kind`).

### Data flow per chat turn

User message → `POST /api/chat_start_turn` → Node driver spawns `claude` with
stream-json → CLI invokes `circuitcode` as a tool call → skill calls
`circuitpy.build_board()`, runs the gauntlet, writes artifacts, prints one JSON line →
driver's mtime snapshotter diffs the workspace → `artifact_changed` over SSE →
viewer reloads catalog, the board workspace updates.

### Two-phase chat: plan → approve → build (+ silent review)

- **Plan** (`--permission-mode plan`): Circuit proposes the engineering spec — block
  choice, power budget math, pin allocation, size, cost band; read-only. Preference
  questions arrive as a fenced ` ```circuit-questions ` JSON block. Driver intercepts
  `ExitPlanMode` → emits `plan_proposed`, ends the turn.
- **Build** (`chat_approve_plan` → `--permission-mode bypassPermissions`): resumes the
  same session (deterministic uuidv5 per project). Autopilot (`autoBuild !== false`)
  chains the build server-side.
- **Review** (automatic, silent, best-effort, inside the build turn): phase 1 *structure*
  (severity `error`, ≤2 rounds) → phase 2 *electrical function* (`kind ∈ {functional,
  power_budget, part_not_orderable, part_drift, netlist_mismatch}`, ≤3 rounds) →
  phase 3 *craft* (always once: rebuild + `Read` `_schematic.png` and `_pcb.png`, break
  when no files change, ≤2 rounds). A review failure never fails the build.

## Common commands

Three independent gates — run only what your change touches. `python3` on this
machine's PATH is 3.9; **always use `/Users/d/miniconda/bin/python3.12`**.

```bash
# circuitpy (Python pipeline)
cd packages/circuitpy && /Users/d/miniconda/bin/python3.12 -m pytest -q

# viewer (client + Node server)
npm --prefix viewer test
npm --prefix viewer run build

# skills
cd skills/circuitcode && /Users/d/miniconda/bin/python3.12 -m pytest tests/ -q
```

Dev / build:

```bash
scripts/dev.sh                          # toolchain setup + skill re-vendor + app on :4179
scripts/build/build-skill-runtimes.sh   # re-vendor circuitpy into the skill runtime
```

## Conventions worth knowing

- **Never trust an exit code:** `tscircuit-cli build` exits 0 even with real errors —
  errors are `*_error` elements inside circuit.json. Every gate parses produced
  artifacts, never `$?`.
- **The CLI is `toolchain/node_modules/.bin/tscircuit-cli` — never `npx tsci`** (an
  unrelated npm package that hard-requires bun). Toolchain resolution: env
  `CIRCUIT_TOOLCHAIN` > repo default.
- **Session-dir encoding footgun (inherited, real):** the encoded Claude session dir
  replaces **every** non-alphanumeric char with `-` (matching
  `cwd.replace(/[^a-zA-Z0-9]/g, '-')`). Workspaces live under
  `~/.autonomous-circuit/projects/<uuid>` — a `/`-only encoding mismatches and the
  driver dies with "Session ID already in use".
- **Cache-bust or suffer:** every media URL carries `?v=<mtime_nanos>-<size>`.
  Browsers cache hard; a re-exported SVG/PNG at the same path WILL replay stale content
  without it.
- **One JSON line, last line wins:** skill runners print exactly one snake_case JSON
  line; parents parse `stdout.splitlines()[-1]` so stray prints don't kill a turn.
- **Artifact order:** the `.board.json` sidecar lands BEFORE `<stem>.circuit.json`
  (the snapshotter has 1s granularity; metadata must be readable when the artifact
  event fires).
- **Errors** from `build_board()` subclass `BuildError` (`ProjectShapeError` /
  `SpecValidationError` / `CompileError` / `ToolchainError` / `ExportError`).
- **Offline default:** `CIRCUIT_PARTS_ENGINE=off` suite-wide in tests/CI — parts resolve
  from `parts.json` + block pins; the one sanctioned network touch in a full build is
  stage 0's parts engine. jlcsearch never appears in the generation loop (cold queries
  take 47–90s); part search lives in parts-book only.
- **kicad-cli is optional to build, required to ship:** probed on PATH then
  `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`; absent → one
  `kicad_unavailable` info, tscircuit-exported gerbers, and a blocking-for-ship
  `unverified_gerbers` warning. kicad-dependent tests skip (not fail).
- **Domain numbers live in `circuitlib/tables.py`** — single owner of every number;
  never transcribe into prompts ("never transcribe — import").
- **Out of scope for v1:** ordering APIs, the 3D viewer tab, the screening loop,
  registry publishing (see `docs/vision-context.md`).
