# terminal-keyboard — the thumb keyboard for the Autonomous Terminal

The Terminal is a BlackBerry-shaped handheld that is a full mobile Linux
computer: you run Claude Code, Codex and any other agent CLI on it, from a
physical keyboard, with the screen above and nothing else competing for your
attention. This board is the part under your thumbs.

It is a 5-row × 10-column diode-isolated key matrix on an RP2040. The RP2040
speaks USB natively, so the board enumerates as a plain HID keyboard over
USB-C — no bridge chip, no driver, and the same firmware path (BOOTSEL → drag
a UF2) every RP2040 board uses.

```
boards/main.tsx          the board program — the only hand-written file
boards/main.circuit.json the compiled IR of record
boards/main.board.json   the sidecar (warnings, BOM summary, fab state)
boards/main_review/      _schematic.png, _pcb.png
boards/main_fab/         gerbers.zip, bom.csv, cpl.csv, board.glb
DESIGN-REVIEW.md         the seven-lens panel, round by round
```

## The key map, and how it becomes a matrix

The legend comes off the real prototype, five rows, left to right:

| row | keys |
|---|---|
| 1 | ESC/~ · FN/+ · CTRL/@ · F1/# · F2/$ · F3/% · F4/( · CTRL · FN/- · BKSP/DEL |
| 2 | Q/= · W/1 · E/2 · R/3 · T/? · Y/{ · U/} · I/[ · O/] · P/\| |
| 3 | A/- · S/4 · D/5 · F/6 · G/& · H/! · J/; · K · L/" · **ENTER** (tall) |
| 4 | TAB/* · Z/7 · X/8 · C/9 · V/, · B/. · N/< · M/> · PgUp/▲ |
| 5 | SHIFT · ALT/! · MIC/0 · **SPACE** (wide) · Home/◀ · PgDn/▼ · End/▶ |

That is 46 legends on a 5 × 10 grid of 50 positions. The board populates all
50 — every position gets its own switch and its own diode, on the same pitch —
and the four extra positions are absorbed by the oversized keycaps:

| position | what sits there |
|---|---|
| R3C9 **and** R4C9 | two switches under the one tall ENTER cap |
| R5C3, R5C4, R5C5 | three switches under the one wide SPACE cap |
| R5C9 | wired spare — in the matrix, no cap in the current body, free for the FN layer |

Putting two or three switches under one big keycap is deliberate, not padding.
A wide cap on a single central switch rocks and needs a stabiliser bar; a wide
cap resting on three switches actuates evenly from any corner with no extra
mechanical parts, and firmware simply maps all three to the same code. Row 4
has nine legends because its tenth position is ENTER's lower half — the map
closes exactly.

Silkscreen carries the legend above each key (`ESC`, `FN+`, `CTL@`, … `SPR`),
so the bare board is populatable and testable without the map in hand.

**Refdes:** switches are `SW10`–`SW59` and diodes `D1`–`D50`, both numbered
row-major, so `D1`/`SW10` is top-left, `D10`/`SW19` is top-right, `D50`/`SW59`
is bottom-right. `SW1`–`SW3` are reserved (the `sw-tact` default and the
RP2040 core's BOOTSEL/RESET), so nothing collides.

## Diode direction: COL2ROW

One 1N4148W (SOD-123, LCSC **C81598**, pinned in the board source the way every
golden block pins its parts — `supplierPartNumbers={{ jlcpcb: [...] }}`) sits in
series with every switch, all 50 facing the same way:

```
net.COL<c> ──▶|── net.K<r><c> ── SW ── net.ROW<r>
            D<n>
        anode   cathode
```

Anode on the column, cathode toward the row: **current can only flow column →
row**, which is what QMK calls `COL2ROW`. Firmware drives one ROW low at a time
and reads the ten COL inputs on their internal pull-ups, so a scan is five
steps. Without the diodes, three keys sharing a row and a column create a
fourth phantom press; with them, the return path through an unpressed key is
blocked and n-key rollover is real.

**Pin allocation** — 15 pins, not 50:

| RP2040 | use |
|---|---|
| GPIO0–GPIO4 | ROW0–ROW4, driven outputs |
| GPIO5–GPIO14 | COL0–COL9, inputs with internal pull-ups |
| GPIO15–GPIO29 | free |

No external pull-ups: the RP2040's internal ones are the pull-up story, exactly
as `sw-tact`'s BLOCK.md specifies.

## Pitch, size, and why

**Pitch: 10.0 mm, both axes.** That is the floor, not a preference. The switch
is a TS-1187A on the footprinter land pattern
`dfn4_p3.6998mm_w7mm_pw0.75mm` — 7.9 mm across the pads, 5.4 mm of courtyard
tall. At 10 mm the pad-to-pad channel between columns is 2.1 mm, which is what
the column trace runs down; below about 9.5 mm the channel closes and the
switch courtyards touch. Real thumb keyboards live at 9–12 mm, so 10 mm is
inside the band, at the tight end.

The diode sits 1.4 mm left and 4.4 mm below its switch. The 4.4 mm is measured,
not guessed: at 3.6 mm the SOD-123 courtyard (4.7 × 2.3 mm) overlapped the
switch courtyard (7.5 × 5.4 mm) by 0.25 mm and the build returned 100
`pcb_courtyard_overlap_error`s. The 1.4 mm is measured too, and it used to be
3.4 mm: at 3.4 mm the diode reached 2.0 mm past its own switch's left pad, so
column 0's five diodes hung 0.75 mm off a 100 mm board. Tucked to 1.4 mm the
diode's copper sits inside the switch's own 7.0 mm land, and the channel
between columns at the diode row opens from 2.1 mm split around an anode pad
to a clear 3.0 mm.

**Board: 100 × 90 mm, 2 layers, 1.6 mm.** Ten columns at 10 mm is 100 mm of key
field measured centre to centre of the outer columns' *keycaps*; the copper is
97 mm and the board wraps it in 1.5 mm of margin a side. The key field occupies
the top 48 mm and the electronics live in a 29 mm strip underneath. It is a big
board — a landscape two-thumb handheld, not a palm-sized one — and that size is
a direct consequence of the switch land pattern. A smaller Terminal needs a
smaller switch, which is a new golden block and a sourcing pass, not a layout
change.

It was 112 mm until 2026-08-16, and the 12 mm was a screw column, not circuit.
JLCPCB's $2 sample price stops at 100 × 100 mm; 112 × 90 quoted **$8.90 for
five bare boards**, 4.5× for margin (EE review 2026-08-15, finding 5). Measured
on the built board, the switch pads span 97.00 mm and all copper spans
99.00 mm — but the M2.5 drills spanned 107.70 mm and their keepouts 108.20 mm,
because the six screws sat outboard of the keycaps at x = ±52.5. The circuit
always fitted; the mounting did not.

The height started at 84 mm and grew to 90 mm for one reason, recorded because
it is the kind of trade that usually goes undocumented: the last blocking
errors were all crowding in the MCU escape, and 6 mm of extra strip was the
only lever left that added room without moving a key. It took the board from
**3 blocking errors to 1**. The spare margin lands at the top of the board,
under the screen bezel, where nothing else needs it.

**Mechanical:** six M2.5 clearance holes (2.7 mm) at x = −47 / 0 / +47,
y = ±42 — three along the top rail, three along the bottom rail. They used to
be six in the two *side* rails at x = ±52.5, and the shrink evicted them: a
2.7 mm drill carries a 1.6 mm keepout and the switch copper reaches
4.5 × pitch + 3.5, so a flank screw needs x ≥ 4.5 × pitch + 5.1 while the board
allows x ≤ 48.4. Those cross at a 9.62 mm pitch, with the keepout touching both
the copper and the edge. **Nothing above a 9.6 mm pitch has a flank screw on a
100 mm board**, so this is a body change, not a margin trim — see *What the
printed body has to change* below. The mid-span pair moved from the middle of
the side edges to the middle of the top and bottom edges, which is the right
swap now that 100 mm is the long span: a thumb press at the centre of the key
field is 42 mm from a screw instead of 63 mm. USB-C is on the **bottom edge**
at x = −23, so the cable runs down and away from the hands, out from under the
screen.

### What the printed body has to change

| | was | is |
|---|---|---|
| Board outline | 112 × 90 × 1.6 mm | **100 × 90 × 1.6 mm** |
| Screw positions | x = ±52.5, y = +41 / 0 / −41 | **x = −47 / 0 / +47, y = ±42** |
| Which rails carry screws | the two side rails | **the top and bottom rails** |
| LED pipe | x = −48 | **x = −45.8** |

Three consequences the body owner has to design for, stated as numbers because
none of them is checkable from the board file:

- **The side rails lose their bosses entirely.** There is nowhere to put one:
  copper reaches x = ±48.500 and courtyards ±48.750, leaving 1.25 mm of flank.
  The stiffness those two mid-span screws bought has to come from the body
  pressing on the bare underside instead — all 137 parts are top-side, so the
  whole bottom face is available for a rib. That is a first-principles claim,
  not an FEA result: nobody has simulated it.
- **A boss on the top or bottom rail has 1.65 mm of board outboard of its
  drill** (drill edge at |y| = 43.35, board edge at 45). It wants a flat washer
  face, not a countersink.
- **Top-row keycaps must not reach above y = +40.65**, where the top drills
  start. On a 10 mm pitch that is a cap no taller than **9.3 mm**. The cap
  dimension is not in this repo, so this is the one number the mechanical side
  has to confirm rather than read.

## Parts and cost

| | |
|---|---|
| Components | **134** (50 switches, 50 diodes, RP2040 core ×22, USB-C+ESD ×7, LDO ×3, LED ×2) |
| Unique part numbers | 17 (5 extended: RP2040, the crystal, the USB-C receptacle, the 27 Ω pair, the USBLC6) |
| Solder joints | 455 |
| Nets | 75 |

Cost at qty 5, assembled, from `circuitlib.helpers.estimate_cost()`:
**$30.37 excluding parts** ($2.00 PCB + $13.37 assembly + $15.00 in extended-part
feeder fees). Parts are roughly **$5.70 a board** at LCSC list
(RP2040 $0.99, flash $2.45, 50 switches $0.90, 50 diodes ≈ $0.50, crystal
$0.33, the rest under a dollar), so about **$59 for five boards, ≈ $12 each**.

The `$2.00` PCB line is JLCPCB's flat prototype price for boards up to
100 × 100 mm, and **as of 2026-08-16 this board earns it**. At 112 × 90 it did
not: five bare boards quoted **$8.90**, so the modelled line under-reported by
4.5× and the honest figures were $15–25 bare and $75–85 assembled. Shrinking to
100 × 90 puts the sample order back at **$2.00 for five bare boards** — the
whole point of the change, and worth more than it looks, because it is the
number a person sees first when they try to make one.

Still true: every number here is *modelled*, not quoted. Nobody has put this
board through a real JLC cart, and the $8.90 above is the one figure that came
from a real quote (2026-08-15).

## Building it yourself

```bash
python3.12 skills/circuitcode/scripts/circuit \
  /abs/path/examples/terminal-keyboard/boards/main.tsx --wall-clock-s 5400
```

Budget **~17 minutes** per build: the source sets
`autorouterEffortLevel="5x"`, which is what gets the error count down (see
below). Drop that one line and a build is ~4:45 with roughly 2.5× the
blocking errors — useful while you are still moving parts around.

## Bring-up

Plug USB-C in: LED1 lights, the board enumerates as `RP2040` mass storage when
BOOTSEL (SW2) is held during reset (SW3), and after a UF2 the host sees a
50-key HID keyboard — press ESC (SW10) first, then any key in the bottom row.

Finding one dead key: every matrix net is exposed on big hand-probe-able
copper. Shorting pads 3 and 4 of a switch to its own signal pads with tweezers
fires that key without the switch; if that works the switch is dead, and if it
does not, walk the column at the diode anode. There is no SWD header — see
below.

## Where it ended up

**46 → 0 blocking errors**, on two layers, with the fab packet intact
(gerbers, BOM and CPL all written from kicad-cli). The board is orderable and
`ORDER.md` is written.

## Honest limits

**This board is fab-ready as of 2026-08-16. `fab.ready` is `true` with zero
blocking findings**, and an independent KiCad run — the packet unzipped into an
empty directory and checked by `kicad-cli` 10.0.5, which knows nothing about
this pipeline — reports **0 error-severity violations and 0 unconnected items**.

| | |
|---|---|
| blocking (`error`) | **0** |
| `warning` | 427 — of which 377 are `drc_violation` and 369 of those are kicad-converter noise (`footprint_symbol_mismatch` ×139, `footprint_symbol_field_mismatch` ×137, `net_conflict` ×93) |
| `info` | 740 |
| `fab.ready` | **true** — `ORDER.md` is written |
| independent DRC | `kicad-cli pcb drc --severity-error` on the shipped `kicad-project.zip`: **0 violations, 0 unconnected** |
| gerber source | `kicad-cli` 10.0.5 — the shipping exporter, not the fallback |
| BOM | 20 grouped lines, **17 orderable** (JLC's own format, ledger #32) |
| route | 252 PCB traces, 213 vias, **zero** errors in the compiled circuit.json |

**The last blocking error was never on the board.** Until 2026-08-16 this
section said `dfm_hole_clearance`: one of U4's pads 0.130 mm from a via at
(10.12, −20.90), against a 0.20 mm rule. The pad is not there. U4 is a SOIC-8,
and its eight 2.25 × 0.63 mm pads carry `ccw_rotation: 90` — the copper is
2.25 mm *tall*. The clearance check dropped the rotation and swung every pad
back onto the x-axis, which reaches 0.81 mm sideways toward a via that is
0.506 mm away. Three things said so before any tool did: the pads sit on a
1.27 mm pitch, so eight pads 2.25 mm *wide* would overlap each other; the KiCad
packet writes each one with a trailing `90`; and KiCad's own hole-clearance
DRC, enabled at 0.2 mm on the same packet, reported nothing. The fix is in the
check (`_pad_copper`, ledger #41), not in this board — no copper moved, and the
route is the same one the 08-16 05:20 source has always produced.

**The shrink to 100 × 90 took this from 12 blocking to 1.** Measured on the
same pipeline and the same command: the 112 × 90 source rebuilt on 2026-08-16
returns **12** blocking — four `shorting_items`, four `solder_mask_bridge`, two
`clearance`, one `hole_clearance` and one `dfm_hole_clearance` — because the
route it lands is incomplete in the key field. Tucking the diodes under their
switches opens the inter-column channel from 2.1 mm to 3.0 mm, and the same
252 traces come back clean there. (The stale `fab.ready: true` you may find in
an older commit of `main.board.json` predates the EE-review pipeline changes;
it is not a number this source ever reaches today.)

The 50-key matrix routes clean — **zero errors anywhere in the key field** —
and every warning that still matters sits in a roughly 30 × 20 mm patch around
the MCU and the USB connector. Details, with numbers, below.

One more thing an EE will spot in the packet: `board.drl` carries **228 drill
hits against 225 holes and vias in the design**. The three extras are real and
ours — `kicad_normalize` bridges B.Cu dead-ends that stop under a top-only pad,
and it added three vias after `circuit.json` was written. Two of them drill
0.15 mm and 0.20 mm, under the `minViaHoleDiameter="0.3mm"` this board
declares. Legal at JLCPCB, not what the board asked for; recorded as ledger
#37.

### How the router actually behaved

The claim that tscircuit's default router "degrades past roughly 50 traces" is
not what happened here. This board has **327 source traces, 252 routed PCB
traces and 213 vias** (2026-08-16; it was 424/349/259 before `sw-tact` went
diagonal and dropped 100 redundant tie traces), and the router placed all of
them — the 50-key matrix, 500 switch-pad connections, 100 diode connections and
the fifteen matrix nets fanning into the QFN — with **zero unrouted nets and
zero errors in the compiled circuit.json**. Scale was not the problem.

Density was. Every error the board ever carried lived in the electronics strip
(the counts below are the history; the board is at zero now):

| what | why it cannot be fixed by moving parts |
|---|---|
| the RP2040 QFN-56 escape | 0.4 mm pitch, 0.2 mm pads → 0.2 mm between adjacent pads. The mandated `minTraceWidth="0.2mm"` needs 0.4 mm of channel to pass between two pads. Nothing can go between them, so the router doglegs and lays parallel traces at ~0.05–0.09 mm apart. |
| the USB D+/D− pair | leaves pins 46/47, two adjacent 0.4 mm-pitch pads; at default effort the router repeatedly shorted DP to DM in the first millimetre of the escape. |
| J1's own drill field | the USB-C receptacle is a hybrid SMD+TH part with four plated legs. V5 and GND have to leave through them, and a V5 track 0.12 mm from one of those drills was the last real error here — closed by the block's keepout (ledger #1). |
| the QSPI bus to U4 | six signals sharing the corridor with the 3V3/GND/DVDD web and the 15 matrix nets converging on the same chip. |

Four things were tried and measured. Blocking-error counts, same board:

| change | blocking errors | build |
|---|---|---|
| baseline, default effort, 2 layers | 39–46 | ~4:45 |
| clearance floor raised to 0.15 mm | **151** | ~4:50 |
| `layers={4}`, default effort | **6** | 6:12 |
| `autorouterEffortLevel="5x"`, 2 layers | **18** | 16:52 |
| + crystal cluster given room (a block fix) | **3** | 16:40 |
| + board 84 → 90 mm, strip moved down 3 mm | **1** | 17:10 |
| + one more placement fix on the last error | **26** | 17:00 |
| 2026-08-16 pipeline, unchanged 112 × 90 source | **12** | ~18:00 |
| + board 112 → 100 mm, diodes tucked 3.4 → 1.4 mm | **1** | ~18:00 |
| + the clearance check reads a rotated pad (no copper moved) | **0** | 15:05 |

The seventh row is the one that taught the most: fixing the final error by
moving the VBUS bulk cap re-solved the whole board and produced 26 different
errors, including two phantom `Track [<no net>]` segments, one 21.2 mm long and
sitting 0.000 mm from a drill. It was reverted. At 2 layers and 0.2 mm, "fix
this error" and "keep the other 25 fixed" are not the same request.

The last two rows are the 2026-08-16 shrink, measured the same way. The
unchanged 112 × 90 source no longer reaches 1 on the current pipeline — it
returns 12, mostly shorts in the key field — and the narrower board with the
diodes tucked returns to 1. That is the opposite of the intuition that a
smaller board is a harder route: the tuck moved 50 parts *out* of the
inter-column channels, and the channel is where the column traces run.

1. **Raising the clearance floor made it worse.** Setting
   `minTraceToPadEdgeClearance` / `minViaEdgeToPadEdgeClearance` to 0.15 mm did
   not make the router route wider — it lays copper at ~0.115 mm either way —
   it only raised the bar the checkers measure against. Via-clearance errors
   went **7 → 125**. Those props are a check threshold in this toolchain, not a
   routing constraint. Reverted.
2. **Moving parts shuffles the failures rather than removing them.** Pushing
   the flash from 13 mm to 22 mm off the QFN took the U4 cluster from 9 errors
   to 3 — and a USB short storm appeared instead (39 → 38 total). Pulling the
   USB connector 11 mm closer fixed the pair's long run and the count went to
   46. At default effort the total sat in a 38–46 band whatever the placement;
   the router's output is close to chaotic with respect to small placement
   moves, so chasing individual errors by nudging parts is not a convergent
   loop.
3. **Four layers fixes it, and the pipeline cannot ship it.** One
   character — `layers={4}` — took the board from **46 blocking errors to 6**.
   The inner planes give the QFN escape and the D± pair somewhere to go. But at
   4 layers `tscircuit-cli export` throws `Inner layer … only supports copper
   gerber`, which aborts the whole fab-packet step: **no bom.csv, no cpl.csv,
   BOM lines reported as 0**. A 4-layer board here is well routed and
   unorderable. Reverted to 2 layers, which is what `product.json` declares and
   what an economy JLC order wants.
4. **Effort level is the lever that actually worked on 2 layers.**
   `autorouterEffortLevel="5x"` took the same board from **46 to 18** blocking
   errors with no other change. It costs **16:52 per build instead of 4:45**,
   which is why it went on last: at 17 minutes a round it is not a knob you
   iterate with, it is one you turn once the placement is settled. It is on in
   the shipped source. Two placement fixes on top of it — space around the
   relocated crystal cluster, and 6 mm more electronics strip — took 18 → 3 → 1.

So the honest verdict is narrow and specific: **the open router closes a 50-key
matrix without complaint; what it cannot close on two layers at a 0.2 mm trace
width is the 0.4 mm-pitch QFN-56 escape and the through-hole USB connector's
own drill field.** What would fix it, in order of preference: (a) four layers,
once the gerber exporter handles inner layers; (b) a 0.127 mm trace/space
rule — JLC's actual floor — instead of 0.2 mm; (c) hand-routed escapes for the
QFN and the USB connector via `pcbRouteHints`, with the router left to do the
matrix, which is the part it is good at.

### Other things you should know

- **kicad's DRC is looser than the fab.** The converted board carries a 0.09 mm
  netclass clearance; JLCPCB's 2-layer minimum spacing in
  `circuitlib.tables` is **0.127 mm**. Passing the kicad DRC here would still
  not prove JLC compliance, and circuitpy's own DFM gate checks trace width,
  via size, annular ring and edge clearance — not trace-to-trace spacing.
- **No test points, and that is a limitation.** Two mechanisms were tried and
  both blocked the packet. `<testpoint>` emits a source component that the
  circuit-json BOM exporter writes into `bom.csv` with an empty LCSC column
  **even with `doNotPlace` set** → 8 × `part_not_orderable` (error).
  `<via connectsTo="net.X">` is net-tagged but the autorouter never routes to
  it → 8 dangling vias and 7 × `unconnected_items` (error). Bring-up therefore
  rides on copper that is already there: U2's SOT-223 tab is V3_3, C2/C3 are
  the rail pads, the four USB shell tabs are ground, and every ROW and COL is
  exposed on 50 tactile-switch pads. **SWCLK/SWD are the real casualty** — they
  exist only on QFN pins 24/25, so there is no probe access for a debugger.
  Reflashing goes through BOOTSEL and USB mass storage.
- **The routing does not look deliberate, and it is not.** On `_pcb.png` the
  rows and columns are point-to-point stars fanning diagonally to the MCU, not
  the straight bussed lines a human would draw. The nets are correct and, after
  the 5x-effort pass, the runs are short; the drawing is still ugly. Forcing
  straight buses means `pcbRouteHints` on fifteen nets, which is a real next
  step and was not taken here.
- **There is no ground pour.** `main.circuit.json` contains zero copper zones:
  GND on this 100 × 90 mm board is a web of 0.2 mm traces serving 134 parts.
  At ~150 mA total the DC drop does not matter; the return path and the loop
  area do, and neither is modelled by anything in this pipeline.
- **`bom.csv` ships with empty `Comment` and `Footprint` columns.** Only
  `Designator` and `LCSC Part #` are populated for all 134 rows. JLC will take
  it; a human checking a 134-line BOM cannot tell a 15 pF from a 10 µF. That is
  the circuit-json exporter, not the board source.
- **The `sw-tact` pad pairing is still hardware-unverified.** Its BLOCK.md says
  so plainly: if the TS-1187A's real internal pairing is 1+3 / 2+4 rather than
  1+2 / 3+4, the block's tie traces are a permanent short and all 50 keys are
  scrap. On a board that is 50 copies of one switch, that is the single largest
  risk in the design. Verify on the first article before anyone orders five.
- **Two golden-block placement bugs were fixed inside this project's own
  `blocks/` snapshot.** Both are placement-only — no net, part, or value
  changed — and both are marked with a `PROJECT-LOCAL` comment in
  `blocks/rp2040-core/rp2040-core.tsx`. They belong upstream:
  1. `rp2040-core` v1 puts the 12 MHz crystal 11.78 mm from `U3.XIN`.
     tscircuit enforces a **10 mm maximum on a crystal connection** and, when
     it cannot be met, **skips autorouting for the entire board**. As shipped,
     no board using this block can route at all. XIN is the bottom-centre pin
     of the QFN, so Y1, C15, C16 and R11 were moved from the left of the chip
     into the strip below it, every endpoint now within 8.5 mm.
  2. The flash U4 sat 5 mm off the QFN edge, in the same corridor as the
     power web and the matrix fan-in; it was pushed out to 22 mm.
  3. Consequence of (1): the relocated crystal row initially sat 8.3 mm below
     the chip, close enough to the 100 nF row at 6 mm that the router threaded
     XIN between C10 and C11 and **shorted it to V3_3**. The row moved to
     10.5 mm, which needed BOOTSEL/RESET (SW2/SW3) pushed from 12 mm to
     15.5 mm to keep the courtyards apart. A crystal that has to move because
     of a routing rule drags the whole bottom of the block with it — the
     upstream fix should re-place the cluster properly, not shim it.
- **A repo-level toolchain trap.** A stale `/Users/d/code/package.json` (dated
  2023, nothing to do with this repo) makes `tscircuit-cli` resolve its project
  root to `/Users/d/code` and write `circuit.json` into `/Users/d/code/dist/…`
  instead of the mirrored work dir. circuitpy then cannot find it and every
  build in `examples/**` fails with `COMPILE_ERROR`. The workaround is the
  one-line `package.json` in this project directory, which pins the CLI's root
  back to the work dir. The skeleton deliberately ships no `package.json`, so
  this file is a workaround and should disappear when the CLI's root resolution
  is fixed. All three example boards built the same night hit this and all
  three landed on the same workaround independently — it is environmental, not
  specific to this project.
- **Never wrap a generated element in a bare `<group>`.** The first version put
  each key cell in `<group key={…}>` with no `pcbX/pcbY`. That hands the board
  to the auto-pack solver, which threw away all 50 computed positions and
  returned `PackSolver2 ran out of iterations` plus 100 pad overlaps. The key
  grid is emitted as flat siblings for exactly this reason.

### Not checked by anything here

Thermal behaviour, EMI, USB signal integrity (the D± pair is not
length-matched and not impedance-controlled — it is a full-speed link, which
is forgiving, but it is not verified), keycap mechanics, actual switch feel,
and whether 10 mm pitch is comfortable for real thumbs. Those need the first
article.
