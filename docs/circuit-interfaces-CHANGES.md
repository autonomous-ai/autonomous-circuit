# circuit-interfaces.md — change log (append-only, newest first)

`docs/circuit-interfaces.md` is frozen as of v0.1 (2026-08-10). Every change lands here
first, in this template, before the doc itself is edited:

```
## YYYY-MM-DD — <one-line title>
- **Change:** what changed, naming exact identifiers/env vars/paths.
- **Why:** the reason, citing the decision/finding (with date) that forced it.
- **Backward compatible:** yes/no — and for no, what breaks (cache invalidation
  consequences stated explicitly).
- **Mechanism:** how the change is applied (code sites, migration, re-vendor).
  Flag "skill runtime re-vendor required" whenever packages/circuitpy changes.
- **Tracks affected:** pipeline / server / client / skills / docs.
```

## 2026-08-11 — `fab.ready: true` is the definition of done, on the first build
- **Change:** §1 gains a *Definition of done* rule and §3 tightens the
  circuitcode done-gate. A board is **complete only when its sidecar carries
  `fab": {"ready": true}`**. `fab.ready: false` is an **unfinished board**, not
  a finished board with caveats — whatever the cause (blocking warnings,
  kicad-cli absent, `gerberSource: "tscircuit"`). The skill's done-gate changes
  from "never declare done with an `error`-severity warning outstanding" to
  literally `fab.ready == true`, and the required final response leads with it.
  The measured target is **first-build fab-ready**: `ready: true` on build #1
  of a cold brief, with zero repair rounds. Nothing about *how* `fab.ready` is
  earned changes — §1 stage 5's rule is untouched (zero `error`-severity
  warnings AND gerbers from kicad-cli). The bar did not move; only what we call
  finished did.
- **Why:** Dee, 2026-08-11: *"All designs generated must be ready to be sent to
  JLCPCB. Perfect, no issue, board generated one shot, printed."* and *"make
  sure our software is good enough to make everything fab ready. users just
  chat with our software, and we generate fab-ready boards."* The old gate let
  an agent finish a turn on a board that no fab would accept, because the
  blocking condition (`error` severity) is narrower than the shipping condition
  (`fab.ready`). Both example-board sets on 2026-08-10 ended with `ready:
  false` and an agent that considered itself done.
- **Backward compatible:** yes for consumers — no field, name, severity or
  artifact changes. It is a behaviour tightening on the agent side and a
  documentation change on the pipeline side. Existing sidecars stay valid;
  boards that were "done" under the old gate are now correctly reported as
  unfinished.
- **Mechanism:** `docs/circuit-interfaces.md` §1 (new *Definition of done*
  paragraph after the artifact table) and §3 (done-gate sentence);
  `skills/circuitcode/SKILL.md` (non-negotiable 5, *Required final response*);
  `skills/design-review/SKILL.md` (panel non-optional in the flow). No
  packages/circuitpy change, so no re-vendor.
- **Tracks affected:** skills / docs.

## 2026-08-11 — Composition closure: the tested space bounds what the planner may emit
- **Change:** §1's board-source rules gain a closure rule. It is not enough for
  every golden block to pass its own gauntlet; **every composition the planner
  can legally emit must itself have been built through the real pipeline**.
  `evals/composition.py` builds the pair matrix (every unordered pair of
  registry blocks, plus every single) as a real board through `build_board()`
  and records the blocking result per cell; `evals/composition-matrix.json` is
  the record. A composition the planner can produce but the matrix has never
  built is an **untested claim**, and the fix for a failing cell belongs in the
  block, `circuitlib.layout`, or the planner defaults — never in a repair the
  agent performs afterwards.
- **Why:** Dee, 2026-08-11: *"just not these 3, they are just the first 3."*
  Getting three known boards to pass is a demo; the guarantee has to be
  structural. All three example boards failed in composition, not in a block
  alone — the block gauntlet was green while every board built from it was
  blocked.
- **Backward compatible:** yes — a new eval and a documented rule; no schema,
  field or behaviour change in the pipeline.
- **Mechanism:** `evals/composition.py` (matrix runner + report),
  `skills/circuitcode/circuitlib/layout.py` (constraints encoded as
  composition rules), `docs/circuit-interfaces.md` §1 board-source rules.
- **Tracks affected:** skills / docs / evals.

## 2026-08-10 — `scripts/check` runs the full pipeline into a tempdir, not stages 0–2
- **Change:** §3 specifies `python skills/circuitcode/scripts/check <same>` as
  "stages 0–2 only, tempdir, paths stripped". circuitpy exposes no
  stages-limited entry point — `build_board()` is the whole §1 public surface —
  so `check` calls `build_board()` with `output_path` inside a
  `circuitcode-check-*` tempdir and presents a stages-0–2-*shaped* result: the
  tempdir is deleted, the path members (`circuit_json_path`, `metadata_path`,
  `schematic_png`, `pcb_png`) and the whole `fab` member are stripped, and the
  two warning kinds that describe only the discarded packet
  (`kicad_unavailable`, `unverified_gerbers`) are dropped. `ok`, `board`,
  `bom`, `warnings`, `error` are unchanged. Every other §3 contract for
  `check` (one JSON line, same arg shape, no workspace writes) holds.
- **Why:** the alternative was re-implementing stage 0 (mirror-copy,
  `tscircuit-cli build` argv, the `dist/<entry>/circuit.json` layout) in the
  skill, duplicating `generation.py`'s private surface and guaranteeing drift
  the moment the pipeline track changes it. The frozen rule is that the spine
  owns the stages; the skill is a CLI over it. Decided while building the
  circuitcode CLI layer (2026-08-10).
- **Backward compatible:** yes for consumers — the emitted JSON is a subset of
  what §3 promises. Not free at runtime: `check` costs a full build (KiCad
  crossing + fab export) instead of a cheap structural pass, so it is slower
  than the contract implies. Reverting to a true stages-0–2 path is a pure
  win the day circuitpy exposes one (e.g. `build_board(..., max_stage=2)`).
- **Mechanism:** `skills/circuitcode/scripts/check/cli.py` (`STRIPPED_KEYS`,
  `PACKET_ONLY_KINDS`). No pipeline change, no re-vendor.
- **Tracks affected:** skills / docs (§3 `check` line when the freeze lifts).

## 2026-08-10 — Fab packet members written on non-ready builds too (ORDER.md stays ready-only)
- **Change:** `build_board()` writes `gerbers.zip` / `bom.csv` / `cpl.csv` into
  `<stem>_fab/` whenever the exports succeed — including builds with
  `error`-severity warnings and kicad-absent builds — not only "when fab-ready"
  as the §1 artifact table's Always? column reads literally. `ORDER.md` remains
  strictly fab-ready-only, and export failures on a board that already has
  error warnings degrade to a `check_failed` warning instead of `ExportError`
  (an otherwise-clean board still raises `ExportError`).
- **Why:** the table's literal reading contradicts §1 stage 5's own gate
  ("kicad absent → tscircuit-exported gerbers plus `unverified_gerbers`" — a
  by-definition not-ready packet that still writes gerbers) and §2's BOM tab,
  which needs `bom.csv` for boards mid-repair. Found while building the
  pipeline track (2026-08-10).
- **Backward compatible:** yes — consumers gate on `fab.ready` +
  `validation.warnings` severity, never on file presence; extra files carry no
  new semantics.
- **Mechanism:** `packages/circuitpy/src/circuitpy/generation.py` stage 5
  block. Skill runtime re-vendor required (packages/circuitpy changed).
- **Tracks affected:** pipeline / docs (table footnote when the freeze lifts).

## 2026-08-10 — Catalog must surface root `parts.json` as an entry
- **Change:** the client (PartsPanel + BomTable enrichment) reads the parts
  lock through a catalog entry whose `file` is exactly `parts.json` (any
  `kind`), fetching `entry.url` verbatim (`?v=` cache-bust). §2's visibility
  rule "`.json` hidden" gets one more exception alongside the sidecar:
  root `parts.json` is surfaced.
- **Why:** §2 requires "PartsPanel replaces CastPanel (reads parts.json)" but
  names no transport for it; the donor precedent (series.json surfaced despite
  the .json-hidden rule) is the cheapest path and keeps the ?v= refetch
  semantics. Decided while building Track E (2026-08-10).
- **Backward compatible:** yes — if the scanner doesn't surface it, the panel
  and the BOM badges degrade to their empty states; nothing breaks.
- **Tracks affected:** server (catalog visibility rule), client (already
  built to this: `lib/boardModel.js selectPartsEntry`).

(No further entries yet.)
