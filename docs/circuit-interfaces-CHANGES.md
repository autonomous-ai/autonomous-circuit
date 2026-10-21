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

## 2026-08-24 — the safety envelope refuses a charger named by cell format
- **Change:** the battery half of the envelope gains one rule, in both tables
  that describe it: `circuitpy.spec._RAW_BATTERY_IC_PATTERNS` (via
  `_CELL_CHARGER_RE`) and `circuitlib.safety.CHARGER_PATTERNS`. It refuses a
  **lithium cell format** (`18650`, `21700`, `26650`, `14500`, `16340`,
  `18350`, `10440`, `20700`, `26800`, `lifepo4`) named within one sentence of a
  **charging role** — `charger(s)`, or `charge`/`charging`/`recharging`
  followed by a word that makes the board the thing doing the charging
  (`ic`, `circuit`, `controller`, `board`, `module`, `dock`, `cradle`,
  `station`, `bay`, `pcb`, `shield`). Either order. Refusal reason is unchanged:
  battery power only via the sealed validated charge/protect block.
- **Why:** recorded in `spec.py` since #8 as a known hole — **"an 18650 charger
  board" was in neither table and passed.** It could not be closed by adding
  the bare cell format, because that also refuses "a gauge for an 18650 pack,
  using the sealed block", which is exactly what the envelope permits, so it
  was left open rather than guessed at. The conjunction is what closes it
  without taking the monitor with it.

  The role half is an **allowlist and not a list of monitoring phrases to
  exclude**, and that is the whole design. An exclusion list refuses every
  phrasing nobody thought of, and the phrasings nobody thinks of here are
  monitors — `charge state`, `state of charge`, `charge level`,
  `charging status`. Built this way they pass by construction and the gate can
  only refuse wording it names out loud. `rechargeable` describes the battery,
  not the board's job, and does not match.
- **Backward compatible:** yes in practice, and measured rather than asserted.
  The rule was run over **all 31 `product.json` descriptions and all 32 board
  sources** in the corpus before it was written in: **zero** of them start
  failing. Nothing else moves — no new warning kind, no severity change, no
  artifact change. The one behaviour that does change is the intended one: an
  ask that names a lithium cell format together with a charging role now
  refuses at spec time instead of building. Negation still applies, so
  "no 18650 charger anywhere" passes.
- **Mechanism:** `packages/circuitpy/src/circuitpy/spec.py` (`_CELL_FORMAT`,
  `_CHARGING_ROLE`, `_CELL_CHARGER_RE`, one added entry in
  `_RAW_BATTERY_IC_PATTERNS`), `skills/circuitcode/circuitlib/safety.py` (the
  same pattern in `CHARGER_PATTERNS`; `_hits` lowercases, so it is written
  lowercase). Both tables changed together on purpose — they drifted apart once
  and the drift only surfaced when one of them started reading descriptions.
  `circuitlib/safety.py` has no vendored copy, so **no skill runtime re-vendor
  is required**; `packages/circuitpy` changed, so the circuitpy half does
  re-vendor with the usual build.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-24 — parts-book reads the board entry, not only `blocks/`
- **Change:** §`parts-book` contract. The skill's candidate slots come from a
  third source: the project's own board entries (`boards/*.tsx`), read with the
  same scanner as a block. A board pins parts two ways — a literal
  `supplierPartNumbers` on the element, and the number passed in as a prop with
  the literal at the call site (`<ComfortLed ledLcsc="C2297" rLcsc="C25091" />`).
  `scan_board_tsx` reads the second via the **`<x>Lcsc` pins the part named by
  `<x>`** convention, which is also where the refdes comes from. The record
  gains an optional **`boards`** member (present only for a part no block owns)
  and `source` gains **`board-source`**. Two related rules: a manual record
  (`override`, or `source: manual`) that no block or board pins is now carried
  forward across runs, and a part id already on disk is kept rather than
  re-derived from source.
- **Why:** measured on weather-badge-27, 2026-08-24 (#25 on the task board,
  found by the build agent auditing its own board). Three parts — **C25091,
  C25117 and C84256** — shipped on the `bom.csv` the fab reads with no locked
  record at all: no stock, no price, no verification date, no row in the parts
  panel. No `supplierPartNumbers` scan could ever have found them, because the
  literal is not on the element. The lock went **21 records → 24 and every BOM
  line now has one behind it.** The `--add` half is the same bug from the other
  side: slots were rebuilt from source every run, so an addition lived exactly
  until the next invocation evicted it (two runs in sequence both returned 21
  records, the second having dropped the first's part).
- **Backward compatible:** yes. The stdout JSON line is unchanged —
  `{ok, parts: [{id, lcsc, stock_checked, basic}]}`, plus the `notes` array it
  already had. `boards` is additive and `circuitpy.spec.load_parts` ignores
  unknown keys (verified against the new file); `part_drift` compares `lcsc`
  only and never reads `blocks`, so a `blocks: []` record does not trip it.
  Block-derived records are still dropped when they leave the source —
  parts.json is a lock, not an attic. **One thing to know:** a board-source
  record lands `basic: false` like any un-looked-up slot, so it draws an
  `extended_part` info until `--lookup` runs. That is the existing convention
  for a new slot, not a claim that the part is Extended.
- **Mechanism:** `skills/parts-book/scripts/parts/cli.py` (`scan_board_tsx`,
  `_PROP_LCSC_RE`, `_merge_source`, `collect_candidates(blocks_dir, board_tsx)`,
  the manual-carry and id-stability passes in `main`), `skills/parts-book/SKILL.md`
  (the record table and a new section). No `packages/circuitpy` change, so **no
  skill runtime re-vendor required.**
- **Tracks affected:** skills / docs.

## 2026-08-11 — Autorouter effort escalation (stage 0b), a `build` sidecar member, 2700s wall clock
- **Change:** three coupled edits. (1) §1 gains **stage 0b**: after stages 1, 2
  and 4a run on the first compile, if any blocking warning is routing-class
  (`pcb_autorouting_error`, `pcb_trace_missing_error`,
  `pcb_port_not_connected_error`, `pcb_trace_clearance_error`,
  `dfm_hole_clearance`, `dfm_trace_width`, `dfm_trace_clearance`) the pipeline
  rewrites the **mirrored** board source with `autorouterEffortLevel="5x"` and
  compiles once more. Exactly one escalation; the cheaper result stands unless
  the harder one has strictly fewer blocking warnings. `CIRCUIT_ROUTING_ESCALATION=off`
  disables it. (2) The sidecar gains a `build` member —
  `{autorouterEffort, attempts, blockingByAttempt}` — and `CircuitcodeResult`
  gains the snake_case equivalent. (3) The skill runner's wall clock rises from
  300s to **2700s**, and `CPU_TIMEOUT_S` with it — enough for the first attempt
  plus a 5x retry (measured: harness-puck took 1240s at 5x, 5 blocking to 1).
- **Why:** `autorouterEffortLevel` is a `<board>` prop with no CLI flag, and
  nothing in the skeleton, `circuitlib` or the skill ever set it — so every
  board ever built routed at the default. Measured on terminal-keyboard
  (2026-08-11): `"5x"` took the same board from **46 blocking errors to 18**
  with no design change, at a build-time cost of 4:45 to about 17 minutes.
  A higher fixed default would charge every simple three-block board twelve
  wasted minutes, so the ladder is conditional. The wall clock had to rise or
  the escalation could never finish: a 5x pass on harness-buck-sized boards
  exceeded the old 600s pipeline timeout outright. Dee, same day: *"even if you
  send 1-day, 2-day or even 3-day to get the build right and verify everything,
  that's still better than waiting 2 weeks from JLCPCB."*
  Measured and **rejected** on the same board, recorded so nobody retries it:
  raising `minTraceWidth`/clearance props as a routing lever made things worse
  (7 errors to 125) — those props gate the checker, not the router.
- **Backward compatible:** yes for consumers. `build` is an added member;
  severity routing, warning kinds and artifact names are untouched. A board
  that was clean at the default effort still builds identically and never
  escalates. Cache behaviour is unchanged (the fingerprint covers the user's
  source, and escalation is a deterministic function of the verdict).
- **Mechanism:** `packages/circuitpy/src/circuitpy/generation.py`
  (`ROUTING_ESCALATION_*`, `_set_autorouter_effort`, `_routing_blockers`,
  `build_block`), `skills/circuitcode/scripts/common/runner.py`
  (`_default_wall_clock_s`, `CPU_TIMEOUT_S`). **Skill runtime re-vendor
  required** (packages/circuitpy changed).
- **Tracks affected:** pipeline / skills / docs (§1 stage table, §1 sidecar
  schema, §3 runner line).

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

## 2026-08-11 — Stages 4c and 5b: the standalone checks join the gauntlet
- **Change:** two new stages in the §1 build table. **4c** runs
  `packages/verify`'s five circuit-json checks (assembly/DFA, net-class current
  capacity, DC operating point, electrical design review, thermal) beside the
  DFM gate. **5b** runs the gerber-truth check against the packet written by
  stage 5. Both add new `validation.warnings[].kind` values — `dfa_*`,
  `netclass_*`, `dc_*`, `review_*`, `thermal_*`, `gerber_*`, plus
  `verify_unavailable` (info) when the package is not importable. `fab.ready`
  is unchanged in definition and now sees more.
- **Why:** the checks existed and the pipeline ignored them, which is strictly
  worse than not having them — the tool would report a board orderable while
  `verifylib` knew it was unprogrammable. Stage 5b in particular cannot live
  anywhere else: the gerber zip is what JLCPCB actually consumes and does not
  exist until stage 5, so an export bug had nowhere to be caught.
- **Backward compatible:** yes for consumers — the `kind` set is documented as
  open and the driver switches only on `severity` (§1). Not free for boards:
  the honest blocking count on the three examples goes **up**, because these
  are defects that were always true and nothing could see. Costs ~1s of
  wall-clock on a multi-minute build; the corner sweep is deliberately excluded
  and runs beside the build behind `CIRCUIT_VERIFY_CORNERS=1`.
- **Mechanism:** `circuitpy/verify_bridge.py` (path resolution + degradation),
  `circuitpy/generation.py` (the two call sites), `circuitpy/fab.py`
  (`VERIFY_BLOCKING_KINDS`, `VERIFY_ESCALATED_KINDS`, `apply_verify_policy` —
  the severity policy lives on the fab profile so an EE moves the line in one
  place, never inside a check). `scripts/build/build-skill-runtimes.sh` vendors
  `verifylib` beside `circuitpy`. Skill runtime re-vendor required.
- **Tracks affected:** pipeline / skills (re-vendor) / docs (§1 stage table
  when the freeze lifts).

## 2026-09-10 — A board sidecar is `boards/<stem>.board.json`; a copy is not a board
- **Change:** the server driver's readers of the sidecar (`collectBoardWarnings`,
  `workspaceFabReady`, `workspaceHasBoard`, `reviewImagePaths`) look only at
  `<workspace>/boards/<stem>.board.json` and `<workspace>/boards/<stem>_review/`.
  The frozen text says the review loop "walks `*.board.json`"; the walk is now
  that one directory, which is the only place the generator ever writes a
  sidecar (§ "Project layout").
- **Why:** Astra run #5 (pomodoro-puck-run5, 2026-09-10 14:18) kept its own
  build checkpoints at `work/best-build-1/boards/main.board.json` — a copy of
  build 1 with one blocking finding and `fab.ready: false`. The whole-tree walk
  counted it: "1 blocking" and `fabReady = false` on a workspace whose real
  board was fab-ready with none, two review rounds spent prompting the model
  to fix a backup, `structure-unresolved` in the chat. Run #4 escaped only
  because the agent had named its copies `.board.json.txt`. The generator and
  the catalog already treat `boards/` as the board directory; the driver was
  the one reader that did not.
- **Backward compatible:** yes for every board this pipeline has produced —
  `build_board()` writes sidecars under `boards/` only. A workspace that kept
  sidecars elsewhere by hand was never a supported layout.
- **Also in this change (not contract):** a review round now reads its own
  stdout for a provider failure (`{"type":"error"}` / `turn.failed`, e.g. the
  usage limit) and stops the loop with a message, instead of draining it and
  reporting the board unresolved after two five-second "rounds".
- **Tracks affected:** server (driver readers). No client, skill or pipeline change.

## 2026-09-10 — Repair mode: the routed board is an input, not only an output
- **Change:** `build_board(reuse_circuit_json=…, repairs=…)` and the skill CLI's
  `--recheck` / `--edits <edits.json>`. Repair mode skips stage 0 (compile +
  router) and three of the four post-route copper passes (the pour pass
  re-runs: repaired copper may now sit inside a pour's clearance, and that
  pass is safe to repeat), takes `boards/<stem>.circuit.json`
  as the routed IR, re-runs only the pour pass, optionally applies `circuitpy.repair` edits (move a route
  point, move a via with the wires on it, insert/delete points — never a net,
  pad, part or layer), and runs every later stage unchanged: scan, checks,
  KiCad ERC/DRC, DFM, verify, packet, gerber-truth, renders, sidecar. The
  sidecar gains `build.repairMode {reusedCircuitJson, postRoutePasses, repairs}`
  and the result line `build.repair_mode`. A build from TSX after a repair round
  reports `repairs_discarded` (info). §1's "stdout JSON line, sidecar
  camelCase, artifact order" are unchanged; §1's "the IR of record is produced
  by compile" now has a second producer, the repair round.
- **Why:** measured 2026-09-10. Astra run #4 spent twelve rebuilds and two
  hours on one via; Claude's desk cube went 3 → 1 → 7 moving a crystal to fix a
  12 mm trace, because every fix was "edit TSX, re-route everything" and the
  defect moved each time. The harness-free Astra board converged in a 1-minute
  fix–check cycle by repairing copper in place. Replaying the gauntlet on a
  repaired IR takes 30 s against 5–12 min; the errors do not migrate.
- **Backward compatible:** yes — nothing changes for a build that does not pass
  the flag. The unchanged-source short-circuit still returns a repaired
  sidecar as long as the TSX is unchanged, which is the persistence a repair
  gets in v1.5; persistence across a re-route is v2.
- **Tracks affected:** pipeline (`generation.py`, new `repair.py`), skill
  runtime (re-vendor; `runner.py`, `cli.py`, SKILL.md), server prompts
  (build + review). Client unchanged.

## 2026-09-10 — Repair mode, second cut: stale compiler findings go; two layer ops
- **Change:** a repair round strips the compiler's geometry finding elements
  (`pcb_trace_too_long_warning`, `pcb_trace_error`, `pcb_via_trace_clearance_error`,
  `pcb_placement_error`, `pcb_port_not_connected_error`, `pcb_trace_overlap_warning`,
  `pcb_autorouting_error`) from the reused `circuit.json` before the scan and
  records the counts in `build.repairMode.strippedCompilerFindings`; stage 2
  recomputes every one of them from the copper. `circuitpy.repair` gains
  `set_layer` and `remove_via`.
- **Why:** first agent-driven repair round (Claude desk cube, 17:12–17:18): the
  hop it repaired from 10.49 mm to 7.95 mm still read "10.49mm" because the
  compile-time element was copied through; and the second segment cannot get
  under 10 mm without dropping its two vias (7.94 mm straight + 3.2 mm of barrel),
  which no planar edit can do. Both diagnosed by the agent in its report.
- **Backward compatible:** yes — repair-mode only.
- **Tracks affected:** pipeline, skill runtime (re-vendor), SKILL.md.

## 2026-09-11 — v1.6: the repair loop Astra described, measured
- **Change:** (1) repair mode runs `kicad-cli pcb drc --refill-zones --save-board`
  so the DRC gate, the margin DRC and the gerber plot see zones KiCad cut
  around the repaired copper (`build.repairMode.zonesRefilled`; also on any
  build under `CIRCUIT_KICAD_REFILL=1`). (2) `build.repairMode.history` carries
  every repair since the last route; `repairs` stays the current round. (3)
  every `--edits` checkpoints `boards/<stem>.circuit.json` (+ sidecar) under
  `.circuit/repair-undo/` (last 10); `--undo-repair` restores the newest and
  re-runs the gauntlet. (4) `circuitpy.repair` gains `reroute`: A* on a 0.1 mm
  grid for one wire-to-wire segment of one trace, obstacle model shared with
  `trace_clearance` (pads, holes, vias, other traces on that layer), 45° moves,
  no corner cutting; refuses across vias/layers or when no path exists. (5)
  `circuitpy.craft` writes `build.craft` (vias, copper, segments, jogs,
  off-grid segments, mean and worst detours) and one `craft_summary` info
  finding on every build. Nothing new blocks.
- **Why:** run 6 (Astra, 2026-09-11): two repair rounds died on a via moved
  under a pour with no new cutout, so the agent kept a board whose crystal net
  the router had stretched to 19.63 mm. Replaying its exact reverted edits with
  the refill: 0 errors, fab-ready, 24 → 21 warnings, the net under 10 mm.
  Astra's own answer to "keep tscircuit, route like you did" named exactly
  these pieces: local reroute that sees all copper, checkpoint/rollback, one
  patched revision for verify and gerbers, and a craft score after the floor.
- **Backward compatible:** yes — repair-mode paths and an info finding.
- **Tracks affected:** pipeline (`generation.py`, `repair.py`, new `craft.py`),
  skill runtime (re-vendor; `runner.py`, `cli.py`, SKILL.md), server prompts.

## 2026-09-11 — v1.6b: the re-pour is every build, and the islands fold
- **Change:** (1) `kicad_normalize` pass 5, `_merge_islands`: same-net zones on
  one layer fold into the one with the largest outline polygon — its outline
  gains theirs (a zone's polygons are a union), their fills go
  (`islands_merged`, `island_polygons_kept` in the `kicad_normalized` note).
  Zones without `(layer …)`/net name and `(layers …)` zones are left alone.
  (2) The DRC gate runs `--refill-zones --save-board` on **every** build, not
  only repair rounds; `build.zonesRefilled` (bool) on every build,
  `build.repairMode.zonesRefilled` kept. `CIRCUIT_KICAD_REFILL=0|off|false|no`
  keeps the converter's fills (for measuring); the segfault fallback stands.
- **Why:** desk-cube-astra-run7 (fresh route, 59 zones): the converter's fill
  of the top GND plane covered 3109 mm² of a 2851 mm² board — 723 of the 770
  DRC errors were that one zone's fill (479 clearance, 134 mask bridge, 110
  hole clearance); the 57 islands contributed 11. Dropping small islands
  changed nothing (773 → 773). `--refill-zones` exit -11 with the islands
  present, exit 0 with only the planes: the same file refilled reads **41**
  errors, all real (the QSPI cluster). Measured on six more cached boards
  (run 6, dc-6, Claude desk cube, three pomodoros): errors never up
  (6 → 0 on run 6, equal elsewhere), warnings down 10–45 on every one, no
  segfault. The pour of record is now what KiCad cuts around the final
  copper — the v2 posture, one layer early.
- **Backward compatible:** yes for readers (one added bool). Gerbers of a
  rebuilt board change: the pour is KiCad's, not the converter's. Frozen
  boards are not rebuilt.
- **Tracks affected:** pipeline (`kicad_normalize.py`, `generation.py`), skill
  runtime (re-vendor; SKILL.md).

## 2026-09-11 — v1.7a: the placement ruler
- **Change:** (1) `circuitpy.placement.score(elements)` — from the IR, routed
  or not: `pins`, `nets`, `ratsnestMm` (MST per net), `crossings` (ratsnest
  edges of different nets that intersect), `congestion` (pins per 5 mm cell,
  worst/mean), `decoupling` (each ground/power cap to the nearest chip pin on
  its power net; `overLimit` at 3 mm), `crystals` (to the nearest chip pin),
  `connectorsToEdgeMm`, `longestNets`. Lands as `build.placement` in the
  sidecar with one `placement_summary` info finding, as `placement` in
  `preflight` and `fastcheck` output (fastcheck: on the geometry as moved).
  (2) `<stem>_review/_placement.png` — the board without its copper, one
  hairline per ratsnest edge (`render_placement.cjs`); written by every
  build (`artifacts.placementPng`) and by `preflight` into the project's own
  review dir (`placement_png`). (3) Repair mode skips `pour_clearance` when
  the KiCad refill is on (`repairMode.postRoutePasses` says which).
- **Why:** run 7 (Astra, desk cube, 2026-09-11) called `preflight` 62× and
  `fastcheck` 11× — the agent iterated placement against a ruler that grades
  overlaps and price tier, not routability — and routed to 190 vias / 789
  jogs. On that board the ruler reads 240 crossings, 10 decoupling caps over
  3 mm (C14 9.9 mm from U4), worst cell 15 pins; the picture shows U4 across
  the board from U3. Nothing new blocks; the loop that pushes these down is
  v1.7b (driver placement phase, block `layout` overrides).
- **Backward compatible:** yes — added blocks, an info finding, one image.
- **Tracks affected:** pipeline (`placement.py`, `placement_image.py`,
  `_js/render_placement.cjs`, `generation.py`, `fastcheck.py`,
  `preflight.py`), skill runtime (re-vendor; SKILL.md).

## 2026-09-11 — v1.7.1: the back side, on purpose
- **Change:** (1) `product.json` gains `assemblyTier`: `economic` (default;
  one side) or `standard` (both sides, finer pitch, rails + fiducials);
  `ResolvedProduct.assembly_tier`; anything else is a `SpecValidationError`.
  `verify_bridge.check_circuit_json(assembly_tier=…)` hands it to
  verifylib's `assembly.check(tier=…)`, which already knew both tiers —
  `dfa_bottom_side` stays an error on economic and is silent on standard.
  (2) Golden block `rp2040-core` takes `layer="bottom"` and `pcbRotation`
  at block level (every inner part follows) besides the `layout` overrides.
  (3) The placement ruler scores per side: `pinsBySide`,
  `congestion.bySide`, `congestion.worstSide`. (4) `build.craft` gains
  `viasByNet` (top 5) and `pairVias` (every DP/DM pair's via count; the
  number to push to is 0). (5) Prompts: PLAN allows two sides when the
  face is spoken for, as a block, with the cost named, and asks for four
  corner mounting holes by default; IMPLEMENT orders place → preflight →
  picture → route, and treats an assembly finding as a 0.4 mm move + a
  preflight, not a rebuild.
- **Why:** the harness-free Astra desk cube (2026-09-11) put the MCU core
  on the back and kept the face for the LED ring, buttons, sensors and an
  OLED on standoffs — twice the area on the same 54 mm. On run 7's board,
  flipping Astra's own RP2040 block to the back with the new prop: worst
  cell 15 → 11 on the front, crossings 240 → 220, before any router runs.
  Our single-side rule was a cost rule (economic PCBA) enforced as if it
  were physics; it is now the plan's decision with the price named.
- **Backward compatible:** yes — the field defaults to economic; added
  score keys. The shipped router's two-sided limit (2026-09-08) stands and
  is measured, not assumed — see the BOARD entry.
- **Tracks affected:** pipeline (`spec.py`, `verify_bridge.py`,
  `generation.py`, `fastcheck.py`, `placement.py`, `craft.py`), golden
  blocks, server prompts, SKILL.md.

## 2026-09-11 — v1.8: Freerouting is the router, routerlib patches, the compiler's copper is the last resort
- **Change:** (1) `toolchain/freerouting/` holds the pinned Freerouting jar
  (2.4.1) and the JRE it needs (Temurin 25, class-file 69);
  `toolchain.freerouting_jar()` / `java_exe()` / `run_freerouting()`;
  `toolchain.versions()["freerouting"]` (export-cache keys change once).
  Installed by `scripts/toolchain/install-freerouting.sh`, never committed.
  (2) `routerlib.specctra`: `write_dsn(problem)` — every pad its own one-pin
  image at the origin, net ids as net names, rails in a `power` class at
  the power width, keep-outs and non-pad drills as obstacles with their hole
  clearance — and `read_ses(text, problem)` back to a `RoutingSolution`.
  (3) `router_bridge.engine()` unset now means **freerouting when the
  toolchain has it**, off otherwise; `CIRCUIT_ROUTER=off|shipped` keeps the
  compiler's router. The engine runs Freerouting at the rules' target
  (0.147 mm clearance, 0.2 mm signal) and, if nets stay open, once more at
  the floor plus a hair (0.11 / 0.15), keeps the more complete, hands the
  open nets to routerlib's relay with Freerouting's copper as obstacles, and
  fills whatever is still open from the compiler's own copper — so the board
  leaves the stage connected and the DRC gate says whether that last copper
  can stay. Pours no longer block the stage: their stale holes are dropped
  (`poursReset`) and KiCad's refill re-cuts them. `build.router` carries
  `freerouting.runs[]`, `patch`, `filledFromIncumbent`, before/after.
  (4) `build-skill-runtimes.sh` vendors `routerlib` beside `circuitpy`.
- **Why:** run 8 (fab-ready, 153 vias) re-routed through the bridge: 
  Freerouting 38/40 nets at 87 vias in 87 s (35/40 at the target rules), the
  last two nets (V3_3, one pixel hop) from the compiler's copper → 40/40 at
  **120 vias**. Codex's harness-free board did exactly this — Freerouting,
  then its own A* for what was left — and reached 83.
- **Backward compatible:** builds on a machine without the jar are
  unchanged; with it, the copper of record changes (fewer vias, Freerouting's
  geometry) and every gate still grades it.
- **Tracks affected:** toolchain, routerlib, pipeline (`router_bridge.py`,
  `toolchain.py`), build script, SKILL.md.
## 2026-09-14 — Harness: the verdict beside the sidecar, `.harness/` skipped, a viewer-only server
- **Change:** three additive pieces so Autonomous Circuit installs as a Harness
  *domain-specific harness* (the `harness.json` manifest at the repo root; the
  contract is `autonomous-harness/dsh/spec`). (1) `build_board()` writes
  `<project>/.harness/verdict.json` right after the `.board.json` sidecar —
  spec-1 shape `{spec, ready, summary, findings[{severity, kind, message, ref}],
  artifact, updatedAt}`, derived from the sidecar by
  `circuitpy.generation.harness_verdict` (`ready` is `fab.ready`, nothing
  weaker; `findings` are `validation.warnings` with `part→ref`, `detail→message`).
  Atomic, best-effort, never fails a build; a failure is one stderr line so the
  stdout JSON line stays clean. A build that dies before the sidecar (compile
  error, timeout, missing pipeline) gets the same file from the skill CLI
  instead — `scripts/circuit/cli.py` `write_failure_verdict`, `ready: false`,
  `summary: "Build failed: <CODE>"`, one error finding — so a host never keeps
  showing the previous build. (2) `.harness` joins the catalog/snapshotter
  skip-list next to `.circuit` (`viewer/src/server/circuit/projects.mjs`). (3)
  `createCircuitServices({ workspaceDir })` / env `CIRCUIT_WORKSPACE`: the
  server serves that one folder as the single project `workspace`
  (`createWorkspaceProjectStore`), reports `viewerOnly: true` on
  `app_settings_read`, and answers `chat_start_turn`, `chat_approve_plan`,
  `chat_request_plan_changes`, `chat_cancel_turn`, `chat_session_create`,
  `project_create`, `project_rename`, `project_delete` with `VIEWER_ONLY` (409).
  The client, on `viewerOnly`, skips the wizard and renders the board workspace
  full width with no chat sidebar, menu row, account card or "Send to AI".
  Skill docs resolve their own location through `CIRCUIT_SKILLS_DIR`
  (default `~/.claude/skills`), since Harness links skills into
  `<workspace>/.claude/skills/` rather than the global folder.
- **Why:** Harness (2026-09-14) runs `claude` in a terminal pane and puts this
  viewer in the pane beside it; it reads one verdict file per workspace for the
  pane header and must not start a second conversation on the same session.
  Four Autonomous products each re-implemented the same driver/chat/catalog
  shell; the shell moves to Harness and each product keeps its skills,
  pipeline and viewer.
- **Backward compatible:** yes — the sidecar, the stdout line, the artifact
  order and every existing command are unchanged; `viewerOnly` is absent (not
  false) outside the mode; the skill commands default to the global path.
- **Mechanism:** `generation.py` (+ `tests/test_harness_verdict.py`, one e2e
  assertion), **skill runtime re-vendor required**; `projects.mjs`, `http.mjs`,
  `server.mjs`, `main.jsx`, `BoardWorkspace.jsx`, `onboardingHelpers.js`,
  `transport.ts`; `harness.json`, `harness/AGENTS.md`, `harness/template/`,
  `harness/toolchain/{setup,doctor,init-workspace,viewer}.sh`.
- **Tracks affected:** pipeline, server, client, skills, docs.

(No further entries yet.)

## 2026-09-15 — v1.8: correct routing scale and make incumbent mixing opt-in
- **Change:** DSN coordinates and dimensions use 10,000 units/mm; via names
  continue to use whole micrometres. The SES reader supports the pinned
  Freerouting 2.4.1 output convention (100,000 units/mm with `resolution um 10`)
  and refuses missing or unsupported resolution declarations. A captured
  real-router DSN/SES pair protects physical dimensions in offline tests.
  `write_dsn` also accepts existing wiring, protection and a power-width override.
- **Change:** filling open nets with compiler copper now requires
  `CIRCUIT_ROUTER_FILL_FROM_INCUMBENT=1`. By default the existing connectivity
  gate retains the whole incumbent if the proposed route connects fewer nets.
  `filledFromIncumbent` is only emitted when mixing is explicitly enabled.
- **Why:** mixing copper routed against different neighbours can introduce
  shorts. Coordinate scale errors invalidate routing geometry and rule sizes.
- **Backward compatible:** sidecar shape stays compatible; default routing
  geometry and fallback behavior change. No v2 engine or cutover is included.
- **Tracks affected:** routerlib, circuitpy router bridge, circuitcode guidance.

## 2026-09-15 — opt-in native KiCad repair tools (v2 spike)
- **Change:** new packages/kicadpy exposes inspect/view/snapshot/apply/route/diff/
  check/commit/undo JSON tools for existing native KiCad projects. Native source
  revisions, candidate checks, UUID/region scope and byte-exact PCB undo have a
  separate contract in docs/architecture/kicad-native-spike.md.
- **Why:** prove persistent local repair on KiCad before generating new boards
  or changing the app's source and viewer adapters.
- **Backward compatible:** yes; v1 runner, sidecars and app dispatch are unchanged.
  The new tool does not publish fabrication-ready sidecars or export fabrication.
- **Tracks affected:** new native Python package, tests/fixtures and spike docs.

## 2026-09-15 — experimental prompt-to-KiCad app mode
- **Change:** `CIRCUIT_DEFAULT_ENGINE=kicad-native` marks newly created projects
  with `project.json.engine`. The marker survives rename/touch/reopen; existing
  unmarked projects retain v1. Native projects use native planning/implementation
  prompts and an independent post-turn check/preview publisher, without v1's
  best-build or repair-review loops.
- **Change:** native `design/<stem>.kicad_pcb` catalog entries expose SVG previews
  and a derived `boards/<stem>.board.json`. Native source changes invalidate
  previews. Native sidecars keep `fab.ready=false`; only CAD-check results are
  reported. Native canvas placement edits remain disabled.
- **Why:** allow the user to paste a brief and exercise native authoring through
  the app, while retaining revision-bound checks and honest readiness states.
- **Backward compatible:** v1 dispatch and existing project metadata defaults
  remain unchanged. `app_info.defaultEngine` reports the default for new projects.
- **Tracks affected:** projects, driver, catalog, viewer board detection/banner,
  new kicadpy preview publisher and workflow guidance.

## 2026-09-15 — native 3D previews
- **Change:** native preview bundles optionally contain `board.glb`, exported by
  KiCad from the checked copy and exposed through the existing `artifact.glbUrl`.
  GLB headers, length and mesh content are validated; export failure adds a warning
  without discarding checked SVG previews. Source changes invalidate 3D as well.
- **Change:** PCB SVGs exclude the drawing sheet. Native verdict text explains
  the missing verified fabrication packet instead of suggesting another rebuild.
- **Why:** make native designs reviewable in the existing 3D tab without implying
  manufacturing readiness from a preview or clean ERC/DRC.
- **Backward compatible:** existing v1 artifacts are unchanged. Native fab.ready
  stays false; native verdicts cannot claim orderability.
- **Tracks affected:** native publisher, catalog, verdict copy and regression tests.

## 2026-09-15 — native prototype manufacturing and review
- **Change:** native implementation now runs an independent manufacturing
  publication plus up to two separate native engineering review/repair rounds.
  Cancellation/provider failure never records a completed review. An unchanged
  source/finding count stops the loop; each successful round republishes.
- **Change:** optional `--manufacturing` exports Gerber/drill, BOM/CPL, manual
  assembly data, native positions, source archive, ORDER.md and parsed reports.
  The checked copy is reconciled with an independent Gerber/Excellon parser;
  a separate DRC applies the factory copper floor after project exceptions.
- **Change:** `manufacturing.json` and hashed `engineering/` evidence are source
  inputs. Seven engineering areas and explicit population/orientation choices
  are required. Prototype readiness does not claim tested hardware.
- **Change:** catalog artifacts include manufacturingReportUrl and use the
  existing packet URLs. nativeManufacturingVerified requires current inputs,
  intact packet hashes, parsed passing reports and the completed review journal
  for that publication. Missing/stale/corrupt data keeps ordering disabled.
- **Why:** allow native projects to progress toward a reviewable prototype order
  without promoting CAD cleanliness or an agent statement to manufacturing pass.
- **Backward compatible:** v1 remains unchanged. The shared verifier exposes
  check_parsed and optional pad mask/paste requirements for the native adapter.
- **Tracks affected:** kicadpy manufacturing/evidence/packet logic, verifylib
  adapter, native driver/review journal, catalog, board actions and verdict UI.

## 2026-09-16 — the native publisher reports; only the review loop sets `fab.ready`
- **Change:** `kicadpy.publish --manufacturing` no longer copies
  `native.manufacturing.prototypeReady` into `fab.ready`; the sidecar leaves the
  app's publisher with `fab.ready=false` every time. `runNativeReviewLoop`
  (nativeEngine.mjs) rewrites each native `boards/*.board.json` at the end of the
  loop with `fab.ready = verified && prototypeReady`, where `verified` requires a
  completed round, a fresh attestation naming this exact `source.fingerprint`,
  no interruption and no cancellation. Any later publish resets the gate.
- **Why:** contract §"fab-ready" says a board is complete only when `fab.ready`
  is true, and every reader outside the viewer (ledger, zips, the design-review
  skill, a human opening the file) trusts that flag alone. PR #34 commit b5144a8
  had the publisher set it from the packet checks, so a native board with a
  passing packet and *no* independent review read as orderable on disk while the
  app said otherwise (the 2026-09-15 CHANGES entry above even states native
  sidecars keep `fab.ready=false`). Found in review 2026-09-16.
- **Backward compatible:** yes for v1 (untouched). For native boards the flag
  can only become true through the loop; nothing else changes shape.
  `publish` adds `prototypeReady` to its stdout result so callers still see the
  packet verdict.
- **Mechanism:** `packages/kicadpy/src/kicadpy/publish.py`,
  `viewer/src/server/circuit/nativeEngine.mjs` (+ test), spike doc and README
  wording. No skill runtime re-vendor (kicadpy is not vendored).
- **Tracks affected:** pipeline (kicadpy) / server / docs.

## 2026-09-17 — zero error findings is the native order gate; the attestation is shown, not required
- **Change:** reverses the gate half of the 2026-09-16 entry. `kicadpy.publish
  --manufacturing` sets `fab.ready = prototypeReady` (zero error-severity
  findings across native ERC/DRC/parity, the factory-floor DRC, the independent
  gerber read, part identities, assembly decisions and the seven hashed
  engineering areas). `catalog.nativeManufacturingVerified` = intact packet +
  parsed passing reports for the current source; the review journal becomes
  `entry.nativeReviewState` (`verified` / `ready-unattested` / `blocked`) and
  no longer gates. `runNativeReviewLoop` republishes once after an interrupted
  round so a finished turn never leaves a stale or `running` sidecar.
- **Why:** owner's rule 2026-09-17 ("không có error thì cho nó xanh luôn").
  On the Claude Pet board the packet was clean at 14:17 but the app kept saying
  a completed independent review was required: the journal gate depended on
  in-process server state that a partial hot reload had left stale. The
  reviewer round is still valuable (it measured and widened four 3V3 necks)
  and still runs; it just cannot be the only thing standing between a clean
  packet and the order button.
- **Backward compatible:** v1 untouched. Native boards published before this
  entry become ready on their next publish if their packet is clean.
- **Mechanism:** publish.py, nativeEngine.mjs (+ tests), catalog.mjs,
  plainLanguage.js wording. No skill re-vendor.
- **Tracks affected:** pipeline (kicadpy) / server / client / docs.

## 2026-09-17 — native design inputs are the design dir, engineering/ and the root JSONs
- **Change:** `kicadpy.project.manifest` and the catalog's staleness check
  ignore every other path in a workspace (`build/`, `tools/`, scratch
  `*.kicad_pcb` copies). `design_dir()` finds the directory holding the
  `.kicad_pro` (the root itself, or one child).
- **Why:** an agent's candidate copies under `build/cand/` changed the
  revision, marked the board stale (previews hidden) and produced
  `engineering_stale` on a review that matched the real design — twice on
  Board mới, 2026-09-17.
- **Backward compatible:** yes; revisions of workspaces without scratch copies
  are unchanged. A workspace that had scratch copies gets a new revision on
  its next publish.
- **Mechanism:** project.py, catalog.mjs, one kicadpy test.
- **Tracks affected:** pipeline (kicadpy) / server / docs.

## 2026-09-17 — a plan turn that ends in prose still proposes its plan
- **Change:** when a PLAN turn ends with no ```circuit-plan fence, no
  ExitPlanMode and no questions (and was neither cancelled nor cut by the
  clock), the driver takes the turn's final text as the plan: it emits
  `plan_proposed` with that text and autopilot chains the build as usual. A
  text carrying a ```circuit-questions fence is still a question, not a plan.
- **Why:** three native plan turns on 2026-09-17 ended with a complete plan in
  prose (the model believed ExitPlanMode was missing and wrote the plan to a
  file); each needed a manual `chat_approve_plan`. The 2026-08-10 fence was
  meant to remove exactly this dead end; this closes the remaining hole.
- **Backward compatible:** yes; fenced plans and questions behave as before.
- **Mechanism:** driver.mjs `spawnTurn` (+ two driver tests).
- **Tracks affected:** server / docs.

## 2026-09-18 — Solder: the KiCad-native pipeline as a Harness domain harness, and its verdict
- **Change:** (1) `kicadpy.publish` writes the Harness verdict
  `<workspace>/.harness/verdict.json` (DSH spec 1) at every sidecar write —
  running, complete, failed — derived from the native `.board.json` sidecars by
  the new `kicadpy.harness` (`ready` is `fab.ready`, nothing weaker; `findings`
  are `validation.warnings` with `detail→message` and `ref` from `ref`/`part`/
  the native finding's reference; `phases` Build / Checks / Fab, folded to the
  worst board). `python -m kicadpy.harness [--active build|checks|fab]` writes it
  on demand so the agent can mark a phase before the first publish. Atomic,
  best-effort, never fails a publish; a failure is one stderr line so the
  publisher's stdout JSON line stays clean. (2) `harness/kicad/` — the **Solder**
  package (`autonomous/solder`, category PCB): manifest, `AGENTS.md` (plan →
  build → the agent's own two review rounds → done means `fab.ready`), the
  `solder` skill card, a template whose marker is `project.json` with `engine:
  kicad-native`, and a toolchain whose `setup.sh` vendors Freerouting and builds
  the viewer, whose `doctor.sh` FAILS without kicad-cli and pcbnew, and whose
  `viewer.sh` runs the viewer-only server with `CIRCUIT_DEFAULT_ENGINE=kicad-native`.
- **Why:** the team moves onto Harness (openharness). Copper (`harness/`, PR #35 on
  main) wraps the v1 tscircuit pipeline; a second harness carries the KiCad-native
  one so the two never share a trunk. Under Harness there is no driver: the
  native review loop (`runNativeReviewLoop`) and the 40-minute plan clock do not
  run, so the loop is protocol text the agent follows and the verdict is the only
  thing the host reads. Harness spawns codex without a sandbox, which is what
  made every Astra build report `gate_did_not_run` in the app.
  (3) `scripts/toolchain/install-freerouting.sh` exited 1 on EVERY run — after a
  successful install — because under `set -eo pipefail` `ls a b | head -1` fails
  when the platform's other glob is unmatched (the `JAVA=` assignment) and
  `java -version | head -1` takes a SIGPIPE; the presence test had the same
  glob and re-downloaded the JRE every time. Harness reported `setup exited 1`
  twice with the files on disk. Now a `find_java` loop, a captured version
  string, and `curl -fsSL --retry 3` so a failed download says so.
  (4) The viewer-only mode (`CIRCUIT_WORKSPACE`, main's 9f864fd from PR #35) is
  cherry-picked onto the v2 branch — it was never there, so Solder's pane opened
  the FULL app over the global project store (71 projects, chat, wizard) beside
  the agent, a second server on the same store as the one on :4179. The
  workspace project store now also carries `engine` from the workspace's
  `project.json`, because the client keys its native-only surfaces on
  `project.engine === "kicad-native"` and a Solder workspace rendered as v1.
- **Backward compatible:** yes. The sidecar is unchanged; the verdict is an added
  file the app ignores. Publish behaviour and its stdout line are unchanged. The
  installer vendors the same pinned jar and JRE; it just exits 0 when it did.
- **Mechanism:** `packages/kicadpy/src/kicadpy/harness.py` (+ hook in
  `publish.py`), `packages/kicadpy/tests/test_harness_verdict.py`,
  `harness/kicad/**`, `harness/kicad/tests/test_package.py`,
  `scripts/toolchain/install-freerouting.sh`.
- **Tracks affected:** pipeline (kicadpy) / harness package / toolchain scripts / docs.

## 2026-09-18 — the KiCad-native harness is named after KiCad
- **Change:** `harness/kicad/` is the **KiCad** tile (`autonomous/kicad`, `formerly:
  ["autonomous/solder"]`), the skill is `kicad`, and the agent's environment is
  `KICAD_HARNESS_PYTHON` / `KICAD_HARNESS_ROOT` / `KICAD_HARNESS_BLOCKS` (never a bare
  `KICAD_*`, which is KiCad's own namespace). Nothing else moves.
- **Why:** the store names a wrapper after the open-source project it wraps when the
  wrapper changes little of it (Blender, Typst, Marp); the owner's team asked for the
  same here (2026-09-18), and Dee had already renamed Copper to Autonomous Circuit.
  `formerly` keeps a machine that installed `autonomous/solder` from the merged
  openharness #91 on the same install.
- **Backward compatible:** for agents, no — a workspace created under the old env names
  keeps running only until its next session reads the new AGENTS.md; nothing on disk
  depends on the names. For the store, yes via `formerly`.
- **Mechanism:** `harness/kicad/**` (text, manifest, skill folder), one docstring in
  `kicadpy/harness.py`.
- **Tracks affected:** harness package / docs.

## 2026-09-21 — viewer-only: a "New board" button that asks the Harness daemon
- **Change:** in viewer-only mode (`CIRCUIT_WORKSPACE`) the board header shows one
  **New board** button in place of the account card, and the server gains one
  command, `harness_new_board` (viewer-only; `UNKNOWN_COMMAND` elsewhere). It
  picks the next free sibling folder (`<parent>/<base>-N`, N from 2, a trailing
  `-N` on the current name is not nested), makes it, opens the Harness daemon's
  loopback WebSocket (`ws://127.0.0.1:18473/api/local-ws`), `machine_select`
  with the local machine from `~/.harness/cli/data/machines.json`, then
  `agent_create { engine, cwd, dsh: $HARNESS_DSH, bypassPermission: true, permissionMode:
  "full" }` — on daemon 0.2.58 `bypassPermission` alone is the "auto" mode (codex:
  `--approve-for-me`, a reviewer model over a workspace-write sandbox, under which a
  kicad-cli DRC hung in an uninterruptible exit for two hours); "full" is the unsandboxed
  mode our pipeline needs — with the engine read from
  the installed manifest at `$HARNESS_DSH_DIR/harness.json`; answers
  `{ agentId, cwd, name, dsh, engine, dshName, hint }`. A refusal is
  `HARNESS_NEW_BOARD_FAILED` (502) with the daemon's reason and the folder is
  removed again. `viewer/src/server/circuit/harnessHost.mjs`; the socket
  factory is injectable (`createCircuitServices({ harnessConnect })`). The
  viewer-only pitch text now says the prompt goes in the terminal beside the
  pane, not "in the chat".
- **Why:** the owner (2026-09-18): a new board took ⌘N → tile → machine →
  folder → engine, and the pane's old PROJECTS "+" is gone by design. Harness
  has no hook for this, but its daemon makes harnesses for the desktop over
  this socket (openharness `store/tools/dsh-e2e.mjs` drives it the same way),
  so the pane asks the daemon. **Known hole, by design of the host:** the
  daemon does not place the new pane in a tab — the desktop does — so the hint
  says ⌘O. The button saves four of five steps, not the fifth. The engine must
  be the installed tile's base or the daemon answers `INVALID_DSH` (measured:
  a stale copy of the manifest saying `claude` against an installed `codex`).
- **Backward compatible:** yes. Nothing changes outside viewer-only mode; the
  sidecar, the verdict and every other command are untouched.
- **Mechanism:** `harnessHost.mjs` (+ `harnessHost.test.mjs`, 6 tests), `http.mjs`
  (+ one `http.test.mjs` case: created / refused / absent), `transport.ts`
  (`harness_new_board`, `NewBoardResult`), `NewBoardButton.jsx`,
  `BoardWorkspace.jsx`, `StartHere.jsx`. Verified live against the daemon on
  this Mac: a `pet-2` harness on codex, materialized, then deleted.
- **Tracks affected:** server / client / docs.

## 2026-09-21 — the native board's size is its outline, not its outline's pen
- **Change:** `kicadpy.worker` reports `boardBoundsMm` from the closed Edge.Cuts
  polygon (`GetBoardPolygonOutlines`) and says so in `boardBoundsSource:
  "outline"`; the edges bounding box (which adds the drawing stroke) is only the
  fallback for a board with no closed outline (`"edges"`).
- **Why:** with a 0.05 mm pen the old box said a 40 × 40 board was 40.05 × 40.05,
  and the packet's independent gerber read then reported `gerber_scale_mismatch`
  "both axes scaled by 0.9988 — a coordinate-format or units error" on a correct
  packet (two Astra runs, 2026-09-21; one of them monkeypatched the publisher
  locally to get past it, which is the wrong place for the fix). The check was
  right to compare, wrong about what it compared against.
- **Backward compatible:** yes for a board with a closed outline (the number is
  now the true one; `manufacture.packet_outline`, `verifylib.gerber_truth` and
  the viewer's bounds all consume it); a board without one is unchanged.
- **Mechanism:** `packages/kicadpy/src/kicadpy/worker.py`, one test in
  `test_transactions.py` (runs real KiCad; skips without it).
- **Tracks affected:** pipeline (kicadpy) / docs.


## 2026-09-22 — bounded native build continuation and incremental repair commits
- **Change:** KiCad tile supplies Codex Stop/Interrupt hooks. `kicadpy.harness --active build`
  arms `.circuit/autofinish.json`; `kicadpy.autofinish` verifies fresh manufacturing publications
  and requests at most eight repair continuations within four hours, respecting interruption.
  Native `commit` accepts optional `allow_improvement: boolean` (default false) and returns
  `acceptance` and `remainingFindings` in addition to its existing fields.
- **Why:** Astra stopped after two review rounds; local fixes on a board with unrelated existing
  errors could not commit (2026-09-22). User requested Circuit-only autonomous completion.
- **Backward compatible:** yes for native transaction callers; strict passing commit remains the
  default. Tile build marker additionally arms continuation. Hooks require explicit Codex trust.
- **Mechanism:** Harness manifest args, kicadpy autofinish/harness/engine, tile instructions.
  Improvement commits compare a freshly checked baseline copy against the hashed candidate report:
  exact findings must strictly decrease, with no new findings or changed coverage/ignored checks.
  No manufacturing gate changes; no circuitpy runtime re-vendor required.
- **Tracks affected:** pipeline / skills / docs / Harness tile packaging.

## 2026-10-13 — Case downloads and Print all in the Harness webview
- **Change:** `export_case {id, stem, part?, format: stl|step|3mf}` saves Case files in `CIRCUIT_EXPORT_DIR` or Desktop, returning `{path, dir, filename, bytes, canOpenBambu}`. `export_open_bambu {path}` opens an exported 3MF in Bambu Studio on macOS. `CIRCUIT_BAMBU_BIN` is a local launcher override for tests/hosts. Existing `export_reveal` shows the saved file.
- **Why:** the Harness webview follows `<a download>` as navigation and shows STL bytes instead of saving a file. The person requested Print all and an easy handoff to Bambu Studio.
- **Backward compatible:** additive commands. Individual parts remain available as STL/STEP. Print all requires a current passing check for the selected board; old Case reports without `meshValidation: surface-v1` require a recheck because matching bounding boxes did not prove the printable STL matched the checked STEP.
- **Mechanism:** local server exports only manifest members inside real `case/out/`, avoids overwrites, and writes standard 3MF Core/OPC ZIP containing separate named printable meshes rotated by `printRotateDeg`, lowered to the bed and spaced apart. `printable: false` parts and legacy `DO_NOT_PRINT` references are excluded from printing and assembly collision checks, while their file hashes remain in the report. It exports geometry, without printer/material profiles or toolpaths, and never starts printing. The Case tab shows the saved path and Finder/Bambu buttons. Catalog status is bound to the selected contract and source freshness. CadQuery's existing VTK dependency compares STL/STEP sampled surfaces in both directions at the existing 0.5 mm tolerance. No circuitpy runtime re-vendor required.
- **Tracks affected:** pipeline / server / client / docs.

## 2026-10-14 — Print all for independently checked handwired cases
- **Change:** Case catalog adds `printStatus {state, reason, scope: pcb|handwired}`. PCB cases retain the existing board/source gates. A manifest declaring `verification.nativePCBCheckerApplies: false` and `verification.report` (relative to `case/out/`, confined to `case/`) uses a separate receipt generated with `$KICAD_CASE_PYTHON -m kicadpy.case check-print . [stem]` in `case/reports/print-check.json`.
- **Why:** a handwired assembly has repositioned modules and no carrier PCB. Comparing it with the original PCB enclosure contract produces irrelevant collision/interface errors and prevents export despite its mechanical report passing.
- **Backward compatible:** additive print readiness. The native Case phase, prototype verdict and PCB checks are unchanged; exporting a handwired package does not certify PCB fit or hardware operation.
- **Mechanism:** `check-print` requires a passing scoped mechanical report with no collision/error findings and current hash coverage of every STEP/STL, including references. Shared CadQuery validation independently checks printable STL/STEP surfaces, frame and oriented bed fit. The receipt binds the selected stem, complete manifest, all part files, mechanical report bytes and print intent. Changes or missing evidence disable Print all in both UI and HTTP. The UI labels handwired validation explicitly and starts with the unused PCB overlay hidden. No agent scripts are imported/executed; no slicing, printing or firmware action occurs.
- **Tracks affected:** pipeline / server / client / docs.

## 2026-10-14 — Prompts, Electric and Case workspace sections
- **Change:** The board workspace has three primary sections with contextual subviews. Prompts shows the original request and a current prompt containing chronological user updates. Electric retains all electrical drawings, parts, packet and firmware. Case contains the overview and 3D/download/Print all view.
- **Why:** the single long tab row mixed the electrical design, evolving request and mechanical assembly. A run's motion overview already explains its moving parts and wiring more clearly than a static assembly.
- **Backward compatible:** existing internal tab IDs/keyboard navigation remain supported; Electric Overview is still the initial view. `workspace_prompts` adds `current {text,at,source,file?}`. It prefers `prompts/current.md` or `PROMPT.md` within the workspace and appends newer user messages; otherwise it preserves the original request and follow-ups in time order, without interpreting questions as approved requirements.
- **Mechanism:** Case catalog exposes cache-busted `overviewUrl` from manifest `overview` relative to `case/out/`, confined to real `case/`; fallback files are `case/overview.html` and `case/motion/motion-view.html`. Case HTML is served with a sandbox CSP allowing inline scripts/styles for self-contained demos, but denying same-origin privileges, network requests, forms and base URL changes. The iframe also uses `sandbox="allow-scripts"`. Native PCB manufacturing verdict is displayed in Electric; handwired print gates remain unchanged.
- **Tracks affected:** server / client / docs.

## 2026-10-14 — Print all includes larger clip/coupon assemblies
- **Change:** Printable-object count uses the existing 128-entry manifest ceiling rather than a second 32-object export limit. Existing total STL byte and per-mesh triangle bounds remain enforced.
- **Why:** the live handwired case gained snap closures and coupons, reaching 38 printable objects. The extra export-only limit rejected a valid checked assembly.
- **Backward compatible:** previously supported packages retain the same geometry and format; no changes to mechanical or surface validation.
- **Mechanism:** removes the redundant printable-object cap in `caseExport.mjs`; independent ZIP/XML regression covers 40 objects and refuses a 129-entry manifest.
- **Tracks affected:** server / docs.

## 2026-10-14 — Interactive Case overview is a required post-3D step
- **Change:** The KiCad agent workflow and tool card require an interactive overview immediately after every case 3D build, in the same turn without another user request. The build calls its overview generator and declares the HTML path in the manifest.
- **Why:** the person wants future runs to automatically produce the useful motion/inside/wiring overview as part of the 3D deliverable.
- **Backward compatible:** no API, manufacturing/fit gate or firmware action changes. Existing motion-specific HTML paths remain supported.
- **Mechanism:** overview must use the final assembled geometry, phone-friendly self-contained HTML compatible with the existing sandbox, shell/wiring inspection and controls for actual checked motion axes. Rebuild it after repairs and verify it through Case → Overview before finishing. Static cases do not invent motion; animation does not certify physical fit.
- **Tracks affected:** agent workflow / tool card / docs.

## 2026-10-15 — Firmware includes a discoverable wiring guide
- **Change:** The firmware workflow requires `firmware/WIRING.md` in the same turn as firmware, linked from README and updated after pin/assembly changes. Catalog adds optional `artifact.firmware.wiringUrl`; the Firmware tab renders it prominently above README/source code.
- **Why:** the person lost the wiring instructions, which were in a case subfolder behind a relative README link. Firmware should carry the assembly instructions as a stable deliverable.
- **Backward compatible:** existing firmware/source/Flash behavior and build gates remain unchanged. Projects without WIRING.md retain their existing view. The guide URL remains discoverable even when the source listing is capped.
- **Mechanism:** exact module/variant and viewing direction, physical pin-to-pin connections verified against firmware/netlist, voltage/polarity/grounds, separate motor power and passives, unpowered checks and first-power-on expectations. Unknown identities remain assumptions. Markdown optionally resolves relative links/images against its source asset while retaining react-markdown's URL sanitization; Firmware README and wiring guide use this base. A changed project/URL clears old guide content immediately. No hardware action is performed.
- **Tracks affected:** agent workflow / tool card / server / client / docs.

## 2026-10-15 — Case instructions include assembly and print lessons
- **Change:** KiCad instructions require the packaged `kicad/references/case-assembly.md` before case design or repair. The handoff includes a numbered assembly sequence, insertion/tool access evidence, physically labeled test coupons, current variant links and an explicit reuse/reprint list.
- **Why:** actual assembly of the pet case exposed tight assumed module mounts, blocked fasteners, difficult joint insertion and coupons mistaken for product parts despite passing final-pose CAD checks. The final direct-drive revision improves the checked insertion path, but physical fit remains untested.
- **Backward compatible:** workflow documentation only; no API, checker threshold, manufacturing verdict, firmware or automatic hardware action changes. No part count or clearance from that one case becomes a universal rule.
- **Mechanism:** the main instructions and tool card link one skill-local reference covering owned hardware/control assumptions, simple mechanisms, staged assembly/service, mating latches, coupon separation, geometry-based shell reuse, stale nested manifest references and honest verification scope. Replaces the old universal clearance suggestion with per-interface fit assumptions and coupon validation.
- **Tracks affected:** agent workflow / tool card / docs.

## 2026-10-15 — Simple, rugged mechanics are the default
- **Change:** Main case instructions, tool card and assembly reference explicitly prioritize few practical parts, direct joints, stock hardware, ordinary tools and serviceable mounts tolerant of declared print/measurement uncertainty. Added cams, gear trains, linkages, custom springs or delicate catches require a named function that the simpler option cannot meet.
- **Why:** the person requested uncomplicated mechanics that are easy to print, assemble and repair. Preserve requested functions while avoiding unnecessary mechanisms and fine adjustment.
- **Backward compatible:** workflow documentation only; no checker, runtime, manufacturing or hardware-action changes.
- **Tracks affected:** agent workflow / tool card / docs.

## 2026-10-16 — Case acceptance includes attachments, fragile parts and rotation clearance
- **Change:** KiCad instructions require an explicit device-to-mount/frame-to-shell attachment chain, independent verification of frame retention and shell closure, preservation of working device mounts, and repair of small features reported breaking. Every mechanical repair triggers checks throughout intended rotation travel, including intermediate/combined poses with installed hardware, closed shells and wires.
- **Why:** physical assembly exposed a frame that did not attach properly to the case, missing TFT screw mounting, flexible servo supports and repeatedly broken small features. Fewer STL files and collision-free nominal geometry did not establish a usable assembly.
- **Backward compatible:** workflow documentation only. Existing checker thresholds, readiness/PCB gates, model outputs and firmware/hardware behavior are unchanged.
- **Mechanism:** main instructions and tool card point to detailed attachment, serviceability, print-orientation/strength assumptions, swept/sample clearance reporting and a user-performed unpowered dry-fit procedure. Physical slipping, breakage or rubbing reopens the relevant finding; CAD results are not relabeled as physical tests.
- **Tracks affected:** agent workflow / tool card / docs.

## 2026-10-16 — Moving case workflow validates the mechanism before covers
- **Change:** KiCad case instructions establish structural layout, device mounting, fixed/moving groups, stock actuator interfaces and wire routes before cosmetic covers. Check the accessible subassembly first, attach shells, then repeat complete-assembly motion checks. Repairs provide a small mating test kit and preserve physically working printed parts.
- **Why:** discussion of a simple two-axis reference pet highlighted that an accessible mechanism and real device mounts should drive the enclosure. Reducing STL count without preserving those connections had caused failed physical assembly.
- **Backward compatible:** workflow documentation only; no runtime, model, firmware, checker thresholds, manufacturing/export gates or agent orchestration changes. Static/direct-shell mounts do not acquire an unnecessary separate frame.
- **Mechanism:** the main instructions and tool card link a skill-local staged workflow. Reference images are limited to visible-layout evidence; dimensions, actuator feedback and full-travel claims need their own support. Physical dry-fit results remain separate from CAD, and inspect-only requests do not trigger changes to a run.
- **Tracks affected:** agent workflow / tool card / docs.

## 2026-10-16 — Standing flash authorization preserves the previous firmware first
- **Change:** The agent workflow honors explicit project-scoped permission to flash completed firmware automatically, without repeated confirmation. Before each write it pins the prior firmware and a verified recovery artifact, then verifies the new write/boot, restores serial services and rolls back failures. The user-selected stable checkpoint remains immutable.
- **Why:** the person requested automatic flashing after firmware work, with the previous version kept easy to restore. The former exception also instructed restoring the backup even after a successful update; successful authorized updates now remain installed.
- **Backward compatible:** no authorization is inferred for other projects; later no-flash instructions override standing permission. Flash-button behavior, target checks, firmware readiness and manufacturing gates are unchanged. No automatic hardware service or actuator operation is added.
- **Mechanism:** main instructions, tool card and a skill-local firmware-recovery reference cover a project FLASH-POLICY.md, exact source/config/binary hashes, device identity/readback, immutable recovery checkpoints, per-attempt receipts and failure restoration. Case instructions refer to that authorization policy instead of a conflicting unconditional no-flash rule. Viewer changes are comments only.
- **Tracks affected:** agent workflow / tool card / docs.

## 2026-10-17 — Preserve prepared print plates in Print all
- **Change:** optional `case/out/parts.json` field `printProject: {file, report}` selects a native slicer 3MF and its verifier receipt, with paths relative to case/out and confined to case/. The receipt binds project SHA256, current production STL hashes/rotations and exactly-once plate membership; coupons remain in a separate test project.
- **Why:** the generic mesh exporter flattened the agent's already-arranged Bambu projects into one virtual build area, forcing the owner to arrange plates again (17 October).
- **Backward compatible:** yes. Manifests without printProject retain the generic 3MF export. A declared stale/invalid project fails export instead of silently discarding plates. Existing case/print readiness checks still apply before either export.
- **Mechanism:** server caseExport.preparedPrintProject validates the binding and copies the exact native 3MF bytes through export_case. Tile AGENTS/tool card/self-contained assembly reference require plate generation and verification after every build/final repair; bind only verified production projects and rerun the normal checker after manifest changes. No slicer or physical print is launched by this validation.
- **Tracks affected:** server / skills / docs.

## 2026-10-19 — Structural repair intent, accessible nuts and play checks
- **Change:** tile AGENTS/tool card and self-contained case-assembly reference require a short joint brief with section/sketch before a long structural repair; distinguish continuous printed members from stock fasteners, fixed/free interfaces and the load path. Review inherited parts after architecture changes, check captive-nut insertion/retention/removal, and assess play/support retention separately from collision-free rotation using a representative supported subassembly.
- **Why:** printed pet feedback exposed loose wheel supports and buried nuts; a long repair used a steel threaded rod before the owner clarified a continuous printed axle. Existing generic simplicity/collision checks did not prevent those mistakes (19 October).
- **Backward compatible:** yes; instruction-only, no API/report schema or physical-test gate changes. Routine choices remain autonomous, and physical rigidity stays pending until observed; no new mandatory user approval.
- **Mechanism:** update the source tile instructions/reference; refresh linked tile and existing runtime instruction snapshot with backups. No model or firmware change is part of this instruction update.
- **Tracks affected:** skills / docs.

## 2026-10-19 — Reuse-first repair of already-printed parts
- **Change:** tile repair instructions prioritize minimum additional print time/material and preserve the actual printed revision/mating geometry. Evaluate small compatible replacements/caps/adapters using existing holes before redesigning bodies; check against the frozen printed baseline and lead with repair-only plates and explicit keep/reprint lists.
- **Why:** the owner clarified that the lower body takes too long to print again and requested a longer round axle plus small cap fitting the existing hole. A clean new integrated structure was solving against a replacement body instead of the physical assembly already owned.
- **Backward compatible:** yes; instruction-only, no export/readiness schema change. Full-kit 3MF remains available, with repair subsets kept separate and honestly labeled.
- **Mechanism:** self-contained assembly reference plus AGENTS/tool-card pointers; refresh existing project runtime/current prompt without altering CAD or interrupting its active repair. Explicit keep-body constraints persist until the owner changes them; incompatible replacements need a concrete reason rather than silent scope expansion.
- **Tracks affected:** skills / docs.

## 2026-10-20 — Preserve existing designs and close physical assembly findings
- **Change:** preservation/local repair is the default even without a repeated reuse request. Tile instructions require assembled dimension-stack checks, functional contact/print-orientation reasoning, robust serviceable mating features, and full-motion clearance of every lid rib/boss against actual installed revisions. Physical need for coarse sanding, forced assembly or tape remains an open defect.
- **Why:** owner reports six print iterations: robot runs, but passive wheel motion is uneven, a complex wheel stack leaves about5mm to compensate, an older head joint breaks, too many screws are needed, and a lid ridge prevents nodding. Owner explicitly requests compatible repair parts rather than discarding existing work.
- **Backward compatible:** yes; instruction-only, no runtime API/report gate or geometry changes. Necessary structural/service screws remain allowed; no arbitrary universal gap, latch size or strength claim.
- **Mechanism:** update AGENTS/tool card/self-contained case-assembly reference; refresh existing runtime instructions and send a scoped learning prompt without restarting current firmware/layout work or automatically redesigning mechanics.
- **Tracks affected:** skills / docs.

## 2026-10-21 — Frame, mounted devices and joints first; case covers last
- **Change:** KiCad design order is explicit: functional frame → real devices/fasteners/joints/wires → exposed structural CAD checks → removable case covers attached to frame → complete assembly/motion checks. Guide/overview must show the exposed frame and assembly sequence. Interfering cover ribs/bosses are repaired locally before changing proven mounts.
- **Why:** owner relayed the Robocon workflow of designing the frame, mounting equipment and completing fasteners/joints before wrapping it in a case; prior physical prints exposed weak supports and a cover ridge blocking neck travel.
- **Backward compatible:** yes; instruction-only, no new report/API/automatic physical-test gate. Existing printed structures stay protected by repair mode; a simple structural base may serve as the frame without redundant components. Actual unperformed physical tests remain pending.
- **Mechanism:** replace/refine the existing mechanism-first wording in AGENTS, tool card and self-contained assembly reference rather than adding a competing workflow; refresh linked live tile/current runtime with guarded backups.
- **Tracks affected:** skills / docs.

## 2026-10-21 — Opt-in contract-first Astra / Sol pilot
- **Change:** add an isolated experimental `kicadpy.parallel` CLI. A read-only Astra lead seals a versioned interface contract; board, firmware and functional-frame Sol workers use owned paths in separate worktrees. Actual native board publication and structural review precede firmware reconciliation and case covers; final native/firmware/case/Print-all binding checks and lead review precede a candidate result.
- **Why:** owner requested implementation of the deferred parallel-pipeline backlog in a separate PR after merging the frame-first instructions. The existing pet and default single-agent path remain in use while a measurable experiment becomes possible.
- **Backward compatible:** yes; no default tile/model/hook/viewer/API/verdict schema change. No automatic pilot, firmware flash, print, order or live-run migration. Physical validation remains pending.
- **Mechanism:** frozen prompt/instructions/checker hashes, revision-bound receipts, import ownership checks, bounded explicit execution/revision, partial-usage/error logs and parallel/sequential/single-builder controls. Existing native gates remain authoritative. Cost fields are unknown rather than inferred from subscription token usage; no speed/quality benchmark is claimed from offline tests.
- **Tracks affected:** Python experimental orchestration / KiCad tool card / docs.

## 2026-10-21 — Print-aware repair inventory and complete assembly handoff
- **Change:** KiCad instructions require a persistent project-local `case/PRINTED-INVENTORY.md`, updated from user confirmations and identifiable photos with revision/quantity/evidence/confidence and fit/breakage status. New/update-only repair projects lead the handoff; already-printed reference plates and new/updated plates stay separate, including within optional full-production projects. Retired geometry and TEST ONLY stay separate; current full-manifest coverage is preserved.
- **Why:** reviewing dog and pet conversations exposed a PCB-carrier/full-robot scope mismatch, substantial fastener counts, repeated clarification of old tread/rail intent, and the owner having to request screw dimensions/visuals at every assembly position. The owner explicitly asked to include these lessons and remembered print/plate separation in PR57.
- **Backward compatible:** instruction-only; no export/verdict/checker schema, threshold, default tile, flash/print/order authorization or existing-run geometry change. A 3MF export/open is not a print, and a photo does not prove hidden hardware or an exact unidentifiable mesh revision. No new automatic physical-test gate.
- **Mechanism:** one current decision/guide, instance-level fastener IDs/sizes/directions including retained assemblies, actual-versus-spare hardware budgets, actuator/wiring handoff, product-scope completion statements, module-input bias/slew review and stop-interlock production-handler tests. Existing preservation, reference-hardware and physical-evidence rules remain in force. This convention adds no inventory parser or automatic plate classifier; agents maintain the evidence and native projects under existing validators.
- **Tracks affected:** KiCad AGENTS / self-contained tool card and assembly reference / docs.
