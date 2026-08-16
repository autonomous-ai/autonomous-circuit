# harness-puck — the Autonomous Harness, as a real PCB

A 70mm round desk puck for the AI coding agents you already run. Press the key
to hand the next job to an agent; the ring of eight addressable pixels around
the rim is the fleet, in light — one pixel per agent slot, colour for state.
USB-C on the back rim carries power and the USB device link, so the same cable
flashes the firmware and lets the puck present itself to the host.

This is the board that goes inside the shipping SKU's shell
(autonomous.ai/harness, $149). It is generated from `boards/main.tsx` by
`skills/circuitcode/scripts/circuit`; the source is the truth and everything
else in `boards/` is an artifact.

```
boards/main.tsx            the board program — the only file written by hand
boards/main.circuit.json   compiled IR
boards/main.board.json     sidecar: warnings, BOM summary, fab state
boards/main_review/        _schematic.png, _pcb.png (+ svg)
boards/main_fab/           gerbers.zip, bom.csv, cpl.csv, board.glb
DESIGN-REVIEW.md           the repair loop and the seven-lens panel
package.json               not in the skeleton — see below
```

**Why there is a `package.json`.** The project skeleton deliberately ships
without one. This project needs it: `tscircuit-cli` picks its `dist/` root by
walking up from its cwd to the nearest ancestor holding a `package.json`, and on
this machine that walk goes past the repo and lands on `/Users/d/code`. The CLI
then writes `circuit.json` outside the build's work directory, circuitpy cannot
find it where it expects, and the build fails as a `COMPILE_ERROR` with a
"✓ Done / Build complete" log attached — a false negative that says nothing
about the board. A two-line `package.json` at the project root pins the CLI to
the mirrored work dir. The real fix belongs in the pipeline (pass an explicit
output root, or drop a marker into the mirror).

Build it:

```bash
python3.12 skills/circuitcode/scripts/circuit \
  /abs/path/examples/harness-puck/boards/main.tsx --wall-clock-s 5400
```

**State (rebuilt 2026-08-16 07:03 from committed source): 137 blocking, 189
advisory, 437 info. `fab.ready` is false, and this board now routes nothing at
all — do not order it.** One placement error is the whole story: Y1's courtyard
overlaps SW1's by 0.900mm, and tscircuit skips autorouting for the *entire*
board when it finds a placement error, so `main.circuit.json` carries **0
`pcb_trace` and 0 `pcb_via`**. Every other blocking finding — 38
`pcb_port_not_connected_error`, 39 `pcb_trace_missing_error`, 56 KiCad
`unconnected_items` — is a consequence of that, not a separate defect.

The cause is a golden block that moved under a board nobody rebuilt.
`rp2040-core`'s crystal cluster was relocated (`pcbX=-8, pcbY=0` →
`pcbX=0, pcbY=-10.5`) by the EE-review fix commits on 2026-08-15; harness-puck
composes that block at `pcbX=-2, pcbY=11`, which lands Y1's centre at
(−2, +0.50) with a 3.6 × 2.9mm body, 3.50mm from SW1 at (−2, −3.00) with a
7 × 4.4498mm body. The bodies overlap by 0.175mm. SW1's position is product
geometry — the key cap presses it — so the lever is the MCU cluster, not the
button. **This is Dee's call and it is unmade.**

The previously committed packet said `fab.ready: true` with 2 blocking. That
artifact was built on 2026-08-13, before the block moved, and it is the
strongest argument in this repo for rebuilding every composing board when a
golden block changes (ledger #39).

## What the board does

| Job | How |
|---|---|
| Take power and speak USB | USB-C receptacle on the back rim; 5.1k CC pulldowns advertise a 5V sink, USBLC6 ESD on D±, 27Ω series into the MCU |
| Think | RP2040 + 16MB QSPI flash + 12MHz crystal, USB-attached (no radio) |
| Show the fleet | 8 × WS2812B on a 28mm-radius ring, one GPIO (GPIO16), 45° pitch, 45° gap at the back where the cable exits |
| Take the press | SW1 (delegate) 3.6mm off board centre under the key cap; SW4 (mode) on the right rim |
| Prove it is alive | LED1 green on the 3V3 logic rail |
| Come back from a brick | SW3 RESET and SW2 BOOTSEL from `rp2040-core` |

## Blocks

Every IC arrives through a golden block; the ring, the glue caps, the holes and
the silkscreen are board-level.

| Block | Instances | What it brings |
|---|---|---|
| `usb-c-data` | J1, R1–R4, U1, C1 | power entry **and** the USB 2.0 pair |
| `ldo-3v3` | U2/C2/C3 · U5/C20/C21 | two 5V→3.3V rails (see below) |
| `rp2040-core` | U3, U4, Y1, R11–R13, SW2, SW3, C4–C17 | the RP2040 minimal system |
| `ws2812-chain` | `Ws2812Pixel` × 8 (D10–D17), C40–C47, R30 | the pixels, and the two rules that make a WS2812 chain work |
| `sw-tact` | SW1, SW4 | the two buttons |
| `status-led` | LED1, R20 | proof of life |

**On the ring.** `ws2812-chain` lays pixels on a line by `pitch`; a puck needs
them on a circle. `boards/main.tsx` places the block's own `Ws2812Pixel`
component around a 28mm circumference, rotated tangentially so each pixel's
DOUT faces the next pixel's DIN, and repeats the block's two non-negotiable
rules verbatim: **one 100nF per pixel, 4.2mm outboard of that pixel**, and **one
330Ω damper (R30) on the first hop only**. Nothing was invented; the chip, the
land pattern, the values and the topology all come from the block.

**On the rails.** The block defaults `rail` to `V3_3` on purpose: a WS2812B
wants VIH ≥ 0.7 × VDD, so a 5V pixel fed 3.3V logic needs 3.5V and is marginal.
Run at 3.3V the data levels are comfortably in spec, and the pixels are simply
dimmer. There is no level-shifter block, so 5V pixels are not an option today.

## Rails and the current budget

```
USB-C VBUS  →  net.V5        5.0V, budget 1.5A (usb-c-data BLOCK.md)
   ├── U2 AMS1117-3.3  →  net.V3_3       RP2040 + flash + LED1     ~105 mA
   └── U5 AMS1117-3.3  →  net.V3_3_LED   8 × WS2812B         ≤480 mA worst
```

| Rail | Load | Current | Dissipated in the LDO |
|---|---|---|---|
| V3_3 (U2) | RP2040 core + flash (block's ~100mA planning figure) + LED1 1.2mA | ~105 mA | (5 − 3.3) × 0.105 = **0.18 W** |
| V3_3_LED (U5) | 8 pixels, block's worst case 60mA each | ≤480 mA | **0.82 W** |
| V3_3_LED (U5) | 8 pixels at the duty the product actually uses (2–3 lit, mid brightness) | ~110 mA | **0.19 W** |
| V5 total | both regulators + quiescent | ~595 mA worst | 40% of the 1.5A budget |

**Two regulators, not one, and why.** 480 + 105 = 585mA through a single
AMS1117 is 0.99W in one SOT-223 and past `ldo-3v3`'s stated ≤500mA budget.
Split, each regulator stays inside the block's own budget and the heat lands in
two packages 23mm apart. `ldo-3v3`'s BLOCK.md sanctions exactly this: a second
domain gets its own net name via `voutNet`.

**Trace width.** `minTraceWidth` is 0.15mm. IPC-2221 via
`helpers.trace_width_for` wants 0.127mm for the 480mA pixel rail and 0.148mm for
the 600mA V5 input at ΔT = 10°C, so the floor covers every rail on this board
with margin. 0.15mm was chosen over 0.20mm because the RP2040 is a 0.4mm-pitch
QFN-56 and a 0.2mm minimum starves its escape — see DESIGN-REVIEW round 3.

**Decoupling.** `helpers.decoupling_for(power_pins=8, rails=3)` asks for 8 ×
100nF + 3 × 10uF. The board carries **18 × 100nF** (RP2040's eight, DVDD, the
flash, and one per pixel) and **8 × 10uF** (VBUS bulk, in/out on both LDOs, the
RP2040 3V3 bulk, and two extra on the pixel rail at C22/C23).

## Mechanical

| | |
|---|---|
| Outline | 70.0mm circle (a 70 × 70mm board with `borderRadius={35}`), 1.6mm FR4, 2 layers, 1oz |
| Assembly side | **all parts top side** — JLC economy single-side PCBA |
| Orientation | the puck sits face-up: pixel side up under a printed diffuser ring, the key cap presses down onto SW1 through the shell |
| Connector | USB-C at the back rim, receptacle facing −Y, centred on the 45° gap in the pixel ring |
| Mounting | 3 × Ø2.2mm (M2) at radius 22.8mm, 120° apart: (0, +22.0), (−19.75, −11.4), (+19.75, −11.4) |
| Ring | pixel centres on r = 28.0mm; per-pixel 100nF on r = 32.2mm; outermost copper r ≈ 32.9mm, 2.1mm inside the edge |
| Tallest part | the USB-C receptacle, ≈3.3mm above the board (estimate, from the package class — not measured) |
| Key cap | SW1 sits at (−2, −3), 3.6mm off the true centre — the RP2040 owns the middle. The cap needs its own guide bore in the shell so it cannot tilt |

## Parts and cost

58 placements, **18 unique parts**, 12 JLC Basic and **6 extended** (RP2040
C2040, crystal C20625731, USB-C C165948, USBLC6 C2687116, 27Ω C25100, WS2812B
C2761795). Every row carries an LCSC number, so the BOM is orderable as-is.

| | 5 boards, assembled |
|---|---|
| PCB | $2.00 |
| Assembly (setup + stencil + 225 joints) | $11.41 |
| Extended-part fees (6 × $3) | $18.00 |
| Parts (≈$5.07/board, from the blocks' pinned LCSC prices) | ≈$25.35 |
| **Total** | **≈$57**, plus $1.50–3.00 slow shipping |

Every number here is **modelled, not quoted** — `helpers.estimate_cost` plus the
per-part prices recorded in the blocks' BLOCK.md on 2026-08-10. The fab
profile's standing band for an assembled 5× run is $75–110; this board comes in
under it because 12 of its 18 lines are Basic passives. Two of the six extended
lines are avoidable: a Basic 27Ω 0402 in `usb-c-data` and a Basic WS2812
equivalent would save ~$6 per order, and both are `parts-book` jobs, not board
edits. `parts.json` has **not** been generated for this project.

## Bring-up

Plug USB-C: LED1 (green, left of centre) lights and both rails read 3.30V ±3% —
probe C3's pad for V3_3 and C21's for V3_3_LED, ground on any of the three
mounting holes' neighbouring GND pads. Then hold SW2 (BOOTSEL) while tapping SW3
(RESET); the puck should enumerate as `RPI-RP2`. Drop on a UF2 that drives
GPIO16 and all eight pixels should chase.

If the ring lights but stops partway round, the failure is almost always the
first pixel that stopped: check its 100nF and its DOUT pad.

Two things bring-up does not have, and you should know before the boards
arrive. **There is no SWD access** — `rp2040-core` brings SWCLK/SWD out as nets
and nothing on this board lands them on a pad, so recovery is BOOTSEL + UF2 only.
(`<testpoint>` compiles, but it emits a BOM row with no LCSC number, which the
DFM gate raises as an error-severity `part_not_orderable` — so it cannot be used
on a board that has to reach zero errors.) And **there is no indicator on the
pixel rail**: LED1 watches V3_3 only, so a dead U5 looks exactly like a dead
first pixel, a dead GPIO16, or wrong firmware. Probe C21 to tell them apart.

## What is not verified

Read this part.

- **No hardware exists.** Nothing on this board has been reflowed. Every claim
  below the schematic is arithmetic or a datasheet reading.
- **Every block is compile-verified, not hardware-verified.** Their own
  BLOCK.md files say so. Two specifically matter here:
  - The WS2812B 5050 land pattern comes from the datasheet, not from a reflowed
    board. First-article check: pin-1 orientation and paste release on the
    corner pads.
  - `sw-tact`'s pad pairing (1+2 / 3+4) is the standard TS-1187A arrangement but
    is **unverified**. If the real pairing is 1+3 / 2+4, all four tactile
    switches on this board are permanent shorts and the boards are scrap.
    Confirm this on one part before ordering five.
- **Thermals are arithmetic, not simulation.** 0.82W in U5's SOT-223 at the
  block's worst case is the number; the junction temperature depends on a θja
  this layout does not pin down, and **there is no copper pour on the regulator
  tabs** — tscircuit's `<copperpour>` is board-wide per layer, so it cannot be
  given to one net's tab, and a board-wide pour trips a `copper_edge_clearance`
  error the dialect gives no prop to raise. Treat the worst case as a firmware
  constraint (clamp aggregate ring duty) until a bench measurement exists.
- **WS2812 current at 3.3V is bracketed, not measured.** The block's 60mA/pixel
  is the conservative bound. Reasoning from the die forward voltages, full white
  at 3.3V is probably nearer 28mA/pixel because the green and blue drivers sit
  in dropout — that is an estimate and nothing on this board depends on it.
- **USB D+/D− run ≈40mm from J1 to U3** as a routed pair with no controlled
  impedance and no length matching. Full-speed (12 Mbps) tolerates this; it has
  not been verified, and the toolchain flags it as `pcb_trace_too_long_warning`.
- **The crystal load is unverified** — `rp2040-core` already flags that its 15pF
  loads against the ABM8-272-T3's real specified load capacitance need a
  first-article frequency check.
- **EMI, signal integrity and real part fit** were not checked by anything. No
  deterministic tool we have looks at them.
- **The enclosure does not exist.** The outline, hole pattern and connector edge
  above are the interface a printed body must be modelled to; none of it has
  been printed and offered up to a board.
- **`rp2040-core` is locally patched in this project.** Y1/C15/C16/R11 were
  moved ~3mm toward U3 because the library placement puts Y1.pin1 11.78mm from
  U3.XIN and the router refuses the crystal net past 10mm. Placement only; the
  deviation is recorded in the block's header comment and belongs upstream.
- **VBUS carries 30µF of bulk**, not the 10µF USB 2.0 allows for inrush: C1 from
  `usb-c-data` plus the two LDO input caps C2 and C20, 10µF each. Most hosts
  will not care; a fussy port can trip on hot-plug. `ldo-3v3` exposes no prop to
  drop an input cap, so this is a block change, not a board change.
- **There is no ground plane.** Every return on this 2-layer board is a trace,
  under eight WS2812 drivers switching three constant-current channels each.
  `<copperpour>` exists in the dialect but fills to 0.200mm of the board edge
  against an exported KiCad rule of 0.290mm, so any pour is a blocking
  `[copper_edge_clearance]`; six candidate board props to raise that clearance
  were tried and all are silently ignored.
- **Prices and stock are from 2026-08-10** and were not re-checked. Nothing was
  looked up online during this build (`CIRCUIT_PARTS_ENGINE=off`).
- **The routing is at the local autorouter's limit.** 182 source traces, 159
  routed traces and 127 vias on two layers. Small placement nudges do not
  converge — they move sub-0.1mm clearance flukes around. Build rounds 8–11 in
  `DESIGN-REVIEW.md` are the evidence.
