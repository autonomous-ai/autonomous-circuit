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
