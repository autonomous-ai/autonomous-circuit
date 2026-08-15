# routerlib — the routing contract, the benchmark, and the scorer

```python
def route(problem: RoutingProblem, budget: Budget) -> RoutingSolution
```

That is the whole interface. Everything else in this package exists to make
that line measurable: adapters so a real board becomes a problem and a solution
becomes a board again, a benchmark of instances stripped out of boards we have
actually built, and a scorer that judges **completeness, then legality, then
quality, then cost** — in that order, because that is the order in which a
defect costs money.

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
| `pads` | every landing: net, centre, size, **rotation**, layers, SMD or plated hole |
| `drills` | every hole, plated or not, as a real slot when it is one |
| `keepouts` | declared no-copper zones, per layer |
| `planes` | poured copper already belonging to a net — a pad inside one is connected |
| `nets` | pad ids, a **net class** (`signal` / `power` / `ground` / `diff_pair`), a diff partner, and the width this net must be routed at |
| `existing_traces` / `existing_vias` | copper the router must keep and must not violate |
| `rules` | see below |

Net identity is tscircuit's `subcircuit_connectivity_map_key`, not a name we
invent — so a net in a problem and a net in a DRC finding are the same object.

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
python -m routerlib list                     # the table below, regenerated
python packages/router/scripts/build_instances.py   # refresh the fixtures
```

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
`test_drc.py` fails if that ever stops being true.

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
4. **cost** — iterations. Wall-clock is recorded and never scored.

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

### What is not checked, said out loud

Shipped with every result as `coverage_gaps`:

* **plane islanding** — a trace that cuts a poured plane in two is not detected
* acid traps and copper balance
* a rectangular pad is modelled as its **inscribed stadium**, which rounds
  corners inward: the model can miss a finding at a sharp corner, it cannot
  invent one
* solder mask, silkscreen and paste — not routing

### The ruler travels with the score

```
ruler : 4c1f0f2f0f5a, 13 check kinds, jlcpcb @ 0.09mm gate
        — compare only against a run with the same hash
```

A rate improves for two reasons and only one of them is good: the routes got
better, or the ruler got shorter. Two scores are comparable only when their
hashes match. A run against a stricter set is a new baseline, not an
improvement.

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

Measured 2026-08-16 over all 16 instances, `ruler b3c77d55b171`:

```
2/16 instances clean, 57.3% mean completeness, 0 DRC errors, 16/16 deterministic
```

Per instance it ranges from 100% (`matrix-status-led`, `matrix-i2c-bus` — the
two-pad instances) down to **19.0%** on `matrix-rp2040-core__usb-c-data`, and it
finishes 65.2% of `terminal-keyboard`'s 89 nets with 158 vias. Both numbers are
floors, not results: the whole 0-error column is close to tautological, because
the baseline asks `Workspace` for permission with the same geometry the scorer
grades with and simply drops any edge it cannot place legally. **Completeness is
the column a real router has to win.**

That the scorer is not merely agreeing with itself is checked against copper it
did not produce. Scored against the shipped autorouter's own output on
`terminal-keyboard`: 100% complete, and **8 errors** — 4 shorts, 2 clearance,
2 `dfm_hole_clearance`. The last two are the defect this package was built
because of.

## Known divergence from the pipeline

`circuitpy.checks` does not read `ccw_rotation` on `pcb_smtpad` or
`pcb_plated_hole`; `routerlib.geometry.rect_capsule` does. Read unrotated, the
eight 2.25 × 0.63mm pills of a 1.27mm-pitch package at 270° become one
horizontal bar overlapping its neighbours. Measured on hydrate-coaster
2026-08-15: six invented shorts on a board that has none. routerlib reads the
field because a benchmark cannot run on invented findings; the pipeline's own
hole-clearance check still has the blind spot and should be fixed there.

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
  cli.py           extract / list / run / score
benchmarks/
  instances/*.json committed fixtures
  manifest.json    what is on disk, with features and baselines
scripts/build_instances.py
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
