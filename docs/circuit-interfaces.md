# Circuit interface contracts

Document version: **v0.1** (2026-08-10). This document mirrors `docs/video-interfaces.md`
(the donor contract, itself descended from `panda-interfaces.md`) for **Autonomous Circuit** —
chat → engineering spec → board source → verified, fab-ready PCB/PCBA packet.
From the moment the build tracks start, this document is **frozen**: changes go through
`docs/circuit-interfaces-CHANGES.md` (append-only: Change / Why / Backward compatible /
Mechanism / Tracks affected).

Who consumes what (the donor repos' sharpest lesson, restated on day one):

- The **stdout JSON line** from a skill script is consumed by the `claude` CLI as the
  tool-call result — it is what the **model** sees. The server never parses it.
  snake_case; fields renameable cheaply.
- The **`.board.json` sidecar** is the **server driver's** machine contract — the review
  loop walks `*.board.json` files and gates on `validation.warnings[].severity`.
  camelCase; renaming fields breaks the review loop.

Substrate ruling (2026-08-10 bake-off, ~70% confidence, see org repo `projects/circuit/`):
**tscircuit** authors the board; **kicad-cli** is the independent second substrate AND the
shipping gerber exporter; **JLCPCB economy PCBA** is the fab target. Two standing rules from
the verdict: (1) **never trust an exit code** — every gate parses produced JSON/artifacts;
(2) the verify/export spine (§1 stages 2–6) consumes Circuit JSON / KiCad files, never TSX —
so a different authoring front-end could be absorbed without rewriting the spine.

Three sections, three build tracks: §1 circuitpy (Python pipeline), §2 server driver + client
transport (Node/React), §3 skills.

---

## §1 `build_board()` contract (packages/circuitpy)

### Project = one device; board = one generation target

```
<project>/                          ← the workspace (one device)
├── product.json                    required once — the product definition (bible)
├── parts.json                      the locked BOM identities — owned WHOLLY by parts-book
├── blocks/                         golden-block library, copied in at project creation
│                                     (frozen with the project; byte-stable fab reproducibility)
├── boards/
│   ├── main.tsx                    the board source (tscircuit TSX); normal case one board
│   ├── main.circuit.json           compiled IR — artifact of record, lands LAST
│   ├── main.board.json             sidecar — written BEFORE main.circuit.json
│   ├── main_review/                _schematic.png, _pcb.png (+ .svg sources; _pcb_bottom.png
│   │                                 when parts sit on both sides)
│   └── main_fab/                   gerbers.zip, bom.csv, cpl.csv, ORDER.md, board.glb
├── tsconfig.json, tscircuit.config.json   from the project skeleton (no package.json,
│                                            no node_modules — the toolchain owns Node deps)
├── inputs/                         chat reference images (excluded from catalog)
└── .circuit/                       build tmp + caches (excluded from catalog)
```

### `product.json` — the bible

```jsonc
{
  "name": "desk-air-monitor",
  "description": "USB-C powered desk air quality monitor",
  "power": "usb-c-5v",              // usb-c-5v | battery-lipo-sealed-block | external-dc-lv
  "envelopeMm": [60, 40],           // max board outline; exceeding it is a blocking warning
  "layers": 2,
  "fab": "jlcpcb",                  // fab profile id (jlcpcb is v1's only real profile)
  "assembly": true                  // PCBA vs bare PCB — controls cpl.csv + ORDER.md content
}
```

`source.fingerprint` folds `boards/<stem>.tsx` + `product.json` + `parts.json` + every local
import (incl. `blocks/`) — donor algorithm, TS import scanner instead of Python `ast`.
Editing the bible or the parts lock invalidates every board.

### Board-source rules (enforced by validators, not convention)

- Composition from **golden blocks + glue** only (passives, LEDs, connectors, headers).
  Never a novel IC circuit invented from a datasheet — values, polarities, and pinouts
  live frozen inside blocks (deterministic checks cannot catch a swapped SDA/SCL or a wrong
  feedback divider; the block is the safety mechanism).
- Safety envelope, blocking and non-negotiable: **no mains, ever** (low-voltage DC ≤24V
  only); battery power **only** via the sealed validated charge/protect block; radio **only**
  as certified modules (ESP32-WROOM-class) — never bare-die RF.
- Generated source carries a pinned-dialect header comment naming the exact tscircuit
  version it targets (in-source pinning, cribbed from Zener's `pcb-version` idiom).
- Board `thickness` is set explicitly (JLC standard 1.6mm; toolchain default is 1.4).
- **Closure under composition (2026-08-11).** A block passing its own gauntlet is
  necessary and not sufficient: every composition the planner can legally emit must
  itself have been built through the real pipeline. `evals/composition.py` builds the
  pair matrix (every single block, every unordered pair) and records each cell in
  `evals/composition-matrix.json`. A composition the planner can produce but the matrix
  has never built is an **untested claim**. When a cell fails, the fix goes in the block,
  in `circuitlib.layout`, or in the planner's defaults — never in a repair the agent
  performs afterwards.

### Toolchain (the ffmpeg posture)

All Node tooling lives in the repo's `toolchain/` (exact-pinned `package.json`:
`tscircuit`, `@tscircuit/cli`, `tsx`, `@tscircuit/checks`, `circuit-to-svg`, `sharp`),
installed by `scripts/setup-toolchain.sh` (run by dev.sh), resolved by circuitpy via
env `CIRCUIT_TOOLCHAIN` > repo default. The CLI binary is `tscircuit-cli`
(**never `npx tsci` — that is an unrelated npm package**); invoked out-of-process exactly
as dramapy invokes ffmpeg. `kicad-cli` is probed on PATH then
`/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`; it is REQUIRED for shipping
fab packets (see stage 5) and optional for everything else. `toolchain.py` is the only
module that names binaries; it raises plain RuntimeError with an 800-char output tail;
callers wrap into the error hierarchy at their own boundary.

### Public entry point

```python
def build_board(
    source_path: Path | str,          # boards/<stem>.tsx or a dir containing boards/main.tsx
    output_path: Path | str,          # …/<stem>.circuit.json (suffix must be .circuit.json)
    *,
    fab: str | None = None,           # default: env CIRCUIT_FAB, default "jlcpcb"
    max_build_s: float | None = None,
) -> dict[str, object]
```

Idempotent short-circuit (donor rule): fingerprint + toolchain versions match the existing
sidecar and every artifact is on disk → zero writes, `"unchanged": True`.
`CIRCUIT_FORCE_REGEN=1` overrides.

### The build stages (each gate parses artifacts, never `$?`)

| # | Stage | Mechanism | Gate |
|---|---|---|---|
| 0 | compile | `tscircuit-cli build` → circuit.json (+ `--schematic-png --pcb-png` review images) | circuit.json exists; fatal eval error → `COMPILE_ERROR` |
| 0b | routing escalation (2026-08-11) | if stages 1/2/4a return a routing-class blocking warning, rewrite the **mirrored** source with `autorouterEffortLevel="5x"` and compile once more | keep the retry only when it has strictly fewer blocking warnings; report it in `build` |
| 1 | error scan | parse circuit.json elements: `type` ending `_error` / `_warning` | harvested into warnings; kind = the element type string verbatim |
| 2 | independent re-check | `@tscircuit/checks` `runAllChecks` over the same JSON | findings → warnings, kind = check name |
| 3 | second substrate | export `kicad_sch`/`kicad_pcb` → `kicad-cli sch erc` + `pcb drc --schematic-parity --exit-code-violations --format json`; parse the JSON report | violations → `erc_violation` / `drc_violation`; kicad absent → one `kicad_unavailable` info |
| 3b | converter audit (v1.1) | netlist diff (SKiDL or `kicad-cli` netlist) between circuit.json and the converted board | mismatch → `netlist_mismatch` warning |
| 4 | DFM + BOM gate | our script over circuit.json + BOM vs the fab profile's limit table (JLC 2-layer: trace/space ≥0.127mm, drill ≥0.3mm, annular ≥0.2mm, edge clearance ≥0.3mm, board ≥3×3mm; every assembly BOM row needs an LCSC number; footprint-IoU <0.5 error / <0.65 warning / <0.85 info) | `dfm_*`, `part_not_orderable`, `extended_part` |
| 5 | fab export | gerbers + drill via **kicad-cli from the converted board** (the shipping path); BOM csv + CPL csv from circuit-json exporters (they carry the LCSC numbers); zip per fab-profile naming; ORDER.md from the profile template; `board.glb` best-effort | kicad absent → tscircuit-exported gerbers **plus a blocking-for-ship `unverified_gerbers` warning** (severity `warning`; ORDER.md is not written) |
| 6 | render + sidecar | normalize review PNGs into `_review/`; write sidecar; move circuit.json into place LAST | sidecar-before-artifact-of-record ordering (mtime snapshotter has 1s granularity) |

Stages 2–6 consume Circuit JSON / KiCad files only — never the TSX (substrate-agnostic spine).
Full builds may touch the network once, in stage 0 (the tscircuit parts engine assigns real
LCSC parts). `CIRCUIT_PARTS_ENGINE=off` disables it (CI/offline: parts resolve only from
`parts.json` + block pins; regression tests run fully offline).

### Artifacts written per call (`<stem>` = source basename)

| File | Always? | Purpose |
|---|---|---|
| `<stem>.circuit.json` | yes | the IR of record — lands LAST, fires the catalog |
| `<stem>.board.json` | yes | sidecar (below) — written before the IR |
| `<stem>_review/_schematic.png` + `.svg` | yes | the review loop `Read`s the PNG |
| `<stem>_review/_pcb.png` + `.svg` | yes | ditto (`_pcb_bottom.png` when double-sided) |
| `<stem>_fab/gerbers.zip` | when fab-ready | kicad-cli-exported gerber+drill set |
| `<stem>_fab/bom.csv` | when fab-ready | JLCPCB columns: `Comment,Designator,Footprint,LCSC Part #` |
| `<stem>_fab/cpl.csv` | when assembly | `Designator,Mid X,Mid Y,Layer,Rotation` (mm, centers) |
| `<stem>_fab/ORDER.md` | when fab-ready | exact-clicks walkthrough incl. the placement-preview warning (JLC rotation conventions differ; the preview screen is the safety net) + cost/turnaround numbers |
| `<stem>_fab/board.glb` | best-effort | 3D body for Vibe enclosure pairing |

"Fab-ready" = zero `error`-severity warnings AND gerbers came from kicad-cli.

### Definition of done (2026-08-11)

**A board is complete only when `fab.ready` is `true`.** Anything else is an
*unfinished board*, not a finished board with caveats — and that holds whatever
the cause: a blocking warning, kicad-cli absent, `gerberSource: "tscircuit"`.
There is no "done, but not orderable" state; a packet a user cannot send to
JLCPCB is work in progress.

The measured target is stronger: **first-build fab-ready** — `ready: true` on
build #1 from a cold brief, with zero repair rounds. `evals/agent/run.py` scores
it. A repair round is not a success story; it is a defect that should have been
prevented upstream, in a block, in `circuitlib`, in the project skeleton or in
the planner's defaults.

What "fab-ready" *means* is deliberately unchanged by this rule and must stay
hard to earn. The number moves because boards get better, never because the bar
moved.

### `.board.json` sidecar schema (camelCase, canonical JSON: `sort_keys`, `(",",":")`)

```jsonc
{
  "generator": "circuitpy",
  "entryKind": "board",
  "source":     { "kind": "tsx", "path": "boards/main.tsx", "hash": "…", "fingerprint": "…" },
  "board":      { "path": "main.circuit.json", "name": "desk-air-monitor",
                  "widthMm": 58.4, "heightMm": 38.0, "layers": 2 },
  "toolchain":  { "tscircuit": "0.0.2279", "checks": "0.0.152", "kicadCli": "10.0.5" },  // kicadCli omitted when absent
  "build":      { "autorouterEffort": "default",   // or "5x" when stage 0b's retry won
                  "attempts": 1, "blockingByAttempt": [0] },
  "bom":        { "lines": 14, "orderable": 14, "basicParts": 9,
                  "estimatedCostUsd": 11.20 },
  "fab":        { "profile": "jlcpcb", "ready": true, "assembly": true,
                  "gerberSource": "kicad-cli",          // or "tscircuit" (never ready:true)
                  "packet": "main_fab/" },
  "validation": { "warnings": [ { "part": "U3.pin7", "kind": "source_trace_not_connected_error",
                                  "detail": "…", "severity": "error" } ] },   // omitted when empty
  "artifacts":  { "schematicPng": "main_review/_schematic.png", "pcbPng": "main_review/_pcb.png",
                  "gerbers": "main_fab/gerbers.zip", "bom": "main_fab/bom.csv",
                  "cpl": "main_fab/cpl.csv", "order": "main_fab/ORDER.md",
                  "glb": "main_fab/board.glb" }          // absent members omitted
}
```

### `validation.warnings` — open `kind` set, closed `severity` set

`{part, kind, detail, severity}`; `part` is pin/net/refdes-localized wherever possible
(pin-level localization is what makes the repair loop converge). Severity routing is the
driver's ONLY gate: `error` blocks, `warning` reviews, `info` advises. The driver never
switches on `kind`. Kind sources: circuit.json element types verbatim (stage 1),
`@tscircuit/checks` names (stage 2), `erc_violation`/`drc_violation`/`netlist_mismatch`
(stage 3), `dfm_*`/`part_not_orderable`/`extended_part` (stage 4),
`unverified_gerbers`/`kicad_unavailable` (stage 5), plus pipeline-owned:
`safety_envelope` (error), `board_exceeds_envelope` (error), `power_budget` (warning),
`part_drift` (warning — BOM row disagrees with `parts.json`), `check_failed` (warning —
a verifier itself raised; checks can never break generation), `functional` (warning —
declared by circuitlib's board-law validators riding the source).

### Error contract

```
BuildError
├── ProjectShapeError        # missing board source / bad product.json / bad output suffix
├── SpecValidationError      # pre-flight source/spec rules failed (safety envelope here)
├── CompileError             # tscircuit eval failed fatally (TSX won't compile)
├── ToolchainError           # a toolchain subprocess failed (cli/checks/kicad-cli missing or crashed)
└── ExportError              # gerber/bom/cpl/render/sidecar writing failed
```

### Fab profile abstraction

`FabProfile` owns: gerber naming + zip layout, BOM/CPL column mapping, the DFM limit
table, the ORDER.md template (+ cost model: 5× PCB ≈ $4–20 all-in; 5× assembled
ESP32-class ≈ $75–110, 1–2 weeks), and rotation-correction data as it accrues
(seed: Fabrication-Toolkit/Bouni DBs). v1 ships `jlcpcb` only; the fab side never
touches the network in v1 (packet + walkthrough, no ordering API — JLCPCB has no
assembly endpoint and gates API access on order history).

### Tests (donor discipline, verbatim)

Real projects on disk, the real toolchain on disk (exactly as dramapy demands real
ffmpeg — never mock circuitpy or the CLI), `CIRCUIT_PARTS_ENGINE=off` suite-wide, an
env guard popping network keys, per-module unit files + one e2e asserting the full
artifact set, sidecar schema, and sidecar-before-IR ordering. kicad-dependent tests
skip (not fail) when kicad-cli is absent.

---

## §2 Server driver + client transport

The donor machinery carries over wholesale; this section records only the deltas.
**All `/api/<command>` names, SSE event names (`chat_event`, `catalog_changed`), and the
9-kind ChatEvent union carry over with ZERO edits** (the client event path is name-coupled).

- Port **4179** (`VIEWER_PORT` overrides). Projects root `~/.autonomous-circuit/projects/<uuid4>/`.
  Session ns constant `CIRCUIT_SESSION_NS = f466e3eb-799c-4a95-bc9a-72092027e9f7`.
- `app_prereq_check`: claude on PATH · node ≥22.12 · toolchain installed
  (`toolchain/node_modules` present) · python ≥3.10 · kicad-cli (reported, not required).
- Driver: identical two-phase plan→approve→build + autopilot + silent review loop.
  Fence: ```circuit-questions (3 coupled sites already renamed). Phase system prompts:
  plan = engineering spec (block choice, power budget math, pin allocation, size, cost band;
  read-only), implement = write `boards/<stem>.tsx`, run the generator, iterate,
  review = fix by severity. The screening loop is DELETED in v1 (a design-review critic
  skill may return post-v1).
- Snapshotter watch list: `.tsx .json .svg .png .zip .csv .md` (≥1s mtime forward or new
  file). Excluded: `inputs/`, `.circuit/`, `.claude/`, `node_modules`, `blocks/`.
- Review loop (mechanics unchanged, caps mirrored): Phase 1 *structure* — walk
  `*.board.json`, blocking = severity `error`, ≤2 rounds. Phase 2 *electrical function* —
  warnings with `kind ∈ {functional, power_budget, part_not_orderable, part_drift,
  netlist_mismatch}`, ≤3 rounds. Phase 3 *craft* — ALWAYS once: rebuild, `Read`
  `_schematic.png` + `_pcb.png` (net labels present? decoupling adjacent to ICs? connector
  orientation/edge placement? silkscreen legible? mounting holes?), break when no files
  change, ≤2 rounds.
- Catalog kinds: `tsx | json | svg | png | zip | csv | md`. Visibility: `.json` hidden
  (sidecar surfaced via the board entry's `artifact.metadataUrl`); `_review/` and `_fab/`
  members hidden, grouped under the board entry's `artifact`; `.tsx` names starting `_`
  hidden; `blocks/` hidden. Board entry `artifact`: `{schematicUrl, pcbUrl,
  pcbBottomUrl?, metadataUrl, circuitJsonUrl, gerbersUrl?, bomUrl?, cplUrl?, orderUrl?,
  glbUrl?}`. Every media URL carries `?v=<mtime_nanos>-<size>`.
- Client surgery: keep chat + stores + transport + ui verbatim. Replace
  `components/episode/*` with `components/board/*` behind the same 6-prop main.jsx seam:
  **BoardWorkspace** (rail of boards w/ status dots — episodeModel logic ports verbatim as
  boardModel) with tabs **Schematic** / **PCB** (pan-zoom SVG, ~100 lines, no new dep;
  top/bottom toggle) / **BOM** (table from bom.csv + parts.json: refdes, part, LCSC link,
  basic/extended badge, unit price) / **Fab** (download buttons from `artifact` +
  rendered ORDER.md + cost line + "not fab-ready" state listing blocking warnings).
  WarningsStrip replaces StoryboardStrip (chip per warning, click prefills the fix request
  "U3.pin7 (source_trace_not_connected_error): "). PartsPanel replaces CastPanel (reads
  parts.json). "Send to AI" sends current tab + board context note. 3D GLB tab: post-v1.
- `hasEpisodeVideo` → `hasBoard` (any `*.circuit.json`). Settings: `renderProvider` dropped.

---

## §3 Skill stdout + artifact contract

Skills: `circuit-analysis`, `circuitcode`, `parts-book`, `board-viewer`.
Anatomy per skill mirrors the donor exactly: 2-field frontmatter (`name`, trigger-dense
`description`), self-contained runtime, vendored circuitpy under
`skills/circuitcode/scripts/packages/circuitpy/` (rsync'd by
`scripts/build/build-skill-runtimes.sh` — which dev.sh now actually runs), `references/`
with named load triggers, `agents/claude-code.md` host overrides, `tests/` with a
circuitpy stub injected via `CIRCUITCODE_TEST_CIRCUITPY_PATH`, `scripts/common/pyversion.py`
re-exec shim kept verbatim (`CIRCUIT_PYTHON` override).

### `circuitcode` generator invocation

```
python skills/circuitcode/scripts/circuit <boards/main.tsx | project_dir>
       [--out-dir DIR] [--stem NAME] [--fab jlcpcb] [--wall-clock-s S]
python skills/circuitcode/scripts/check  <same>     # stages 0–2 only, tempdir, paths stripped
python skills/circuitcode/scripts/review <project>  # re-surface warnings + regenerate _review pngs
```

Runner: subprocess + wall clock (default **1800s** since 2026-08-11, was 300s —
see the -CHANGES entry; `CIRCUIT_WALL_CLOCK_S` override; parent
`subprocess.run(timeout=…)`, always returns a dict, never raises), rlimit ceilings kept as
a runaway backstop. The elaborate user-code import sandbox is GONE — the board source is
TSX executed inside the toolchain's own process, not in ours. The generation loop never
touches jlcsearch (cold queries take 47–90s); part search lives in parts-book only.

Domain law lives in `skills/circuitcode/circuitlib/` (the dramalib mirror): `tables.py`
(rails, trace-width-per-amp, JLC DFM/cost bands — single owner of every number; "never
transcribe — import"), `blocks.py` (the golden-block registry: pin contract, rail budget,
pinned LCSC parts, provenance), `safety.py` (the envelope as hard refusals;
`not_screened` ≠ `pass`), `helpers.py` (`board_plan()`, `validate_board_law()` — soft
warnings riding the source as `functional`), `golden.py` (invariant CI: known-good boards
stay clean, a seeded-defect sentinel MUST trip — "if the sentinel passes, the eval went
blind"). Every golden block ships BLOCK.md + a graded testbench (topology assertions +
circuit-json snapshot + pinned-BOM assertion — the Zener testbench idiom).

### Stdout: exactly one JSON line (snake_case; parent parses `stdout.splitlines()[-1]`)

```typescript
interface CircuitcodeResult {
  ok: boolean;
  circuit_json_path?: string; metadata_path?: string;         // workspace-relative
  schematic_png?: string; pcb_png?: string;
  board?: { width_mm: number; height_mm: number; layers: number };
  bom?: { lines: number; orderable: number; estimated_cost_usd: number };
  fab?: { profile: string; ready: boolean; packet_dir?: string };
  warnings?: { part: string; kind: string; detail: string; severity: string }[];
  error?: { code: "VALIDATION_FAILED" | "COMPILE_ERROR" | "TOOLCHAIN_ERROR"
                 | "EXPORT_ERROR" | "BUILD_TIMEOUT" | "RUNTIME_ERROR" | "PART_ERROR";
            message: string; traceback?: string };
}
```

The SKILL.md carries the error-code → fix-target routing table and the loop:
edit TSX → run generator → read verdict → `Read` BOTH pngs → smallest responsible fix →
soft cap 4 iterations. **Done-gate (tightened 2026-08-11): `fab.ready == true`, full
stop** — plus both review images Read. The old gate ("no `error`-severity warning
outstanding") is strictly weaker: it passes a board whose gerbers are unverified. A turn
that ends with `fab.ready: false` reports an unfinished board and says exactly what is
missing; it never presents the board as done.

### `circuit-analysis` output contract (read-only, no artifacts)

Exactly one fenced ```circuit-brief JSON block (product, chosen blocks[], power story,
io/controls, size class, est parts-cost band, enclosure interface for the Vibe 3D pairing,
safety-envelope verdict accept | reject-with-reason) + 2–3 sentence summary, then STOP.
Any number not owned by a circuitlib table is marked "estimate".

### `parts-book` contract (lock the BOM)

Owns `parts.json` WHOLLY (simpler than the donor's guarded block — no markers needed;
circuitcode never writes it, parts-book never writes TSX). Per part id: LCSC C-number,
mfr, package, basic/extended, stock + unit price + checked date, datasheet URL. Offline it
writes candidate slots from `circuitlib.blocks` defaults; `--lookup` hits jlcsearch
(retries, 90s timeout, local cache; the jlcparts-style SQLite mirror is the v1.1 upgrade)
and degrades gracefully with a `lookup_note`. Prints one JSON line
`{ok, parts: [{id, lcsc, stock_checked, basic}]}`. Rules: prefer Basic parts (extended =
~$3/line loading fee); one part = one exact orderable number; a swap that changes the
footprint warns loudly (invalidates layout, not just BOM).

### `board-viewer` contract (the thinnest skill — no server, matching the app's design)

Read `~/.autonomous-circuit/server.json`; if the project is listed print
`http://127.0.0.1:<port>/?project=<id>&file=boards/main.tsx` (workspace-relative file
value) — else do NOT start anything, do NOT invent a port; the app shows artifacts live
via `artifact_changed`. The URL is a deliverable for the user; for the agent's own QC,
`Read` the `_review` PNGs instead. (The donor's §3 drifted from its skill on this point;
this contract matches the skill from day one.)
