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

## 2026-08-12 — Planner registry mirrors every golden block API exactly
- **Change:** `circuitlib.blocks.BLOCKS["sw-tact"].props` adds the already
  supported `signalTraceWidthMm`, `variant`, and `layer` controls. The
  composition contract now derives `REGISTERED_BLOCKS` from the complete live
  registry and requires exact set equality with both `evals.composition`
  constructors and each golden TSX export; every required golden prop must be
  supplied by the constructor.
- **Why:** the prior API drift test covered only four USB/RP blocks. The
  Terminal expert's compact BOOTSEL/RESET requirement had reached the reusable
  switch source, but a future planner could not select that variant because
  its registry still advertised the older seven-prop surface.
- **Backward compatible:** yes for existing calls; the three registry props
  are additive and already accepted by the pinned golden block. Future API
  drift now fails earlier in structural CI.
- **Mechanism:** the planner registry is corrected at its source and
  `evals/tests/test_composition_contract.py` audits every registered block,
  with exact registry/composition set equality. No generated circuitpy or
  skill runtime copy changes, so no skill re-vendor is required.
- **Tracks affected:** planner / skills / evals / docs.

## 2026-08-12 — Sidecars content-address every routing-effort candidate
- **Change:** the sidecar `build` object adds `attemptEvidence`, one ordered
  record per initiated routing attempt. Completed records carry `effort`,
  `status="completed"`, the exact `circuitSha256`, parsed `blocking`,
  `routingBlocking`, and `blockingKinds`; a timed-out/failed alternate records
  `status="failed"`. `attempts` counts initiated attempts, while the legacy
  `blockingByAttempt` remains the ordered counts for completed artifacts.
  The state machine is exactly one completed primary plus at most one 5x
  alternate after a default primary; the alternate is selected iff its parsed
  blocking count is strictly lower. Literal authored efforts are recorded as
  written, dynamic authored effort as `authored`, and routing-disabled boards
  as `disabled`.
- **Why:** counts alone could not prove that a nominal 5x retry was an
  independently compiled candidate, and a failed retry previously left
  `attempts=1`. That made the bounded effort policy materially less auditable
  than its documentation claimed. The published board must now hash to the
  completed record selected by `autorouterEffort`.
- **Backward compatible:** no for committed evidence. Old sidecars without
  `attemptEvidence` are intentionally incomplete and must be rebuilt. Board
  source and manufacturing files are unchanged.
- **Mechanism:** `circuitpy.generation` records and validates attempt evidence;
  `examples_lock.py` and `review-packet` independently require it and compare
  the selected hash to `main.circuit.json`. Cold 1x/5x cache-isolation tests
  prove a preceding route cannot supply stale copper to the alternate. The
  live output directory is removed before retry, so a CLI failure that emits
  no artifact cannot be mistaken for a completed second attempt.
  Skill runtime re-vendor required.
- **Tracks affected:** pipeline / evals / skills / docs.

## 2026-08-12 — Imported golden blocks require a content-hashed project snapshot
- **Change:** a board that imports any project-relative `blocks/` entry must
  carry `golden-blocks.lock.json` schema 1. The lock names a sorted explicit
  block set and SHA-256 for every selected byte plus `glue.tsx`, including
  non-imported `BLOCK.md`/`REVIEW.md` and third-party license/provenance files.
  Its tree hash participates in `source.fingerprint`. Missing, malformed,
  changed, incomplete, unselected-import, path-traversal, and symlink cases are
  `ProjectShapeError`s before the tscircuit process starts.
- **Why:** a source fingerprint previously covered only imported TSX leaves.
  A project could silently hand-edit a frozen block or omit the manufacturer
  reference/license while still producing source-fresh-looking evidence. The
  three canonical examples also had divergent copied block trees with no
  machine-readable statement of which golden bytes they claimed to freeze.
- **Backward compatible:** no for projects importing `blocks/` without the new
  lock. They must be migrated through the synchronizer once, then rebuilt so
  the new lock-aware fingerprint and evidence are recorded. Inline boards with
  no project-block import remain valid without a lock.
- **Mechanism:** `scripts/sync_golden_blocks.py` copies an explicit selected set,
  preserves unmanaged project sources, writes the deterministic lock, refuses
  to overwrite locally modified managed bytes, requires explicit
  `--replace-unlocked` authority for a reviewed legacy adoption, and has
  local/upstream check modes. `circuitpy.block_snapshot` independently validates the lock at build
  time. `examples_lock.py` and `review-packet` repeat the read-only evidence
  gate. The synchronizer and circuitpy runtime are vendored into circuitcode;
  skill runtime re-vendor required.
- **Tracks affected:** pipeline / skills / evals / docs.

## 2026-08-12 — Sidecars pin the exact checks bundle
- **Change:** the sidecar `toolchain` object adds
  `checksBundleSha256`, the SHA-256 of the installed
  `@tscircuit/checks/dist/index.js`, alongside the existing core, capacity,
  and props bundle identities. Export-cache keys and source-fresh evidence
  comparisons consume the complete object, so this new field participates in
  both automatically.
- **Why:** the pinned checks package required an audited source-trace width
  scoping correction without an upstream semver change. Recording only
  `checks: "0.0.152"` would allow a sidecar created before the correction to
  look current after it, even though the acceptance verdict changed.
- **Backward compatible:** no for cached or committed evidence. Older
  sidecars lack the new identity and are intentionally stale; they must be
  rebuilt under the exact installed verifier. Board source and packet schemas
  otherwise remain unchanged.
- **Mechanism:** `circuitpy.toolchain.versions()` hashes the checks runtime;
  existing whole-toolchain cache/evidence comparisons invalidate on any value
  change. Focused tests pin the 64-hex shape and prove changing only this hash
  changes an export-cache key. Skill runtime re-vendor required.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-12 — Trace-width verification is scoped to exact authored route identity
- **Change:** the exactly pinned `@tscircuit/checks@0.0.152`
  `checkSourceTracesMatchPcbTraceThickness` first measures every `pcb_trace`
  carrying the source edge's exact `source_trace_id`. Connectivity-wide
  selection remains only as the fallback when no exact PCB identity exists.
  Multiple PCB traces claiming one source identity are all measured, so a
  duplicate thin route still fails closed.
- **Why:** a valid authored rail deliberately combines 0.2mm component necks
  and 0.8mm trunks in one connected tree. The upstream checker ignored each
  PCB trace's retained source ID and assigned the net-wide 0.2mm minimum to
  every 0.8mm trunk. The coherent RP2040 fixed-copper preflight produced twelve
  such false warnings even though each selected trunk route was uniformly
  0.8mm. Width verification must grade the authored edge, not the narrowest
  unrelated edge reachable through its electrical net.
- **Backward compatible:** intentionally tighter by identity and otherwise
  compatible. Exact authored routes stop inheriting unrelated net widths.
  Aggregate/legacy routes without an exact ID keep the previous connectivity
  fallback. Duplicate exact IDs remain fail-closed and are graded at their
  thinnest claimed copper.
- **Mechanism:** `scripts/build/apply-toolchain-patches.mjs` carries an exact
  package-version, source-map, input-hash and output-hash guarded stage.
  `scripts/tests/checks-source-trace-width-identity.test.mjs` proves the
  pristine false positive, the corrected mixed-width tree, the identity-absent
  fallback, duplicate-ID failure, syntax, and patch idempotency. No circuitpy
  or skill runtime source changed; no skill re-vendor is required for this
  patch itself (the checks-bundle sidecar hash change above is separate).
- **Tracks affected:** pipeline / toolchain / docs.

## 2026-08-12 — Decoupling distance supports cited ref-scoped vendor envelopes
- **Change:** `product.json.layout.decoupling` accepts optional
  `overrides: [{match, maxDistanceMm, source}]` rules in addition to the required
  product-wide `maxDistanceMm` and explicit `exclude`. `match` is one fnmatch
  component-ref pattern or a non-empty list; `source` is a required non-empty
  manufacturer reference URI or document identifier. An override must match a
  populated chip, cannot also match an exclusion, and overlapping rules use
  the strictest applicable distance.
- **Why:** the blanket 2.0mm default made the RP2040 block geometrically
  impossible while preserving its QSPI escape corridor. Direct measurement
  of Raspberry Pi's routed Minimal R3-S1 board found legitimate nearest
  supply-pad to bypass-pad centre distances from 2.618mm through 4.630mm.
  Treating a generic house preference as a silicon-vendor requirement drove
  repeated impossible placement searches. A cited component-specific bound
  is more truthful than excluding the IC or weakening every product.
- **Backward compatible:** yes. Products without overrides retain their exact
  existing bound. Malformed, unmatched, or exclusion-conflicting overrides
  fail earlier; wildcard overlap is deterministic and fail-safe. The authored
  port-to-port topology, populated-capacitor, measurable-pad, parsed-copper,
  and DRC requirements are unchanged.
- **Mechanism:** circuitpy validates the closed schema; the independent verify
  checker resolves rules against populated chip refs and measures each power
  pin using the strictest matching maximum. `circuitlib.layout.product_layout`
  validates and defensively copies the same rules for generator callers.
  Focused positive, overlap, unmatched, conflict, malformed, and copy tests
  pin the contract. Skill runtime re-vendor required.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-12 — Board-owned rail trees replace exact block boundary leaves
- **Change:** golden `UsbPowerEntry` accepts
  `externalFaultPullupPort="R32"`, and golden `StatusLed` accepts
  `externalRailAttachmentPort="R"`. Each option suppresses only the named
  block-to-rail leaf; the block's local signal, protection, value, and load
  topology remain intact. The consuming board must attach its authored tree
  at `.R32 > .pin2` or the status resistor's `.pin1`, respectively.
- **Why:** composing either block with one board-level V3_3 `PowerTrunk` left a
  second ordinary named-net leaf. Core then rebuilt those leaves as a wide
  aggregate MST, defeating the declared narrow local neck plus sole wide
  boundary topology and causing strict-width routing to stall.
- **Backward compatible:** yes. Omitting the prop preserves the standalone
  block's ordinary rail leaf. Supplying it without a replacement board tree is
  intentionally detectable as an unconnected rail attachment.
- **Mechanism:** a focused composition fixture proves that each option removes
  exactly one source trace, preserves every other source identity, and accepts
  one explicit board-owned attachment tree. The circuitlib registry exposes
  both props so generated boards cannot rely on an unregistered escape hatch.
  Skill runtime re-vendor required.
- **Tracks affected:** golden blocks / skills / docs.

## 2026-08-12 — PowerTrunk supports one explicit off-pad face transition
- **Change:** golden `PowerTrunk` retains its existing same-layer behavior and
  gains an all-or-nothing cross-layer mode. The board supplies `sourceLayer`,
  `trunkLayer`, the physical `sourcePoint`, and an off-pad `trunkVia`; optional
  limits default to a 2mm source neck, .8/.5mm via copper/hole, and .15mm
  via-edge-to-boundary-pad clearance. The helper rejects partial mode props,
  equal layers, non-finite points, invalid dimensions, overlength necks, and a
  via too close to either exposed boundary pad before compilation. It anchors
  the fixed .2mm source path to its board-owned start pad, carries one connected
  .8mm tree through the declared via, and retains one marked named-net edge.
- **Why:** a planner-generated power stack placed the LDO source and its wide
  board trunk on different useful corridors. Leaving the transition to an
  aggregate named-net route produced a three-endpoint .8mm connection and no
  stable way to retain the required .2mm local neck. A first fixed-path attempt
  anchored to the transformed LDO and double-applied its coordinates. Owning
  the path from the absolute probe pad makes the topology and geometry
  construction invariant.
- **Backward compatible:** yes. Calls without the new transition props take
  the original same-layer branch and emit the same two pads and three traces.
  Cross-layer calls are new and intentionally fail closed unless their physical
  points, layers, via geometry, and clearance are complete.
- **Mechanism:** `packages/golden-blocks/blocks/glue.tsx` owns validation and
  emission. A real AMS1117 bench proves a top TAB-to-probe .2mm neck of
  1.913172155mm, an off-pad .8/.5mm via, a .8mm bottom trunk, one acyclic
  source-to-net tree, zero parsed findings, zero independent routing checks,
  and zero layout-intent findings. Focused invalid-prop regressions preserve
  fail-closed construction and the legacy same-face fixture.
- **Tracks affected:** golden blocks / skills (re-vendor).

## 2026-08-12 — Golden route benches inherit the product clearance contract
- **Change:** every routing-enabled golden bench is machine-classified as an
  authoritative 0.15mm trace/via-to-pad proof, a named current blocker, or a
  geometry-only fixture. `UsbPowerEntry` now authors `TR_U7_en` as a
  transform-safe local `pcbPath`; `SensorBme280` similarly authors its CSB
  mode strap; the level-shifter bench supplies the plane its one-port GND
  fanouts require. `Ws2812Chain` replaces its aggregate V5 MST and named-net
  data leaves with an authored 0.8/0.2mm rail tree and direct 0.25mm pixel
  hops, each proven on both faces.
- **Why:** the previous power-entry bench inherited a looser default and
  passed, while the same 0.20mm EN connection failed composition by
  0.0024403mm at the real product clearance. Applying the real floors across
  the suite exposed two more local assumptions, a physically invalid WS2812
  V5 aggregate, and the still-open RP/QSPI topology. The WS2812 block was
  corrected upstream; RP remains a named blocker. A golden bench that proves
  a different design rule from its consumers is not evidence for a reusable
  block.
- **Backward compatible:** electrically yes for callers using the default
  nets and counts; the public dimensions are additive. The WS2812 block's
  internal source-trace identities, phase allocation, node refs, and physical
  pixel order intentionally change, so snapshots and consumers that named
  those internal edges must migrate. The power-entry and sensor same-face
  escapes retain their netlists while making the existing bounds and 0.15mm
  clearance deterministic on both faces.
- **Mechanism:** block-local vertices use `localX`; focused artifact tests
  require exact X/layer mirrors, widths, bounds, and zero parsed findings.
  `test_routing_board_contracts.py` asserts classification-set equality,
  requires both board floors for every authoritative source, and compiles the
  compact benches. Routed top and bottom WS2812 artifacts independently pin
  the direct data phases, own-face plane contacts, acyclic V5 source graph,
  widths, and mirror transform. The protected-power composition cell
  independently passes the same rule. Re-vendor the final golden blocks into
  skill runtimes.
- **Tracks affected:** blocks / skills / evals / docs.

## 2026-08-12 — Evidence locks validate structure; review publication requires fab readiness
- **Change:** `evals/examples_lock.py` now requires a typed validation block,
  boolean `fab.ready`, non-negative BOM line count, canonical
  `board.path = "main.circuit.json"`, the unconditional PCB/schematic review
  images, and every declared artifact to exist inside `boards/`. Invalid
  evidence becomes blocking `IncompleteSidecar` and cannot be accepted.
  `scripts/review-packet` is deliberately stricter: it enumerates every
  product project and publishes only when the sidecar proves clean validation,
  `fab.ready == true`, KiCad-sourced Gerbers, an orderable BOM, and all seven
  canonical packet artifacts.
- **Why:** source and toolchain freshness alone did not make a sidecar
  complete. An empty artifact manifest, missing validation/BOM/fab members,
  or an absent sidecar could be treated as zero blocking work; the review
  script also skipped projects without evidence instead of refusing them.
- **Backward compatible:** no for incomplete or non-ready publication inputs;
  they now fail closed. The regression lock still permits an honestly
  non-fab-ready board as a baseline measurement, while review publication
  intentionally does not.
- **Mechanism:** both consumers perform independent structural and contained-
  path validation before reading scores. Focused tests cover each missing
  member, missing files, unreadable roots, non-ready sidecars, absent project
  sidecars, and byte-identical `--accept` refusal. CI executes both suites.
  No skill runtime re-vendor is required.
- **Tracks affected:** pipeline / evals / docs.

## 2026-08-12 — A plan is not buildable until service nets are physically exposed
- **Change:** `circuitlib.helpers.board_plan()` accepts `exposed_nets` and
  `BoardPlan.buildable` is false while `must_expose` is non-empty. The block
  registry now carries the frozen required prop surfaces for USB raw entry,
  protected USB power entry, USB data pairing, and RP2040 debug/power nodes.
  `BLOCK_BOX_MM` covers every registered block, including the measured
  `usb-power-entry` box, and the composition fixture supplies every required
  hidden-node contract.
- **Why:** the planner already reported SWCLK/SWD in `must_expose` but ignored
  that tuple when computing `buildable`, so a caller could generate an MCU
  board that could not be programmed. Independently, `board_plan(power-usb)`
  selected `usb-power-entry` while placement had no box for it, and therefore
  raised after the supposedly valid plan was returned.
- **Backward compatible:** no for callers that treated a bare MCU plan as
  buildable. They must compose the real debug connector/probe and pass those
  actual nets through `exposed_nets`. Plans with no exposure obligation and
  already-complete placement metadata are unchanged.
- **Mechanism:** `circuitlib.helpers` folds `must_expose` into the buildable
  predicate; focused regressions prove the unresolved/resolved pair.
  `circuitlib.blocks`, `circuitlib.layout`, and `evals.composition` carry the
  updated frozen metadata, while `measure_block_boxes.py` now includes the
  compiler diagnostic when a measurement artifact is missing. Re-vendor the
  skill runtime with the final golden snapshot.
- **Tracks affected:** skills / evals / docs.

## 2026-08-12 — Drill clearance covers vias, slots, and foreign copper
- **Change:** `FabProfile` gains an independent
  `min_via_to_copper_mm = 0.20` floor. Stage 4's blocking
  `dfm_hole_clearance` gate models NPTH, PTH, and via drills as
  rotation-aware swept stadiums and measures them against trace capsules,
  SMD copper (rectangular, rotated, polygonal, and pill), and other via pads.
  Same-net, same-port, and own-feature copper is legal; unidentified copper
  is exempt only while wholly contained by that drill feature's annular pad.
- **Why:** KiCad found via-drill clearances of 0.132mm and 0.148mm after the
  earlier stage-4 check had passed, and the old circular approximation could
  erase 0.4mm from each end of a 0.8 x 1.6mm USB-C slot. A later-only DRC
  cannot trigger routing escalation, while a net-blind rule makes every
  connected plated hole look illegal.
- **Backward compatible:** yes for legal connected copper and geometry at or
  above the declared floors. Boards with previously invisible via/slot-to-
  foreign-copper violations now fail earlier and intentionally. Malformed
  geometry remains never-raise and cannot crash the fabrication report.
- **Mechanism:** `circuitpy.checks` builds the drill/copper geometry and emits
  localized, minimum-gap-deduplicated findings; `circuitpy.fab` supplies the
  distinct via floor and blocks the result. Permanent failure-corpus cases
  pin both observed via distances, the USB slot endpoint, and the own-pad
  exemption. Skill runtime re-vendor required because `packages/circuitpy`
  changed.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-12 — Product intent can reserve measurable component zones
- **Change:** `product.json.layout.componentZones` accepts a non-empty list of
  placement rules. Each rule has `match` (one fnmatch pattern or a list),
  `containment` (`"center"` or `"courtyard"`), and one board-coordinate
  `shape`: `circle` (`center`, `radiusMm`), `annulus` (`center`,
  `innerRadiusMm`, `outerRadiusMm`), or `rect` (`center`, `widthMm`,
  `heightMm`). Dimensions must be finite and positive, and an annulus must
  have `innerRadiusMm < outerRadiusMm`.
- **Why:** the fully populated Harness puck proved that a clean central-only
  fixture was insufficient: U7 overlapped one pixel sector and an RP2040
  decoupler overlapped another even though the 70mm outline had ample area.
  The design intent is annular—pixels belong in their reviewed ring and dense
  electronics belong in reserved central/gap zones—so a generic overlap
  error could detect the symptom but could not prevent the next placement
  from consuming the same corridor.
- **Backward compatible:** yes when `componentZones` is omitted. A declared
  zone is intentionally fail-closed: unmatched rules emit
  `layout_intent_component_zone_unmatched`; populated components outside the
  selected zone emit localized `layout_intent_component_zone`. Boundaries
  are inclusive, and courtyard containment uses the compiled rotated
  courtyard polygon with a component-body fallback.
- **Mechanism:** circuitpy validates and resolves the strict schema in
  `layout_intent.py`; independent `verifylib.intent` evaluates the compiled
  component geometry, including an annulus's inner void. Both finding kinds
  are blocking in `fab.py`. Self-contained
  `circuitlib.layout.product_layout(component_zones=...)` validates and
  deep-copies the same contract for generated projects. Focused schema,
  boundary, rotation, unmatched, wrong-zone, and planner-emission regressions
  pass. Skill runtime re-vendor required because `packages/circuitpy`,
  `packages/verify`, and the circuitcode runtime changed.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-12 — Fixed paths inside rotated blocks stay in one local frame
- **Change:** reusable or repeated block geometry must express `pcbPath`
  vertices in the owning component's local frame. Prefer
  `pcbPathRelativeTo` when a named local anchor exists. A composer must not
  pre-rotate those vertices into board coordinates before placing or rotating
  the component/group.
- **Why:** the eight-pixel puck supplied already-global capacitor-path
  coordinates to traces whose `from` components were rotated. Core applied
  the source-component transform again, sending copper to approximately
  `±46mm` and outside a 70mm board. The same local vertices compile into eight
  legal 1.8003–1.8005mm branches at all 45-degree orientations.
- **Backward compatible:** yes for paths already written in their source
  component's local frame. Pre-rotated paths intentionally move to their
  physically correct positions; parsed copper therefore changes on boards
  that depended on the double transform.
- **Mechanism:** the Harness ring source now owns one component-local
  pin-to-capacitor path and lets group placement rotate it exactly once.
  `skills/circuitcode/references/tsx-idioms.md` records the rule, and the
  isolated eight-angle artifact regression checks compiled endpoints,
  lengths, and board containment. Re-vendor the skill runtime with the final
  golden snapshot.
- **Tracks affected:** skills / docs.

## 2026-08-12 — Pipeline7 retries one failed layer-stack orientation without changing successful copper
- **Change:** pinned `@tscircuit/capacity-autorouter@0.0.782` preserves its
  original Pipeline7 attempt exactly. Only after that attempt reports failure
  on a board with at least two layers, it runs one non-recursive retry with
  every layer-bearing connection, obstacle, preloaded trace/via and structured
  option reversed through the layer stack. A successful retry is mapped back
  to the authored layer names and unchanged connection/port IDs, then must
  pass the existing final exact-DRC and native differential-pair gates. If
  both attempts fail, no copper is exposed and the error reports both causes.
  Retry-internal cache keys use `p7-layer-reversal-v1:`; pinned core's
  whole-phase cache descriptor separately gains
  `capacityLayerReversalRetry: "p7-layer-reversal-v1"`.
- **Why:** on 2026-08-12 an exact X/layer mirror of the golden USB direct pair
  failed only on the bottom face because RectDiff's greedy seed tie-break
  produced a different mesh. The literal X mirror of the successful top
  copper passed all bottom routing checks with unchanged width, clearance,
  skew and uncoupled-length metrics. Product nudges or manual copper would
  therefore encode a router ordering accident rather than engineering intent.
- **Backward compatible:** yes for every route that already solves: the retry
  object is never created and the cold top regression's serialized copper is
  byte-identical to the pre-patch output. A previously failing route may now
  succeed through the symmetric portfolio. A route impossible in both
  orientations still fails closed, and the whole-phase cache is intentionally
  invalidated once by the new semantic-version member.
- **Mechanism:** `scripts/build/apply-toolchain-patches.mjs` advances capacity
  `e7c2ab3d003ad010db4a648cfb15355256763c226bbf146f8f491640d321780c ->
  6d9e591861f3e6cc66af1cf86d230fdd0ac3a7673ec6f2565a2466527bf9a8b7`
  and core
  `8359d3082f85ccb2010810e8dfe9730fce9d2efb264d33aa96750d24d0a968d9 ->
  6e014654d0bf4ce38d400ddf15ed3c6042d166771b3bc4e308785db48167a37b`
  with exact hashes and source-map guards. The reduced cold regression in
  `scripts/tests/layer-reversal-retry.test.mjs` pins original identity,
  mirrored bottom success, IDs/layers, preloads/obstacles/options, exact DRC,
  pair postprocessing, dual-failure behavior, no-nominal legacy behavior and
  cache semantics. No circuitpy code changed, so skill runtime re-vendor is
  not required.
- **Tracks affected:** pipeline / docs.

## 2026-08-12 — Explicit routed widths are hard minima, not neck-down hints
- **Change:** pinned `@tscircuit/capacity-autorouter@0.0.782` now treats a
  connection's `nominalTraceWidth` as the minimum requested by its exact
  source trace. The effective target is `max(nominalTraceWidth,
  minTraceWidth)`. A route that cannot clear at that width fails during the
  trace-width phase; the solver may not retry a midpoint, fall back to the
  board floor, or taper an endpoint below the connection's request. A
  connection with no per-connection width retains its existing behavior.
- **Why:** on 2026-08-12 the golden USB CC traces entered Pipeline7 at 0.25mm
  but were deliberately reduced to 0.20mm and then 0.15mm. Restoring 0.25mm
  exposed real clearance conflicts, proving that post-hoc widening is unsafe:
  the router must either find legal full-width geometry or reject the route.
- **Backward compatible:** no for output that depended on an implicit router
  neck-down below an authored trace width. Such a route now fails closed and
  must provide a wider corridor or express each intentional narrow neck as a
  separate source connection with its own explicit contract.
- **Mechanism:** `scripts/build/apply-toolchain-patches.mjs` applies the exact
  guarded bundle transition
  `38b34259144c87a5040b02a4f3958760ead65a3700550e4b205d1dab642a5853 ->
  e7c2ab3d003ad010db4a648cfb15355256763c226bbf146f8f491640d321780c`.
  `scripts/tests/explicit-trace-width.test.mjs` runs cold positive and negative
  pipelines plus terminal, degenerate-route, and unmarked-legacy regressions.
  No circuitpy package changed, so skill runtime re-vendor is not required.
- **Tracks affected:** pipeline / docs.

## 2026-08-12 — Explicit source trace minima cannot degrade to router preferences
- **Change:** stage 4c's compiled-layout check now joins each routed
  `pcb_trace` to its exact `source_trace_id` and blocks
  `layout_trace_below_requested` when any non-zero wire segment is thinner
  than that source trace's `min_trace_thickness`. Exact identity keeps a
  deliberate narrow leaf on the same electrical net from being compared with
  a separate wide trunk contract.
- **Why:** the golden USB block requested 0.25mm CC routes and the phase SRJ
  preserved that number, but Pipeline7's width solver treated it as nominal,
  fell through to the 0.15mm board floor, and emitted apparently clean copper.
  Expanding that copper back to 0.25mm revealed six real 0.15mm-clearance
  conflicts, so blindly widening output is unsafe and silently accepting it
  contradicts the authored source.
- **Backward compatible:** no for a board whose compiled copper was already
  thinner than its explicit source minimum. It must make room for the stated
  width or split the geometry into separately authored, bounded neck-down and
  trunk traces.
- **Mechanism:** `verifylib.layout._requested_trace_widths` performs the exact
  source/output join and measured segment comparison; the JLCPCB fab profile
  classifies the finding as blocking. Focused verifier and policy regressions
  cover both sides, and the retained golden USB artifact reproduces two
  0.15mm outputs against 0.25mm requests. Skill runtime re-vendor required
  because `packages/verify` is vendored beside circuitpy.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-12 — USB plans own a protected raw-to-load power boundary
- **Change:** the block planner now treats both USB connector blocks as raw
  `VBUS_RAW` sources and composes `usb-power-entry` before any `V5` load. The
  protected entry carries the exact TPS2553/59k contract: a 500mA source
  ceiling and 400.6mA normal-operation ceiling. `BoardPlan.source_budget`
  retains physical peak current while optionally applying explicit
  per-block `firmware_load_caps_ma` only to normal operation, and exposes the
  corresponding fixed load plus count/per-device/aggregate physical and
  operational breakdown needed to populate `product.json`; an uncapped or
  over-limit plan is not buildable. `UsbSourceContract` makes the protected
  entry the single owner of the exact raw/protected nets, attach limit,
  populated limiter identity/pins, its ILIM setting-resistor identity/value/
  return topology, and trip range; the new
  `usb_power_budget_for_plan()` compiles the complete `powerBudget` object and
  requires the caller to supply only the board's actual load-refdes patterns.
  Purely per-unit loads no longer add their typical idle number on top of
  every device's worst case. The legacy
  `power_budget()` helper now derives its USB ceiling from the same block
  instead of assuming 1.5A.
- **Why:** the reviewed Harness example put roughly 40uF and a 480mA LED peak
  directly on unadvertised USB VBUS. Moving pixels to 5V fixed LDO heat but
  made attach current and source overdraw worse. Those are architectural
  errors the planner already has enough information to prevent; they must not
  be rediscovered as product-specific layout warnings.
- **Backward compatible:** no for generated USB plans that relied on a direct
  connector-to-`V5` edge or the historical 1.5A guess. They must compose the
  current-limited entry and either stay below 400.6mA in normal operation or
  declare a measurable firmware load cap while retaining physical peak for
  copper and fault analysis. A declared `powerBudget.usb.currentLimiter` must
  now also include `settingPin` and `settingResistor` (`ref`, `lcsc`,
  `resistanceOhms`, `returnNet`); an older range declaration that does not tie
  itself to the setting network fails closed at spec time.
- **Mechanism:** `circuitlib.blocks` owns the raw connector and protected-entry
  contracts; `circuitlib.helpers.board_plan` resolves the dependency and
  emits the two-number source budget, and `usb_power_budget_for_plan` performs
  the final schema projection without duplicating engineering constants.
  The independent artifact gate measures that the declared setting resistor
  is populated, carries the exact C-number and resistance, and is the sole
  resistor from the limiter's declared setting pin to the declared return net;
  a limiter IC alone no longer proves the claimed trip range.
  Planner tests cover power-only and MCU data compositions,
  physical/operational accounting, invalid caps, missing/extra load patterns,
  exact schema output, and the 500mA legacy helper boundary. Skill runtime
  re-vendor is required because the validated circuitpy schema and independent
  verifylib artifact gate changed alongside the skill's planner sources.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-12 — Sidecars identify the installed patched router bundles
- **Change:** `sidecar.toolchain` now records the versions of
  `@tscircuit/core`, `@tscircuit/capacity-autorouter`, and `@tscircuit/props`
  plus SHA-256 identities for each installed runtime bundle, in addition to
  the existing tscircuit/checks/KiCad versions. Build short-circuit and export
  cache identity therefore change whenever an audited compiled patch changes,
  even though the upstream semver pin remains constant. The example evidence
  lock compares this compiler/router identity while treating the optional
  local KiCad version as artifact provenance rather than a portable CI pin.
- **Why:** our fail-closed clearance, plane, authored-tree, differential-pair,
  and via-in-pad repairs patch exact pinned bundles without publishing a new
  upstream package version. Version-only identity could reuse copper produced
  before one of those safety fixes—the precise stale-evidence class the new
  example lock is meant to prevent.
- **Backward compatible:** additive for sidecar readers; intentionally cache
  invalidating for old sidecars and exports that lack the bundle identities.
  They rebuild once under the installed audited runtime.
- **Mechanism:** `packages/circuitpy/src/circuitpy/toolchain.py` hashes the
  installed core/capacity/props `dist/index.js` files; generation already folds
  the complete toolchain block into freshness and export-cache checks. Tests
  pin the 64-hex identities, sidecar fields, export-cache keys, example locks,
  review-packet freshness, and stale-bundle rejection. Skill runtime re-vendor
  required.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-12 — Example regression locks require fresh, complete evidence
- **Change:** the fast `evals/examples_lock.py` ratchet now recomputes each
  board's source-graph fingerprint and requires it to match the committed
  sidecar, including the entry source, static local imports, `product.json`,
  and `parts.json`. It also requires the circuit and every artifact promised
  by the sidecar. Missing, unreadable, stale, crashed, or removed-project
  evidence is an unconditional failure. `--accept` refuses to rewrite the
  baseline while any such evidence exists **or while any measured regression
  exists**; it cannot erase a prior `fabReady: true` verdict at an equal
  blocking count or silently retain an older score while returning success on
  a worse board.
- **Why:** a sidecar from an older source graph could previously be reported as
  today's board result—or even ratcheted—while the actual edited design had
  never built. This is the same stale-evidence failure mode that contaminated
  the earlier router-effort comparison, now closed at the example-product
  boundary.
- **Backward compatible:** no for workflows that edited sources without
  rebuilding committed artifacts. They must now regenerate the board and all
  declared outputs before the fast lock can pass or accept an improvement.
  The stored baseline schema itself is unchanged.
- **Mechanism:** `evals/examples_lock.py` uses
  `circuitpy.source_hash.board_source_hash`; isolated tests cover entry/import/
  product/parts drift, missing evidence, crashed builds, legacy baselines, and
  byte-identical `--accept` refusal for both stale evidence and real blocking/
  fab-readiness regressions. CI runs both those tests and the real fast lock
  against committed examples. Skill runtime re-vendor is not required.
- **Tracks affected:** pipeline / docs.

## 2026-08-12 — Phased differential pairs retain exact selected trace identity
- **Change:** after a native `<differentialpair>` selects one direct two-port
  `source_trace`, pinned core now resolves its phase SRJ connection by that
  exact `source_trace_id`. It no longer expands the selection through the
  shared `subcircuit_connectivity_map_key`. Source validation, pair gap, skew,
  uncoupled-length, phase-coherence, and fail-closed rules are unchanged.
- **Why:** the compact USB topology has ordinary routed connector-orientation
  edges beside the selected connector-to-ESD edges. Each pair of edges shares
  one electrical connectivity key but has a distinct physical source trace and
  routing phase. Core correctly selected the direct edge first, then
  incorrectly re-expanded it and aborted because the key matched both SRJ
  connections.
- **Backward compatible:** yes for a valid direct-pair selector. The pair rule
  now remains on the physical edge it names. A selected trace absent from the
  phase SRJ, a duplicate exact source identity, a named-net aggregate, or a
  composed selector still fails closed rather than attaching constraints to a
  neighboring edge.
- **Mechanism:** the deterministic core stage in
  `scripts/build/apply-toolchain-patches.mjs` advances
  `@tscircuit/core@0.0.1642` from
  `f16cc7ee806d3afa14b639e784eefde1014d141f22fdd087b9309a8a64b361c0`
  to
  `8359d3082f85ccb2010810e8dfe9730fce9d2efb264d33aa96750d24d0a968d9`
  under exact package, input/output hash, replacement-count, syntax, and
  idempotency guards. A cold three-phase connector/reversible-edge fixture
  asserts the final pair names only its two selected source traces. No skill
  runtime re-vendor is required.
- **Tracks affected:** pipeline / docs.

## 2026-08-12 — New two-layer plans require stitched GND material on both faces
- **Change:** `circuitlib.helpers.BoardPlan.ground_plane_layers` and
  `circuitlib.layout.product_layout(...).ground_plane_layers` now default to
  `("top", "bottom")` instead of a bottom-only plane. Callers may still
  request a partial or single-face plane explicitly; the compiled artifact
  must satisfy the same routed-length, fanout-length, and stitching-pitch
  contract.
- **Why:** the Terminal Keyboard review requires signal and power routing over
  dual GND pours. Treating that as a one-board edit would leave every future
  generated two-layer board with longer return paths and routed GND spokes on
  its opposite-side components. The reusable lesson is that a two-face return
  structure is the safe default, while an exception needs deliberate geometry
  and proof.
- **Backward compatible:** no for callers that relied on omission to mean a
  bottom-only plane. They must now either render and verify both faces or pass
  `ground_plane_layers=("bottom",)` explicitly. Existing explicit layer lists
  are unchanged.
- **Mechanism:** defaults live in
  `skills/circuitcode/circuitlib/{helpers,layout}.py`; focused planner and
  product-contract tests pin both faces. No skill runtime re-vendor is
  required because these are already the skill runtime sources.
- **Tracks affected:** skills / docs.

## 2026-08-12 — Differential-pair grids exclude numerical zero-length edges
- **Change:** the pinned native differential-pair postprocessor no longer adds
  a planar adjacency when its endpoints are at most `1e-10mm` apart, matching
  the existing tolerance required to construct a direction. All real grid
  edges, layer transitions, coupling/skew rules, and bounded search limits are
  unchanged.
- **Why:** the rotated USB fixture produced a symmetric pair midpoint at
  `-1.1102230246251565e-16` while the matching coarse-grid coordinate was
  exact zero. The composite graph stored two node IDs and connected them; its
  direction consumer then aborted on that `1.11e-16mm` edge before it could
  report the actual routing contract.
- **Backward compatible:** yes for physically meaningful routes. A numerical
  edge with no direction is unusable and is now absent. Searches that cannot
  meet their pair contract still fail closed: the retained USB composition
  now reports `11.438486mm` of uncoupled copper against its `3mm` maximum
  instead of exporting copper or throwing the internal graph exception.
- **Mechanism:** the deterministic capacity-router stage in
  `scripts/build/apply-toolchain-patches.mjs` advances
  `@tscircuit/capacity-autorouter@0.0.782` from
  `ce318ec3a3120490459c0c5cdaea710a1345884aeb5f88f559188c255ab9c318`
  to
  `38b34259144c87a5040b02a4f3958760ead65a3700550e4b205d1dab642a5853`
  with exact bundle hashes and source-map guards for both the grid producer
  and direction consumer. A cold two-connection/one-obstacle regression
  proves a permissive contract remains routable and a strict contract reports
  measured uncoupled copper. No skill runtime re-vendor is required.
- **Tracks affected:** pipeline / docs.

## 2026-08-12 — Shipping references remain complete after solder-mask subtraction
- **Change:** converted KiCad footprints now keep a visible populated-part
  reference only when its normalized 1mm/0.15mm text envelope is inside the
  outline and clear of same-face mask openings and other reference text. An
  unsafe reference moves to the nearest deterministic compass slot outside
  its footprint envelope; if no slot fits, the pipeline emits blocking,
  localized `silkscreen_refdes_unreadable`. Explicitly hidden routing-node
  references remain exempt. Gerber parsing now preserves `%LPD`/`%LPC`
  polarity and operation order, so `gerber_silk_over_pad` measures the final
  composite ink instead of treating a later negative pad flash as positive
  silk.
- **Why:** a real KiCad 10 fixture proved `--subtract-soldermask` can safely
  erase 29.9% of `R123` while leaving a visibly partial designator; the old
  normalizer and checks both passed. The Gerber parser also discarded layer
  polarity, so it reported 18 false overlaps on the safely subtracted plot and
  could not distinguish complete ink from erased ink.
- **Backward compatible:** yes for already printable or explicitly hidden
  references. Populated parts whose complete fab-floor reference cannot fit
  now intentionally block instead of shipping an unreadable partial label;
  relocated KiCad text changes only the silkscreen position, never copper,
  mask, assembly identity, or source board placement.
- **Mechanism:** `packages/circuitpy/src/circuitpy/kicad_normalize.py` performs
  the idempotent post-size relocation and exposes localized findings consumed
  by `generation.py`. `packages/verify/src/verifylib/gerber.py` retains plot
  polarity/sequence, and `gerber_truth.py` suppresses an overlap only when a
  later standard clear aperture provably erases the complete intersecting
  ink. Unit tests cover relocation, impossible placement, bottom/rotation,
  idempotency, positive overlap, and negative subtraction; real KiCad 10
  plotting loads the rewritten board. Skill runtime re-vendor required.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-12 — Local routing prevents unrequested vias in SMD pads
- **Change:** pinned Pipeline7 exact DRC now measures every routed via edge
  against physical single-layer SMD pads using
  `minViaEdgeToPadEdgeClearance`. Its targeted repair moves only the reported
  movable via; an immovable conflict remains unresolved and fails closed. A
  second, algorithm-independent core boundary checks local output before
  caching, phase accumulation, or copper export, so cached, Pipeline9, fanout,
  and custom-local results cannot bypass the rule. Existing explicit
  `allowViaInPad` remains valid only for a connected same-net SMD pad;
  different-net pads always reject, and same-net plated through holes remain
  legal.
- **Why:** the Hydrate C8 failure proved that post-route detection was only a
  smoke alarm. Pinned `high-density-repair03` checked trace-to-pad and
  via-to-via geometry but had no via-to-SMD-pad check, so the illegal via had
  zero optimization cost and was accepted at the pad center.
- **Backward compatible:** yes for boards without implicit via-in-pad and for
  the existing explicit same-net opt-in. Invalid routes that previously
  exported and failed later now repair or intentionally serialize
  `pcb_autorouting_error` with no routed via copper.
- **Mechanism:** deterministic stages in
  `scripts/build/apply-toolchain-patches.mjs` advance
  `@tscircuit/capacity-autorouter@0.0.782` from
  `e9646104761010ac37d935e839781b0a755870a7e56f0db7cfd4ccd9dbc7a973`
  to
  `ce318ec3a3120490459c0c5cdaea710a1345884aeb5f88f559188c255ab9c318`
  and `@tscircuit/core@0.0.1642` from
  `2ccd8305aef9a52a6f12df388efcc53e91298e55d8afb261247f920bca958613`
  to
  `f16cc7ee806d3afa14b639e784eefde1014d141f22fdd087b9309a8a64b361c0`.
  Focused cold regressions cover single/multiple/immovable vias, explicit
  same-net opt-in, different-net rejection, PTH preservation, serialized
  failure, zero-copper export, exact hashes, and idempotency. No skill runtime
  re-vendor is required.
- **Tracks affected:** pipeline / docs.

## 2026-08-12 — Differential-pair trace selectors bind their own physical endpoints
- **Change:** when a `<differentialpair>` connection names one unique
  `source_trace`, its initial point-to-point design-rule check uses that
  trace's own `connected_source_port_ids`. It no longer expands the selector
  through adjacent reversible-pad edges or declared package-internal
  connectivity. Port selectors and duplicate trace names keep the existing
  connectivity-map behavior; the later direct-two-port/no-named-net contract
  remains unchanged and fail-closed.
- **Why:** the reusable USB-C topology names the physical connector-to-ESD
  section while a separate authored edge joins the reversible connector pads.
  Pinned core expanded the selected section to all three electrical terminals
  and emitted a false ambiguous-trace warning even though the selected source
  trace itself has exactly two ports. That warning blocked the golden USB
  clean gate and would push generators toward an electrically wrong bypass.
- **Backward compatible:** yes for unique trace-name selectors: the change
  makes validation match the selected physical edge. Ambiguous names and port
  selectors intentionally retain their prior network-level diagnostics.
- **Mechanism:** the deterministic core patch chain in
  `scripts/build/apply-toolchain-patches.mjs` advances
  `@tscircuit/core@0.0.1642` from
  `1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae`
  to
  `2ccd8305aef9a52a6f12df388efcc53e91298e55d8afb261247f920bca958613`.
  A cold connector/reversible-edge fixture proves the old two warnings are
  gone while both selected traces remain exact two-port/no-net edges. Exact
  hashes, syntax, successor-chain guards, and idempotency are tested. No skill
  runtime re-vendor is required.
- **Tracks affected:** pipeline / docs.

## 2026-08-12 — Golden powered consumers expose measured local bypass trees
- **Change:** `SensorBme280`, `Ws2812LevelShifter`, and `Ws2812Chain` now
  author each `requires_power` pin directly to its own bypass-capacitor rail
  pad. The sensor and shifter expose that local tree through a marked wide
  named-rail boundary. The chain instead gives every capacitor a bounded
  0.2mm neck into a mask-covered node, joins the nodes with one acyclic 0.8mm
  backbone, and exposes only the final node through one marked boundary; its
  internal pixel data is a direct, ordered 0.25mm hop chain rather than
  aggregate named-net leaves.
  Their reusable APIs add `localPowerWidthMm` (default 0.2),
  `railTrunkWidthMm` (default 0.8), and `maxDecouplingLengthMm` (default 2).
  The WS2812 chain additionally exposes `maxRailNeckLengthMm`,
  `railNodeRefs`, `railRoutingPhaseIndex`, and `dataRoutingPhaseIndices`.
  BME280 and WS2812 composition also propagates an explicit `layer`; ordinary
  data/I2C traces default to 0.25mm.
- **Why:** the EE review found that the old blocks placed capacitors nearby but
  connected the IC/pixel and capacitor as independent named-net leaves. That
  let aggregate MST routing choose 6–10mm power loops and made proximity a
  visual comment instead of an electrical/layout invariant.
- **Backward compatible:** electrically yes for callers using the defaults;
  the same rails and signals remain connected. Routed geometry and source
  trace identity intentionally change, invalid dimension overrides now throw,
  and consumers depending on the former independent-leaf topology must
  migrate to the authored local tree. New `layer` and dimension props are
  additive.
- **Mechanism:** golden block sources and `BLOCK.md` contracts freeze the
  local/wide split. Compiled tests require exact port-to-port endpoints,
  0.2mm/no-via local copper within 2mm, the chain's 0.8mm node backbone and
  sole boundary, direct data-edge phases, routed top/bottom propagation, and
  zero findings from the independent product decoupling verifier.
  `circuitlib.blocks` exposes the same typed composition controls to planning.
  Vendored example copies must be refreshed together with their board
  composition; no circuitpy skill runtime re-vendor is required.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-12 — Named-net backbone copper no longer impersonates a local source edge
- **Change:** a routed `pcb_trace` whose `connection_name` resolves to a
  `source_net` keeps that aggregate identity and has no `source_trace_id`.
  Direct authored routes continue to carry their exact source-trace identity.
- **Why:** core inferred an aggregate MST segment's source trace from one of
  its endpoint ports. In an authored V5 tree this made 7.6mm rail-backbone
  copper impersonate an unrelated device-to-capacitor branch and inherit that
  branch's 2mm `maxLength`. A named-net segment spans boundary points; it is
  not the physical implementation of any one local source edge.
- **Backward compatible:** yes for direct source traces and named-net
  connectivity, which remains explicit in `connection_name`. Consumers that
  incorrectly treated an aggregate segment's arbitrary `source_trace_id` as
  meaningful must use `connection_name`/the source-net identity instead.
- **Mechanism:** the final deterministic core stage in
  `scripts/build/apply-toolchain-patches.mjs` advances
  `@tscircuit/core@0.0.1642` from
  `77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05`
  to
  `1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae`.
  A cold two-subtree fixture asserts exact local IDs/widths, an aggregate
  three-point backbone with no source IDs, no leaked maximum-length warning,
  independent DRC, exact hashes and idempotency. No skill runtime re-vendor is
  required.
- **Tracks affected:** pipeline / docs.

## 2026-08-12 — Trace-local via style survives autorouter output reinsertion
- **Change:** routed via dimensions are resolved per output
  `source_trace_id`. Each hole and pad is the maximum of the route point,
  owning trace's inherited `pcbStyle.viaHoleDiameter` /
  `pcbStyle.viaPadDiameter`, and the board's
  `minViaHoleDiameter` / `minViaPadDiameter`. The resolved values are written
  identically to the `pcb_trace.route` via point and standalone `pcb_via`.
- **Why:** pinned core treated board minima as overrides and resolved one style
  from the routing group. A VBUS trace scoped to legal 0.8/0.5mm power vias
  therefore became generic 0.6/0.3mm copper after async autorouter output
  reinsertion, even though signal vias on the same board should remain at the
  smaller board floor.
- **Backward compatible:** yes for routes at or above both the local style and
  board floors. A local style smaller than the board is intentionally raised;
  an invalid final pair whose hole is non-positive or not smaller than its pad
  now fails instead of serializing invalid drill geometry.
- **Mechanism:** the deterministic final core stage in
  `scripts/build/apply-toolchain-patches.mjs` advances
  `@tscircuit/core@0.0.1642` from
  `ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b`
  to
  `77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05`.
  Cold cross-layer fanout fixtures prove board 0.6/0.3mm plus local 0.8/0.5mm
  emits 0.8/0.5mm in both records, while local 0.4/0.2mm is floored to
  0.6/0.3mm. A fixed-path fixture separately proves the same contract survives
  async reinsertion. Exact hashes, syntax and idempotency are guarded. No
  skill runtime re-vendor is required.
- **Tracks affected:** pipeline / docs.

## 2026-08-12 — Native differential pairs preserve and enforce their physical contract
- **Change:** native `<differentialpair>` declarations now retain
  `pcbTraceGap`, `maxLengthSkew`, and `maxUncoupledLength` through both pinned
  capacity pipelines. The positive and negative selectors must each name one
  direct two-physical-port source trace with no `net.*` endpoint;
  `maxUncoupledLength` additionally requires `pcbTraceGap`. Final
  postprocessed copper is checked for skew, total uncoupled length, and exact
  DRC before export.
- **Why:** the pinned stack lost selected source-trace identity when a
  named-net/composed USB path became an aggregate capacity connection, dropped
  `maxUncoupledLength`, ignored the physical trace gap in Pipeline9, and could
  throw `cannot offset a reversing spine corner` on a trivial symmetric pair.
  Those behaviors either blocked valid direct pairs or, more seriously,
  accepted copper without enforcing the declared coupling contract.
- **Backward compatible:** yes for a direct two-port pair that already states
  all geometry needed by its contract. No, intentionally, for named-net or
  composed selectors, for `maxUncoupledLength` without `pcbTraceGap`, and for
  final pair copper that violates skew/coupling/DRC: these formerly silent or
  ambiguous cases now serialize `pcb_autorouting_error` and fail closed. A
  flow-through protection device declares separate connector-side and
  device-side direct pair sections, joined by the component's internal pin
  connectivity; it does not add an external bypass.
- **Mechanism:** deterministic final stages in
  `scripts/build/apply-toolchain-patches.mjs` advance exact pinned capacity
  from
  `471c49fbb77192e8161ac8dadbb3b51781c10b19dfc088a952964c71ded114b7`
  to
  `e9646104761010ac37d935e839781b0a755870a7e56f0db7cfd4ccd9dbc7a973`
  and core from
  `c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77`
  to
  `ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b`.
  Source-map guards where shipped, exact hashes, syntax, idempotency, direct
  Pipeline7/Pipeline9 positives, uncoupled negatives, named-net rejection, and
  missing-gap rejection are covered by cold fixtures. No skill runtime
  re-vendor is required.
- **Tracks affected:** pipeline / docs.

## 2026-08-12 — Product decoupling is a measured local-topology contract
- **Change:** `product.json.layout` accepts optional
  `decoupling: {maxDistanceMm, exclude?}`. `maxDistanceMm` is a required
  positive supply-pad-to-capacitor-pad edge distance. `exclude` is an explicit
  reference-designator glob or non-empty list for parts such as rail-reference
  ESD clamps that carry a `requires_power` pin but are not powered loads. For
  each populated `simple_chip` port whose compiled source metadata says
  `requires_power: true`, the independent intent verifier now requires a
  populated capacitor from the same rail to GND, an authored two-port path
  from the supply pad to that capacitor's rail pad, measurable PCB copper, and
  the declared distance. Violations are blocking
  `layout_intent_decoupling_{missing,topology,geometry,distance}` findings.
- **Why:** the EE review required every bypass capacitor to sit within 1–2mm
  of the pin it serves. Merely placing a capacitor somewhere on the same named
  rail lets an aggregate-net/MST router choose the power-up loop, and is why
  the examples produced decorative 6–10mm decouplers. An advisory 5mm
  component-centre review could describe the problem but could not enforce the
  product's actual first-build requirement.
- **Backward compatible:** yes for handwritten products that omit
  `layout.decoupling`; no, intentionally, for callers that regenerate layout
  through `circuitlib.layout.product_layout()`, which now emits the 2.0mm
  contract by default. Missing local topology, non-populated caps, absent pad
  geometry, or excess distance then stop fabrication. Exclusions are visible
  product decisions rather than hidden component-name heuristics.
- **Mechanism:** `circuitpy.layout_intent` validates the closed schema;
  `verifylib.intent` joins source power metadata through PCB ports to emitted
  pad rectangles, indexes same-rail-to-GND capacitors, and traverses only
  explicit two-port source traces (including masked-node authored trees).
  `circuitpy.fab` classifies all four mismatches as blocking. The skill's
  `circuitlib.tables.DECOUPLING_MAX_DISTANCE_MM` and `layout.product_layout()`
  make the safe contract the default for newly generated products. Focused fixtures
  cover clean, distant, MST-only, DNP/missing, and explicit-exclusion cases.
  **Skill runtime re-vendor required.**
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-12 — Decoupling limits apply only to physical cap-to-device branches
- **Change:** `@tscircuit/core@0.0.1642` now infers a capacitor's
  `max_decoupling_trace_length` only for a trace with exactly two physical
  port endpoints and exactly one capacitor endpoint. Cap-to-cap authored-tree
  edges and port-to-`net.*` boundaries receive no implicit limit. An explicit
  trace `maxLength` remains authoritative. When an explicitly limited
  one-port trace belongs to a `fanout` or `single_layer_fanout` phase whose
  `fanoutPourNetMap` contains that trace's net, straight-line preflight does
  not measure the local plane drop against unrelated remote net ports.
- **Why:** RP2040 C12/C13 inherited their 1mm decoupling default onto the DVDD
  cap-to-cap tree, its marked rail boundary, and unrelated GND fanouts. The
  preflight ran before asynchronous pour material was present, treated each
  one-port GND drop as a route to remote GND endpoints, and skipped all
  autorouting even though the planned same-layer contacts are 0mm. The limit
  describes the physical IC-to-cap branch, not every edge incident to a
  capacitor.
- **Backward compatible:** yes for direct capacitor-to-device branches and
  every explicit `maxLength`; both retain fail-closed enforcement. A
  cap-to-cap or cap-to-net trace that intentionally needs its own bound must
  now state `maxLength` explicitly instead of accidentally inheriting another
  branch's decoupling policy. Solved fanout length and physical pour
  connectivity remain independently verified.
- **Mechanism:** a deterministic final stage in
  `scripts/build/apply-toolchain-patches.mjs` advances exact pinned core from
  `84be17d3b2beb909426dcf5140cd141bd6417bc2c22e465daa0e5c502d8684b8`
  to
  `c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77`.
  Cold fixtures prove cap-to-cap/boundary exclusion, planned 0mm plane
  contacts in the presence of remote same-net ports, explicit 2mm enforcement,
  preserved automatic 1mm direct-branch inference, parsed errors, exact
  independent checks, input/output hashes and idempotency. No skill runtime
  re-vendor is required for this toolchain-only correction.
- **Tracks affected:** pipeline / docs.

## 2026-08-11 — Explicit authored power trees contract safely into global rails
- **Change:** trace props add the opt-in boolean
  `authoredNetTreeBoundary` on the sole port-to-`net.*` edge of an authored
  local routing tree. A marked component must contain at least one
  port-to-port branch, be acyclic (`E = V - 1`), and touch its named net at
  exactly that one boundary. Valid internal branches remain exact connections;
  their internal ports contract out of the named-net aggregate while the
  boundary remains. Multiple marked subtrees and unmarked loads on one rail
  therefore route only their boundary backbone. Unmarked traces retain their
  prior SRJ byte-for-byte.
- **Why:** explicit RP2040 decoupling and power-trunk copper already formed an
  intentional non-collinear tree. The generic capacity merge joined every
  shared-point connection, replaced those branches with another MST, and then
  attempted a redundant whole-rail Steiner route. This both discarded scoped
  widths/topology and made dense power phases fail nondeterministically.
- **Backward compatible:** yes for every existing unmarked source. The new
  marker is explicit and additive. A source that opts in with a cycle, a
  second rail boundary, a missing rendered endpoint, or a marker on a
  non-boundary trace intentionally fails closed as a parsed
  `pcb_autorouting_error` rather than routing an ambiguous topology.
- **Mechanism:** deterministic exact-version stages in
  `scripts/build/apply-toolchain-patches.mjs` patch the
  `@tscircuit/props@0.0.618` runtime/types, annotate and contract marked trees
  in `@tscircuit/core@0.0.1642`, and make
  `@tscircuit/capacity-autorouter@0.0.782` exclude only annotated edges from
  shared-point DSU merging. Exact final hashes are props runtime
  `1bb4a3838f05e926bf5400978156b4184a6b70f62adc94d6d47b0fe8c356da98`,
  props declarations
  `45d5570d87d256212596ad84908f5066f75b2294b97dd4b5ce78fb0e52e40a6d`,
  capacity
  `471c49fbb77192e8161ac8dadbb3b51781c10b19dfc088a952964c71ded114b7`,
  and core
  `84be17d3b2beb909426dcf5140cd141bd6417bc2c22e465daa0e5c502d8684b8`;
  package versions, input/output hashes, source maps where shipped,
  match counts, syntax, idempotency and cold behavioral fixtures are guarded.
  The complete SRJ remains part of the existing configuration-complete cache
  key, so contracted and legacy inputs cannot cross-contaminate. No skill
  runtime re-vendor is required for this toolchain-only contract.
- **Tracks affected:** pipeline / docs.

## 2026-08-11 — Fixed `pcbPath` copper participates in product verification
- **Change:** `verifylib.model.Board` resolves a compiled `pcb_trace` through
  `connection_name` or, for authored/fixed `pcbPath` copper, its serialized
  `source_trace_id`. Every downstream trace query therefore sees manual and
  autorouted copper through the same net identity.
- **Why:** Hydrate's VBUS crossover, power trunks and fixed debug escapes had a
  `source_trace_id` but no `connection_name`. The independent model silently
  assigned them to no net, letting manual power copper evade trunk-width,
  ground-length and other product-intent checks even though the same geometry
  would block when emitted by the autorouter.
- **Backward compatible:** no, intentionally, for manual copper that violated
  an already-declared product rule. Artifact shape and authoring APIs are
  unchanged; previously invisible fixed traces now receive the same verdict as
  equivalent autorouted traces.
- **Mechanism:** the model's trace join falls back to `source_trace_id`, whose
  source trace carries the connectivity key. A focused model regression proves
  the join/length/width and a product-intent regression proves a narrow fixed
  power trunk can no longer bypass the blocking gate. **Skill runtime
  re-vendor required** so the packaged verifier receives the corrected model.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — USB power budgets are compiled-artifact contracts
- **Change:** `product.json` accepts an optional top-level `powerBudget.usb`
  policy with `rawVbusNet`, `protectedVbusNet`,
  `rawAttachCapacitanceMaxUf`, `sourceCurrentMaxMa`,
  `fixedOperationalLoadMa`, an exact `currentLimiter` identity/pin/trip-range,
  and zero or more `firmwareLimitedLoads` carrying match patterns,
  per-device physical peak and aggregate operational maximum. The independent
  `verifylib.power_intent` check measures the compiled hardware against it.
- **Why:** Harness Puck placed roughly 40uF directly on raw USB VBUS and could
  physically demand about 595mA from a default 500mA source. Neither copper DRC
  nor a firmware-brightness comment proves safe attach/current behavior. The
  physical peak must remain visible even when firmware deliberately caps the
  operating load.
- **Backward compatible:** yes for products that omit `powerBudget`. A product
  that opts in intentionally fails spec resolution for inconsistent limits and
  blocks fabrication when raw capacitance, limiter identity/topology, protected
  load topology or the operating budget contradicts its compiled board.
- **Mechanism:** `circuitpy.power_intent` validates the contract before any
  toolchain process. Stage 4c passes it through `verify_bridge` to the
  independent verifier, which sums raw-VBUS-to-GND capacitors, proves the exact
  populated LCSC limiter's input/output nets, counts matched protected loads,
  retains their calculated physical peak, and compares the operational sum to
  the worst-case trip point. The JLC policy classifies every mismatch as
  blocking. Standalone verify CLI loads both layout and power intent from the
  adjacent product file. **Skill runtime re-vendor required.**
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — Solved silkscreen-on-pad overlap blocks fabrication
- **Change:** the JLC fab profile escalates `gerber_silk_over_pad` to an error.
  The existing Gerber-truth check intersects plotted silk strokes with actual
  top/bottom solder-mask openings; a source footprint or KiCad warning is not
  used as a proxy. Shipping KiCad Gerbers are exported with
  `pcb export gerbers --subtract-soldermask`, so reference/outline strokes are
  clipped from every exposed pad before the packet is checked and zipped.
- **Why:** increasing the examples' previously unprintable 0.033mm silk to the
  fab's 0.15mm floor exposed dozens of strokes inside mask openings. A fab may
  clip those strokes—silently deleting reference marks—or leave ink where a
  solder joint must wet. Neither outcome is a fab-ready packet.
- **Backward compatible:** no, intentionally. A solved packet with silk inside
  a solderable opening was warning-only. Shipping KiCad output now clips it
  automatically; a non-KiCad or otherwise malformed plot is refused if the
  independent Gerber check still finds exposed-pad overlap.
- **Mechanism:** `circuitpy.fab.VERIFY_ESCALATED_KINDS` promotes the existing
  Gerber finding, with policy regression coverage. The geometric checker stays
  in `verifylib.gerber_truth`; `circuitpy.generation` owns the mask-aware KiCad
  plot command, with a command-policy regression that runs even where KiCad is
  unavailable. **Skill runtime re-vendor required.**
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — USB routed-pair skew blocks fabrication
- **Change:** the JLC fab profile now escalates `netclass_pair_skew` from the
  verifier's measured warning to a fabrication-blocking error. The existing
  checker compares emitted USB D+/D- copper against a 3.8mm intra-pair budget;
  no source name, routing phase, or autorouter success claim substitutes for
  that measurement.
- **Why:** the three example routes exposed 3.7–18.9mm USB skew while otherwise
  looking routed. USB full-speed reliability is a product requirement, not an
  aesthetic advisory, and an intermittent data link is not fab-ready.
- **Backward compatible:** no, intentionally. A packet with routed USB skew
  beyond the existing budget was formerly warning-only and now cannot report
  `fab.ready=true` until the pair is length-matched or shortened.
- **Mechanism:** `circuitpy.fab.VERIFY_ESCALATED_KINDS` includes
  `netclass_pair_skew`; the policy test freezes the promotion. The geometry and
  3.8mm threshold remain owned by `verifylib.netclass`, so the fab layer does
  not duplicate route arithmetic. **Skill runtime re-vendor required.**
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — Net classes size vias as well as traces
- **Change:** each `product.layout.netClasses[]` policy may now declare
  `minViaOuterDiameterMm` and `minViaHoleDiameterMm`. Verification associates
  every emitted via with the class through its routed trace, explicit source
  net, or connectivity identity and blocks undersized copper or drills.
- **Why:** a 0.8mm power trunk can still bottleneck through the router's
  generic 0.6/0.3mm signal via. The EE review explicitly calls for at least
  0.8/0.5mm on power transitions (or a separately proven parallel-via design),
  so checking trace width alone gives false confidence about ampacity and
  annular margin.
- **Backward compatible:** yes for products that omit the two optional
  members. Products that declare them intentionally reject packets whose
  power/current-class vias were previously accepted at generic signal size.
- **Mechanism:** `circuitpy.layout_intent` validates positive dimensions and
  requires the outer floor to exceed the drill floor; `verifylib.intent`
  measures raw `pcb_via` geometry for each matched compiled net. The JLC fab
  policy preserves these findings as blocking errors. Focused tests cover
  schema refusal and a wide V5 trace containing a 0.6/0.3mm bottleneck.
  **Skill runtime re-vendor required.**
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — Fixed `pcbPath` vias obey board minima and scoped power sizing
- **Change:** manual `pcbPath` rendering now floors via pad and drill diameters
  at the compiled board's `minViaPadDiameter` and `minViaHoleDiameter`. The
  resolved values are serialized identically on the fixed trace's via route
  points and its `pcb_via` elements. A trace that needs larger vias uses the
  existing typed, inheritable `pcbStyle`; a small wrapper group scopes
  `viaPadDiameter` / `viaHoleDiameter` to that trace rather than raising every
  board via.
- **Why:** Hydrate's preserved VBUS crossover proved that every manual path via
  silently used the library fallback (0.3mm outer / 0.2mm drill) even though
  the board declared 0.6/0.3mm. That produced a blocking 0.05mm annular ring.
  VBUS also belongs to a power class requiring 0.8/0.5mm, so globally raising
  the board minimum would fix the power trace by unnecessarily enlarging every
  signal via. Manufacturing rules are a floor; a local electrical net-class
  requirement is a scoped override.
- **Backward compatible:** no, intentionally, for illegal manual vias. Fixed
  paths that relied on a via smaller than the board contract now receive the
  legal minimum and may expose a real clearance/layout failure. Unstyled paths
  otherwise keep their board rules, and an inherited larger `pcbStyle` remains
  larger. Automatic router vias are unchanged.
- **Mechanism:** the final exact-SHA `@tscircuit/core@0.0.1642` patch stage
  resolves manual route dimensions before inserting the `pcb_trace`, then
  reuses those exact values for `pcb_via`. A cold TSX regression compiles one
  unstyled fixed signal crossover at 0.6/0.3mm and one locally styled power
  crossover at 0.8/0.5mm, parses zero serialized `*_error` elements, and gets
  zero independent `@tscircuit/checks` findings. No skill runtime re-vendor is
  required for this toolchain-only stage.
- **Tracks affected:** pipeline / docs.

## 2026-08-11 — Same-layer plane fanout is an explicit, physically verified contact
- **Change:** the exact-pinned core patch accepts a plane target on the source
  pad's own layer. When `fanoutPourNetMap` maps one net on both outside layers,
  each one-port fanout selects the layer of its physical PCB pad; a mapping to
  only one layer preserves the existing cross-layer behavior. Same-layer
  termination emits one explicit `is_inside_copper_pour: true` route-point
  marker on `fanout:<source_trace_id>`, tied to the source trace/net and route
  layer, with no copper segment, via, or added length. Golden `GndPlanes` now
  maps every poured layer by default, accepts explicit `fanoutLayers`, and
  retains singular `fanoutLayer` only as a compatibility/single-face escape.
  The independent
  solved-BREP plane-connectivity check now also runs when zero pour islands
  were emitted, binds a one-point marker to its sole compiled source pad's
  exact position/layer, and requires that pad to land in the dominant
  connected same-net/same-layer material island.
- **Why:** the pinned fanout solver rejected dual-face mappings and returned no
  route whenever `targetLayer == sourceLayer`. For Harness, forcing all 60 top
  GND pads to a bottom pour produced unnecessary vias and left five fanouts
  unresolved; a component-boundary experiment still could not place the
  RP2040 exposed-pad via within the product's 2mm limit. The pad already lies
  on a requested top GND pour, so the correct electrical operation is direct
  same-layer contact, not a manufactured dogbone. A logical marker alone is
  insufficient because the pour solver may omit, void, mis-net, or fragment
  the copper after routing, so artifact verification must prove the physical
  contact independently.
- **Backward compatible:** no, intentionally, for invalid plane claims. A
  single-layer pour mapping produces the same dogbone and via as before. A
  dual-face map now avoids needless vias by choosing each pad's own face.
  Same-layer fanouts that previously disappeared may now succeed, but a board
  with a missing, wrong-net, or isolated solved pour now fails closed instead
  of accepting a logical plane label without copper.
- **Mechanism:** the final `@tscircuit/core@0.0.1642` stage in
  `scripts/build/apply-toolchain-patches.mjs` is exact-version, exact-input-SHA,
  exact-output-SHA, atomic, and idempotent. Router fixtures assert a 0mm
  same-layer marker, unchanged <=2mm cross-layer via fanout, per-pad top/bottom
  selection, and sole-layer compatibility. `packages/verify` fixtures prove a
  connected solved BREP passes while a displaced/unmarked contact and missing,
  wrong-net, or fragmented pours fail. The exact Harness 60-drop SRJ routes
  60/60 with no vias and zero exact DRC; its real solved-pour artifact has zero
  plane-connectivity findings.
  **Skill runtime re-vendor required** because `verifylib.intent` changed.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — Planner refuses marginal linear-regulator heat
- **Change:** `circuitlib.helpers.BoardPlan.overheats` now returns true for a
  planned regulator verdict of either `warning` or `error`; consequently
  `BoardPlan.buildable` refuses a linear-rail architecture with less than
  30degC estimated junction headroom at the 45degC planning ambient.
- **Why:** the eight-pixel Harness plan dissipates about 1.01W in its naïve
  single AMS1117 model and reaches roughly 108degC—below the 125degC absolute
  limit, but with only about 17degC headroom in an enclosure. The old planner
  labelled that marginal and then returned `buildable=True`, guaranteeing a
  hot architecture even though it still had time to select a 5V pixel rail
  and logic-level buffer.
- **Backward compatible:** no, intentionally. Marginal linear-regulator plans
  formerly returned buildable and now require a cooler power architecture.
  Existing compiled boards retain the thermal finding's measured warning/error
  severity; this changes generation-time selection, not artifact truth.
- **Mechanism:** the `BoardPlan.overheats` policy treats both non-OK thermal
  severities as a planning refusal. Tests pin the eight-pixel rejection and
  retain a short, cool chain as buildable. No runtime re-vendor is required;
  `skills/circuitcode` is itself the runtime source.
- **Tracks affected:** skills / docs.

## 2026-08-11 — Ground-plane intent limits every pad-to-plane escape
- **Change:** `product.layout.groundPlanes` accepts the optional positive
  `maxFanoutLengthMm`. When present, verification measures every compiled
  `pcb_trace_id` beginning with `fanout:` and blocks each individual escape
  whose emitted wire length exceeds the limit.
- **Why:** a plane-termination phase reports connection success, not the
  electrical quality of each emitted pad escape. Direction selection, large
  pads, or a future router regression can still create a return longer than
  the product permits. A total routed-GND budget can pass while one critical
  decoupling return is bad, and does not identify which pad owns it.
- **Backward compatible:** yes. Products that omit the new member retain the
  existing ground-plane, total routed-length, stitching, island-connectivity,
  and pour-short checks. Products that declare it intentionally reject long
  fanouts that older packets accepted.
- **Mechanism:** `circuitpy.layout_intent` validates the new member and
  `verifylib.intent` measures compiled fanout copper per source trace, naming
  the offending authored trace in the finding. Focused tests cover schema
  refusal at zero and a misleadingly named 8mm fanout against a 2mm policy.
  **Skill runtime re-vendor required.**
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — Edge-connector intent uses the cable-mating datum
- **Change:** `product.layout.edgeConnectors` now measures a compiled
  connector's finite board-global `pcb_component.cable_insertion_center`
  against the requested outline edge and centreline. Components without that
  datum retain the footprint-body fallback.
- **Why:** `harness-puck` places its USB-C receptacle at the mechanically
  correct bottom edge: the cable insertion point is 0.052mm outside the
  outline, while the connector body is 2.33mm inside. Measuring the body
  rejected that valid placement; moving the part to satisfy the false metric
  put its body outside the board and overlapped nearby parts.
- **Backward compatible:** yes for connectors without a cable-insertion
  datum. A connector that declares the datum is intentionally judged by the
  mechanically meaningful mating location instead of its body bounds.
- **Mechanism:** `verifylib.model.Component` preserves the compiled global
  datum and `verifylib.intent` prefers it for both edge distance and transverse
  alignment. Focused tests cover an overhanging, centred mating point and the
  legacy body fallback. **Skill runtime re-vendor required.**
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — Power trunks are acyclic source-to-rail branches
- **Change:** golden `PowerTrunk` now accepts one physical `source` pad and one
  bare `net` name instead of two arbitrary component endpoints. It emits the
  only source-to-rail branch through two DNP boundary/probe pads; the consuming
  source block must omit its ordinary trace from that pad to the same rail.
  `UsbCData`, `UsbCPower`, and `Ldo3v3` now expose the typed
  `externalPowerTrunkPort` opt-out for their eligible source pads.
- **Why:** the first helper revision was electrically redundant when both
  component endpoints were already members of the named power net. One closed
  source-net cycle compiled by accident, while two independent V5/V3V3 cycles
  reproducibly stalled the pinned core at `Generating circuit JSON...` before
  any routing phase, including with routing disabled. The equivalent two-rail
  tree topology compiles cleanly, retains both 0.8mm trunks and four short
  0.2mm escapes, and contains no duplicate connectivity path. A timeout would
  contain the hang but would not make the authored circuit correct.
- **Backward compatible:** no for the unreleased `PowerTrunk` call signature.
  Calls using `from`/`to` must migrate to `source`/`net` and remove the source
  block's duplicate source-to-net trace. Existing boards not using the helper
  are unchanged.
- **Mechanism:** `packages/golden-blocks/blocks/glue.tsx` owns and validates the
  tree-shaped API. Its routed testbench compiles two rails simultaneously and
  asserts physical widths, escape lengths, absolute boundary positions, and
  that only each OUT leg owns the named rail. Golden-block consumers must
  re-vendor the helper explicitly; circuitpy's hard process-tree timeout
  remains crash containment, not a topology workaround.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — Exact-pinned autorouter fixes use board rules, isolated caches, and fail-closed output
- **Change:** setup now patches `@tscircuit/capacity-autorouter@0.0.782`
  and `@tscircuit/core@0.0.1642` deterministically. Pipeline7 and Pipeline9
  evaluate the SRJ's effective `minTraceToPadEdgeClearance` and
  `minViaEdgeToPadEdgeClearance`; their final outputs refuse unresolved DRC.
  The local route-cache identity includes the resolved pipeline/preset,
  effort, capacity hyperparameters and package version, phase/solver/fanout
  inputs, and clearance rules. Circuitpy also clears only its private mirrored
  `.tscircuit/cache` before a changed-effort retry and kills the complete CLI
  process group on timeout. A bounded core fallback retries automatically
  inferred fanout directions without overriding authored directions. For an
  ordinary `<autoroutingphase>`, an authored `region` now becomes that phase's
  SRJ `routingBounds`; reroute phases retain their separate region-crop
  semantics. Unknown local preset strings now fail descriptively instead of
  silently selecting the default capacity router. Fixed `pcbPath` and
  `pcbStraightLine` copper in the current subcircuit is preloaded into every
  later route phase instead of disappearing from the SRJ. Dynamic exact DRC
  keeps raw trace connection identities until the connectivity comparison, so
  same-net fanout drops and local ties may meet at their shared pad. It also
  grades preloaded wires/vias from their exact geometry rather than their
  conservative `trace_obstacle_*` rectangles used only for topology search;
  the approximation remains authoritative for `through_obstacle` route points
  that have no exact dynamic model.
  Finally, a net covered by `fanoutPourNetMap` no longer becomes a redundant
  aggregate capacity connection after its direct fanouts terminate at the
  plane; explicitly authored local ties remain routable.
- **Why:** the pinned Pipeline7/Pipeline9 evaluators graded a board declaring
  0.15mm copper-to-pad rules at 0.10mm and could return a route with a non-zero
  `finalDrcIssueCount`. Core keyed cached copper only by core version and SRJ,
  so a different pipeline or `5x` retry could silently reuse the first
  attempt. The earlier terminal-keyboard and harness-puck `5x` comparisons
  were therefore cache-contaminated and do not establish an effort benefit.
  The starter fanout fixture separately proved the fixed inferred direction
  could stop at 13 of 14 connections when the opposite exit reaches 14 of 14.
  Core also parsed ordinary phase regions without passing them to the phase
  SRJ: the exact Terminal QSPI input with all 504 board obstacles failed three
  of five critical connections globally, but the critical geometry plus a 6mm
  region (`x=-9.658..9.21`, `y=-10.7..24.5301`) routed 5/5 with zero exact
  repair DRC. Finally, pinned props accepts `autorouter="freerouting"`, while
  pinned core has no such local strategy and previously fell through to the
  default capacity router without warning. Core also excluded
  source-associated `pcb_trace` elements from obstacles, preserved only child
  subcircuit traces, and nevertheless suppressed the current source
  connection. Hydrate's fixed USB VBUS `pcbPath` consequently appeared in
  final Circuit JSON but in neither phase connections nor preloaded traces,
  allowing later SWD copper to route through it. Hydrate then exposed two
  connectivity/topology defects: premature canonicalization made a fanout and
  same-net local tie look unrelated at their shared pad, while the GND pour
  phase still emitted one unnecessary board-spanning aggregate GND
  connection. Terminal exposed a distinct false alarm: phase 2 reported
  0.137/0.104mm against synthetic rectangles sampled from phase-1 IO3, but
  exact segment geometry measured the IO0/IO3 edge gap at 0.354mm. Those
  rectangles are a conservative routing aid, not authoritative copper.
- **Backward compatible:** no, intentionally. Routes which violate the board's
  declared clearance or retain final DRC are now rejected instead of being
  accepted. Old route-cache entries miss once and rebuild under the complete
  configuration identity; a genuinely cold, stricter route can take longer or
  fail where stale copper previously appeared to succeed. Valid source and
  artifact schemas are unchanged. A formerly ignored ordinary phase region
  now constrains routing as authored, and unsupported preset strings now throw
  rather than changing router identity silently; custom local presets remain
  available through `platform.autorouterMap`. Fixed manual copper is newly
  authoritative during autorouting; arbitrary existing automatic route state
  is intentionally still rebuilt rather than frozen. Valid diagonal
  preloaded copper is no longer rejected by its routing approximation. A
  plane-terminated net intentionally loses only its redundant aggregate route;
  its fanouts, pour connectivity, and authored local ties are unchanged.
- **Mechanism:** `scripts/setup-toolchain.sh` runs the exact-version,
  exact-SHA, source-map-guarded, atomic patcher in
  `scripts/build/apply-toolchain-patches.mjs`; `toolchain/PATCHES.md` records
  the upstream defects and removal criteria. Focused fixtures cover
  P7-to-P9 cache isolation, final-DRC refusal, patch tamper/idempotency, and
  bounded fanout retry, ordinary-region SRJ bounds with unchanged reroute
  semantics, the 504-obstacle Terminal QSPI regression, and unknown-preset
  refusal/registered-preset acceptance. A fixed-horizontal/manual versus
  automatic-vertical crossing fixture proves the fixed trace enters the SRJ
  and forces the automatic route onto another layer, while an unmarked prior
  route is not preserved. A same-net fanout/local-tie fixture pins the raw
  connectivity identity fix. Parallel diagonal preloaded traces prove that a
  true 0.160mm gap passes without AABB false positives while a true 0.140mm
  gap still fails closed; a crossing `through_obstacle` fixture proves the
  conservative fallback also still fails closed. The plane-routing fixture
  asserts three direct GND
  fanouts plus one local tie, no aggregate GND connection, zero routing errors,
  and zero plane-connectivity/pour-short findings. A toolchain-only patch would
  require no skill
  runtime re-vendor; **skill runtime re-vendor is required here** because
  circuitpy's retry-cache clearing and process-tree timeout behavior also
  changed.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — Source-backed interactive component placement in the PCB viewer
- **Change:** the PCB workspace gains Move mode (`D`): click-drag a component,
  nudge it with arrow keys on a 0.25mm grid (`Shift` = 10 steps), preview its
  body and connection lines, and stage the exact old/new centre in chat. The
  request names the stable source component/group IDs and explicitly updates
  TSX, reroutes, and re-verifies; it never mutates generated `circuit.json`.
- **Why:** the Terminal EE review asked for the ordinary EDA operation of
  selecting a component and moving it left or right. KiCad couples move/drag
  with attached-track feedback and exact relative positioning; Altium keeps
  connection lines visible during placement. The code-authored product needs
  the same spatial feedback without creating a second, divergent board source.
- **Backward compatible:** yes. Existing selection, pan, measure, layer, and
  masking controls are unchanged; Move mode is opt-in. A staged ghost is
  discarded when a rebuilt artifact arrives.
- **Mechanism:** `boardPlacement.js` owns snap/nudge, same-face proximity,
  board-edge and ratline calculations plus source-backed request text;
  `PcbCanvas` owns the live drag ghost; `BoardWorkspace` carries the staged
  edit into chat context. Node and component tests cover the geometry and
  shortcuts.
- **Tracks affected:** client / docs.

## 2026-08-11 — Swallowed async effects fail closed
- **Change:** circuitpy reconciles the pinned CLI's
  `Async effect error ... "autorouting"` output with the compiled element
  array. If the CLI exits zero after an async route failure and omitted its
  matching `pcb_autorouting_error`, circuitpy appends and persists one before
  scan, retry selection, checks, or export. An already serialized error is not
  duplicated. Any rejected non-routing effect (including footprint loading,
  board DRC, silkscreen, or copper-pour solving) raises `CompileError` before
  export because circuit-json has no schema-safe generic async-error element.
- **Why:** the Terminal ground-plane phase completed the CLI process and wrote
  a partial `circuit.json` after only 20 of 31 fanouts reached the breakout
  boundary. The current compiler happened to serialize the failure, but the
  rejected effect and error serialization are independent async paths; process
  success is not evidence that the route completed.
- **Backward compatible:** yes for successful boards and boards whose routing
  error was already serialized. A formerly silent partial artifact is
  intentionally newly blocked. Autorouting retains its diagnostic artifact;
  other rejected effects refuse the partial artifact entirely.
- **Mechanism:** `packages/circuitpy/src/circuitpy/generation.py` normalizes the
  CLI log, reconciles the explicit async-autorouting signal into
  `circuit.json`, and rejects every other async-effect failure; focused
  fixtures prove output-only persistence, no-duplication, non-routing refusal,
  harvesting as an error, and no mutation on success. **Skill runtime
  re-vendor required.**
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — Sourced compact tactile-switch variant in reusable RP2040 furniture
- **Change:** golden `SwTact` and `Rp2040Core` accept
  `variant="compact"` / `buttonVariant="compact"`. The compact option is the
  pinned two-pin TPT-2C1 (JLC/LCSC `C2828561`) with its imported 3x2mm SMD
  footprint; the existing four-pin C318884 remains the default.
- **Why:** the Terminal Keyboard EE review requires smaller BOOTSEL and reset
  controls. Shrinking the old C318884 footprint would make the assembly model
  physically false, while inventing a terminal-only part would not improve
  future products. A sourced block-level variant makes the choice explicit and
  reusable.
- **Backward compatible:** yes. Existing consumers omit the prop and retain the
  standard switch and topology. Consumers selecting compact intentionally get
  a two-pin topology and must re-run placement and routing gates.
- **Mechanism:** `packages/golden-blocks/blocks/sw-tact/sw-tact.tsx` owns the
  variant, part identity, footprint, and topology; `Rp2040Core` propagates the
  option to both buttons. Compiled testbenches assert the LCSC identity, body
  envelope, port count, RUN/QSPI isolation, and zero parsed geometry errors.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — Mixed-width power trunks and physical plane-fanout connectivity
- **Change:** golden `PowerTrunk` creates an explicit fixed-width rail between
  two DNP probe/boundary pads while routing short endpoint escapes at a
  separately declared neck-down width. Its `start`/`end` points are
  board-absolute and are emitted without an auto-placeable wrapper, so the
  boundary pads and fixed copper cannot move independently. Golden `GndPlanes` and
  `GndFanoutTrace` reserve routing phase 10 for explicitly selected one-port
  ground drops, map exactly one fanout layer, and require declared stitch
  coordinates for multilayer pours. The independent product-intent verifier
  now emits blocking `pcb_plane_connectivity_error` when a fanout reported as
  plane-terminated reaches no material pour or a physically isolated
  non-dominant island. Same-net vias across poured layers are traversed before
  calling an island isolated. Different-net solved pour faces that touch or
  overlap emit blocking `pcb_copper_pour_short_error`.
- **Why:** the Terminal EE review requires 0.6–1.0mm V5/V3V3 trunks but the
  pinned router can promote one wide automatic trace across a whole net and
  make fine-pitch escapes impossible. A starter-board composition also proved
  an unnamed wrapper could auto-place the two boundary pads while leaving the
  authored trunk path behind, stretching wide copper through unrelated parts.
  Separately, the Hydrate fanout fixture
  reported 38/38 routes and zero compiler errors while `TR_C7_g` landed on a
  10.59mm² GND island disconnected from the 5931.08mm² material plane. Net
  labels alone were hiding a physical open.
- **Backward compatible:** yes for source schemas. Existing designs using
  ordinary traces still compile. A board that relied on a falsely successful
  plane termination is intentionally newly blocked.
- **Mechanism:** `packages/golden-blocks/blocks/glue.tsx` plus its compiled
  testbenches preserve 0.8mm/0.2mm mixed widths and prove per-drop plane
  fanout/stitching; `verifylib.intent` parses pour BREP faces, builds
  cross-layer connectivity through real vias, checks every `fanout:*`
  termination after pours are solved, and rejects different-net pour
  intersections. **Skill runtime re-vendor required** because verifylib
  changed.
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — Pipeline implementation revision invalidates artifacts and export caches
- **Change:** board sidecars gain `generatorRevision`, a SHA-256 identity of
  the runnable `circuitpy` and `verifylib` Python implementations. The
  unchanged-build shortcut and content-addressed exporter cache both require
  it to match. `scripts/review-packet` also refuses a missing or mismatched
  revision before writing any review output.
- **Why:** all three example investigations found source-fresh sidecars that
  could still contain pre-fix checker, normalization, or packet output because
  the old cache key covered only product source and external tool versions.
  A pipeline fix must never silently reuse the artifact containing the bug it
  fixed.
- **Backward compatible:** no for cached artifacts: legacy sidecars lack the
  revision and intentionally rebuild once. The new sidecar field is additive
  for readers.
- **Mechanism:** `generation.pipeline_revision()` hashes the installed package
  sources; `generation._unchanged_prior_result`, `export_cache.export_key`,
  and `scripts/review-packet` compare it. **Skill runtime re-vendor required.**
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — Product-level physical layout intent is a compiled-artifact gate
- **Change:** `product.json` gains optional `layout`. Its closed members are
  `boardSizeMm` / `boardSizeToleranceMm`, `minCopperClearanceMm`, ordered
  `componentSides` rules, `edgeConnectors`, `groundPlanes`, and `netClasses`.
  The clearance contract must be declared to the authoring router and also
  tightens the independent KiCad project rule. Net classes distinguish a
  wide trunk from a short endpoint neck-down (`minTrunkWidthMm`,
  `minNeckdownWidthMm`, `maxNeckdownLengthMm`) instead of forcing trunk width
  through fine-pitch pads. Stage 4c gains the independent `intent` verifier,
  which measures those declarations against `circuit.json`; contradictions are
  blocking `layout_intent_*` findings under the JLCPCB profile. Unknown layout
  members fail at spec time rather than silently disabling a misspelled rule.
- **Why:** the Terminal Keyboard EE review (2026-08-11) required an exact key
  envelope, front-side switch/diode population with electronics on the back,
  centred bottom-edge USB, dual GND pours, and 0.6–1.0mm power trunks. The old
  product contract could express only a maximum envelope and assembly yes/no,
  so the generator could violate every physical requirement and still believe
  it had implemented the product. The three examples also proved a single
  global trace width is not a net-class policy.
- **Backward compatible:** yes. Omitted `layout` is `{}` and adds no blocking
  requirements; the standalone verifier states the absent contract in
  coverage. Once a product declares layout intent, contradicting it is
  intentionally a build failure.
- **Mechanism:** `packages/circuitpy/src/circuitpy/layout_intent.py` validates
  the contract; `spec.py`, `generation.py`, and `verify_bridge.py` carry it to
  `packages/verify/src/verifylib/intent.py`; `fab.py` owns blocking policy. The
  standalone verify CLI auto-loads `product.json.layout`. **Skill runtime
  re-vendor required** (circuitpy and verifylib changed).
- **Tracks affected:** pipeline / skills / docs.

## 2026-08-11 — Product-selected Economic or Standard PCBA assembly tier
- **Change:** `product.json` gains optional `assemblyTier`, whose closed values
  are `"economic"` and `"standard"`; omission preserves the existing
  `"economic"` default. `ResolvedProduct.assembly_tier` carries the resolved
  value. Stage 4c passes it to the assembly/DFA check, so Economic applies its
  one-sided placement rule while Standard permits two-sided SMT placement and
  uses the Standard pitch floor. `assembly: false` continues to skip
  assembly-line-only rules regardless of the selected tier. The sidecar's
  `fab` object gains additive `assemblyTier`, and a ready packet's `ORDER.md`
  names the matching PCBA type and assembly side.
- **Why:** the terminal-keyboard review on 2026-08-11 explicitly requires the
  50 switches and 50 diodes on top and all other parts on the bottom. The
  verifier already knew JLCPCB Standard is two-sided, but circuitpy discarded
  that product intent and always called it as Economic, producing a false
  blocking `dfa_bottom_side` finding for an intentional Standard PCBA.
- **Backward compatible:** yes — existing product files omit the field and
  resolve exactly as Economic; existing Python callers also retain Economic
  defaults. The sidecar member is additive. Selecting Standard intentionally
  changes only tier-dependent DFA and order-walkthrough behavior.
- **Mechanism:** `packages/circuitpy/src/circuitpy/spec.py` (enum validation and
  `ResolvedProduct`), `verify_bridge.py` + `generation.py` (stage-4c
  propagation and sidecar), `fab.py` (tier-correct `ORDER.md`), with focused
  product-resolution, bridge, and walkthrough tests. **Skill runtime re-vendor
  required** (packages/circuitpy changed).
- **Tracks affected:** pipeline / skills / docs.

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

(No further entries yet.)
