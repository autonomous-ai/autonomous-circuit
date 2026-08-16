# Mixture of routers: composing the experts, and the data flywheel

**Status: design, 2026-08-15.** The tournament (`docs/architecture/routing.md`, in progress)
builds seven routing algorithms and scores them independently. This is what happens next:
stop treating them as seven candidates for one job, and start treating them as seven
specialists who can work on the same board.

## Why composition beats selection

Selection asks *which algorithm routes this board best*. That is already worth having — the
per-instance winner varies and the features predicting it are cheap. But it throws away the
more useful fact: **the winner usually varies within a single board, not just between boards.**

Our own keyboard makes the point. It has three routing problems wearing one coat:

| Region | What it is | Who should route it |
|---|---|---|
| The 10x5 key matrix | perfectly regular, 10mm pitch, 100 parts | channel/river router — a textbook fit |
| The RP2040 cluster | 0.4mm-pitch QFN, dense escape | exact (ILP/SAT) on a small window, or A* with a good escape pattern |
| GND | ~30% of all pads | not routed at all — vias into the plane |
| USB D+/D- | a coupled pair with a length and gap constraint | the diff-pair router, together, before anything else competes for the space |
| Everything else | ordinary signals across open board | PathFinder |

No single algorithm is the right answer to all five. A general router that handles the matrix
adequately and the QFN adequately is worse than two specialists, and it is *much* worse than
two specialists plus a plane router that deletes a third of the problem before anyone starts.

## Five ways to compose, in increasing ambition

**1. Net-class decomposition — already the strongest, already half-built.**
Route by class, in dependency order: plane vias first (GND stops being a routing problem),
then power at its computed width, then diff pairs as coupled pairs, then signals. Each class
goes to the expert that understands it. The `plane-and-classes` family in the tournament is
this idea applied once; generalising it is the first thing to build, because it removes work
rather than redistributing it.

**2. Spatial decomposition — regions to specialists.**
Partition the board (regularity detection, density map, block boundaries — golden blocks give
us natural seams), route each region with its best expert, then stitch across the boundaries.
The stitching is the hard part and where this usually fails: a region routed optimally in
isolation can leave no legal crossing at its edge. Mitigation is to route the *inter-region*
nets first at low resolution, fixing the crossing points, then let each region solve inside
fixed boundary conditions. That is how large-scale place-and-route has always worked.

**3. Cascade — cheap first, expensive on the remainder.**
Pattern-route everything (milliseconds), hand the failures to PathFinder (seconds), hand
PathFinder's remaining conflicts to the exact solver on small windows (minutes). Each stage
only sees what the previous could not do, so the expensive methods run on tiny inputs where
they are actually affordable. This is the highest value-per-effort composition and should be
the default pipeline.

**4. Portfolio best-of-N — run several, keep the best.**
Only possible because every algorithm is deterministic and scored on one scale. Embarrassingly
parallel, and strictly better than picking one. Cheap insurance; already in the tournament's
portfolio phase.

**5. Solution recombination — the interesting one.**
Take N complete solutions and build a better one net by net: for each net, choose the routing
from whichever solution did it best, then repair the conflicts the merge creates. This is
crossover across *algorithms* rather than within one. It can beat every input solution, and
nothing else here can. The conflict repair is real work and it may not pay — worth trying
precisely because we cannot predict the answer.

## The data flywheel

Every routing attempt is a labelled example, and we are about to generate thousands. **If we
do not log them from the first tournament run, that data is gone** — which is why the schema
matters now rather than later.

One row per attempt, appended to `packages/router/data/attempts.jsonl`:

```
instance_id, instance_features{net_count, pad_count, area_mm2, pad_density,
  regularity_score, min_pitch_mm, gnd_pad_fraction, has_diff_pair, layer_count},
algorithm_id, params{...}, seed,
result{completeness, violations_by_class, vias, length_mm, diff_pair_coupling,
  iterations, wall_s},
score, timestamp, git_head, ruler_hash
```

`ruler_hash` is not optional. A score is meaningless without the check set that produced it —
the same discipline the north star already demands of the eval numbers, for the same reason:
a rate improves either because the router got better or because the ruler got shorter, and
the number alone cannot tell you which.

### What the data is worth, in order of how soon it pays

**Now — algorithm selection.** A contextual bandit over (features -> algorithm) trained on
attempts. This is the portfolio, learned instead of hand-written, and it improves every time
anyone routes anything. Fifteen instances cannot train it; a few thousand user boards can.

**Soon — parameter tuning.** Each algorithm has knobs (via cost, history weight, cooling
schedule, grid resolution). Bandits over knob settings per feature-bucket is a well-understood
win and needs no new machinery.

**Then — learned net ordering.** Ordering is the single biggest lever in every family here,
and it is exactly the kind of sequential decision RL is good at: state = partially routed
board, action = which net next, reward = final score. A policy trained on our own attempts
would transfer across boards because the features are geometric, not product-specific.

**Later — a learned cost function, or a learned router.** Predict, for a candidate path, how
much it will constrain the nets that follow — the thing human designers do by instinct and no
heuristic captures. This is the ambitious end and it needs the data the first three stages
generate. Worth naming as the destination; not worth starting first.

### Growing the benchmark

The suite starts as ~15 instances stripped from boards we already have. Every board any user
routes becomes a candidate instance, and the ones worth keeping are the ones that were *hard*:
anything that failed, needed repair rounds, or where the algorithms disagreed. A benchmark of
easy boards teaches nothing.

Two rules, both learned the hard way this week:

- **Instances are frozen once admitted.** A benchmark that drifts cannot measure progress.
  New instances are added; existing ones are never quietly edited.
- **Every reported score carries its ruler.** When the check set changes, the new number is a
  new baseline and must be reported as one — not as an improvement.

## What to build first

1. **Attempt logging** into the tournament's harness, before the tournament's results are lost.
2. **The cascade** (composition 3) — highest value per unit effort, and it subsumes the
   portfolio as its first stage.
3. **Net-class decomposition** (composition 1) generalised out of the `plane-and-classes`
   family — it deletes work rather than moving it.
4. Then spatial decomposition and recombination, judged on the same benchmark, in the same
   tournament, against the same ruler.

Nothing here is worth anything without the harness that scores it. That is the whole reason
the tournament builds the scorer before it builds a single algorithm.

---

## Measured: spatial decomposition, 2026-08-16

Composition 2 above is now built (`packages/router/src/routerlib/compositions/spatial.py`)
and measured. **The plan in this document is wrong in an interesting way**: regions-to-experts
loses badly, and one piece of it — routing the fine-pitch escape *before* the inter-region
nets, which is the opposite of what "the stitching is the hard part" argues for — is the
largest single win anyone has measured on this benchmark.

Ten arms, 16 instances, one budget (`max_iterations=2_000_000, max_nodes=20_000_000, seed=0`),
ruler `e1ee2a5623d0`, `scripts/spatial_suite.py`. Full record in
`packages/router/benchmarks/tournament/spatial-2026-08-16.json`.

| arm | what it is | mean routed | nets | boards at 100% | s |
|---|---|---|---|---|---|
| `single` | one router, whole board | 81.5% | 320/380 | 3/16 | 312 |
| `relay` | four routers, each asked only for the residue | 90.6% | 350/380 | 5/16 | 589 |
| `spatial` | regions to specialists, crossings first | 70.9% | 261/380 | 3/16 | 190 |
| `spatial-flat` | same partition, one router everywhere | 75.6% | 287/380 | 3/16 | 172 |
| `spatial-tight` | same, cutting harder (76.8% interior, not 61.8%) | 74.0% | 276/380 | 3/16 | 213 |
| `spatial-shuffled` | **same staging, nets dealt out by hash** | 79.8% | 286/380 | 4/16 | 170 |
| `spatial-chain` | crossings retried by three more families | 78.3% | 296/380 | 3/16 | 268 |
| `spatial-escape-first` | fine-pitch regions before the crossings | 86.9% | 319/380 | 6/16 | 241 |
| `spatial-residue` | `spatial` + the relay's follower chain | 83.8% | 321/380 | 5/16 | 642 |
| **`spatial-best`** | **escapes first + the relay's chain** | **94.3%** | 346/380 | **8/16** | 743 |

`mean routed` is the mean of per-instance completeness and `nets` is the pooled count; they
disagree whenever an arm wins on small boards. **`boards at 100%` is the column the fab-ready
bar reads**, because a board with one net missing is not a board.

### The escape goes first, and that is the result

`spatial-best` is the first composition in this package to beat the relay: **8 boards finished
against 5**, 94.3% against 90.6%, for 26% more wall clock and 4 fewer nets connected overall.
The difference between it and `spatial-residue` — same partition, same followers, same
everything else — is one line: fine-pitch regions route *before* the crossing stage rather
than after. That single reordering is worth **+11.3 points and three more finished boards even
on a single router** (75.6% / 3 → 86.9% / 6).

The reasoning behind "crossings first" is sound and still holds for most regions: a region
routed in isolation can leave no legal crossing at its edge. It fails for an escape because
the asymmetry runs the other way. A 0.4mm-pitch QFN has one or two channels out from each pin;
a crossing net can detour round the whole package. **Whoever has no alternative should go
first.** Fine-pitch regions were the worst class on the board by a distance — 28 of 88 interior
nets connected when they went second — and they are the class that improves when they go first.

### The seam is worth nothing except for finding the escape

`spatial-shuffled` is the control: identical region count, identical group sizes, identical
staging, nets dealt into groups by SHA-256 of their id instead of by position. With the
crossings first it **beats the real partition**, 79.8% against 75.6% and 4 finished boards
against 3.

So the crossing/interior split carries *negative* information. Splitting nets by geometry and
routing the groups in stages is worse than splitting them at random, and both are worse than
handing the whole board to one router (81.5%). PathFinder's entire mechanism is global
negotiation — it never commits, so ordering stops mattering by the fourth pass — and staging
destroys exactly that: each stage's copper is immovable to the next.

The same partition read differently — *which region is a dense escape* — is worth 11 points.
Same geometry, two uses, opposite signs.

### Regions to specialists loses at region level too

The table this composition was designed around was measured on the identical partition,
region order and budget against routing every region with `pathfinder-negotiated`:

| region character | nets asked | `exact-and-structured` | `pathfinder-negotiated` |
|---|---|---|---|
| lattice | 123 | 94 | **114** |
| fine-pitch | 88 | 22 | **28** |
| open | 24 | — | 23 |

Both hypotheses are the region-level form of rules `routerlib.portfolio.REJECTED_RULES`
already refuted at board level, and they fail the same way. They are recorded in
`spatial.REJECTED_ASSIGNMENTS`, the default map is the constant, and a test fails if either
comes back without a new measurement.

### What the partition actually finds

Per-instance, at the default parameters (balance 0.30, depth 3, a split must isolate more nets
than it cuts):

- 44–78% of nets end up interior to a region on the four large boards — `terminal-keyboard`
  78% across 8 regions, `matrix-rp2040-core__usb-c-data` 67%, `harness-puck` 53%,
  `hydrate-coaster` 44%.
- 18% on `matrix-ldo-3v3__rp2040-core__usb-c-power`: five regions that isolate three nets.
- **No seam at all** on `matrix-i2c-bus`, `matrix-ldo-3v3__usb-c-power` and
  `matrix-status-led`. `Partition.seam` is False with a reason and the composition degenerates
  to the plain global router, so it can never quietly become per-board selection.

Cutting harder does not help: `spatial-tight` raises the interior share from 61.8% to 76.8%
and drops completeness by 1.6 points.

### Two things this turned up that are not about spatial decomposition

**The relay's 98.0% was the broken pad model.** Re-measured on the corrected geometry it is
**90.6%**, 350 of 380 nets, 5 of 16 boards finished. That is a new baseline, not a regression
— `docs/architecture/routing.md`'s relay numbers were taken against ruler `b3c77d55b171`.
Confirmed independently by `scripts/relay_baseline.py`, which agrees instance for instance.

**Stage copper ids collide, and nothing checks it.** `routerlib.connectivity` unions
`(copper id, layer)` nodes and `routerlib.drc` skips a pair when the two ids match, so two
stages that both mint `v0` become one node carrying two nets — a connection that does not
exist and a short that is never checked, at once. The relay does this: 35 colliding ids across
the suite, 23 of them on `matrix-rp2040-core__sw-tact`. Every solution in the table above was
therefore scored twice, once as returned and once with every id made unique, and **the two
scores are identical everywhere** — the collisions have not yet bought a false number. It is
latent, not active, and `spatial` namespaces its stages so it cannot happen there.

### What is not measured

- **KiCad on the real boards.** Every arm scores 0 harness errors, and harness-vs-KiCad rank
  correlation is +0.93 since the pad model was fixed, but agreement is not a measurement. The
  copper from `spatial-best` has not been through `kicad-cli pcb drc`.
- **`boundary_clearance`.** The knob exists (widen the crossing stage's target clearance so it
  leaves room for the regions) and was never run at anything but 1.0.
- **Determinism of the four-router arms at `--runs 2`.** `spatial` and `spatial-flat` are
  16/16 on two runs; the arms marked `n/m` in the record were routed once, and the record says
  `null` rather than a vacuous `true`.
