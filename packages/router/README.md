# routerlib — the routing contract, the benchmark, and the scorer

```python
def route(problem: RoutingProblem, budget: Budget) -> RoutingSolution
```

That is the whole interface. Everything else in this package exists to make
that line measurable: adapters so a real board becomes a problem and a solution
becomes a board again, a benchmark of instances stripped out of boards we have
actually built, and a scorer that judges **completeness, then legality, then
quality** — in that order, because that is the order in which a defect costs
money. There used to be a fourth tier for cost and it is gone; see below.

## Why this package exists

The autorouter we ship cannot represent the rules we grade against. Measured
this week, on our own boards:

| What it does | What that means |
|---|---|
| Runs copper 0.07mm from a hole that needs 0.28mm | It has no model of drill clearance |
| Produced byte-identical copper with and without a ground pour | It sees a plane as 73 obstacles, not as a net |
| Routes USB D+/D- like two unrelated wires | It has no net classes |
| Targets 0.10mm clearance, our floor, and lands under it | Its target is our gate, so it aims at the failure line |

Four attempts to steer it failed for that one reason: **you cannot configure a
router into understanding a rule it has no representation for.** Our domain is
narrow — 2 layers, known golden blocks, ≤130 parts — so a router that natively
knows our rules is tractable. This package is the ground the tournament to build
one runs on.

## The contract

### `RoutingProblem` — a placed board with holes in its copper

Components are where they are. The router may not move them.

| Field | What it carries |
|---|---|
| `board` | outline polygon (the real routed edge, not the bounding box), layer count, thickness |
| `pads` | every landing: net, centre, size, **rotation**, **shape**, layers, SMD or plated hole |
| `drills` | every hole, plated or not, as a real slot when it is one |
| `keepouts` | declared no-copper zones, per layer |
| `planes` | poured copper already belonging to a net — a pad inside one is connected |
| `nets` | pad ids, a **net class** (`signal` / `power` / `ground` / `diff_pair`), a diff partner, and the width this net must be routed at |
| `existing_traces` / `existing_vias` | copper the router must keep and must not violate |
| `rules` | see below |

Net identity is tscircuit's `subcircuit_connectivity_map_key`, not a name we
invent — so a net in a problem and a net in a DRC finding are the same object.

**A pad's shape is data, not a label.** `Pad` carries the vertices of a polygon
pad and the corner radius of a rounded one; `Drill` carries its hole shape and
`Keepout` its rotation and outline. Until 2026-08-16 none of them did: a pad was
width, height and rotation, so a USB-C shell tab reached the router as the
inscribed stadium of its bounding box and every rectangle reached it with its
corners rounded off. See *The shape model* below — it is the single change that
made the harness agree with KiCad.

### `DesignRules` — read from the pipeline, never transcribed

`DesignRules.from_profile(circuitpy.fab.get_profile("jlcpcb"))`. Every DFM
number has one owner in this repo and it is not this package.

One distinction matters more than the rest:

```
min_clearance_mm     0.10   what JLC holds
clearance_gate_mm    0.09   what we block at (floor − drc_tolerance)
target_clearance_mm  0.147  what a router should design to
```

The gate sits below the floor because two geometry engines disagree by a few
microns and a gate at the exact floor rejects legal boards. The **target** sits
above the floor because a router that aims at the floor lands under it. That
gap is the diagnosis of the router we ship, encoded as a number.

Copper-to-hole is three rules, not one: 0.20mm to a via hole, 0.28mm to a
component plated hole, 0.20mm to a non-plated one.

### `Budget` — counted, never timed

```python
Budget(max_iterations=200_000, max_nodes=5_000_000, seed=0)
```

* `max_iterations` — one unit of algorithm-defined outer work
* `max_nodes` — expanded search nodes; the finer currency, and the one that
  actually bounds an A*
* `seed` — every random choice derives from this

`wall_clock_cap_s` exists **only as a safety valve against a hung run**. It is
deliberately generous, and a solution that hits it is reported with
`stop_reason="wall_clock"`, which the harness treats as a failed run rather than
a result. A number produced by a clock is not comparable to anything.

Wall-clock as a budget is precisely why the current router is nondeterministic:
the same board on a loaded machine gets less search and therefore a different
board.

### `RoutingSolution` — copper, and an honest claim

`traces` (one per layer run), `vias`, `complete`, `unrouted_nets`,
`iterations`, `nodes_expanded`, `wall_clock_s` (information only), `notes`.

**`complete` is never trusted.** The scorer recomputes connectivity from the
copper and reports both, plus whether they agreed. Saying *"I did not finish"*
costs nothing; it is always the better answer than copper you cannot defend.

### Writing a router

```python
from routerlib.model import Budget, RoutingProblem, RoutingSolution
from routerlib.workspace import Workspace

class MyRouter:
    name = "my-router"

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        meter = budget.meter()
        ws = Workspace(problem)              # obstacles, indexed, per layer
        ...
        if ws.path_ok("top", points, width, net.id) is True:
            ws.commit_trace(trace)
        return RoutingSolution(router=self.name, traces=..., vias=...,
                               complete=..., iterations=meter.iterations)
```

`Workspace` answers *"may I put this copper here?"* with the same geometry the
scorer uses, so a router cannot pass its own check and fail the score. It
designs to `target_clearance_mm` by default.

Register the class in `routerlib.cli.registry()` and it is in the tournament.

## The benchmark

16 instances, extracted from boards we have built and **committed as fixtures**
— `examples/` is rebuilt by other agents several times a day, and a benchmark
that moves under you cannot be compared to itself.

```
python -m routerlib list                            # the table below, regenerated
python packages/router/scripts/build_instances.py   # rebuild from today's boards
python packages/router/scripts/upgrade_instances.py # re-baseline in place
```

Two ways to refresh, and they are not the same operation. `build_instances.py`
re-derives an instance from whatever `examples/` and the matrix contain today,
which **moves the placement** — `terminal-keyboard` has drifted 104 pads since
the tournament, and copper measured against the old placement becomes
unscoreable. `upgrade_instances.py` changes what an instance *records* about a
placement it does not touch, and prints every hash it moves.

Sources: the three example boards, ten composition-matrix cells (every one a
composition the planner can legally emit), and three synthetic ground-plane
variants. A plane variant is a genuinely different problem — and it is the
problem the current router cannot express.

Planes are **synthesised, never inherited**: the pours on our built boards were
generated *after* routing, so their outlines are carved around the old traces.
Reusing one would leak the previous solution into the problem.

Every instance records its features, because the portfolio phase selects on
exactly those, and every feature is measured from geometry:

* `finest_pin_pitch_mm` — smallest centre-to-centre distance between two pads of
  one component. A pitch, not a package guess.
* `regular` / `grid_score` — the share of components sitting on their own
  footprint group's fitted lattice. A key matrix scores 0.83; a mixed analog
  board scores 0.25.
* `mst_length_mm` — the sum of every net's Euclidean minimum spanning tree. A
  hard lower bound on total copper and the best single difficulty proxy.
* `routing_demand_per_mm2` — that, divided by board area.
* plus net/pad/drill counts, density, diff pairs, ground pads, `has_plane`.

Each instance also stores a `placementHash` (see below) and its **baseline**: the DRC result of the *empty*
solution. Any violation there belongs to the placement, not to a router. All 16
shipped instances have an empty-solution baseline of zero errors, and
`test_drc.py` fails if that ever stops being true. That still holds under the
true shape model, which is worth stating plainly: the placements themselves are
legal, so every finding the corrected ruler produces belongs to a router.

**Re-baselined 2026-08-16, schema `@2`.** A pad now records a polygon outline
and a corner radius, a drill its hole shape, a keepout its rotation and
outline — and `placementHash` covers all of it, because two placements that
differ only in whether a pad is a rectangle or a polygon are two different
boards. Every hash moved once; the before and after are in
`benchmarks/manifest.json` under `rebaseline`, per instance, with the board each
shape was read from. Coordinates did not move: the fixtures were upgraded in
place precisely so the 208 copper sets already on disk stayed scoreable.

## The scoring harness

```python
from routerlib.scoring import score
result = score(problem, solution)
result.key()   # lexicographic, lower is better
```

1. **completeness** — fraction of nets fully connected, recomputed by union-find
   over copper that actually overlaps on the same layer. An unrouted net is a
   dead board.
2. **legality** — DRC errors, then warnings. A violation is a scrapped board:
   two weeks and about $85.
3. **quality** — via count, total copper length, differential-pair coupling,
   power-net width. In that order.
4. **cost** — nothing, and that is the honest answer. `iterations` was this
   tier until the judge measured what nine families count with it: 20
   negotiation rounds, 283 nets, 38,427 enumerated candidates, all at the same
   cap. The contract defines an iteration as *algorithm-defined outer work*, so
   ranking on it compares accounting units. It is still reported, with
   `nodes_expanded` and wall-clock, and none of the three is ranked on.

### Where each legality check comes from

**The pipeline's, delegated to `circuitpy.checks.dfm_warnings`** — the same
function that decides `fab.ready`: `dfm_hole_clearance`, `dfm_trace_width`,
`dfm_via_diameter`, `dfm_drill_size`, `dfm_annular_ring`, `dfm_edge_clearance`,
`dfm_power_width`. A scorer that disagrees with the pipeline is worse than no
scorer, so it does not get its own opinion about any of those. Even the geometry
primitives are *imported* from `circuitpy.checks` rather than copied, and
`geometry.assert_primitives()` fails loudly if they are renamed.

**Ours, because nothing upstream of KiCad can see them** — `short`, `clearance`,
`via_in_pad`, `keepout`, `trace_edge` / `off_board`. Copper-to-copper clearance
has no representation in circuit.json at all; today it is found by exporting
gerbers and running KiCad DRC, which costs a minute and a toolchain. A router
being scored a thousand times cannot pay that, and cannot *consult* it while
routing.

Board-level findings that no router can influence — `dfm_price_tier`,
`dfm_board_size`, `dfm_thickness` — are reported separately and never counted
against a route.

### The shape model

Every distance here is between two **rounded convex cores**: a polygon swept by
a radius. That one form covers everything on a board without approximating any
of it.

| shape | core | radius |
|---|---|---|
| trace segment | the segment | half the width |
| via, round pad | a single point | half the diameter |
| pill (`rotated_pill`) | the spine | half the short side |
| rectangle | its four corners | 0 |
| rounded rectangle | the corners, inset | the corner radius |
| polygon pad | its own outline | 0 |

Distance is exact in both directions: the minimum over every pair of core edges
when the cores are apart, and the separating-axis penetration depth when they
overlap. A stadium stays a plain 5-tuple, so the hot path is unchanged.

**What this replaced, and what it cost us.** Until 2026-08-16 every rectangle —
pad *and* keepout — was its inscribed stadium. On a square pad that is the
inscribed circle, so each corner protruded by `(√2−1)·w/2`: **0.21mm on a 1.0mm
pad, against a 0.09mm gate**. The docstring called it "can miss a finding, never
invent one". It was the dominant failure mode of the whole benchmark:

* Harness-clean routers were pipeline-dirty and the ranking inverted.
  `plane-and-classes` and `maze-astar` each scored 7/12 harness-clean and 1/12
  pipeline-clean.
* Worst case measured: `maze-astar` on hydrate-coaster, a 2.34 × 3.6mm pad —
  the model reported 0.210mm of air where the trace sat 0.250mm *inside* the
  pad. A 0.46mm modelling error against a 0.09mm gate.
* On a keepout it was worse. The 7.3 × 1.23mm `pcb_keepout_0` of the USB-C
  block loses 0.255mm at each corner, 2.8× the gate, which is how our copper
  turned a `fab.ready` board into five blocking findings the harness could not
  see.

Re-run against the corrected model, `maze-astar` goes from 401 harness errors
to **0** at the same completeness, and from 213 real KiCad copper errors to
**0** over all 12 boards that match their instance.
`pathfinder-negotiated` reaches the same zero and pays 29 nets for it. The
whole comparison is `scripts/rerun_table.py`.

Re-scored on the same copper, the harness's error count per family now tracks
KiCad's at **Spearman +0.93**, against **0.00** before — before, every family
scored zero errors on the real boards, so there was no signal to correlate.

`scripts/pad_corner_gap.py` is the tripwire, and it shares no code with
`geometry.py`: it measures every pad-trace pair of every copper set on disk
against its own rotated-rect, stadium and polygon arithmetic and reports any
pair where the two disagree across the gate. That count was **3 to 137 per
router**. It is now **0 for all fourteen**, over 224 copper sets. A non-zero
number there is a regression in one of the two implementations, and they were
written separately so that it means something.

Routers design to this model, not only get graded by it. Every grid rasteriser
in `algorithms/` stamps a shape against its core's outward edge lines: negative
inside the shape, exact outside it, and a slight under-read in the wedge past a
corner, so a router now claims a cell or two extra near a pad corner where it
used to be 0.21mm optimistic. `topological-graph` is the exception and says so
in its own docstring: it covers obstacles with discs by construction, so it
plans against the inscribed stadium and lets `Workspace` refuse anything that
model let through.

### What is not checked, said out loud

Shipped with every result as `coverage_gaps`:

* **plane islanding** — a trace that cuts a poured plane in two is not detected
* acid traps and copper balance
* an elliptical pad is measured as a pill — the ellipse's outer bound, so a
  finding near the minor axis can be invented, never missed
* how *deep* two overlapping shapes overlap is measured on their convex hulls,
  so the depth reported for a re-entrant polygon pad can be overstated. The
  short-versus-clearance verdict itself is exact
* `circuitpy.checks` still reads pads and holes unrotated in its own
  hole-clearance check, so the delegated `dfm_*` findings carry that blind spot
* solder mask, silkscreen and paste — not routing

### The ruler travels with the score

```
ruler : fe41e1dbd433, 13 check kinds, jlcpcb @ 0.09mm gate,
        circuitpy.checks a25a24c06979
        — compare only against a run with the same hash
```

A rate improves for two reasons and only one of them is good: the routes got
better, or the ruler got shorter. Two scores are comparable only when their
hashes match. A run against a stricter set is a new baseline, not an
improvement.

**The delegated checks are part of the ruler**, as a sha256 of the
`circuitpy.checks` source. Not a version string: that module is edited in this
repo, often by another agent, and a working-tree edit has no version. It is in
the hash because a change there changes our numbers — on 2026-08-16 somebody
taught it to read `ccw_rotation` and `dfm_hole_clearance` went from 2 findings
across the tournament to 107, with nothing in the ruler moving to say so. A
score that can change while its ruler stays still is not carrying its ruler.

The ruler moved three times on 2026-08-16, `b3c77d55b171` → `032bfa67418e` →
`56d69c365a72` → `fe41e1dbd433`: pads and keepouts became their true shapes,
the cost tier left the ranking key, and the delegated checks joined the hash.
The last move changed no number. **Nothing measured against `b3c77d55b171`
is comparable to anything here.** Every headline number in
`docs/architecture/routing.md` from before that date is a number about a
different ruler, and the re-scored ones are a new baseline — not an improvement
and not a regression.

### A score refuses when it would be about the wrong thing

Every instance carries a `placementHash` over the geometry a router may not
move — board, pads, drills, keepouts, and no copper, so a routed board and the
instance it came from hash the same. `routerlib score` compares it before it
measures anything and **refuses (exit 2) on a mismatch**, naming the pads that
moved, appeared or vanished. `--allow-drift` scores anyway.

The failure it exists to stop is real and was caught on this package: scoring
the shipped router's copper for `harness-puck` printed *"0.0% routed, 36 nets
unconnected"*. Nothing was wrong with the router. Another agent's rebuild had
left that board's `circuit.json` with zero traces in it. A benchmark whose worst
number can be produced by an empty file is not measuring an algorithm.

### Determinism

```python
determinism_check(router, problem, budget, runs=2)
```

Runs twice and requires **byte-identical output**, on two axes that fail
differently: the *fingerprint* (copper with ids and timings stripped — a router
that finds the same copper in a different internal order still passes) and the
*serialised circuit.json bytes* (a router that finds the same copper but mints a
different id fails, and should: the pipeline hashes what it writes). Determinism
is a scored property. A router that fails this cannot be compared to anything,
including itself yesterday.

## The baseline

`baseline-pattern` — straight/L/Z pattern routing with fanout vias, no rip-up,
no search. Deliberately the dumbest thing that works, so that every later
algorithm has a floor that was not tuned.

Nets in class order (ground, power, pairs, signals). A net with a plane gets one
via per pad into the plane and no traces between pads. Everything else becomes a
Euclidean MST and each edge is tried against a fixed pattern list, top layer
first. **If no pattern is legal, the edge is left unrouted** — it never places
copper it cannot defend.

Its score shape is the point: **legality perfect, completeness poor**. That is
the opposite failure to the one we ship, and the two together bracket what a
real router has to do.

Measured 2026-08-16 over all 16 instances, `ruler fe41e1dbd433`:

```
2/16 instances clean, 57.3% mean completeness, 37 DRC errors, 16/16 deterministic
```

Per instance it ranges from 100% (`matrix-status-led`, `matrix-i2c-bus` — the
two-pad instances) down to **19.0%** on `matrix-rp2040-core__usb-c-data`, and it
finishes 65.2% of `terminal-keyboard`'s 89 nets with 158 vias.

That error column used to read **0**, and the difference is not a regression in
the baseline — it is the same copper measured with a ruler that can see a pad
corner. It also kills the old claim that the 0-error column was tautological
because the router asks `Workspace` with the same geometry the scorer grades
with. It asks with the same *shape model* now, but it stamps its obstacles into
a grid, and 37 findings is the size of the gap between "the grid said yes" and
"the geometry says no". **Completeness is still the column a real router has to
win.**

That the scorer is not merely agreeing with itself is checked against copper it
did not produce. Scored against the shipped autorouter's own output on
`terminal-keyboard`: 100% complete, and **8 errors** — 4 shorts, 2 clearance,
2 `dfm_hole_clearance`. The last two are the defect this package was built
because of.

## Known divergence from the pipeline

`circuitpy.checks` does not read `ccw_rotation` on `pcb_smtpad` or
`pcb_plated_hole`; `routerlib.geometry` does. Read unrotated, the eight 2.25 ×
0.63mm pills of a 1.27mm-pitch package at 270° become one horizontal bar
overlapping its neighbours. Measured on hydrate-coaster 2026-08-15: six invented
shorts on a board that has none. routerlib reads the field because a benchmark
cannot run on invented findings; the pipeline's own hole-clearance check still
has the blind spot and should be fixed there.

The pipeline also models a rectangle as its inscribed stadium, which is the
defect this package fixed on its own side of the line. Our numbers are now the
stricter ones, and `dfm_hole_clearance` — which we delegate — is still measured
the old way.

**One throw can read as a clean board.** `@tscircuit/checks` `runAllChecks`
runs every check in one pass with no guard, so a single failure returns an
empty findings list. Reproduced 2026-08-16: strip the `points` off a polygon pad
— exactly what routerlib used to write back — and it throws inside
`SpatialObjectIndex.addObject` and reports nothing. Twelve of the sixteen
instances carry a USB-C receptacle and twelve of sixteen lost their whole
report that way. Two defences, on our side of the line:
`scripts/_js/routing_checks.cjs` runs each check by name and reports a throw as
a throw, and every report carries `complete`, which `tournament_results.py`
drops rather than counting as zero. The root cause was ours and is closed — a
pad keeps its outline now — but the guard stays, because the next shape we get
wrong should be loud.

## The portfolio

Nine families, and the per-instance winner varies — so the last stage is
`routerlib.portfolio`: features in, algorithm out, with the rule that fired.

```bash
python3.12 packages/router/portfolio.py rules
python3.12 packages/router/portfolio.py select --instance harness-puck
python3.12 packages/router/portfolio.py suite --mode relay --budget-class thorough
```

Two findings shape it, and the first is negative. **Feature-based selection
loses to a constant**: every single-threshold rule over 21 features, leave-one-
out cross-validated, leaves 17 nets unrouted and 202 real DRC errors against 13
and 80 for "always `pathfinder-negotiated`". One family is at or tied with the
best completeness on 12 of 16 instances, and the hindsight oracle beats it by
two nets out of 216. So there are two rules — one for the ≤ 2-net instances,
one default — and a `REJECTED_RULES` list carrying the measurement that killed
each intuition, pinned by a test.

The win is in composing, not picking. `mode=relay` runs the lead router, then
hands each follower **only the nets still unconnected** with the copper already
down as obstacles: 98.0% mean completeness against the best single family's
94.0% and the oracle's 95.7%, 16/16 deterministic, 262s for the whole suite.
Verified through KiCad it also carries a third more violations per millimetre,
and it clears the same 3 of 10 boards at the fab-ready bar.

`docs/architecture/routing.md` has all of it, including the A/B against the
shipped autorouter, which the shipped autorouter still wins — on completeness
now, not on legality. Re-run against the corrected pad model, boards at
`12a6dd6`:

| board | incumbent | portfolio relay/thorough |
|---|---|---|
| hydrate-coaster | 100.0% routed, 7 KiCad | 87.5%, **0** |
| harness-puck | 97.2%, 4 KiCad | 94.4%, **0** |
| terminal-keyboard | 98.9%, 3 KiCad | 92.1%, **0** |

Every one of the incumbent's remaining findings is `holes_co_located`, which is
a fab query. Ours are gone. **We do not win**: a board with 7 nets missing is
not better than a board with a duplicate drill, and neither side is
`fab.ready`. The one place we are straightforwardly ahead is
`matrix-ldo-3v3__usb-c-power` through the real shipping gate — 100% routed,
`fab.ready: true`, 0 blocking, **7 vias against the incumbent's 16**.

## Layout

```
src/routerlib/
  model.py         the contract: problem, solution, budget, rules, Router protocol
  geometry.py      capsules, polygon + grid indexes; primitives imported from circuitpy
  adapters.py      circuit.json <-> RoutingProblem / RoutingSolution
  workspace.py     mutable board for algorithms to route into
  drc.py           legality: delegated + the five checks we own
  connectivity.py  completeness, recomputed from copper
  scoring.py       the lexicographic score, the ruler, the determinism check
  bench.py         instance format, measured features, the suite runner
  baseline.py      the floor
  portfolio.py     features in, algorithm out; single / best-of-n / relay
  cli.py           extract / list / run / score
portfolio.py       the portfolio CLI: select / rules / run / suite
algorithms/*.py    the nine tournament families, loaded by path
benchmarks/
  instances/*.json committed fixtures
  manifest.json    what is on disk, with features and baselines
  tournament/      results-2026-08-16.json, portfolio-2026-08-16.json
                   rescore-truepads-2026-08-16.json   the same copper, true shapes
                   rerun-truepads-2026-08-16.json     re-routed against them
scripts/build_instances.py    rebuild the fixtures from today's boards
scripts/upgrade_instances.py  re-baseline them in place, printing every hash
scripts/rescore.py            replay the copper on disk against a new ruler
scripts/rescore_table.py      the corrected table, and the KiCad rank correlation
scripts/rerun_table.py        routed-against-the-stadium vs routed-against-the-truth
scripts/pad_corner_gap.py     the pad-model tripwire: independent arithmetic, must be 0
scripts/ab_incumbent.py       our copper vs the shipped autorouter, same board
```

## Running it

```bash
cd packages/router
python3.12 -m pytest -q                       # the suite
python3.12 -m routerlib list                  # instance table
python3.12 -m routerlib run --router baseline-pattern --report /tmp/run.json
```

Tests never touch the network; kicad-dependent checks skip rather than fail when
`kicad-cli` is absent.
