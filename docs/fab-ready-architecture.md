# Fab-ready generation architecture

This is the first-principles answer behind the rebuild. The three example
products are acceptance fixtures for the system; a coordinate repair that
helps only one of them is not a pipeline fix.

## Decision: tscircuit is necessary, but not sufficient by itself

tscircuit is a useful source model, compiler, renderer, and local router. It
can express the boards in scope and, with pinned fixes, can produce legal
copper for them. It is not the manufacturing authority:

- its CLI can exit zero while Circuit JSON contains `*_error` elements;
- the router cannot invent a legal topology or corridor that placement did
  not provide;
- one serializer or checker can share the same incorrect assumption as the
  source that produced the board;
- its fallback Gerbers are deliberately not accepted as shipping evidence;
- electrical values, supplier identities, assembly policy, and enclosure
  access are outside geometric routing.

The supported production stack is therefore:

1. **Typed product and planner contract** — safety envelope, capabilities,
   power/thermal budget, exposed debug nets, side policy, component zones,
   edge-connector datum, net classes, and manufacturer-cited exceptions.
2. **Frozen golden blocks** — reviewed circuits own local bypass, breakout,
   rail, pair, footprint, and provenance geometry by construction. Products
   import a content-locked snapshot; they do not redraw blocks.
3. **Pinned tscircuit substrate** — compile and route with exact package and
   patched-bundle hashes. Every output is parsed; exit status is never a
   verdict.
4. **Independent Circuit JSON gates** — circuitpy and verifylib grade DFM,
   topology, intent, current, thermal, planes, holes, widths, and assembly
   without trusting the router's self-assessment.
5. **KiCad 10 second substrate** — convert the selected Circuit JSON, apply
   the fab profile, run ERC/DRC, and plot mask-subtracted shipping Gerbers.
6. **Packet truth gate** — inspect the actual Gerber/drill archive and require
   complete, orderable BOM/CPL/KiCad/review artifacts.
7. **Content-addressed evidence** — bind source graph, golden snapshot,
   toolchain bundles, routing candidates, selected Circuit JSON, validation,
   and packet artifacts. Cache reuse and publication fail on any drift.

This combined stack is the thing we claim can produce fab-ready boards. A
tscircuit build, a routing-disabled geometry fixture, or a clean image is not
that claim.

## Orchestration decision

Multiple agents are useful for independent geometry search, toolchain
minimization, and consumer integration. A shared mutable runtime and informal
handoffs are not a correct architecture. The required operating model is:

### Ownership by failure layer

- **Block/geometry owner:** proves one reusable circuit and both transforms.
- **Runtime/checker owner:** works only from a minimized legal positive and an
  impossible negative; never patches a solver to hide bad source geometry.
- **Consumer owner:** integrates frozen block bytes into a product and owns
  board-global placement, phase corridors, trees, planes, and mechanics.
- **Acceptance owner:** independently verifies retained artifacts and rejects
  partial, stale, routing-disabled, or mismatched evidence.

An agent may investigate another layer, but it does not silently mutate that
owner's source or broaden a contract.

### Immutable runtime epochs

Installing or patching the shared Node toolchain is a transaction:

1. all build owners reach an explicit idle barrier;
2. the toolchain owner applies the exact guarded chain atomically;
3. pristine-to-final replay, syntax, hashes, idempotency, and focused positive
   and negative fixtures pass;
4. a real CLI smoke test completes after dependency installation has stopped;
5. only then is a new runtime epoch released to board builders.

Source-only analysis may continue during the barrier. Board evidence produced
across two runtime epochs is inadmissible.

### Machine handoff, not narrative handoff

`READY` requires a reproducible artifact set, not a sentence. A handoff names:

- exact source and artifact paths plus SHA-256;
- whether routing and schematic generation were enabled;
- selected routing attempt and retained candidate evidence;
- parsed error/warning counts;
- independent Circuit JSON/intent/DFM results;
- toolchain bundle hashes;
- the exact reproduction command;
- the earliest remaining blocker, if any.

A reduced subprobe is labelled `fixed-physical`, `cold-routed`, `negative`, or
`source/static`; it cannot satisfy a broader product gate. The acceptance
owner consumes the files and commands, not the author's conclusion.

### Freeze before fan-out

Golden blocks change until one coherent top and bottom contract is green.
Consumers do not copy intermediate bytes. After freeze, one deterministic
sync updates every selected product and its lock, and every consumer rebuilds
from a cold cache. This prevents three agents from fixing three stale forks of
the same bug.

## Product process

Every generated board follows the same monotonic sequence:

1. **Spec refusal:** reject unsafe, incomplete, thermally invalid, unexposed,
   or unsupported plans before running Node.
2. **Plan:** choose only registered blocks; emit physical, power, assembly,
   and fab contracts from circuitlib.
3. **Place:** use measured block extents and real mechanical datums. Reserve
   corridors for critical buses, power trees, planes, debug, and enclosure
   access before autorouting.
4. **Author local topology:** pin-to-cap leaves, pair egress, wide rail trees,
   plane contacts, and sole named boundaries belong to blocks or typed board
   helpers, not an aggregate MST.
5. **Compile and scan:** parse Circuit JSON and persist asynchronous failures.
6. **Route by dependency:** fixed critical copper first, then bounded phases,
   power, control, planes, and ordinary signals. One cold higher-effort retry
   is allowed only for routing-class blockers and wins only when its retained
   scan is strictly better.
7. **Verify independently:** exact copper checks, layout/power intent, DFM,
   BOM/parts lock, and assembly checks all consume the selected artifact.
8. **Run KiCad:** normalize, ERC/DRC, Gerber/drill export, and KiCad-project
   retention.
9. **Verify the packet:** inspect final Gerber polarity/layers/mask/silk/holes,
   BOM/CPL orderability and DNP exclusion.
10. **Publish only when literal `fab.ready` is true:** review images and an
    `ORDER.md` are outputs of the accepted packet, not substitutes for it.
11. **Work backward from every repair:** add the smallest negative, legal
    positive, automatic inheritance, and CI ratchet at the earliest layer that
    could have prevented the defect.

## Completion evidence for the first three products

Harness, Hydrate, and Terminal are complete only when each has, from the same
fresh source graph and frozen golden snapshot:

- a cold selected routing artifact with all completed candidates retained;
- zero parsed error elements and zero blocking validation findings;
- satisfied layout, power, topology, plane, pair, width, clearance, thermal,
  assembly, and debug-access contracts;
- KiCad ERC/DRC-clean shipping Gerbers and drills;
- an orderable, exact supplier-locked BOM and matching CPL;
- a complete KiCad project, review renders, enclosure data, and `ORDER.md`;
- `main.board.json.fab.ready == true`;
- a review/publication check that reproduces every evidence hash.

Until all three satisfy that list, the pipeline goal remains active. The live
status and open defect classes are tracked in
[`prevention-control-ledger.md`](prevention-control-ledger.md) and
[`lessons.md`](lessons.md); neither a private temp artifact nor an earlier
committed packet is completion evidence.
