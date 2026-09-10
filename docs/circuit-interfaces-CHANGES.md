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

(No further entries yet.)
