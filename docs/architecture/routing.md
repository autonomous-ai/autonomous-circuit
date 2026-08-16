# Routing

On 2026-08-16 nine routing algorithms were run over sixteen benchmark
instances, judged by an agent that wrote none of them, and their copper put
through KiCad on the real boards. This document is what that measurement says:
what each family does, which one wins where, how the portfolio picks, and the
six places where our router is still worse than the one we ship.

The short version, because the rest is long. **Selecting an algorithm per board
buys almost nothing; composing several buys 4 percentage points of
completeness — and neither gets a board to the fab-ready bar, where the shipped
autorouter still beats us on all three example boards.**

---

## The problem is copper, and it is exactly specified

Input: a **placed** board. Components are where they are and the router may not
move them. With it comes a netlist — which pads must be electrically joined —
and a rule set.

Output: copper. Polylines, each on a layer, each with a width, plus vias
(position, drill, pad). Every net connected, nothing in violation.

Two copper layers. Traces cross only by going through the board to the other
side. The rules come from JLCPCB's published capabilities and live in exactly
one place, `packages/circuitpy/src/circuitpy/fab.py`:

| Rule | Value |
|---|---|
| trace-to-trace, trace-to-pad clearance | ≥ 0.10mm (gated at 0.09 after tolerance) |
| copper to a plated component hole | ≥ 0.28mm |
| copper to a via hole / a non-plated hole | ≥ 0.20mm |
| minimum trace width | 0.10mm |
| power nets | ≥ 0.5mm |
| vias | 0.3mm drill, 0.6mm pad, never inside an SMD pad |
| board edge | ≥ 0.2mm |

Three numbers, not one, for copper-to-hole. A router that keeps a single
"clearance" number cannot express that, and that is the whole reason this
package exists.

## Every routed net makes the next one harder

Routing two layers is NP-hard, and the reason is worth stating concretely
rather than by citation: the resource a net consumes is the resource the next
net needs. Route net 3 straight through the channel and net 9 has no way
across. Nothing in net 3's own cost function knows that.

That gives every greedy router the same shape of failure — high completeness
early, a wall late, and a final few nets that are unroutable not because they
are hard but because of the order. Real routers answer it in one of three ways:
search the *order* rather than the path, price contention so nets negotiate, or
tear routed nets out and try again. Three of the nine families below are one of
each.

Our domain is narrow enough that this is tractable: two layers, a catalogue of
golden blocks, at most 130 parts, and boards under 10000mm². That is why we can
write a router that knows our rules natively instead of configuring one that
does not.

## The router we ship cannot represent the rules we grade against

Four attempts to steer the tscircuit autorouter failed, and all four failed for
one reason. Measured on our own boards:

| What it does | What that means |
|---|---|
| Runs copper 0.07mm from a hole needing 0.28mm | No model of drill clearance |
| Produced byte-identical copper with and without a ground pour | A plane is 73 obstacles to it, not a net |
| Routes USB D+/D− as two unrelated nets | No net classes |
| Targets 0.10mm clearance, which is our floor | Its target is our failure line |

You cannot configure a router into understanding a rule it has no
representation for. So: nine families, one contract, one scorer, one benchmark
— `packages/router/README.md` has the contract and the harness.

---

## The nine families

All nine implement `route(problem, budget) -> solution`, all nine take a
**counted** budget (iterations and expanded nodes, never a clock), and all
sixteen instances came out of boards we have actually built.

| Family | The idea |
|---|---|
| `baseline-pattern` | Straight/L/Z patterns on an MST, no search, no rip-up. Drops any edge it cannot place legally. The floor nobody tuned. |
| `maze-astar` | Lee's grid maze router grown up: A\* over `(layer, cell)`, one grid per trace width, a cell has an *owner* rather than a free/blocked bit, a via is an edge between the two layer copies. |
| `topological-graph` | Decide which side of each obstacle a wire passes first, draw copper second. Obstacles get covered by discs, the disc centres are Delaunay-triangulated, and a route is a shortest path in the dual graph through measured gates. |
| `ripup-reroute` | The commercial shape. Route greedily; when a net cannot get through, ask *who is in the way*, rip those out, make the region expensive, and reroute the group in a different order. |
| `meta-anneal` / `meta-genetic` | Hold the inner router fixed and search the **net order**, layer preference and topology. Simulated annealing and a GA over the same genome, both reported against `maze-greedy`, the same inner router run once. |
| `pathfinder-negotiated` | McMurchie–Ebeling PathFinder. Let every net take the route it wants, price contested resources with a *present* cost that rises within a round and a *history* cost that rises permanently between rounds, and iterate until nobody is fighting. Never commits, so ordering stops mattering by the fourth pass. |
| `plane-and-classes` | A plane is a net: where a layer is poured, that net is not routed at all — every pad drops one via into copper it is already sitting on. Everything else is routed in net-class order, constrained classes first. |
| `exact-and-structured` | Detect the lattice (a key matrix is a channel-routing problem with a known answer) and lay a spine; for everything else, enumerate candidate copper, compute which candidates exclude each other, and solve the choice as an integer program by branch-and-bound. |

## The tournament: one budget, one ruler, two rankings that disagree

Budget `max_iterations=2_000_000, max_nodes=20_000_000, seed=0` for everyone,
two runs per cell, one subprocess per cell, ruler `b3c77d55b171`.

**Harness ranking** (`routerlib.scoring`, lexicographic: completeness →
legality → quality → cost), 16 instances:

| family | mean routed | clean | vias | copper mm | det | suite s |
|---|---|---|---|---|---|---|
| pathfinder-negotiated | 94.0% | 7/16 | 662 | 10751 | 16/16 | 202 |
| maze-astar | 92.7% | 7/16 | 576 | 8831 | 16/16 | 239 |
| plane-and-classes | 90.1% | 8/16 | 499 | 7278 | 16/16 | 227 |
| ripup-reroute | 86.2% | 6/16 | 966 | 9145 | 16/16 | 135 |
| meta-anneal | 84.0% | 4/16 | 717 | 9238 | 16/16 | 193 |
| meta-genetic | 83.5% | 4/16 | 775 | 10387 | 16/16 | 168 |
| topological-graph | 78.9% | 3/16 | 213 | 6444 | 16/16 | 139 |
| exact-and-structured | 78.6% | 4/16 | 116 | 4750 | 16/16 | 31 |
| baseline-pattern | 57.3% | 2/16 | 863 | 7476 | 16/16 | 6 |

Every family is deterministic, 16 of 16, and every self-reported number
reproduced when re-run by the judge.

**Pipeline ranking** — the same copper dropped into the real board and put
through `@tscircuit/checks` and `kicad-cli pcb drc`, empty-solution control
subtracted, on the 10 composition cells that rebuild to a byte-matching
placement:

| family | mean routed | KiCad copper errors | errors / 100mm |
|---|---|---|---|
| baseline-pattern | 62.8% | 10 | 0.55 |
| exact-and-structured | 84.0% | 13 | 1.07 |
| pathfinder-negotiated | 96.7% | 48 | 1.91 |
| topological-graph | 83.8% | 37 | 2.24 |
| ripup-reroute | 92.4% | 58 | 2.41 |
| maze-astar | 97.3% | 132 | 6.12 |
| meta-anneal | 85.6% | 184 | 8.59 |
| meta-genetic | 86.3% | 201 | 9.22 |
| plane-and-classes | 93.5% | 350 | 18.31 |

The two tables rank differently, and the reason is a bug in the ruler rather
than a subtlety in the algorithms. `routerlib.geometry.rect_capsule` models a
rectangular pad as its **inscribed stadium**, which rounds the corners inward
by `(√2−1)·w/2` — 0.21mm on a 1.0mm square pad, more than twice the 0.09mm
gate. A trace can sit a quarter of a millimetre inside a pad and score 0.21mm
of clearance. Ranking all nine families by the harness key and taking the
winner produces 220 real KiCad errors across the 12 real boards; ranking by the
pipeline's own answer produces 144 at the same completeness. The two disagree
on 7 of 12 boards. Fixing the pad model is filed and not done.

---

## Nothing about the board predicts which algorithm wins

This is the result the portfolio was supposed to be built on, and it is
negative. Algorithm selection is a real technique and it usually beats any
single algorithm — when no algorithm dominates. Here one does.

`pathfinder-negotiated` is at or tied with the best completeness on **12 of 16**
instances; the next best is `maze-astar` at 11. Ten of the sixteen instances
have more than one family at the top score, so the selection question only
bites on six.

Every rule that suggested itself was tested and every one is false:

| Intuition | What the tournament says |
|---|---|
| regular matrix → the structured router | `exact-and-structured` ranks 7th or 8th of 9 on all four instances with `grid_score ≥ 0.5`, including `terminal-keyboard` (0.83), the most regular board we have |
| GND > 30% of pads → plane-first | on the 7 instances where ground is ≥ 25% of pads, `plane-and-classes` ranks 3rd to 8th and never 1st; on the 38% board it is 8th |
| a poured plane → plane-first | `plane-and-classes` wins 1 of the 3 plane variants, is 4th on the other two, and carries the worst real-board legality of any family |
| small and dense → exact on windows | `exact-and-structured` ranks 8th of 9 on all five instances with pad density ≥ 5 pads/cm² |

And the general form fails too. Searching every single-threshold rule over 21
features (`feature < cut → router A, else router B`), leave-one-out
cross-validated across the 12 instances with a real board, leaves **17 nets
unrouted and 202 real DRC errors** against **13 and 80** for the constant
"always pathfinder". With 12 labelled instances the rule family fits its own
fold and nothing else. A richer family fits harder.

The oracle says the same thing from the other side: choosing the per-instance
best of all nine with hindsight leaves 11 nets unrouted against pathfinder's
13. Two nets out of 216. **There is no headroom in picking.**

### One feature does predict, and it predicts difficulty, not the winner

Net count separates the benchmark cleanly and monotonically:

| routable nets | at least one family finishes the board |
|---|---|
| ≤ 14 | 8 of 8 |
| 15–20 | 1 of 2 |
| ≥ 21 | 0 of 6 |

That is a property of the instance. It is what the selector uses to set the
honest forecast it returns with every pick, and it is why the default mode is
the wide one rather than the cheap one.

## All the headroom is in composition, not selection

The nine families between them connect **378 of 380** nets across the sixteen
instances. Every net but two is routed by *somebody*. Pathfinder alone connects
347. The gap between 347 and 378 is eight times the gap between pathfinder and
the oracle, and no amount of picking can reach it, because picking gets you one
router's answer.

Merging independent runs does not reach it either, and that was measured first.
Handing the whole board to a second family and unioning the copper on
`matrix-ldo-3v3__rp2040-core__usb-c-power` added 575mm of copper and 39 vias for
**zero** extra nets across three extra families — each one re-solved the nets
that were already solved.

**Relay** does reach some of it. Run the lead router on the whole board, then
hand each follower *only the nets still unconnected*, with the copper already
down as `existing_traces`. Each stage sees a smaller, real problem, and the
composition cannot invent a clearance violation neither stage could see,
because the follower's `Workspace` treats the lead's copper as an obstacle.

Chain: `pathfinder-negotiated → maze-astar → plane-and-classes →
exact-and-structured`, all 16 instances, same budget, two runs each.

| | best single family | oracle of 9 | relay of 4 |
|---|---|---|---|
| mean completeness | 94.0% | 95.7% | **98.0%** |
| harness-clean instances | 7/16 | — | 8/16 |
| deterministic | 16/16 | — | 16/16 |
| wall clock, whole suite | 202s | 699s | 262s |

The relay beats the hindsight oracle by 2.2 points and costs 30% more than its
lead router — less than running the four families independently, because a
follower asked for three nets finishes in seconds. On
`matrix-rp2040-core__usb-c-data` it reaches 95.2% where **no** single family
exceeds 85.7%.

It is not monotone against the oracle. On
`matrix-ldo-3v3__rp2040-core__usb-c-power-plane` the relay reaches 94.1% where
`plane-and-classes` alone reaches 100%: pathfinder leads, its copper is in the
way, and the family that would have solved the board never gets a clean sheet.
Running the relay from more than one lead and keeping the best result fixes
that and has not been built.

### The relay's extra nets cost extra violations

Verified on the 10 rebuilt composition cells, control subtracted:

| | mean routed | KiCad copper errors | errors / 100mm | fab-ready |
|---|---|---|---|---|
| pathfinder-negotiated | 96.7% | 48 | 1.91 | 3/10 |
| exact-and-structured | 84.0% | 13 | 1.07 | 3/10 |
| oracle of the 9 | — | — | — | 3/10 |
| portfolio relay | 98.9% | 66 | 2.55 | 3/10 |

Two things to read here, and the second matters more.

The extra nets are not free and not merely a volume effect: copper rose 3% and
errors rose 38%, so errors *per millimetre* went up by a third. The residual
nets are the ones routed into the tightest space left, and they are dirtier per
millimetre than the ones routed first.

And against the north star — 100% routed **and** zero pipeline findings — the
relay clears the same 3 of 10 boards as the best single family, as the second
best, and as the oracle. Composition bought four points of completeness and zero
fab-ready boards.

---

## The selector: two rules, a forecast, and a list of rules that failed

`packages/router/portfolio.py` (implementation in
`routerlib.portfolio`). Features in, algorithm out, with the rule that fired.

```
$ python3.12 packages/router/portfolio.py select --instance harness-puck
harness-puck
  nets=36 pads=228 area=3848mm2 density=5.9/cm2 plane=False grid=0.28
  -> pathfinder-negotiated  [dominant-default]  mode=relay
     candidates=pathfinder-negotiated+maze-astar
     expect: 36 routable nets; no family finished any of the 6 benchmark
             instances with >= 21 nets (best 85.7-95.5%)
     fallback: baseline-pattern
```

Two rules, in order, first that fires wins:

1. **`trivial-cheapest`** — ≤ 2 routable nets → `baseline-pattern`. On both such
   instances all nine families return 100% routed with identical real-board
   DRC, so take the one that does no search: 6s against 202s over the suite.
2. **`dominant-default`** — everything else → `pathfinder-negotiated`. Best or
   tied on 12 of 16, the lowest unrouted total on the real boards (13 of 216
   nets), and 0.57 real DRC errors per connected net, on a par with the
   do-nothing baseline while connecting 2.3× as many nets.

There are two rules because only two survive. The four intuitions above are in
the code as `REJECTED_RULES`, each with the measurement that killed it, and a
test fails if that list shrinks — a tried-and-failed rule is worth more than an
untried one, and without it the next agent re-derives "regular matrix →
structured router" from the same data that already refuted it.

Every pick carries an `Expectation` from the net-count thresholds: `routable`,
`hard`, or `beyond`, with the count of benchmark instances behind it. A board
with 36 nets is told, up front, that no family has ever finished one.

**Three modes**, chosen by budget class (`cheap` 1 router, `standard` 2,
`thorough` 4):

- `single` — apply the rules, run one router. Within two nets of the oracle.
- `best-of-n` — run the candidates on the whole board, keep the best-scoring
  result. Strictly no worse than its best member and only possible because
  every family is deterministic. Measured gain over `single`: one DRC error
  across 12 boards. It is in for the floor it guarantees, not the mean it
  moves. When it ranks with the harness key it says so in its notes, because
  that key picks a different router than the pipeline does on 7 of 12 boards;
  `pipeline_key_factory` swaps the legality tier for a real engine's answer.
- `relay` — the default at `standard` and above, and the mode above.

## Against the shipped autorouter, on the three boards we actually build

Same board out of a named revision, pours stripped, the problem derived from
that same file so the placement matches by construction, three copper sets
through one checker with an empty-solution control subtracted (which was zero
on all three).

`packages/router/scripts/ab_incumbent.py`, boards at `08fc6878f`,
relay/thorough:

| board | | routed | harness err | KiCad copper err | tscircuit routing err | vias | copper mm |
|---|---|---|---|---|---|---|---|
| hydrate-coaster | incumbent | **100.0%** | 0 | **7** | 0 | 108 | 1152 |
| | portfolio | 96.9% | 0 | 12 | 10 | 76 | 1011 |
| harness-puck | incumbent | 94.4% | 0 | **7** | 0 | 125 | 1625 |
| | portfolio | **97.2%** | 0 | 35 | 16 | 79 | 1447 |
| terminal-keyboard | incumbent | **98.9%** | 2 | **3** | 0 | 213 | 2839 |
| | portfolio | 94.4% | 0 | 27 | 24 | 138 | 2821 |

**The incumbent wins on all three, and the default does not move.**

The incumbent routes more on two of the three, and its findings are a different
kind: all 17
of its KiCad copper findings are `holes_co_located` — drilled holes on top of
each other. Ours are 35 clearance violations, 13 shorts, 12
copper-to-hole violations and 13 solder-mask bridges. A duplicate drill is a
fab query; a short is a scrapped board.

Three specific things this comparison surfaced:

- **Our harness scores all three boards at zero errors while KiCad finds 12, 35
  and 27.** The inscribed-stadium pad model is not somebody else's problem now.
  Until it is fixed, no harness-clean claim from this package means the board
  is clean.
- **We commit the exact defect the package was built to fix.** Six
  copper-to-hole findings on harness-puck against a 0.20mm rule: three distinct
  gaps of 0.00mm, 0.05mm and 0.10mm, each reported twice, naming `D14` and
  `U3`. `pathfinder-negotiated` alone produced four of the same kind on this
  board in the tournament, so it is not only a relay artifact. The cause is not
  located yet — the first guess, two vias drilled into each other, is wrong:
  the closest via pair on this board is further apart than drill + 0.20mm.
  KiCad does not give coordinates for these and our own harness reports zero,
  which is the second thing wrong here.
- **`tscircuit`'s own checks score the incumbent at zero and us at 10–24.** That
  is the weakest column in the table: those checks and the incumbent router
  come from the same codebase, so agreement between them is not independent
  evidence. KiCad is the column to read.

### Through the whole pipeline, our copper fails a board the incumbent passes

The A/B above scores copper in isolation. The flag lets the same question be
asked by the gate that actually ships boards. `matrix-ldo-3v3__usb-c-power` —
five nets, the simplest real composition in the matrix — built twice, same
source, same machine:

| | `fab.ready` | blocking findings |
|---|---|---|
| `CIRCUIT_ROUTER` off | **true** | 0 |
| `CIRCUIT_ROUTER=portfolio-force` | false | 5 × `pcb_trace_error` |

Both routed 100% of nets. All five findings are the same thing: our traces
overlap `pcb_keepout_0`, a 7.3 × 1.23mm rectangle. Measured against the true
rectangle, the worst offender sits 0.168mm inside it; measured the way
`routerlib` measures, it is clear.

That is the inscribed-stadium model again, and the keepout is where it does the
most damage: on a long thin rectangle the stadium cuts 0.255mm off each corner,
which is 2.8 times the clearance gate. `rect_capsule` is used for pads *and*
keepouts, so one fix covers both — and this is the shortest path from "our
router is interesting" to "our router does not break a board that was already
fab-ready".

## What is still worse than the incumbent, said plainly

1. **Fab-ready rate: unchanged.** 3 of 10 verified composition cells, the same
   as the best single family and the same as the oracle. Zero of the three
   example boards.
2. **Completeness on the boards we ship.** 94.4% against 98.9% on
   terminal-keyboard, 96.9% against 100% on hydrate-coaster.
3. **Real DRC errors: 2× to 9× the incumbent's**, and worse in kind.
4. **The shipping gate.** Forced on, the flag turns the simplest real
   composition cell from `fab.ready: true` into five blocking findings.
5. **Pours.** The pipeline stage refuses any board that already carries a
   `pcb_copper_pour`, because the pour was generated around the incumbent's
   traces and re-pouring after our route is not written. That is one of the
   three example boards.
6. **Diff pairs.** Coupling on harness-puck: 0.30 for us against 0.07 for the
   incumbent — better, but the pipeline's `diffpair.py` pass gets a stronger
   result than either, and the two stages have not been measured together.

## Wiring, and how to reproduce any number here

Off by default. The stage sits between `circuit_normalize` (0a) and the
differential-pair pass (0c) in `generation.py`, and lives in
`circuitpy/router_bridge.py`:

```bash
CIRCUIT_ROUTER=portfolio          # keep our copper only if it connects >= as many nets
CIRCUIT_ROUTER=portfolio-force    # always keep it — measurement only
CIRCUIT_ROUTER_MODE=relay         # single | best-of-n | relay
CIRCUIT_ROUTER_BUDGET=thorough    # cheap | standard | thorough
CIRCUIT_ROUTER_ALLOW_POURS=1      # route a board with a pour anyway
```

The stage never raises: a routing experiment that can fail a build that would
otherwise pass is a stage nobody will leave on. Every outcome — routed,
declined, refused, crashed — lands in the sidecar as `build.router` and as a
`router_applied` / `router_declined` info finding.

```bash
python3.12 packages/router/portfolio.py rules
python3.12 packages/router/portfolio.py select --instance terminal-keyboard
python3.12 packages/router/portfolio.py suite --mode relay --budget-class thorough \
    --runs 2 --out work/portfolio/relay-thorough
python3.12 packages/router/scripts/ab_incumbent.py --board hydrate-coaster --rev HEAD
python3.12 packages/router/scripts/collect_portfolio_results.py
```

Results: `packages/router/benchmarks/tournament/results-2026-08-16.json` (the
tournament, 208 cells) and `portfolio-2026-08-16.json` (the portfolio, all
three measurements).

## What to build next, in the order the measurements argue for

1. **Fix `rect_capsule`.** It models pads *and* keepouts, and both are wrong in
   the same direction: 0.21mm of missed overlap on a 1.0mm square pad, 0.255mm
   on the corners of the keepout that broke the end-to-end build. It is also
   what the routers design to, so fixing it makes them route better rather than
   only score honestly. Every ranking in this document was measured with it, and
   the copper is already on disk — re-scoring all 208 tournament cells against a
   true rotated rectangle costs minutes and no router has to run again. Nothing
   else here is trustworthy until this is done.
2. **Find the copper-to-hole violations.** Twelve findings across the three
   boards, at gaps down to 0.00mm on a 0.20mm rule, and our harness reports
   zero of them. Start by making KiCad's report carry coordinates so the
   geometry can be looked at rather than guessed at — the obvious guess was
   already wrong once.
3. **Relay from several leads, keep the best.** The one instance where the relay
   loses to the oracle loses because the lead's copper blocked the family that
   would have finished the board. Four leads × the same followers is under 20
   minutes of compute for all 16 instances, against a two-week fab round trip.
4. **Re-pour after routing**, so the pipeline stage stops refusing a third of
   our own example boards.
5. **A legality-aware residual stage.** The relay's extra nets carry a third
   more violations per millimetre than the nets routed first. A follower that
   is allowed to rip up the lead's copper locally — rather than only routing
   around it — is the standard answer and none of the nine families does it
   across a stage boundary.

## What the negative results taught

The ground-plane experiment is the model here: adding a plane with 73 vias
changed the shipped router's trace count by zero connections, and that single
non-result explained more than three theories about why it was failing. This
round produced four of the same kind, and they are worth as much as the relay:

- **Feature-based selection loses to a constant** under honest
  cross-validation. Sixteen instances is not a dataset; it is nine anecdotes
  and seven ties.
- **Merging independent routes adds copper and no connectivity.** The nets a
  second family solves in isolation are not the nets it can solve once the
  first family's copper is down.
- **A clean harness score is not a clean board**, and now it is our own router
  scoring 0 where KiCad scores 35.
- **A crashed check reads exactly like a clean one.** While this was being
  assembled, a KiCad conversion failed on one cell and left
  `copperFindingCount: 0` beside an error. It scored byte-identical copper at
  zero findings that another run scored at seven, and it only surfaced because
  two rows that had to agree did not. The collector now reads an errored row as
  *no measurement*.
