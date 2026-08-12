# Cross-product prevention control ledger

This ledger is the causal view of `docs/lessons.md`. The examples and the
RP2040 integration are defect finders, not deliverables. A row is useful only
when it answers four questions:

1. What is the earliest layer that could have prevented the failure?
2. Is the cause product geometry or reusable software?
3. Which minimized negative and positive/fail-closed behaviors prove the fix?
4. How does a future generated product inherit the control without remembering
   this incident?

## Evidence vocabulary

| Class | What it can prove | What it cannot prove |
|---|---|---|
| **cold-routed** | The exact pinned router produced copper from an empty/private cache; parsed artifacts and independent checks accepted it. | A different dense consumer or changed block snapshot. |
| **fixed-physical** | Authored copper, endpoints, widths, layers, vias, and clearances are physically legal. | Autorouting completion. `routingDisabled` is acceptable only under this label. |
| **negative** | The bad case fails at the intended boundary and emits no unsafe copper/evidence. | That a positive board is manufacturable. |
| **source/static** | API shape, topology intent, provenance, or a required declaration exists. | Routing, DRC, fabrication, or schematic completion. |
| **stale** | Nothing. Exit code 0, an old sidecar, an unlocked block copy, or consumer-only coordinates are not evidence. | Everything. Rebuild. |

A product is green only when its selected `main.circuit.json` matches
`build.attemptEvidence`, every parsed `*_error` is absent, the exact
golden-block lock and patched toolchain hashes match, `fab.ready` is true, and
the required routed/fixed/intent gates all consume that same artifact. A CLI
exit code or a clean routing-disabled layout can never substitute.

## Failure to preventive-control matrix

| ID | Exposed by | Failure and classification | Earliest preventive boundary | Minimized negative / positive evidence | Automatic inheritance | State |
|---|---|---|---|---|---|---|
| P01 | Aknos/starter, Hydrate, Harness, Terminal | CLI exit 0 and rejected async effects could leave real routing errors outside the artifact. **Runtime/evidence defect.** | Circuitpy compile boundary. | `test_async_autorouting_guard.py`: output-only async failure is persisted as `pcb_autorouting_error`; non-routing async rejection fails compilation; duplicates are refused. | Every circuitpy build scans stdout and parsed Circuit JSON before checks/export. | Control closed; each product still needs fresh evidence. |
| P02 | Aknos/starter, Terminal, Harness | Changed effort/pipeline could reuse stale route copper; nominal 5x comparisons were contaminated. **Cache/evidence defect.** | Core cache key plus circuitpy retry orchestration. | `test_router_cache.py` cold/shared comparisons; `RoutingEscalation` stash/state-machine tests; sidecar selected-hash mutation negatives. | Exact core/capacity/config key, private cache clear, bounded one-retry policy, content-addressed selected artifact. | Control closed; old 5x improvement claims remain withdrawn. |
| P03 | Terminal, Hydrate | Router graded 0.15mm boards at 0.10mm, accepted unresolved final DRC, used conservative preload rectangles as authoritative copper, or omitted fixed `pcbPath` copper. **Solver/runtime defects.** | Capacity exact evaluator and core SRJ construction. | `dynamic-trace-connectivity`, `preloaded-trace-exact-drc`, `manual-pcb-path-preservation`, and final-DRC cases in `scripts/tests`; impossible cases emit no accepted output. | Exact-version guarded patches are applied by `setup-toolchain.sh`; bundle hashes enter sidecar/cache identity. | Control closed. |
| P04 | Starter, Hydrate, Harness/WS | Inferred fanout direction, layer bias, or via placement chose an avoidable illegal route; same-layer poured pads were forced through vias; vias could land in SMD pads. **Solver defects.** | Fanout/local router before output, with an algorithm-independent core gate. | `fanout-direction-retry`, `same-layer-plane-fanout`, `plane-net-layer-selection`, `via-in-smd-pad`, and `layer-reversal-retry` positive/impossible cases. | Every installed pinned router gets bounded steering/retry and fail-closed final geometry gates. | Control closed; real products must still prove solved-pour connectivity. |
| P05 | Terminal expert review, Hydrate, Harness USB/RP | Requested power/signal widths and via dimensions were silently reduced to global defaults. **Router/render/checker defects; corridor availability remains geometry.** | Trace-width solver, manual/automatic via renderer, exact source-ID checker. | `explicit-trace-width` wide/narrow corridor twins; `manual-pcb-path-via-rules`; `automatic-via-style`; `checks-source-trace-width-identity` mixed-tree/duplicate-ID negatives. | Per-connection minima and trace-local `pcbStyle` are hard contracts; exact checker identity is toolchain-hashed. | Control closed; a too-tight board now fails honestly and must be repacked. |
| P06 | Hydrate, Harness, RP | Aggregate named-net MSTs rebuilt intentional local decoupling/power trees; plane-terminated GND reappeared as one global connection; raw aliases made same-net fanout/local ties conflict. **Topology/runtime defects plus source-design errors.** | Reusable block/planner topology first; core contraction only for an explicit typed boundary. | `authored-net-tree` non-collinear/two-subtree/cycle/multi-boundary cases; `plane-terminated-net-routing-plan`; `dynamic-trace-connectivity`; golden external-rail attachment tests. | Golden blocks expose local neck/trunk/boundary APIs; planner registry mirrors them; invalid marked trees fail before copper export. | Core control closed; consumer migration remains open under lessons #27. |
| P07 | Terminal, Harness USB | Differential-pair selection lost direct trace identity, postprocessing created zero-length edges, and max-uncoupled/skew constraints were dropped. Some layouts physically cannot meet 3mm. **Runtime defects separated from geometry.** | Direct source-trace contract and pair solver; block placement for physical egress. | `differential-pair` selector/phase/uncoupled/skew/DRC cases; `differential-pair-zero-length-edge` graph regression; golden top/bottom USB routed contract. | USB block owns native pair rules and direct flow-through edges; registry exactness prevents stale selectors. | Golden control closed; dense product synchronization/reroute remains open under lessons #12. |
| P08 | Terminal, Harness/RP | Global obstacle portfolios and phase ordering made local critical buses non-deterministic; an ordinary phase `region` was ignored; unknown `freerouting` silently meant capacity routing. **Planner/runtime defects; phase partition is board intent.** | Board-owned phase/region plan, then core config validation. | `phase-region-routing-bounds` bounded positive/unbounded negative; `unsupported-local-autorouter-preset`; cold RP/Terminal phase artifacts must retain all previous traces. | Typed golden phase props plus strict core preset mapping; future boards cannot silently request a nonexistent local backend. | Runtime control closed; coherent RP full-route artifact pending. |
| P09 | Terminal expert review, Hydrate, Harness, RP | Power width, USB current limit, thermal load, GND topology, and decoupling were comments or net leaves rather than enforceable intent. **Planner/block/product-contract defects.** | Product schema and circuitlib plan before TSX; golden local topology; independent artifact intent after compile. | Power/layout intent positive and mutation negatives; limiter-setting topology; decoupling override typo/conflict tests; planner peak-current/thermal sentinels. | Skeleton/product generator emits typed contracts; circuitpy refuses malformed intent; verifylib grades compiled copper and populated parts. | Framework closed; lessons #27 and RP coherent integration remain open. |
| P10 | Terminal expert review, composition matrix | Planner used centred sizes, omitted a plannable block box, or carried stale block props; compact switch/layer/width APIs existed only in TSX. **Planner/registry defect.** | Circuitlib registry and measured layout metadata before source generation. | Measured-box set equality, composition registry/constructor set equality, and exact exported-prop/required-prop tests across every registered block. | Any golden API or registry change must update all planner consumers in the same CI change. | Control closed by the all-registry API equality gate. |
| P11 | All products, RP vendor-derived cluster | Consumer block copies, provenance, sidecars, or patched npm bytes could drift while looking version-current. **Vendoring/evidence defect.** | Snapshot synchronization, source fingerprint, toolchain identity, publication gate. | Golden-lock missing/drift/symlink/provenance tests; skill byte equality; review-packet and examples-lock stale-artifact/hash mutations. | Generated products must carry `golden-blocks.lock.json`; RP license/reference bytes vendor with the block; publication refuses drift. | Control closed; products require a post-freeze re-sync/rebuild. |
| P12 | RP full power-tree probe | Center-collapsed schematic placement sends the dense rail graph into an effectively unbounded overlap-shift path; `schematicDisabled` only hides it. **Supported-mode/runtime limitation.** | Board schematic configuration until upstream solver is bounded. | Faithful 9-spoke V3 + 3-spoke DVDD no-autolayout control remains unsolved in `TraceOverlapShiftSolver`; `multibranch-authored-rail-schematic` with `schAutoLayoutEnabled` completes parsed-clean with real schematic output and legal fixed spokes. | Dense generated RP boards must enable supported schematic autolayout; permanent regression keeps schematic enabled. | Positive control closed; coherent RP consumer must adopt it. |
| P13 | Terminal, Hydrate, Harness, RP | A board-local nudge or routing-disabled probe was called green while the routed consumer, opposite face, or downstream phases were unproven. **Acceptance/orchestration defect.** | CI/evidence classification, not the router. | `test_routing_board_contracts` set equality; bottom-transform tests; selected-artifact hash; async guard; composition matrix and fresh example locks. | Every routed bench is authoritative or an explicit blocker; geometry-only tests cannot satisfy routed product gates. | Control closed; current consumer blockers stay explicit below. |

## Product closure ledger

| Product | Reusable controls learned | Product geometry that software must not pretend to solve | Current acceptable evidence / next closure gate |
|---|---|---|---|
| **Aknos/starter** | Cache-complete retries, deterministic failed-bus direction retry, unsupported-preset refusal, bounded final DRC. | Component spacing and a legal fanout exit corridor. | Minimized cold fixtures are the transferable evidence. Do not claim a product release without a fresh parsed/fab artifact. |
| **Hydrate coaster** | Pour cutout math, fixed-path preservation, legal manual vias, same-layer plane contact, via-in-pad prevention, plane-net contraction, async persistence. | Split dense connection portfolios, keep local ties/tree branches acyclic, and reserve real power/debug corridors. | Existing committed sidecar is stale/blocking. Re-sync golden blocks, declare supported phases/autolayout, route all phases, then pass intent + KiCad/fab gates on the selected artifact. |
| **Harness puck** | Peak-current/thermal planning, protected USB contract, dual-face GND contact, hard widths, layer-reversal retry, direct native USB pair, exact source-ID width grading. | Annular placement zones, connector egress, RP/package power corridors, and 3mm pair coupling. | Golden USB both-face routed proofs are reusable; the dense Harness consumer and coherent RP integration still need fresh full-route evidence. |
| **Terminal keyboard** | Compact switch API, bottom transform, real clearance floors, hard power widths, GND-plane topology, bounded phase regions, pair/fixed-copper gates. | Expert requirements remain board intent: 5x10/front key+diode field, remaining parts on the opposite face, exact keyboard outline, centered bottom USB, compact SW2/SW3, clear power/signal channels. | No routing-disabled layout or prior 5x artifact is green. Sync the frozen APIs and lock, then prove all routes/pours, assembly faces, KiCad packet, and selected-attempt hash. |
| **RP2040 golden core** | Cited 5mm U3-only decoupling override, official-cluster provenance, fixed QSPI identity verifier, authored mixed-width trees, schematic autolayout boundary. | Manufacturer-guided flash/cap placement, legal .2mm local branches, .8/.5 acyclic rails, and full clock/QSPI/power/control phase compatibility on both transforms. | Reduced QSPI/local-power fixed probes are not a full board. Landing requires one coherent shared source plus top and exact bottom full physical/routed evidence; no private temp artifact may freeze the API. |

## CI and handoff rules

- A runtime defect needs a minimized failing fixture, a legal positive twin, and
  an impossible negative that still fails closed. Exact package/source-map/hash
  guards and idempotency are mandatory for pinned toolchain patches.
- A board-geometry defect is fixed in the reusable block, planner metadata, or
  product contract. The router is patched only after a physically legal route
  is independently demonstrated and the solver cannot produce it.
- Every new plannable block is in exact set equality across the registry,
  composition constructor, measured boxes, golden source API, skill vendoring,
  and project snapshot synchronizer.
- Any change to golden source, pipeline semantics, or patched toolchain bytes
  invalidates consumer locks/sidecars. Re-sync and rebuild; never hand-edit a
  generated copy or accept stale review images.
- Handoff messages must name the artifact path, whether routing was enabled,
  parsed error/warning counts, independent checks, exact toolchain hashes, and
  the first remaining blocker. “Compiled” without those fields is not a green
  claim.
