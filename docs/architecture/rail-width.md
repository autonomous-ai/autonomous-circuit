# Rail width: the router can be told, and told naively it wrecks the board

EE review 2026-08-15, finding 4 — *"the main power nets, 5V and 3V3, are
currently the same width as a signal, 0.2mm"*. Ledger #31 has been open since,
because `circuitpy.powerwidth` (stage 0d) widens copper the autorouter already
laid and **the narrowest point never moves**: `dfm_power_trace_width` reports
the minimum, so a rail that runs 0.5mm for 60% of its length and 0.15mm past one
obstacle still fires.

This is what we measured on 2026-08-16 about why it does not move, what happens
when the width is handed to the router instead, and what the geometry says can
never happen at all. **The source change was built, graded and reverted**; the
numbers are the deliverable.

---

## 1. The router does take a per-net width. We had never given it one

Read in the shipped bundle
(`toolchain/node_modules/tscircuit/dist/webworker.min.js`, tscircuit 0.0.2279):

- `<trace thickness="…">` writes `source_trace.min_trace_thickness`
  (`min_trace_thickness:this._getExplicitTraceThickness()`).
- `getSimpleRouteJsonFromCircuitJson` gives each net-shaped connection
  `nominalTraceWidth = max(min_trace_thickness)` over every source trace joined
  to that net (`nominalTraceWidthFromConnectedTraces`, offset 8,565,832). **One
  declaration anywhere on a net sets the whole net**, and the `max` means a
  partial marking is identical to a full one.
- The router's `TraceWidthSolver` reads it per connection
  (`connectionNominalTraceWidthMap`), builds
  `TRACE_WIDTH_SCHEDULE = [nominal, (nominal + min) / 2]`, walks the route with a
  0.1mm cursor and steps down when
  `clearance < targetWidth / 2 + obstacleMargin`, and tapers into a terminal pad
  narrower than the trace (`getTerminalPadWidthLimit`, `getTaperWidthAtDistance`).

`<net nominalTraceWidth="…">` exists in `netProps` and is **dead** in this
version — the `Net` component's `doInitialSourceRender` writes only
name/is_ground/is_power, and nothing anywhere reads
`_parsedProps.nominalTraceWidth`. The trace prop is the only live path.

**Proved in isolation** (`work/railwidth/spike/a.tsx`; one trace of net V5 carries
`thickness="0.5mm"`, nothing else changed):

```
$ toolchain/node_modules/.bin/tscircuit-cli build a.tsx --disable-parts-engine
net V5   routed widths [0.5]
net SIG  routed widths [0.15]
```

**Proved to route *around*** (`work/railwidth/spike/d.tsx`; a wall of NPTH holes
with one 0.46mm channel): the 0.5mm net holds 0.5mm up to the wall, tapers
0.5 → 0.375 → 0.25 through the gap and opens back to 0.5mm on the far side. No
clearance is broken. The width is a constraint the search obeys and narrows
locally, not copper stamped on afterwards.

**Proved on a 7-block composed board** (`work/railwidth/tsxcheck`, ldo-3v3 +
usb-c-power + status-led + i2c-bus + sw-tact + DebugPort + MountingHole):
GND/V3_3/V5 came out at 0.5mm with local tapers to 0.325mm, SDA/SCL/BTN/SWCLK/SWD
untouched at 0.15mm, zero errors.

## 2. Handed to every rail at once, it wrecks harness-puck

`work/railwidth/ab.py` builds one example twice through the whole pipeline —
as committed, and with every golden-block rail trace declaring 0.5mm (118 traces
across 10 files, `work/railwidth/declare-rail-width.patch`). Parts engine off on
both sides so the two are comparable (ledger #36).

```
$ /Users/d/miniconda/bin/python3.12 work/railwidth/ab.py harness-puck both
$ /Users/d/miniconda/bin/python3.12 work/railwidth/compare.py harness-puck
=== harness-puck
  base  ready=True  blocking=0   {'traces': 158, 'vias': 122}
        rail minima: V3_3 0.150, V3_3_LED 0.150, V5 0.150
  wide  ready=False blocking=33  {'traces': 158, 'vias': 125}
        rail minima: V3_3 0.150, V3_3_LED 0.325, V5 0.500
```

| | base | all rails at 0.5mm |
|---|---|---|
| `fab.ready` | **true** | **false** |
| blocking findings | **0** | **33** |
| V5 narrowest | 0.150 mm | **0.500 mm** — `dfm_power_trace_width` gone |
| V3_3_LED narrowest | 0.150 mm | 0.325 mm |
| V3_3 narrowest | 0.150 mm | 0.150 mm |
| build wall clock | 1719 s | 4530 s (escalated to a second compile) |

The 33 include two real shorts — *Items shorting two nets (V3_3 and SWCLK)* and
*XOUT/R11 against Y1/XIN* — plus nine `clearance` violations at 0.015–0.084 mm
against 0.090 mm, three `hole_clearance` at 0.066–0.190 mm against 0.200 mm, and
an accidental via contact. **Every one of them is inside or beside U3's fanout.**

That is a scrap board, so under this repo's own rule it is a failed experiment
and it was reverted, not traded. What it bought is real and worth naming: **V5's
narrowest point moved 0.150 → 0.500 mm and its `dfm_power_trace_width` warning
disappeared — the first time finding 4 has been closed for any rail on any
board.**

terminal-keyboard's pair of builds was killed by `SIGTERM` at 3518 s and 3101 s
(both in `highDensityStitchSolver`/`portPointPathingSolver`, both making
progress; the machine was at load 76–126 from other work). No A/B for it.
hydrate-coaster was never started. Neither is evidence either way.

## 3. What the placement forbids outright, with the arithmetic

A rail is only as wide as its worst point, and its worst point is where it
reaches a pad. On a QFN with pitch `p` and pad width `w` across the pitch, a
track centred on one pad has its neighbour's near edge `p − w/2` away, so

```
max half width = p − w/2 − clearance
max track      = 2 (p − w/2 − clearance)
             = 2 (0.400 − 0.100 − 0.100) = 0.400 mm     RP2040 QFN-56
pitch needed for 0.500 mm = 0.250 + 0.100 + 0.100 = 0.450 mm
```

The RP2040's QFN-56 is 0.400 mm pitch with 0.200 mm pads. It is short of what a
0.5 mm rail needs by **0.050 mm**, and no router effort, keepout, reservation or
repair pass changes that.

Measured rather than argued — `work/railwidth/escape.py` fans 180 directions out
of every rail pad and reports the widest track that can leave it against the
**placement only** (pads, drills, keepouts, board edge; no traces, no vias, no
pour, because those are the router's output):

```
$ /Users/d/miniconda/bin/python3.12 work/railwidth/escape.py \
    examples/harness-puck/boards/main.circuit.json
  V3_3: 25 pads, 8 cannot escape at 0.5 mm
    U3.IOVDD2   pcb_smtpad_89  pad 0.85 x 0.2 mm  escape 0.4000 mm, held by pcb_smtpad_88
    U3.IOVDD1   pcb_smtpad_96  pad 0.2 x 0.85 mm  escape 0.4000 mm, held by pcb_smtpad_97
    …
```

| board | rail | pads | cannot reach 0.5 mm | worst escape |
|---|---|---|---|---|
| harness-puck | V3_3 | 25 | **8**, all U3 | 0.4000 mm |
| harness-puck | V3_3_LED | 21 | 0 | 1.3000 mm |
| harness-puck | V5 | 6 | 0 | 1.1000 mm |
| terminal-keyboard | V3_3 | 25 | **8**, all U3 | 0.4000 mm |
| terminal-keyboard | V5 | 4 | 0 | 1.1000 mm |
| hydrate-coaster | V3_3 | 25 | **8**, all U3 | 0.4000 mm |
| hydrate-coaster | GND | 34 | **1**, U3.TESTEN | 0.4000 mm |
| hydrate-coaster | V5 | 4 | 0 | 1.1000 mm |

Every constrained pad on all three boards is an RP2040 pin and every one of them
measures **exactly 0.4000 mm**, the arithmetic above to four decimals. Nothing
else on any of these boards is placement-limited at all — V5's worst escape is
1.1 mm and V3_3_LED's is 1.3 mm, so those two rails are missing the floor purely
because nobody told the router.

**And the router is stricter than that ceiling: at the pad it uses the pad's own
width.** Measured on an isolated three-pad 0.4 mm-pitch escape
(`work/railwidth/spike/e.tsx`), a net declared at 0.5 mm comes out

```
wire top 0     0      w= 0.2      <- the pad itself
wire top 0     0.3    w= 0.2
wire top 0     0.6    w= 0.375
wire top 0.2   0.8    w= 0.45
wire top 0.673 0.764  w= 0.5
```

So on any RP2040 board `dfm_power_trace_width` will report **0.200 mm** for
V3_3 — which is the pad's width, not the rail's — for as long as it measures
every route point including the pad entry. The floor is not moved to meet that
and neither is where the check measures; both are the one rule this repo is
organised against.

## 4. Two artifacts inside our own widener's obstacle model

`work/railwidth/necks.py` walks each rail at 0.05 mm, asks stage 0d's own ruler
(`powerwidth._segment_limit` over `diffpair.collect_obstacles`) for the widest
legal copper at every point, and re-asks with each suspected artifact removed.
harness-puck V3_3:

| reading | widest legal | set by |
|---|---|---|
| as the pass sees it | **−0.7000 mm** | `pcb_via_38.drill` — *the rail's own via* |
| its own vias exempt | **−0.2000 mm** | `pcb_copper_pour_0` — the bottom pour |
| vias and pour exempt | **+0.1784 mm** | `pcb_via_106.pad` — another net's via |

1. **A rail is measured against the holes it is itself routed through.**
   `collect_obstacles` gives a via's *pad* the via's net, so same-net copper is
   exempt, and gives its *drill* no net at all — so every rail reads −0.70 mm at
   every one of its own vias. `pcb_plated_hole` drills already carry the same-net
   exemption; vias were missed. Ledger #24 one element further out.
2. **A pour blanks its whole layer.** The pour obstacle is built from
   `brep_shape.outer_ring` alone and the inner rings — the holes the pour solver
   carved around this very trace — are never read, so any point on the poured
   layer measures as inside the pour. On harness-puck and terminal-keyboard
   (bottom GND pour) that makes the entire bottom layer unwidenable by
   construction.

Neither is changed here. A repair pass's obstacle model is a ruler, and a ruler
gets changed on its own evidence rather than as a side effect of chasing a
number.

**One thing was fixed**, because it is reporting rather than rule: the sidecar's
`limiter` named the obstacle of the piece that *realised* the minimum width, not
the one that *caused* it. A route point takes the min of the two pieces meeting
at it, so the tight one is as often the neighbour. That is how harness-puck's
V3_3_LED and V5 both shipped `limiter: null` for rails that neck to 0.15 mm —
"nothing is holding this back", about copper something plainly was.
`powerwidth._widen_route` now carries `limiter_at` beside `allowed_at` and
reports the causing side; pinned by
`test_the_reported_limiter_is_the_obstacle_that_caused_the_neck`.

## 5. What the next attempt has to be

The measurements name their own next step, and it is not "declare it everywhere".

**Declare a rail's width only where every pad on that net can escape at it.** By
the table above that is V5 on all three boards and V3_3_LED on harness-puck —
exactly the rails that gained without touching U3 — and it excludes V3_3 and
hydrate's GND, which is where all 33 findings came from.

That cannot live in a block. The width is resolved **per net, as the `max` over
every trace joined to it**, so any partial marking of a shared net is the same as
marking all of it, and no block can see the whole consumer set of a rail it is
handed by name. The owner is the board or `circuitlib`'s planner, which can run
`escape.py`'s measurement over the placement it just produced and write the
number it computed. All three of our boards are hand-placed, so today that means
a per-board declaration and a per-board rebuild to grade it.

**Not measured, and it should be next:** V3_3 declared at its own measured
ceiling of 0.400 mm rather than 0.500 mm — the constraint the geometry permits
instead of the one the profile prefers. That is one build per board.
