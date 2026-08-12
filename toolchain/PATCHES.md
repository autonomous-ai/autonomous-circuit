# Pinned toolchain patches

`scripts/setup-toolchain.sh` applies
`scripts/build/apply-toolchain-patches.mjs` after every clean install. These are
temporary, exact-version fixes for defects in the pinned tscircuit stack; they
do not change the selected default router.

The patcher refuses fuzzy application. It checks the npm package version, the
complete input and output SHA-256, every replacement count, and (where the
package ships one) the audited source-map source. A toolchain upgrade must
therefore remove the obsolete patch or deliberately rebase it.

`scripts/tests/toolchain-patches.test.mjs` also replays the complete capacity
chain after reconstructing the exact npm-pristine bundle from any recognized
installed endpoint. This is the restart boundary: a one-byte edit inside a
compiled replacement payload fails before it can strand `npm ci` / `dev.sh`
halfway through the chain, even when the shared install still contains the
previous valid final bundle.

## `@tscircuit/props@0.0.618`

Trace props now expose the opt-in boolean `authoredNetTreeBoundary`. It marks
the one authored port-to-named-net edge that terminates a local physical
routing tree. The runtime Zod schema and all three generated input/output type
shapes are patched together, so JSX type checking and the compiled trace
component see the same contract. Unmarked traces parse exactly as before.

The exact runtime transition is
`ed97ae48b9af99131ab6bf57c4c66345d2b091215bb7e788eba955e6ea08b257 ->
1bb4a3838f05e926bf5400978156b4184a6b70f62adc94d6d47b0fe8c356da98`;
the declaration transition is
`d07c579a868b831e6968ef8eada02be4de36f710336e653a8c2f8921d70e6883 ->
45d5570d87d256212596ad84908f5066f75b2294b97dd4b5ce78fb0e52e40a6d`.
The runtime stage also guards the audited
`lib/components/trace.ts` source in the published source map. Remove these
stages only when the pinned props package ships the boolean in both its parser
and generated declarations.

## `@tscircuit/capacity-autorouter@0.0.782`

- Pipeline7's exact evaluator hard-coded 0.10 mm. Pipeline9 spread the same
  0.10 mm preset into its joint evaluator. Both now derive the effective
  clearance from the board's `minTraceToPadEdgeClearance` and
  `minViaEdgeToPadEdgeClearance` instead of grading 0.15 mm boards at 0.10 mm.
- Pipeline7's exact DRC portfolio could set `solved=true` with a non-zero
  `finalDrcIssueCount`. It now fails when used as Pipeline7's final repair.
  Pipeline9 intentionally uses that portfolio as a preliminary pass, so its
  preliminary output remains allowed; Pipeline9 instead rechecks and refuses
  unresolved DRC after its terminal and regional repair passes.
- The lightweight exact engine resolved each dynamic trace's connection name
  to a representative too early, then asked the connectivity map about those
  representatives. That loses source-topology aliases: a fanout drop and a
  local tie on the same net could be rejected where they meet at their shared
  pad. Dynamic geometry now retains the raw `connection_name`; the engine's
  single `areConnected` boundary performs canonicalization and connectivity
  lookup exactly once.
- Pipeline7 turns preloaded traces into conservative axis-aligned rectangles
  for topology search. Its exact evaluator also consumed those routing-only
  rectangles even though it already evaluates the same preloaded wires and
  vias as exact dynamic geometry. On diagonal traces, that double
  representation produced false 0.104/0.137mm findings against copper whose
  true edge gap is 0.354mm. The exact evaluator now excludes only generated
  wire/via `trace_obstacle_*` rectangles; topology search still sees them, and
  the original preloaded wires/vias remain in the final exact DRC input.
  `through_obstacle` route points do not yet have an exact dynamic model, so
  their conservative obstacles deliberately remain in DRC.
- `mergeConnections` normally unions every connection that shares a point.
  That is useful for implicit net stars, but it collapsed a marked authored
  tree into a new minimum spanning tree and discarded the designer's explicit
  non-collinear edge pairs and widths. Core now annotates only validated local
  tree edges with `__preserveConnectionTopology`; capacity excludes those
  edges from its DSU union while leaving every legacy connection merge
  unchanged. The exact final transition is
  `a4fac8c772142d037d80eb78f42e7751767ba3095c0e2b9fa3b7c58125c4af99 ->
  471c49fbb77192e8161ac8dadbb3b51781c10b19dfc088a952964c71ded114b7`, guarded
  against the audited `NetToPointPairsSolver/mergeConnections.ts` source.
- Native differential-pair routing dropped `maxUncoupledLength`, Pipeline9 did
  not convert `pcbTraceGap` into a centerline constraint, and the shared
  postprocessor could throw on a 180-degree spine station or export modified
  copper without a terminal DRC gate. Both pipelines now retain the complete
  pair contract, use the final routed widths to resolve edge gap, measure
  length skew and total uncoupled planar copper after postprocessing, and
  recheck final DRC. A bounded outgoing-normal fallback handles the degenerate
  reversing station. Pipeline9 may reuse its already-clean input only when the
  postprocessor returns byte-identical copper; changed output without an exact
  evaluator fails closed. The exact final capacity transition is
  `471c49fbb77192e8161ac8dadbb3b51781c10b19dfc088a952964c71ded114b7 ->
  e9646104761010ac37d935e839781b0a755870a7e56f0db7cfd4ccd9dbc7a973`.
  The published source map guards the length-matching, Pipeline7, and Pipeline9
  sources that the bundled replacements were audited against.
- The exact repair engine never compared routed vias with SMD-pad obstacles,
  so an unrequested same-net via at a pad center had zero DRC cost and could be
  accepted unchanged. Physical single-layer SMD pads now participate in exact
  via-edge clearance using `minViaEdgeToPadEdgeClearance`. A movable via gets a
  targeted force away from that pad; on boards with several vias the repair
  moves only the reported via. An immovable terminal via remains an unresolved
  exact finding and fails closed. Same-net via-in-pad remains available only
  through the existing explicit `allowViaInPad` contract, which never exempts
  a different-net pad; plated through holes retain their existing legal
  same-net behavior. The exact final capacity transition is
  `e9646104761010ac37d935e839781b0a755870a7e56f0db7cfd4ccd9dbc7a973 ->
  ce318ec3a3120490459c0c5cdaea710a1345884aeb5f88f559188c255ab9c318`.
  The source-map guards cover both the exact DRC engine and its force helper.
- The bundled differential-pair composite grid could connect two distinct node
  IDs whose coordinates differed only by floating-point roundoff. In the USB
  regression, the symmetric pair midpoint was `-1.1102230246251565e-16` while
  the matching coarse-grid boundary was exact zero. `createDirection` quite
  correctly rejects an edge of length `<= 1e-10`, but `connect` admitted it,
  so a valid bounded search aborted with `composite grid produced a
  zero-length planar edge`. The graph builder now refuses adjacency at the
  same geometric tolerance as its direction consumer. Nonzero grid edges,
  endpoints, layer changes, pair constraints, and iteration bounds are
  unchanged. The exact transition is
  `ce318ec3a3120490459c0c5cdaea710a1345884aeb5f88f559188c255ab9c318 ->
  38b34259144c87a5040b02a4f3958760ead65a3700550e4b205d1dab642a5853`.
  A two-connection/one-obstacle cold fixture proves the graph remains
  connected under a permissive coupling contract and that an impossible 3mm
  contract fails on measured uncoupled copper rather than an internal graph
  exception. The retained USB composition likewise advances to its real
  `11.438486mm > 3mm` uncoupled-copper blocker; this patch does not weaken or
  claim to satisfy that product constraint.
- `TraceWidthSolver` treated each connection's `nominalTraceWidth` as a
  preference: it tried that width, then a midpoint, then silently serialized
  the board-wide minimum. That allowed an authored 0.25mm CC trace to leave
  the router as 0.15mm copper even though core had preserved the 0.25mm source
  request in the phase SRJ. An explicit per-connection width is now a hard
  minimum, floored at the board minimum. The solver tests that one width and
  fails at the trace-width phase when it does not clear; it never falls back
  or applies a terminal taper below the request. Connections without an
  explicit width retain their prior pass-through behavior. The exact
  transition is
  `38b34259144c87a5040b02a4f3958760ead65a3700550e4b205d1dab642a5853 ->
  e7c2ab3d003ad010db4a648cfb15355256763c226bbf146f8f491640d321780c`.
  A cold corridor fixture proves both sides: a corridor that accepts 0.15mm
  but not 0.25mm now terminates with a descriptive routing error and no thin
  output, while a wider twin serializes every wire segment at 0.25mm. Separate
  one-point and narrow-terminal cases pin the no-undercut rule.
- Pipeline7's RectDiff seed mesh has a directional tie-break: the same legal
  two-layer USB pair solved on top but, after an exact X/layer mirror, chose a
  different bottom mesh and failed its explicit 0.15mm width at an adjacent
  connector pad. A literal mirror of the successful top copper passed the
  full bottom routing checks, so changing product placement or weakening the
  clearance would only hide a solver asymmetry. Pipeline7 now preserves the
  original attempt exactly and starts one bounded retry only after that
  attempt reports failure. The retry reverses every layer-bearing SRJ field
  (connections, obstacles, preloaded wires/vias, named and numeric Z fields)
  and structured P7 option, runs the same exact DRC and native pair gates,
  then maps only a successful result back to the authored layer names and
  unchanged IDs. It cannot recurse, and if both orientations fail it exports
  no copper and reports both causes. Retry-internal cache keys use the
  `p7-layer-reversal-v1:` namespace. The exact capacity transition is
  `e7c2ab3d003ad010db4a648cfb15355256763c226bbf146f8f491640d321780c ->
  6d9e591861f3e6cc66af1cf86d230fdd0ac3a7673ec6f2565a2466527bf9a8b7`.
  A reduced cold fixture proves the pristine top output is byte-identical and
  does not retry, the bottom retry is its physical mirror with identical
  endpoints and zero final DRC, and an impossible width corridor still fails
  both attempts. Connections without an explicit width retain their legacy
  board-floor behavior.

The upstream lightweight engine exposes one generic trace/copper clearance,
not distinct trace-to-pad and via-to-pad obstacle rules. The patch uses the
larger declared value for that generic rule and keeps the full final
`@tscircuit/checks`/KiCad verification in circuitpy authoritative. It can make
an impossible dense route fail honestly; it cannot manufacture the missing
board area or routing channel.

Audited upstream source paths are recorded in the patch manifest. Remove this
patch only after a candidate capacity-router package both consumes the SRJ
rules and refuses non-zero final DRC in the synthetic cache/clearance fixture
and the dense-board gauntlet.

## `@tscircuit/core@0.0.1642`

The local route-cache key was `core version + SimpleRouteJson hash`. It omitted
the resolved pipeline, effort, capacity solver version and hyperparameters, so
a Pipeline9 or 5x retry could receive Pipeline7/default copper without running.
The key now includes:

- core and capacity-router versions;
- resolved preset/pipeline and effort;
- capacity depth and target capacity;
- phase/stage and solver-mode flags;
- fanout inputs; and
- normalized effective clearance inputs.

The pinned CLI exposes no cache-disable flag. Circuitpy additionally deletes
only its private build mirror's derived `.tscircuit/cache` before a changed
effort/strategy retry. User-project caches are never touched.

The same core wrapper also treated its first inferred fanout direction for
each bus as final. When the complete 14-connection starter fixture needs one
failed bus to leave from the opposite side, that produced a false “not enough
space” error. After exhausting the normal layer assignments, core now performs
a deterministic greedy fallback over only the failed, automatically inferred
buses. It accepts only a strict increase in routed connections, tries at most
32 candidates, and leaves every authored direction/preferred exit locked. If
no candidate improves the route, the original fail-closed error is preserved.

An ordinary `<autoroutingphase region={...}>` was parsed into `plan.region`
but never copied into the `routingBounds` consumed by
`Group_filterSimpleRouteJsonForPhase`; only breakout phases supplied routing
bounds. Ordinary phase regions now constrain that phase's SRJ bounds. A
`reroute` phase deliberately keeps `routingBounds` unset because its existing
region-crop path owns those semantics. The exact Terminal QSPI fixture with
all 504 board obstacles is the regression: Pipeline7 failed three of five
critical connections at global board bounds, while bounds
`x=-9.658..9.21, y=-10.7..24.5301` (the critical geometry plus 6mm) routed
5/5 with zero exact-repair DRC; a 4mm margin still failed two. This is why the
patch exposes authored local routing intent instead of silently choosing a
global obstacle portfolio.

Finally, `getPresetAutoroutingConfig` silently mapped every unknown preset to
the default local capacity router. That included `autorouter="freerouting"`,
which the pinned props package accepts even though pinned core contains no
local FreeRouting implementation. Unknown preset strings now throw a
descriptive error. Built-in presets, an omitted preset, server configuration
objects, and custom local presets registered through
`platform.autorouterMap` remain supported.

Current-subcircuit fixed trace copper had a separate omission. Core excluded
every `pcb_trace` carrying a `source_trace_id` from static obstacles, preserved
only traces owned by child subcircuits, and then suppressed the corresponding
current-subcircuit connection as though its copper had been preserved. A
manual `pcbPath` therefore disappeared from the SRJ while remaining in final
Circuit JSON; later automatic phases could route through it. Core now preloads
only source traces explicitly fixed by `pcbPath` or `pcbStraightLine`, keeps
their exact wire/via geometry and connectivity, and suppresses a connection
only when that copper is actually present in `simpleRouteJson.traces`. It does
not freeze arbitrary existing autorouter state. The focused crossing fixture
places a fixed 0.4mm top trace across an automatic vertical connection: the
unpatched SRJ omits it and routes straight through, while the patched SRJ
preloads it and the same solver changes layer around the fixed copper.

The same manual-render path also created every `pcbPath` via from the library's
0.3mm pad / 0.2mm drill fallback, ignoring a board that declared larger
`minViaPadDiameter` / `minViaHoleDiameter` rules. Hydrate consequently emitted
a 0.05mm annular ring on fixed VBUS copper despite the board's 0.6/0.3mm
minimum. Manual route points and their matching `pcb_via` elements now share
resolved dimensions floored at the board minima. The existing typed
`pcbStyle` inheritance remains the local override: wrapping only a power trace
in a group with `viaPadDiameter: 0.8mm` and `viaHoleDiameter: 0.5mm` preserves
that larger legal pair without inflating every signal via on the board. A cold
TSX fixture asserts 0.6/0.3mm signal vias, 0.8/0.5mm power vias, no serialized
errors, and zero independent `@tscircuit/checks` findings.

Power distribution can also be authored as an explicit tree instead of asking
the capacity router to synthesize a second N-point Steiner tree over the same
pins. The sole port-to-net edge of each local subtree is marked:

```tsx
<trace
  from=".C17 > .pin1"
  to="net.V3_3"
  thickness="0.8mm"
  authoredNetTreeBoundary
/>
```

Core activates this behavior only when that typed marker is present. Starting
at the marked port, it validates the connected port-to-port component has at
least one branch, `E = V - 1`, and exactly one port-to-net boundary. It also
proves every contracted source port exists in the original named-net PCB
aggregate. Cycles, a second boundary, missing rendered endpoints, and a marker
on the wrong trace throw and serialize one `pcb_autorouting_error`; a CLI exit
code cannot hide the failure.

After validation, internal subtree ports are removed from the global aggregate
and the boundary port remains. A complete tree therefore needs no redundant
aggregate route. Multiple marked subtrees on one rail compose with unmarked
loads: the aggregate contains only each boundary and the remaining load, and
inherits the largest marked boundary width. Each authored local branch keeps
its source-trace ID, endpoint pair and width through capacity routing. With no
marker, the transform returns the original SRJ object without cloning or
mutation. Because core's existing route-cache key hashes the complete SRJ,
the preservation annotations and contracted point set also produce a distinct
cache identity.

The focused regressions route a non-collinear three-edge tree without an
aggregate, route two marked subtrees plus an unmarked load as an exact
three-point backbone, fail closed on a cycle and two boundaries, and prove
unmarked identity. Both valid artifacts have zero serialized errors and zero
independent `@tscircuit/checks` findings. The exact core transition is
`ea0435854d9be2b4b5dfba53e75c636e0208b2e0b72eea12d7cadcd304f25e41 ->
84be17d3b2beb909426dcf5140cd141bd6417bc2c22e465daa0e5c502d8684b8`;
it follows manual-via sizing and precedes the decoupling-limit stage below.

Core also inferred a capacitor's `max_decoupling_trace_length` onto every
trace that touched that capacitor. A cap-to-cap rail edge, a marked
cap-to-rail boundary, and even a one-port cap-to-plane fanout consequently
inherited the default 1mm limit. The preflight checker then measured the
one-port plane drop against remote ports on the same global net because the
asynchronous copper pour did not yet exist in Circuit JSON. Valid authored
power trees were rejected before routing.

Automatic decoupling-length inference now requires exactly two physical port
endpoints whose source components contain exactly one capacitor. That retains
the intended cap-to-device constraint while excluding cap-to-cap tree edges
and port-to-net boundaries. An explicit trace `maxLength` is still serialized
unchanged. For an explicit limit on a one-port plane drop, the preflight uses
the already-authored `fanout`/`single_layer_fanout` phase and
`fanoutPourNetMap` instead of depending on later rendered pour material; it
does not compare that local drop to unrelated remote ports. The actual routed
fanout length and solved-pour connectivity remain independently checked.

The cold regressions prove a cap-to-cap rail and marked boundary receive no
implicit limit, two explicit 1mm plane drops terminate as 0mm solved-pour
contacts despite remote same-net ports, an explicit 2mm cap-to-device branch
still fails preflight when physically impossible, and an unannotated direct
cap-to-device branch still inherits 1mm and fails closed. That stage's exact
core transition is
`84be17d3b2beb909426dcf5140cd141bd6417bc2c22e465daa0e5c502d8684b8 ->
c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77`.

Core now also gives native differential pairs a safe source-identity contract.
Each `positiveConnection` and `negativeConnection` must name a direct source
trace with exactly two physical ports and no named-net endpoint. Pinned core
cannot preserve the selected source-trace identity after a composed/named-net
aggregate is expanded into point pairs, so those selectors now serialize a
descriptive `pcb_autorouting_error` and throw instead of silently attaching the
pair constraint to the wrong or missing capacity connection. A pair that
declares `maxUncoupledLength` must also declare `pcbTraceGap`, because otherwise
there is no geometric threshold that defines coupled copper.

The supported production form is therefore:

```tsx
<trace name="TR_USB_DP_DEVICE" from=".U_ESD > .IO1B" to=".R_DP > .pin2" />
<trace name="TR_USB_DM_DEVICE" from=".U_ESD > .IO2B" to=".R_DM > .pin2" />
<differentialpair
  name="USB_DEVICE_PAIR"
  positiveConnection="TR_USB_DP_DEVICE"
  negativeConnection="TR_USB_DM_DEVICE"
  pcbTraceGap="0.15mm"
  maxLengthSkew="0.2mm"
  maxUncoupledLength="1mm"
/>
```

For a flow-through ESD part, the connector-side IO1/IO2 traces and the
device-side IO1B/IO2B traces are separate physical differential-pair sections;
each section gets its own direct pair declaration. The component's declared
internal pin connectivity joins the sections electrically. Do not select a
`net.USB_*` boundary trace or add an external IO-to-IOB bypass merely to make a
single composed selector. The differential-pair core transition is
`c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77 ->
ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b`.
Cold Pipeline7 and Pipeline9 fixtures prove the direct form routes through the
former reversing-spine case, an insufficiently coupled pair fails with its
measured uncoupled length, named-net selectors fail as parsed artifacts, and a
missing gap fails before copper export.

The async autorouter output path had one more via-sizing defect. It resolved a
single PCB style from the routing group, then used
`board.min_via_* ?? styleDefault`; the board minimum consequently acted as an
override, and a power trace scoped to 0.8/0.5mm was serialized as the board's
generic 0.6/0.3mm via. This also discarded the dimensions carried by a
preloaded fixed route when the autorouter reinserted it.

Core now maps each output route back to its exact `source_trace_id`, reads that
trace component's inherited `pcbStyle`, and resolves both dimensions as the
maximum of the route point, trace-local style, and board manufacturing floor.
It writes the same result to the `pcb_trace.route` via point and standalone
`pcb_via`, and fails if the final hole is non-positive or not smaller than the
pad. Aggregate routes with no unique source trace retain the routing group's
style. The exact final core transition is
`ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b ->
77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05`.
A cold cross-layer fanout fixture proves board 0.6/0.3mm plus local 0.8/0.5mm
produces 0.8/0.5mm in both records, while an explicit local 0.4/0.2mm is
floored to 0.6/0.3mm. The manual fixed-path fixture now runs with async routing
enabled as a separate reinsertion regression.

Aggregate named-net copper had a related source-identity leak. After the
capacity router split a named-net connection into MST segments, core inferred a
`source_trace_id` from each segment's endpoint. In an authored rail, that
arbitrarily made the global backbone impersonate an incident local branch, so
the branch's 2mm `maxLength` and other trace-local policies were applied to a
7.6mm backbone segment. `connection_name` already carries the correct
`source_net_id`; an aggregate segment has no single source edge.

`getSourceTraceIdForRoutedTrace` now detects a `connection_name` that resolves
to `source_net` and leaves `source_trace_id` unset before any endpoint-based
inference. Direct authored edges retain their exact ID, aggregate connectivity
continues through `connection_name`, and the via-style resolver correctly uses
the routing-group fallback when no unique source trace exists. The final core
transition is
`77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05 ->
1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae`.
The two-authored-subtree fixture gives every local edge an 8mm maximum, routes
the three-point rail backbone, and asserts that local IDs and widths remain on
local copper while both aggregate segments have no source-trace ID and receive
no leaked maximum-length warning.

Differential-pair source DRC also used the selected trace's whole connectivity
component to decide whether an exact trace-name selector was point-to-point.
That makes a valid connector-to-ESD edge appear three-terminal whenever a
neighboring reversible-pad edge or package-internal channel shares one of its
ports. For a unique trace-name selector, core now resolves the terminal set
from that `source_trace.connected_source_port_ids` directly. Port selectors
and duplicate trace names retain the existing connectivity-map behavior, and
the later direct-two-port/no-named-net source contract remains fail-closed.

The exact core transition is
`1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae ->
2ccd8305aef9a52a6f12df388efcc53e91298e55d8afb261247f920bca958613`.
A cold fixture names two connector-to-ESD traces whose adjacent reversible-pad
edges expand each electrical network to three terminals; it proves each
selected source trace still has exactly two physical endpoints and emits no
false ignored-property warning. The deterministic patch manifest derives each
stage's permitted successor set from later exact digests of the same compiled
file, so clean installs and already-advanced installs remain strictly guarded
and idempotent.

Every local autorouter implementation now crosses one additional core output
boundary before its traces are cached, accumulated, or exported. That boundary
rechecks every output via against physical SMD obstacles and the board's
via-to-pad clearance. It therefore also protects cached results, Pipeline9,
fanout, and registered local algorithms that do not use Pipeline7's internal
exact engine. Explicit via-in-pad is accepted only when the routed trace and
pad share a serialized connectivity identity; a different-net overlap always
throws. The throw is caught by the existing phase boundary and serialized as
`pcb_autorouting_error`, leaving no routed via copper in Circuit JSON. The
exact final core transition is
`2ccd8305aef9a52a6f12df388efcc53e91298e55d8afb261247f920bca958613 ->
f16cc7ee806d3afa14b639e784eefde1014d141f22fdd087b9309a8a64b361c0`.
`allowViaInPad` also participates in the route-cache descriptor, so a route
computed under the explicit permission cannot be reused by a later phase or
build that requires external vias.

The direct differential-pair contract had a second identity expansion at the
SRJ boundary. After selecting one exact source trace, core retained only its
`subcircuit_connectivity_map_key` and searched every source trace in that
electrical component for a capacity connection. A separately phased
reversible-connector orientation edge therefore made the selected direct edge
match two SRJ connections and aborted before its pair phase. Core now carries
the selected `source_trace_id` through that lookup and requires the SRJ
connection with that exact ID. The connectivity key is retained for diagnostics
and source validation, but it no longer expands the physical pair section.

The exact final core transition is
`f16cc7ee806d3afa14b639e784eefde1014d141f22fdd087b9309a8a64b361c0 ->
8359d3082f85ccb2010810e8dfe9730fce9d2efb264d33aa96750d24d0a968d9`.
A cold fixture routes DP and DM connector-orientation edges in separate early
phases and routes the named direct connector-to-ESD pair later. It asserts the
pair SRJ names are exactly those two selected source traces, not either
orientation edge. A missing or duplicate exact SRJ source ID still fails
closed; named-net/composed pair selectors remain unsupported.

Because the whole-phase core cache sits outside Pipeline7, a local capacity
patch cannot invalidate an older successful phase entry merely by changing an
internal cache provider. The route-cache descriptor now includes
`capacityLayerReversalRetry: "p7-layer-reversal-v1"`. This makes pre-retry and
retry-aware whole-phase results different cache identities while leaving the
SRJ, effort, pipeline and all previously recorded inputs intact. The exact
final core transition is
`8359d3082f85ccb2010810e8dfe9730fce9d2efb264d33aa96750d24d0a968d9 ->
6e014654d0bf4ce38d400ddf15ed3c6042d166771b3bc4e308785db48167a37b`.

When a net is named in a board/group or phase `fanoutPourNetMap`, its fanout
drops establish the pad-to-plane termination and the copper pour establishes
the global connection. Core previously also emitted one aggregate capacity
connection spanning every member of that net. That redundant cycle made the
Hydrate GND phase global and fragile, and could turn authored local ties into
whole-board routing. The planner now omits only the aggregate connection for
plane-terminated nets. Direct fanout drops and explicitly authored local
same-net traces keep their phases and route normally.

Plane fanout also assumed that the target pour had to be on a layer below the
source pad. It rejected a net mapped to both outside layers and returned no
route when a pad was already on the mapped layer. That forced unnecessary
dogbones and vias, and made a large exposed ground pad impossible to terminate
within the product's 2mm fanout limit even when it was physically covered by a
same-net pour. Core now preserves a single-layer mapping's existing cross-layer
dogbone-and-via behavior, but a net mapped on both faces selects the physical
pad's own face. A same-layer result is not fabricated copper: it is a
single-point plane-contact marker (`is_inside_copper_pour: true`) on
`fanout:<source_trace_id>`, carrying the source trace's net and the selected
route layer, with no route segment, via, or added length.

The marker is only routing intent; it cannot prove that the later pour solve
left copper under the pad. The independent solved-BREP connectivity gate in
`packages/verify/src/verifylib/intent.py` therefore requires the marked point
to lie in exactly one same-net, same-subcircuit, same-layer material island
that reaches the dominant plane. Missing pours, wrong-net pours, and isolated
fragments fail closed. The gate no longer returns early when the board has no
solved pour faces. In the exact 60-drop Harness SRJ, same-layer termination
routes all 60 pads with 0mm fanouts, no vias, and zero capacity exact-DRC
issues; injecting those marked traces into the real solved-pour artifact also
produces zero plane-connectivity findings.

Remove the same-layer stage only when an upgraded core supports same-layer and
per-source-layer plane termination natively, serializes an explicit contact
marker, retains the sole-layer cross-layer behavior, and passes both the
router fixture and the independent connected/missing/wrong-net/fragmented
solved-pour regressions.

Remove the manual-via stage only when upstream manual trace rendering floors
both serialized route-point and `pcb_via` dimensions at the board rules,
retains larger inherited `pcbStyle` values, and passes the cold power/signal
fixture. An upstream typed per-`pcbPath`-point via-size API could replace the
small wrapper group, but is not required for safe scoped sizing.

## `@tscircuit/checks@0.0.152`

`checkSourceTracesMatchPcbTraceThickness` previously ignored the exact
`pcb_trace.source_trace_id`. For every authored source edge, it gathered all
PCB traces in the connected electrical net and compared the source edge's
minimum against the smallest width anywhere in that net. A valid 0.8mm power
trunk sharing a tree with a 0.2mm package neck was therefore reported as
0.2mm, even when the trunk's own serialized PCB trace was uniformly 0.8mm.

The checker now selects every PCB trace whose `source_trace_id` exactly equals
the source edge first. This is deliberately plural: if two PCB traces claim
one source identity, every claimed route participates and a thin duplicate
still fails closed. Only when no exact identity exists does the checker retain
the old connectivity-net fallback for aggregate and legacy copper. The exact
transition is
`69dccac8dda12a4e32172f42e08671efecb0464838d8f270b8aa1882fda9600d ->
b77c5ae972302489becb20ddc1963c23eb01d7db610fd3bae3400aee6507192d`,
guarded against the published
`lib/check-source-traces-match-pcb-trace-thickness.ts` source map.

The cold mixed-tree regression contains a 0.2mm A-to-B neck and a 0.8mm
B-to-C trunk. The pristine checker falsely reports the exact 0.8mm trunk at
0.2mm; the patched checker accepts it. Removing the trunk's source identity
retains the 0.2mm fail-closed fallback, while adding a second exact-identity
0.6mm route reports 0.6mm. On the private coherent RP2040 fixed-copper
artifact, the same stage removes exactly twelve false V3/DVDD trunk warnings:
the selected source traces and their serialized routes are uniformly 0.8mm;
none of the 0.2mm neck identities is reclassified.
