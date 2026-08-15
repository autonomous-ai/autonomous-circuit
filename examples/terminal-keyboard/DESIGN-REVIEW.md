# terminal-keyboard — design review

Seven lenses, scored independently, per `skills/design-review/SKILL.md`.

Evidence read each round: `boards/main.board.json`, `boards/main_review/_pcb.png`,
`boards/main_review/_schematic.png`, `boards/main_fab/{bom.csv, cpl.csv}`,
`product.json`, `boards/main.tsx`, and this project's `blocks/` snapshot.

Note on rounds: the panel only convenes on a board that already passes the
gauntlet, so the twelve builds circuitcode spent getting from
`COMPILE_ERROR` → 336 blocking errors → 1 are **not** panel rounds; they are
its own repair loop, and they are recorded in README → *How the router actually
behaved*. Round 1 below is the first time the panel scored anything.

**Stale geometry below, kept as the record of what was scored.** Round 1 read a
112 × 90 mm board. On 2026-08-16 the board became **100 × 90 mm** so it fits
JLCPCB's $2 sample tier — the diodes tucked 2.0 mm right, the status LED moved
2.2 mm east, and all six M2.5 holes left the side rails for the top and bottom
rails at x = −47 / 0 / +47, y = ±42. That answers the *cost* note on the board
outline and rewrites the *product-fit* note on H1–H6; it does not touch the
`sw-tact` pairing risk, the test-point gap or the routing note. See
`boards/main.tsx` and README → *What the printed body has to change*.

---

## Round 1 — board 112 × 90 mm, 1 blocking error

```design-review
{
  "board": "boards/main.tsx",
  "round": 1,
  "verdict": "iterate",
  "ready_to_make": false,
  "lenses": [
    {"lens": "power", "score": 8, "notes": [
      {"severity": "should-fix", "target": "net.GND",
       "detail": "there is no copper pour anywhere on this board — 0 pcb zones in main.circuit.json. GND is a routed web of 0.2mm traces serving 134 parts across 112x90mm, so every one of the 15 matrix nets returns through whatever trace the router happened to draw. At 150mA total the DC drop is irrelevant; the return path and the radiated loop area are not.",
       "fix": "add a GND pour on the bottom layer once tscircuit exposes one to board source"},
      {"severity": "consider", "target": "U2",
       "detail": "AMS1117 drops (5.0-3.3)*0.15A = 0.26W into a bare 2.34x3.6mm SOT-223 tab with no pour. Inside the block's <=500mA budget with room to spare, and the load here is ~150mA, so no action — recorded so the number is on the page rather than assumed."}
    ]},
    {"lens": "manufacturability", "score": 6, "notes": [
      {"severity": "must-fix", "target": "net.V5 at J1",
       "detail": "one blocking DRC error remains: a V5 track passes 0.1196mm from one of J1's four plated legs against a 0.2mm hole-clearance rule. fab.ready is false and ORDER.md is not written while it stands.",
       "fix": "give V5 a path out of J1 that does not re-enter the connector's drill field"},
      {"severity": "should-fix", "target": "D1-D50",
       "detail": "50 SOD-123 diodes all in the same orientation. SOD-123 is in the known JLC rotation-mismatch class; if the CPL rotation is wrong it is wrong for all fifty at once and every board is scrap. cpl.csv gives every diode Rotation 0.",
       "fix": "check the diode orientation on JLC's placement-preview screen before paying, not after"},
      {"severity": "should-fix", "target": "boards/main_fab/bom.csv",
       "detail": "every row has an empty Comment and Footprint column - only Designator and LCSC Part # are populated. JLC accepts it, but a human reviewing a 134-line BOM cannot tell a 15pF from a 10uF.",
       "fix": "exporter-side; nothing the board source can do"},
      {"severity": "consider", "target": "SW10-SW59",
       "detail": "50 copies of one Basic switch and 50 of one Basic diode is about as assembly-friendly as a 134-part board gets: 17 unique part numbers, 455 joints, 5 extended lines."}
    ]},
    {"lens": "layout", "score": 6, "notes": [
      {"severity": "must-fix", "target": "U3",
       "detail": "the QFN-56 escape cannot be done cleanly at the mandated 0.2mm trace width: 0.4mm pitch with 0.2mm pads leaves a 0.2mm channel, and a 0.2mm trace needs 0.4mm to pass between two pads. At default router effort this produced parallel traces 0.017-0.09mm apart and outright DP/DM shorts; 5x effort clears them, but the geometry has not changed and the margin is luck, not design.",
       "fix": "hand-route the QFN escape with pcbRouteHints, or move to 0.127mm trace/space (JLC's real floor), or 4 layers"},
      {"severity": "should-fix", "target": "board",
       "detail": "the rows and columns are routed as point-to-point stars fanning diagonally into the MCU, not as straight bussed lines. Visible on _pcb.png. Electrically correct, and after the 5x-effort pass the runs are short, but no human would draw it this way and it makes a 50-key board hard to read during rework.",
       "fix": "pcbRouteHints on the 15 matrix nets"},
      {"severity": "consider", "target": "the D+/D- pair",
       "detail": "not length-matched and not impedance-controlled. Full-speed USB is forgiving and the run is now ~22mm on a clear diagonal, but this is unverified, not verified."},
      {"severity": "consider", "target": "SW10-SW59 grid",
       "detail": "the grid itself is exact: 50 switches and 50 diodes on a 10.00mm pitch, every legend in place, courtyards clear. Zero errors anywhere in the key field. The whole failure surface is the 30x20mm MCU patch."}
    ]},
    {"lens": "testability", "score": 5, "notes": [
      {"severity": "must-fix", "target": "board",
       "detail": "no test points and no SWD access. Two mechanisms were implemented and both broke the fab packet: <testpoint> lands in bom.csv with an empty LCSC column even with doNotPlace set (8x part_not_orderable, error), and <via connectsTo=...> is net-tagged but never routed to (8 dangling vias, 7x unconnected_items, error). SWCLK/SWD exist only on QFN pins 24 and 25, so a debugger cannot be attached at all.",
       "fix": "an orderable through-hole header for V3_3/GND/SWCLK/SWD - which needs a pinned LCSC part this project does not have"},
      {"severity": "consider", "target": "SW10-SW59",
       "detail": "the mitigation is real but partial: every ROW is exposed on pads 3/4 of 50 tactile switches and every COL on a diode anode, so any single key can be fired with tweezers and any matrix net probed on 0.75mm copper. U2's SOT-223 tab is a 2.3x3.6mm V3_3 pad, C2/C3 are the rail caps, the USB shell tabs are ground, and LED1 answers is-the-rail-up without a meter. What is missing is firmware debug, not electrical access."}
    ]},
    {"lens": "cost", "score": 8, "notes": [
      {"severity": "consider", "target": "R3/R4 (C25100, 27ohm)",
       "detail": "a sub-cent extended resistor carrying a ~$3 feeder fee, flagged in usb-c-data's own BLOCK.md. One of 5 extended lines that together are $15 of a ~$30 non-parts cost. A Basic 27ohm 0402 substitute pays for itself immediately and is a same-footprint swap.",
       "fix": "parts-book --lookup for a Basic 27ohm 0402"},
      {"severity": "consider", "target": "board outline",
       "detail": "112x90mm is outside JLCPCB's flat $2 prototype tier (100x100mm max is the panel limit, but the $2 price band does not cover this area), so the modelled $2.00 PCB line under-reports. Budget $15-25 bare, ~$75-85 for five assembled - which is the fab profile's own $75-110 band."},
      {"severity": "consider", "target": "Y1 (C20625731)",
       "detail": "15.7k stock is the thinnest line in the BOM and it is an extended part. Irrelevant at qty 5; name an alternate before a hundreds run."}
    ]},
    {"lens": "safety", "score": 9, "notes": [
      {"severity": "consider", "target": "SW10-SW59",
       "detail": "sw-tact's BLOCK.md states its own pad pairing (1+2 / 3+4) is not hardware-verified. If the real TS-1187A pairing is 1+3 / 2+4, the block's tie traces short each key's node net to its row net permanently - 50 stuck keys, not a fire, but 100% scrap. This is the single largest risk on a board that is 50 copies of one switch. Not a safety-envelope breach: no mains, no battery, no radio, USB 5V only, ESD array on D+/D-, 5.1k CC pulldowns present.",
       "fix": "verify the pairing on the first article before ordering five"}
    ]},
    {"lens": "product-fit", "score": 7, "notes": [
      {"severity": "should-fix", "target": "board outline",
       "detail": "the TS-1187A land pattern is 7.9mm across the pads, which sets a 10mm pitch floor, which makes the key field 100mm wide, which makes the board 112mm wide. That is a landscape two-thumb handheld, not the portrait BlackBerry shape the Terminal is described as. The form factor is being chosen by the switch, which is backwards.",
       "fix": "if the Terminal is portrait, a smaller tactile switch is a new golden block and a sourcing pass - not a layout change"},
      {"severity": "consider", "target": "J1",
       "detail": "USB-C is on the bottom edge with 0.826mm of edge gap (from main_fab/enclosure.json), so the cable runs down and away from the thumbs and out from under the screen. Correct edge for this product."},
      {"severity": "consider", "target": "H1-H6",
       "detail": "six M2.5 holes at x=+/-52.5, y=+41/0/-41. The mid-span pair is what stops a 112mm board flexing under thumbs; the enclosure.json lists all six for the printed body."}
    ]}
  ],
  "must_fix_count": 3,
  "bring_up": "plug USB-C in, expect LED1 lit; hold BOOTSEL (SW2) and tap RESET (SW3) to enumerate as RP2040 mass storage, drag a UF2, then press ESC (SW10) and watch the host receive a keystroke.",
  "blocking_warnings": 1
}
```

**Summary.** The 50-key matrix is the strong half of this board and the panel
found nothing wrong with it — exact grid, clear courtyards, zero errors in the
key field. Everything that is wrong lives in a 30 × 20 mm patch around the
RP2040: one remaining DRC error on V5 at the USB connector's drill field, a
QFN escape that only clears at high router effort rather than by design, and no
test points at all because both ways of adding them break the fab packet.
Verdict is **iterate**, not ready to make.

**Not checked, and it should be said plainly:** thermal behaviour, EMI, USB
signal integrity, keycap mechanics, real thumb ergonomics at 10 mm pitch, and
whether the TS-1187A's internal pad pairing is what the block assumes. None of
those are inside any deterministic tool here; the scores above treat them as
judgement, not measurement.

---

## Round 2 — routing the one must-fix that was actionable

Of the three `must-fix` notes, exactly one could be attacked from the board
source: **net.V5 at J1**. The other two are toolchain-bounded (the QFN escape
needs a trace width the brief fixes at 0.2 mm; test points need a part the
project does not have).

**The change.** V5 forked out of J1 in both directions — right to the VBUS bulk
C1 at the block's `pcbX={8}`, left to the LDO at x = −40 — so it crossed the
connector's four plated legs twice. C1 was moved to `pcbX={-8}` inside this
project's `usb-c-data` snapshot so the whole net runs one way,
J1 → C1 → U2, and never re-enters the drill field. Placement only, no net or
part changed.

**The measured result: 1 blocking error → 26.** Not a regression in the fixed
net — a different board. The router re-solved everything and produced seven new
`accidental contact` trace overlaps around U3/U4, five `tracks_crossing` on
matrix nets, and two phantom `Track [<no net>]` segments (one 21.2 mm long,
sitting 0.000 mm from a drill and shorting into `U4.IO2`). The change was
**reverted**; the shipped board is the 1-error version.

**Re-scored:** manufacturability and layout only — the two lenses whose inputs
changed. On the reverted board both are unchanged at 6 and 6, because the board
is byte-for-byte the round-1 board.

**Verdict: iterate, and stop.** This is the point the skill's cap rule is for.
Two of three must-fixes cannot be closed inside the constraints, and the third
demonstrably cannot be closed by placement: at 2 layers and 0.2 mm the router's
output is chaotic with respect to small moves, so "fix the error" and "keep the
other 25 fixed" are not the same request. The options for whoever picks this up:

1. **Four layers.** Measured at 6 blocking errors instead of 46 at the same
   effort level. Blocked today by `tscircuit-cli export` throwing
   `Inner layer … only supports copper gerber`, which kills bom.csv and cpl.csv.
   Fix the exporter and this is the answer.
2. **0.127 mm trace/space**, which is JLCPCB's actual 2-layer floor and what
   `circuitlib.tables` already records — rather than the 0.2 mm this board was
   specified at. A 0.4 mm-pitch QFN-56 escape is a normal thing to do at
   0.127 mm and an impossible thing to do at 0.2 mm.
3. **Hand-route the escape.** `pcbRouteHints` on the QFN fan-out, the D± pair
   and V5 at J1, and leave the router the 50-key matrix — the part it closed
   without complaint on the first attempt.

Nobody should pay a fab for this board until option 1, 2 or 3 lands, and until
`sw-tact`'s pad pairing is confirmed on a first article.

---

## Final rounds — fab-ready

What follows is the repair loop that took the board from **1 blocking error to
0**. The error classes, in order of removal, each with the exact measured row
that cleared it. Every build below is a full board through the real pipeline at
`autorouterEffortLevel="5x"` (≈11–19 min each); the router is global, so each
change re-solves the whole board and the defect count re-measures from zero.

### Round 3 — decoupling row out of the QSPI corridor

The top decoupling row (C4–C8, C17) sat at block `pcbY={6}` (board y≈−19),
directly across U3's QSPI pins, which exit the QFN's **top** edge at y=−21.575.
The router couldn't escape `U3.QSPI_SD1` without violating pad/trace clearance.
Moved the row to block `pcbY={13}` (board y≈−12), opening a clean east-west
corridor above the chip. Placement only; netlist untouched. Cleared the three
`U3 QSPI_SD1` pad/via clearance errors (0.086–0.092 mm) from the baseline.

### Round 4 — SWD test points

Added a 3-pin SWCLK/SWD/GND testpoint header (`DebugPort`, 1.0 mm pads at
2.54 mm pitch) in open copper east of U4 at (38,−33), copied from the
harness-puck idiom. This satisfied `review_debug_unreachable`; the pads carry
no BOM rows and no drills.

### Round 5 — flash rotation: QSPI out of the bottom row

`W25q128` shipped at rotation 0, which puts **CS / DO / WP on U4's bottom row**
(board y=−28.53) while U3's QSPI pins sit on its **top** edge (y=−21.575). The
CS net had to drop 7 mm past U4 and wrap through the GPIO/COL8 zone to reach
R13–SW2 (BOOTSEL), and the router parked a via 0.021 mm from the COL8 trunk
(`pcb_via_248`) and another −0.243 mm inside U4's CS pad region
(`pcb_via_238`, `dfm_hole_clearance`).

Rotating U4 to **270°** puts CS/DO/WP on the **left face** facing U3
(`U4.CS` at (18.47,−23.1) vs (20.1,−28.53)), so the bus escapes sideways
instead of through the bottom row. One change, and the board dropped from
**8 errors → 1**: every via/trace collision above cleared, plus the two U4
clearance flukes (0.058, 0.021) and the U3 0.085.

### Round 6 — the crystal cluster's XIN via (the last one)

The single remaining error was `drc_violation [clearance]` on **R11**
(crystal XOUT series path), 0.0850 mm vs the 0.09 mm floor. KiCad DRC located
it exactly: the XIN via `(0.015,−34.35)` — the crystal's **top-pad gap** —
sits 0.085 mm from `Y1.pin3` (XOUT). The router reliably re-drops that via on
every pass; the violation is stable across board and micro-builds.

A keepout over the package (r=2.4) was tried and **reverted** (16 errors: it
blocks the crystal's own GND pad traces — `pcb_trace_error … overlaps with
pcb_keepout`). The fix that held: drop the whole crystal row (**Y1, C15, C16,
R11**) 0.7 mm, from block `pcbY={-10.5}` to `pcbY={-11.2}`. The XIN straight
line stays 8.6 mm — under the crystal ceiling — and the router now exits XIN
below the part instead of threading the top-pad gap. Cleared the 0.085
violation.

### The measured result

| Build | Error-severity warnings |
|---|---|
| round-1 (pre-fix, committed) | 7 |
| after round 3 + 4 edits | 8 (different list — router re-solved) |
| after round 5 (U4 rot270) | **1** |
| after round 6 (crystal row) | **0** |

`fab.ready: true` — 0 error-severity warnings, 371 non-blocking findings
reviewed, gerbers from kicad-cli, `ORDER.md` present. BOM 137 lines / 134
orderable. The board is now orderable by the tool's own bar.

