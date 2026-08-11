# Pipeline v2 — build the board the way a board is built

**Status: proposed, 2026-08-11.** Written after a day spent failing to get three
boards to fab-ready, and it exists because the failure was structural rather
than a run of bad luck.

## What went wrong, in one paragraph

We spent the day tuning an autorouter. We changed its effort level, tried a
second router, and attempted to configure its clearance. None of it worked, and
each attempt produced a defect somewhere else. The reason is that **the
pipeline asks the router to make decisions an engineer makes earlier**:
placement, layer count, and which nets get a plane instead of a trace. By the
time the router runs, those choices are already fixed and usually wrong, so the
router's only options are bad ones — a via in a pad, a track 0.07mm from
another, or a short.

An electrical engineer would not be in this position, because their workflow
puts those decisions **before** routing and treats routing as the consequence.
Ours puts them nowhere and treats routing as the cause.

## The evidence, all measured today

| What we observed | What it means |
|---|---|
| Effort `5x` produced **fewer** blocking findings than `10x` on the same board | The effort dial is not monotonic. There is no setting that reliably improves a board. |
| `freerouting` fixed hydrate-coaster and **shorted two nets** on terminal-keyboard | Router choice is per-board at best. No router is a default. |
| `autorouter={{ traceClearance, allowViaInPad }}` left `autorouter_configuration: NONE` | We cannot configure the router's constraints at all. |
| hydrate-coaster builds **byte-identically with and without `GndPour`** | The pour runs *after* routing. The router never sees the plane. |
| 115 traces and 99 vias on a 2-layer board with two pours | GND is being routed as ordinary traces. On a 2-layer board that is the single largest source of congestion. |
| terminal-keyboard: 424 traces, 2 layers, never clean | A density an engineer would put on 4 layers. We are trying to solve in software what costs about $2 in fabrication. |
| The plan turn never invoked `circuit-analysis` — one `ls`, then the model wrote the plan itself | Stages that exist on paper do not run. "Design with the skills' knowledge" reads as flavour. |
| A repair loop took a `fab.ready` board and made it un-ready over five rounds | Without a ratchet, iteration walks downhill. |

**The through-line: every one of these is a decision made too late, or not at
all.**

## The shape of v2

The order below is not ours. It is how a board is designed, and each stage
exists because the next one cannot be done without it.

```
ask → brief → schematic → floorplan → stackup → route → pour → verify → fab
                             ▲            │
                             └── density says 2 layers is not enough
```

### 1. Brief — what it must do

Unchanged in intent, fixed in practice: **the stage must actually run.** Today's
plan turn skipped it. A stage that is optional is a stage that does not exist.

**Gate:** capabilities all map to released blocks, or are named as gaps. No
option is offered that the catalog cannot deliver.

### 2. Schematic — what connects to what

Blocks and nets. **No geometry.** Today we write TSX that mixes the circuit and
its placement in one file, so a placement change is a source edit and the
netlist is never separately verifiable.

**Gate:** every block's required nets are provided; power budget closes; safety
envelope holds. This is the stage where a wrong value is caught by a human
once, not by a check that cannot see it.

### 3. Floorplan — where things go

**The new stage, and the most important one.** The rule an engineer works to is
*if routing is hard, the placement is wrong*. We currently place blocks with a
measured-box helper and then hope.

Floorplan owns: board outline, connector positions on the edge, mounting holes,
keepouts, and each block's location and rotation. It reasons about
**congestion** — pins per square millimetre, escape paths out of fine-pitch
parts, whether the decoupling actually sits beside the pin it serves.

**Gate:** a congestion estimate under threshold, every connector reachable from
outside, every fine-pitch part with a clear escape corridor. **Routing does not
start until this passes.** A board that fails here gets re-placed, which is
cheap, instead of routed and repaired, which is not.

### 4. Stackup — how many layers, and which one is ground

Decided from floorplan density, not assumed. Two layers with a bottom ground
plane is the default; four layers when density demands it.

**Gate:** the chosen stackup can carry the estimated routing. If it cannot, say
so here — where the answer is "$2 more" — rather than after twelve failed
routing attempts.

### 5. Route — in classes, with the plane already there

Three passes, not one:

1. **Power and ground** — GND connects by via to the plane. It is not routed.
   This alone removes most of what congests a 2-layer board.
2. **Critical** — differential pairs, crystal, anything with a length or
   symmetry constraint. Deterministic, from block-declared rules.
3. **The rest** — the autorouter, doing the job it is actually good at:
   leftover point-to-point signal connections on a board that has already been
   made easy for it.

**Gate:** DRC clean, or a named list of what is not.

### 6. Repair — a ratchet, never a rewrite

Already landed in the driver: a round may leave the board no worse than it
found it, and the moment one turns orderable into not-orderable the loop stops.
The same rule belongs in the skill.

**Repair may not move a part.** If the fix requires re-placement, it fails back
to floorplan. Repair that edits geometry the floorplan chose is how a local fix
becomes a new defect three millimetres away — which is exactly the whack-a-mole
we watched all day.

### 7. Verify and fab — unchanged

Two substrates, gerber truth, the seven-lens panel, the fab packet. This half
works; it is the half that has been telling us the truth all day.

## What changes for the agents

Today they are organised by **artifact** — one writes the brief, one writes the
source, one picks parts, one reviews. v2 organises them by **stage of the
physical design**, because that is where the decisions actually live and it is
what makes a gate meaningful.

| Agent | Owns | Gate it must pass |
|---|---|---|
| **Brief** | capabilities, budget, gaps | every capability maps to a released block |
| **Schematic** | blocks, nets, values | netlist complete, power closes, envelope holds |
| **Floorplan** | outline, placement, rotation, keepouts | congestion under threshold, connectors reachable |
| **Stackup** | layer count, plane assignment | the stackup can carry the routing |
| **Route** | the three passes | DRC clean or a named list |
| **Verify** | the gauntlet | unchanged |
| **Panel** | seven lenses | unchanged |

Three properties this buys that v1 does not have:

- **A failure names its own stage.** "This board cannot be routed" becomes "the
  floorplan is too congested for two layers" — actionable, and actionable
  *early*.
- **No stage can be skipped silently.** Each has an artifact on disk and a
  gate. The plan turn could skip `circuit-analysis` precisely because there was
  nothing to check.
- **Iteration happens at the right level.** A routing failure re-places rather
  than re-routes with a different dial.

## What we are deliberately not doing

- **Not writing our own autorouter.** The problem was never the router's
  quality; it was that we handed it an impossible board.
- **Not banning the autorouter.** It is good at leftover signal routing. It is
  bad at deciding a stackup.
- **Not tuning effort or switching routers as a strategy.** Both are measured
  non-monotonic. They stay available per board, recorded beside the score.

## How to land it without stopping

v1 works end to end and produces boards that are close. v2 is an insertion, not
a rewrite:

1. **Let the router see the plane.** Pour before route, or declare the plane so
   GND connects by via. Highest value, smallest change, and it is the one that
   makes 2-layer boards tractable.
2. **Add the floorplan gate** in front of routing, using the congestion measure
   the composition matrix already implies.
3. **Route in classes**, power and ground first.
4. **Split the stages into agents** once the stages exist. Not before — an
   agent per stage is only useful when the stage has a gate.

Step 1 is worth more than everything tried today. It is also a few hours of
work rather than a redesign, which is the point of ordering it first.
