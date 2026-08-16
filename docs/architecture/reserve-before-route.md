# Reserve before route

**Decision.** We build **one** reservation mechanism, in the pipeline, as a
graded retry: when stage 0c refuses a differential pair for want of room, the
build re-compiles the mirrored board source with a reservation written into it,
strips the reservation out of the artifact before anything reads it, and keeps
the reserved board only if the pipeline's own gate says it is not worse. The
reservation serves the **differential pair and nothing else**. It does not serve
the power rails, and the measurements below say no reservation ever will —
finding 4's fix lives in the route stage, not here.

**And there is a gate in front of it.** The shipped tscircuit bundle already has
a first-class `<differentialpair>` constraint we have never used, wired into the
router's own solver. That is a reservation with no geometry, no removal step and
no area cost. It gets spiked first, on the same injector this design builds, and
if it works the keepout corridor is not built for the pair at all.

---

## The corridor exists before the autorouter and does not exist after

This is the whole argument, and it is measured from both sides.

**From the placement side** — the widest channel between the pair's two ends,
obstacles = pads + drills + existing keepouts + board edge only, using stage 0c's
own ruler (`collect_obstacles`, `_Grid`, `half_span = pitch/2/MITRE_LIMIT +
width/2` — `diffpair.py:1454`):

```
/Users/d/miniconda/bin/python3.12 work/probe-placement/corridor2.py …
```

| board | placement-only probe | after the autorouter |
|---|---|---|
| harness-puck | corridor **YES** at 0.991 mm, YES at 0.574 mm | **NO**, NO |
| terminal-keyboard | **YES**, YES | **NO**, NO (10 free cells left at the connector end) |

**From the copper side** — one reserved rect on harness-puck, before and after:

```
/Users/d/miniconda/bin/python3.12 work/keepout-proof/measure.py <circuit.json> 2.4 -21.0 3.6 -7.5
```

| | before | after |
|---|---|---|
| trace segments crossing the rect | 12 | **0** |
| copper length inside | 4.822 mm | **0.000 mm** |
| vias inside | 2 | **0** |

The route was deterministic first, so the change is attributable: the committed
artifact and a fresh baseline build hash identically
(`work/keepout-proof/routehash.py`, `traces=158 vias=122 withIds=37cc6dbbb31d`),
and the reserved build hashes `46bc4e9f3016` — 158 traces still, 122 → 129 vias,
88 of 123 traces re-routed.

The mechanism is in the bundle, not inferred: the router's obstacle builder has
an explicit `element.type === "pcb_keepout"` branch pushing an oval or rect with
`connectedTo: []`. **A keepout is an obstacle the autorouter honours and it is
not copper.** Ledger #45.

## The declaration is tried before the corridor, because it costs nothing

Read in the shipped bundle today
(`grep -o … toolchain/node_modules/tscircuit/dist/webworker.min.js`, verified
2026-08-16 in this session):

- `DifferentialPair:()=>DifferentialPair` is in the component registry (offset 1,949,876).
- `differentialPairProps` = `{name?, positiveConnection, negativeConnection, maxLengthSkew, pcbTraceGap, maxUncoupledLength, targetDifferentialImpedance}` (offset 3,277,883).
- `subcircuitComponent.selectAll("differentialpair")` → `getDifferentialPairsForSimpleRouteJson` → `simpleRouteJson.differentialPairs` (offsets 8,568,598 / 8,549,309).
- A `LengthMatchingSolver` consumes `params.differentialPairs` (offset 5,654,978), and the solver params carry `minimumCenterlineDistance` / `maximumCenterlineDistance` (18 and 19 occurrences).

That is the constraint reaching the router **before it routes** — which is what
ledger #12 has been asking for since it was opened. One element, no geometry to
compute, nothing to remove, nothing to strangle.

**Spike D, before any corridor work.** Inject
`<differentialpair positiveConnection="net.USB_DP" negativeConnection="net.USB_DM"
pcbTraceGap="0.2mm" maxLengthSkew="3.8mm" />` into the mirrored source of
harness-puck and terminal-keyboard with the injector this design builds, rebuild,
and measure with `verifylib.netclass` plus the attempt-level blocking count. Two
builds, ~250 s and ~300 s.

Two failure modes are already visible in the bundle text and should not surprise
the spike: the resolver **throws** `Could not find an SRJ connection for trace
name or port selector` when the pair's connection cannot be matched one-to-one
(our D+/D− reach the MCU through a *net*, and net connections are a different
code path from `source_trace`), and it throws `Differential pair … cannot be
split across autorouting phases`.

**If the declaration delivers the pair on both boards at no cost in blocking
findings, we stop here.** `reserve.py` keeps its injector and its strip pass, the
corridor planner is never written, and this document records why.

## Reservation is a retry, not a stage that always runs

A reservation is a denial to every net on the board — 16.2 mm² re-solved 88 of
123 traces — so the default is not to make one. It fires only where it has a
consumer: a pair stage 0c refused *for want of room*.

The build shape is stage 0b's escalation, exactly:

1. Attempt 1 compiles and runs 0a → 0b → 0c → 0d → 0e, then `_scan`. Unchanged.
2. The effort escalation runs as it does today, and keeps whichever attempt it keeps.
3. **Stage 0R** fires iff the kept attempt's `diffPair` carries
   `status="refused"` with `reasonCode="no_corridor"`. Everything else — routed,
   skipped, refused for any other reason — is not a consumer.
4. The corridor is planned from the kept attempt's **own placement**, the
   reservation is written into the mirrored entry, and the board is rebuilt.
5. The reserved attempt is graded against the kept attempt and thrown away
   whole unless it wins. Attempt directories are copied, not rebuilt, the way
   stage 0b already does it (`generation.py:1099`).

Compiles are capped at **3** for the whole build (one baseline, one effort rung,
one reservation) and stage 0R is skipped when less than one measured compile of
wall-clock budget remains.

## The geometry comes from attempt 1's placement, not from a second compile

Placement is invariant under everything that happens here, and both directions
are measured:

```
/Users/d/miniconda/bin/python3.12 work/probe-placement/padcompare.py probe.circuit.json examples/harness-puck/boards/main.circuit.json
```

- A `routingDisabled` probe build against the routed artifact of record:
  **287 of 291 identical** (224 smtpads, 4 plated holes, 5 holes, 61 components,
  1 outline). The 4 that differ are the USB-C polygon pads, and the only
  difference is the repeated closing vertex stage 0a opens (ledger #33).
- The same script on baseline-final against reserved-compiled: **295 of 295
  identical**. The reservation does not move the thing it was computed from, so
  no fixed-point iteration is needed.

So v1 needs no probe: the corridor is planned from the attempt we already built,
reading **placement elements only** — `pcb_smtpad`, `pcb_plated_hole`, `pcb_hole`,
`pcb_keepout`, courtyards, the board outline — and explicitly **ignoring
`pcb_trace` and `pcb_via`**, because that copper is attempt 1's and the rebuild
re-solves it (122 → 129 vias on the measured build).

The `routingDisabled` probe stays documented and unbuilt. It is cheap and
faithful — 31.33 s on harness-puck, 19.15 s on terminal-keyboard against 247.71 s
for a routed compile of the same copy
(`/usr/bin/time -p toolchain/node_modules/.bin/tscircuit-cli build boards/main.tsx --disable-parts-engine`)
— and it is what the plan-time endgame below will need. It is not needed by the
retrofit path, and an unbuilt moving part cannot fail.

## The exact geometry we emit

**Consumer.** The one pair stage 0c refused. Its two anchors are the two
`pcb_port` pairs the pass already resolved.

**Width.** The pass's own number, plus a margin:

```
half_span   = pitch / 2 / MITRE_LIMIT + width / 2      # diffpair.py:1454
reserve_r   = half_span + RESERVE_MARGIN_MM            # RESERVE_MARGIN_MM = 0.15
```

The margin is not decoration. With the corridor measurably empty (0 segments,
0 vias, both reserved builds), **stage 0c still refused**, because its A* rasters
at `GRID_MM = 0.05` and the emptied corridor left **0.0093 mm** of slack over its
dilation at the tightest point (worst legal half-width 0.5050 mm against 0.4957 mm
required, set by `pcb_smtpad_156`). A reservation sized at exactly the pass's
minimum is a reservation the pass cannot enter. 0.15 mm is three raster cells;
**the smallest margin stage 0c can actually use is unmeasured, and the first
reserved build measures it.**

**Layer — one, and never the poured one.** Measured today:

```
/Users/d/miniconda/bin/python3.12 -c "… Counter(e['layer'] for e in els if e['type']=='pcb_copper_pour')"
harness-puck 18 pours bottom · terminal-keyboard 31 pours bottom · hydrate-coaster 2 pours top
```

The pour solver has **0** references to `keepout` against 7 to `pcb_cutout`
(`grep -o keepout toolchain/node_modules/@tscircuit/copper-pour-solver/dist/index.js`),
and on the proof rect the bottom pour went **13.5025 → 16.0775 mm²** of a
16.200 mm² rect — it filled *more*. A reservation on a poured layer reserves
nothing.

Reserve the **single-layer** corridor on the un-poured layer first. It is the
narrower ask, from the pass's own refusal text
(`jq '.build.diffPair.pairs[0].reason' examples/*/boards/main.board.json`):
harness-puck needs **0.57 mm** one-layer against 0.99 mm two-layer;
terminal-keyboard **0.77 mm** against 1.04 mm. On both of those boards the
un-poured layer is top and the pour is bottom, so the corridor also puts a
continuous reference plane under the pair — the EE's third clause, which is
**0% on all three boards** today (`netclass_pair_reference`). Escalate to the
two-layer corridor only if the one-layer attempt is refused by the gate, and
only inside the area cap.

**Shape — a chain of overlapping circles.** `pcb_keepout` has exactly two shapes
in circuit-json: axis-aligned `rect` (no rotation field) and `circle`. A rect is
not rotation-safe — the renderer computes `isRotated90` and swaps width/height
only at 90/270 degrees (bundle offset 8,128,950), so at any other angle it emits
the wrong shape silently. A corridor that turns is therefore discs of radius
`reserve_r`, spaced `≤ reserve_r` along the planned centreline. Measured shape on
harness-puck: **74 discs of r = 0.4957 mm over a 36.35 mm path** (0.99× the
straight line), **36.8 mm² swept**, 0.96% of the 3848 mm² board.

**Centreline.** A* over the placement-only obstacle field using
`diffpair._Board` → `collect_obstacles` → `_Grid.block(o, reserve_r)` — the
consumer's own primitives, so the channel reserved is the channel the pass later
asks for. A corridor planned with a different ruler is a corridor the pass
cannot use, and that is not a hypothesis: it is the 0.0093 mm above.

**End pockets.** `_pair_seed` only looks within `SEED_RADIUS_MM = 2.5 mm` of the
pad-pair midpoint and needs a straight fan line through single-track free space.
At each anchor the planner reserves the largest disc centred on the pad-pair
midpoint that touches no pad, drill, courtyard or foreign keepout, capped at
1.0 mm radius. **If that disc is smaller than `reserve_r`, refuse before
building** — a corridor the pass cannot enter is pure cost.

**We do not invent a keep-off distance for fine-pitch escapes.** The measured
damage from the two graded reservations was 6 and 16 new
`pcb_via_trace_clearance_error` at **0.056–0.088 mm against a 0.100 mm floor, all
around U3** — the RP2040's 0.400 mm-pitch QFN escape. That is evidence that
reserving near a fine-pitch escape costs clearance; it is not a threshold. v1
reserves the trunk and the largest clear pockets and lets the gate decide, and
the *next* measurement sets a keep-off number if one is needed. Inventing one
now would be a number with no ruler behind it.

**Injection.** One text edit on the mirrored entry only, immediately after the
`<board …>` tag — exactly `_set_autorouter_effort`'s pattern
(`generation.py:666`, "only ever touches the copy inside `.circuit/build/`").
The user's file is never written.

## Removal is by the record we wrote, matched on numbers, exactly one match or nothing

**There is no provenance field to delete by, and that is measured, not assumed.**

- The emitter inserts exactly `{layers, shape, radius|width/height, center,
  subcircuit_id, pcb_group_id, excluded_pcb_component_ids?}` — no name, no
  description (bundle offset 8,128,892).
- `pcbKeepoutProps` accepts `name` and never emits it; `pcb_keepout.description`
  exists in the circuit-json schema and nothing writes it.
- `subcircuit_id` does not separate authors: all four of harness-puck's keepouts
  report `subcircuit_source_group_12`, the board root, though one comes from
  `usb-c-power`'s belly rect and three from `MountingHole`.
- **Ids renumber.** In the proof build the new element came back as
  `pcb_keepout_1`; in the baseline `pcb_keepout_1` is a mounting-hole keepout.
  Deleting by id deletes ledger #7's fix.

So `reserve.py` records the exact tuple it wrote — `(shape, layers, center x/y,
radius or width/height)` — and the strip pass matches on that tuple at 1e-6
tolerance and **requires exactly one match per entry**. Zero matches or two:
strip nothing, discard the whole reserved attempt, restore attempt 1, and say so.
That rule is the only thing protecting a user's keepout, and it protects the
block-authored ones too: every keepout on every board today is block-authored
(harness-puck 4, terminal-keyboard 7, hydrate-coaster 5 —
`Counter(e['type'] for e in els)`, re-run this session), and ledger #1 and #7 are
closed by exactly those elements.

**Never "delete all keepouts". Never delete by id. Never delete by type.**

## Order relative to stages 0a–0e

| when | what | why there |
|---|---|---|
| before the compile | **0R reserve** — plan, inject into the mirrored TSX, rebuild | the router runs *inside* the tscircuit compile, so a reservation can only be born in TSX |
| after `_canonicalise_file` | 0a — open closed polygon rings | unchanged |
| **immediately after 0a** | **0R′ release** — strip the recorded keepouts from circuit.json | everything downstream reads a keepout as a hard obstacle at clearance **0.0** (`diffpair.py:761`, and `powerwidth.py:578` through the same collector), so a corridor left standing blocks the pass it exists to serve *and* necks every rail beside it. Our own router must see the true board too |
| 0b | router bridge / effort escalation | unchanged |
| 0c | pair pass | now runs on a board whose corridor is clear by construction |
| 0d | power widening | unchanged, and now cannot be capped by our own invention |
| 0e | pour clearance | unchanged, last, sees the final copper |

Stripping is also what keeps the packet honest: keepouts never reach the fab
today — harness-puck's committed `kicad-project.zip` contains **0** occurrences
of `keepout` or `rule_area` while its circuit.json carries 4 (re-run this
session) — so a reservation left in the artifact would be a routing restriction
no source explains and no consumer honours.

## What it refuses to do

1. **No consumer, no reservation.** Stage 0c must have refused with
   `reasonCode="no_corridor"`. A board whose pair routes never gets one, and
   nothing is ever reserved speculatively.
2. **Two-terminal pairs only.** The connector-side `USB_DP_CONN/USB_DM_CONN` has
   5 pads per net and stage 0c skips it; we do not reserve for a pair no pass can
   route.
3. **No corridor on the placement-only field → refuse and name the pinch.** The
   blocker is placement, not routing, and that message is actionable. On
   harness-puck the measured pinch is the switch row: SW1→SW2 **0.500 mm** and
   SW3→SW1 **0.500 mm** (`work/keepout-proof/blocks-angle/strip2.py`), against
   0.99 mm needed two-layer. Never narrow the requirement to make a corridor
   appear.
4. **No enterable end pocket → refuse before building.**
5. **Zero overlap.** Any intersection with a pad, drill, courtyard, board-edge
   margin or a keepout we did not write is a refusal. Not resolve, not merge,
   not shrink, not move, not delete. A foreign keepout that closes the only
   channel is named in the report.
6. **Never reserve on a poured layer.**
7. **Never strangle the board.** Remove the corridor from free space and check
   that every net's terminals still lie in one connected component of free space
   on the union of layers. If any net is cut, refuse.
8. **Area cap: never exceed a size whose cost has never been measured.**
   `RESERVE_MAX_AREA_MM2 = 53.3` — the largest reservation that has been built
   and graded at all. It ratchets *down* to the largest reservation that has ever
   graded clean, once one exists. Today that number is zero, so the gate is
   carrying the whole load and this document says so.
9. **Never reserve for a power rail.** See the next section.
10. **The gate decides, and it is the pipeline's.** Keep the reserved attempt only
    when, against the attempt it replaces: the attempt-level blocking count
    (`sum(severity == "error")` over `_scan`, the identical number stage 0b
    escalation uses and the sidecar reports as `blockingByAttempt`) is **not
    higher**; no `pcb_trace` is lost; and the pair's measured skew and coupled
    fraction both improve. Otherwise attempt 1's whole directory is restored and
    the sidecar reports the attempt and the reason it lost.
    *Not-higher rather than strictly-lower, on purpose:* the escalation exists to
    remove blocking findings, so it must remove one; the reservation exists to buy
    pair geometry, so it must not cost one.
11. **A gate that cannot run is a refusal, not a pass** (ledger lesson E).
12. **Never change a check, a floor or a rule to make a board pass.** Not the
    clearance floor, not `warn_power_trace_mm`, not where
    `dfm_power_trace_width` measures.

**What the gate says on the board we have measured.** Both graded reservations on
harness-puck lose:

| | pair skew | coupled | worst gap | attempt-level blocking |
|---|---|---|---|---|
| baseline | 10.394 mm | 1.2% | 7.379 mm | **2** |
| 0.991 mm corridor | 14.407 mm | 2.7% | 19.297 mm | 8 (+6 via/trace clearance, +1 hole clearance) |
| 1.292 mm corridor | **0.494 mm** | 31.1% | 2.664 mm | 19 (+16 via/trace clearance, +1 hole clearance) |

The wide reservation gives the EE his pair geometry — **10.394 → 0.494 mm skew**,
inside the 3.8 mm USB 2.0 budget — and breaks 16 other clearances at
0.056–0.088 mm against 0.100 mm. Under this repo's own rule that is a failed
experiment and it gets reverted. So the honest current answer for harness-puck is
*"refused, and here is the pair geometry it would have bought if the board had
53 mm² to spare"* — and that points at placement, which is where it belongs.
Neither of those two attempts was the single-layer, un-poured-layer corridor this
design specifies (0.57 mm + margin on top, ~0.87 mm swept), which is the narrowest
reservation that can serve the pass and has **not** been measured.

## Finding 4 is not closed by this design, and no reservation closes it

Three measurements, not an argument.

**1. Half the necks do not exist when a reservation is planned.** Re-running the
shipped widener as an instrument over the three committed artifacts
(`powerwidth.widen_power_traces(copy, profile, grade=…)`; the same values are in
each sidecar under `build.powerWidth.nets[].limiter`, re-read this session):

| board | rail | limiter |
|---|---|---|
| harness-puck | V3_3 | `pcb_smtpad_94` (pad) |
| harness-puck | V3_3_LED, V5 | none named |
| hydrate-coaster | GND | `pcb_via_64.drill` |
| hydrate-coaster | V3_3 | `pcb_smtpad_82` (pad) |
| hydrate-coaster | V5 | `pcb_via_84.drill` |
| terminal-keyboard | V3_3 | `pcb_via_28.pad` |
| terminal-keyboard | V5 | `pcb_smtpad_420` (pad) |

Three of eight are pinned by a **via**, and every via on these boards is router
output — pre-route via count is 0, harness-puck's post-route count is 122 against
224 pads, a 54% growth in the obstacle field that no planner can see. You cannot
reserve around an obstacle the router has not invented yet.

**2. Two of the three pad-pinned necks are geometrically unreachable.**
harness-puck's `pcb_smtpad_94` and hydrate-coaster's `pcb_smtpad_82` both resolve
to **U3 pin 47** — the RP2040 QFN-56 escape, 0.200 mm pads on 0.400 mm pitch, so
0.200 mm of copper-free space between neighbours. 0.5 mm of rail cannot exist
there at any router effort with any keepout. `dfm_power_trace_width` reports the
minimum, so the check keeps firing, correctly.

**3. A keepout reserves emptiness; a rail needs copper.** A keepout is net-blind
by construction — the obstacle builder pushes `connectedTo: []` (offsets
2,274,878 and 8,017,872) and never reads `excluded_pcb_component_ids`. You can
deny space to everyone; you cannot grant it to a net. An empty 0.5 mm strip does
not make the router lay 0.5 mm copper in it: it still routes at
`minTraceWidth`, and stage 0d still has to widen afterwards — now into a strip
whose position was guessed. And a rail is not a corridor between two points:
harness-puck's V3_3 is a **25-terminal Steiner tree** (24 traces / 25 distinct
`pcb_port`s by `source_trace → source_net`), V3_3_LED 21 pads across 20 parts
spanning the whole board. There is no A and no B.

**Where the fix lives.** `routerlib` already models what the shipped autorouter
cannot: `net_class`, `min_width_mm` and `diff_pair`/`diff_partner` on its net
model (`packages/router/src/routerlib/bench.py`), which is `router_bridge.py`'s
own stated reason for existing. Per-net-class widths **applied while routing** is
ledger #31's named lever and pipeline-v2 §5's second pass. That is a build in the
route stage, not a reservation, and this design does not pretend otherwise.

**The second mechanism is refused, and this is the justification the brief asked
for.** A rail "spine" — one wide reserved channel from the regulator toward the
densest consumer cluster — would cost a second planner, a second removal path, a
second area budget and a second way to strangle a board, and by the measurements
above it would leave every one of the eight necks exactly where it is on at least
five of them and provably all of them on two. Two mechanisms for one closed
finding and zero for the other is the wrong trade. One mechanism, one consumer.

## What it costs, and what has to move with it

- **Builds.** One extra compile on a board that gets a reservation (247.71 s
  measured on harness-puck, `/usr/bin/time -p`), and the retry can compound with
  the effort escalation to three compiles. Capped at 3, and skipped when the
  wall-clock budget cannot pay for one.
- **Global re-solve.** 16.2 mm² re-routed 88 of 123 traces and added 7 vias
  (+5.7%). A reservation is never a local edit; every board that gains one is
  re-graded end to end.
- **Cache key.** The reserve planner's identity joins
  `_unchanged_prior_result`'s key beside the source fingerprint, the toolchain
  block and the vendored-runtime identity, or the build refuses. Otherwise every
  existing packet keeps claiming a verdict earned under a planner that no longer
  exists — ledger #42, exactly.
- **Sidecar, always.** A new `build.reserve` block: whether it fired, which pair,
  layer, span, margin, disc count, area and area-as-fraction, path length against
  the straight line, blocking before and after, the pair's before/after skew and
  coupling, kept or discarded and why, and how many elements the strip pass
  removed. **A reservation that fired and lost still gets reported** — an absent
  value makes no claim, and nothing checks a claim nobody makes (ledger lesson F).
- **`diffpair.py` gains a machine-readable refusal.** `reasonCode`, `needMm` and
  `layerMode` beside today's prose `reason`, because stage 0R keys on it.
- **`scripts/shift-left-check` gains the envelope rule** (worth shipping whether
  or not the corridor is): a block-authored `<keepout>` must lie wholly inside its
  own courtyard envelope. `usb-c-power`'s belly rect and `MountingHole`'s circles
  both satisfy it, and nothing today would catch one that did not — `keepout`
  appears in `checks.py` exactly once, in a BOM word list.

## How we will know it worked

Per board, against the committed baseline at 6205ad6 (all three `ready=true` with
ORDER.md written — that is the floor):

- **hydrate-coaster** — routed today (coupling 7% → 95%, skew 13.91 → 1.95 mm).
  Must not change. No reservation fires; the consumer test excludes it.
- **harness-puck, terminal-keyboard** — pair skew ≤ 3.8 mm, coupled fraction ≥
  90%, attempt-level blocking not higher than 2 and 1 respectively, `fab.ready`
  still true, no trace lost.
- **`netclass_pair_reference`** — 0% on all three today. A top-layer corridor over
  a bottom pour should move it for the first time. Measure it; do not promise it.

**One known blind spot in the gate, named rather than papered over.** The
attempt-level blocking count is circuit.json's verdict, and `[clearance]` and
`[shorting_items]` are KiCad's, three stages later — ledger #27, the identical
hole the effort escalation has. A reservation that adds no attempt-level finding
but breaks something only KiCad sees would be kept. Closing #27 closes it here
too; until then the reserved build's full verdict is read before the board is
called done.

## What we are deliberately not building yet

- **The planner emitting corridors into the `.tsx` it writes.** This is the right
  endgame — the corridor belongs with the placement that fixed the pair's
  endpoints, the same arc `autorouterEffortLevel` took from pipeline injection to
  the skeleton — and the measurement that makes it possible already exists: the
  pair's exit offsets are stable per block and transform correctly
  (`usb-c-data R3.pin2 = origin + (+2.01, +7.000)` on 3/3 boards;
  `rp2040-core U3.USB_DP = origin + (+1.00, +3.425)` on 3/3; hydrate's rotated
  group predicted (−21, −25.425), measured (−21, −25.425)). It is not built
  because **all three of our boards are hand-placed** — none called
  `place_board` — so a planner-emit scheme covers zero of the boards the EE
  reviewed, and because an absolute rectangle written into a file a user then
  edits is a stale obstacle bought for nothing.
- **`pair_claims` on blocks.** A frame-free declaration of where a pair leaves and
  arrives is the right shape for the plan-time path, and v1 has no use for it:
  the consumer is a refusal that already names the pair and resolves its ports.
- **The `routingDisabled` probe.** Cheap and faithful, needed only by the
  plan-time path. Numbers above so nobody re-measures them.
