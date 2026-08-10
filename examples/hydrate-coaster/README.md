# hydrate-coaster

The board inside the **Autonomous Hydrate** coaster (autonomous.ai/habit/hydrate) — a
$39 desk coaster with a small agent living in it. The agent's whole job is to notice
whether you are actually drinking and to be rude about it when you are not. This board
is its brain and its senses.

It is deliberately the **simple** one of the three reference boards: an RP2040, a USB-C
port, two copper plates, four resistors and two LEDs. The point is to take a small board
all the way — routed, checked, costed, reviewed — not to see how much fits on one.

**It is not orderable.** Two DFM errors remain at the USB-C receptacle and `fab.ready`
is `false`. What they are, what was tried, and why the fix is not a board-file edit are
in `DESIGN-REVIEW.md`. A board that says so beats a board that pretends otherwise.

---

## What the board does

- **Feels the cup.** Two mask-covered copper plates on the top layer sit under where the
  mug goes. Each is driven from one shared GPIO through a 1 MΩ resistor and read back on
  its own GPIO — the RC charge-time self-capacitance trick. The **sum** of the two reads
  moves with the water in the cup; the **difference** tells you a mug is on the coaster
  at all and roughly where. No sensor IC, no invented circuit: two plates and four
  resistors.
- **Talks to the computer.** USB-C, full-speed USB 2.0 straight into the RP2040. That is
  also the only way firmware gets on (BOOTSEL + drag a UF2).
- **Shows life.** `LED1` is wired to the 3.3 V rail through 1 kΩ — a power light the
  firmware cannot fake. `LED2` is the agent's own light on GPIO0.
- **Can be told to shut up.** `SW1` pulls GPIO1 low, active low against the RP2040's
  internal pull-up.

## Blocks

| Capability | Golden block | Refdes it brings |
|---|---|---|
| USB-C power + USB 2.0 data | `usb-c-data` | J1, R1, R2, R3, R4, U1, C1 |
| 5 V → 3.3 V logic rail | `ldo-3v3` | U2, C2, C3 |
| The brain | `rp2040-core` | U3, U4, Y1, R11–R13, SW2, SW3, C4–C17 |
| Power light | `status-led` | LED1, R20 |
| Agent light | `status-led` (2nd instance, GPIO-driven) | LED2, R21 |
| Mute button | `sw-tact` | SW1 |

Board-level glue (allowed — passives, copper, holes, no ICs): `R30`/`R31` 1 MΩ sense
drive, `R32`/`R33` 1 kΩ pin protection and pour anchors, two `copperpour` electrodes,
four M3 holes, silkscreen.

## Rail tree

```
USB-C VBUS ──> net.V5   5 V, sink advertises up to 3 A via 5.1k/5.1k CC (budget 1.5 A)
                 │  C1 10µF bulk at the connector, C2 10µF at the LDO input
                 └─> U2 AMS1117-3.3 ──> net.V3_3   3.3 V logic
                                          │  C3 10µF out, C17 10µF bulk,
                                          │  8 × 100nF across the RP2040 supply pins
                                          └─> U3, U4, LED1, R32/R33 pull-ups n/a
net.DVDD  1.1 V core, generated inside the RP2040 (VREG_VOUT). Never driven externally.
```

Draw, all **modelled, not measured**: RP2040 + flash ≈ 100 mA (the block's planning
figure), LED1 + LED2 ≈ 2.4 mA, cup sense ≈ 0 (1 MΩ). Call it **≈ 105 mA on 3.3 V**.
The LDO burns `(5 − 3.3) × 0.105 = 0.18 W` — a third of the 0.51 W the block calls a
comfortable SOT-223 load, so no copper pour on the tab is needed.

## Pin allocation

The `rp2040-core` block is instantiated inside `<group pcbRotation={180}>`. That turns
the QFN so its USB/QSPI side faces the USB connector below it, and it is the single
change that took the board from 66 routing errors to 3. After the rotation the GPIO
sides are: **right** = GPIO0–11, **left** = GPIO18–29, **top** = GPIO12–17.

| Pin | Net | What |
|---|---|---|
| GPIO2 | `CAP_DRIVE` | shared drive into both 1 MΩ resistors |
| GPIO3 | `CAP_A_SENSE` | left plate read-back (through R32, 1 kΩ) |
| GPIO4 | `CAP_B_SENSE` | right plate read-back (through R33, 1 kΩ) |
| GPIO0 | `LED_NUDGE` | the agent's light |
| GPIO1 | `BTN_MUTE` | mute button, active low, internal pull-up |
| USB_DP / USB_DM | to `usb-c-data` | 27 Ω series + USBLC6 ESD |
| SWCLK / SWD | brought out as nets, **no header** | see *what we could not verify* |

Everything the board adds leaves the QFN on its **right** side and runs up the empty
corridor at x = −8…9 mm. The chip's top side is left entirely to the crystal, RUN and
SWD — putting cup-sense nets up there forced the router to squeeze vias into the 2.8 mm
gaps beside Y1, which produced the last clearance errors of the previous revision.

## Numbers

| | |
|---|---|
| Board | **80 × 80 mm** squircle, corner radius 16 mm, 1.6 mm, 2 layers |
| Warning state | **2 blocking, 4 advisory, 334 info** (+182 converter artifacts — see below) |
| Orderable | **No.** `fab.ready: false`. Two `hole_clearance` errors at the USB-C receptacle |
| Envelope in `product.json` | 82 × 82 mm — inside it |
| Placed parts | **41** |
| Unique BOM lines | **17** (12 JLC Basic, **5 extended**) |
| Min trace / via | 0.15 mm trace, 0.6 mm via pad on a 0.3 mm drill |
| Parts cost | **$4.32 / board** (LCSC prices checked 2026-08-10, in `parts.json`) |
| 5 assembled, JLCPCB economy | **≈ $49.60** all-in ex-shipping → **$9.92 / board** |
| | PCB $2.00 · assembly $10.99 (175 joints) · extended-part fees $15.00 · parts $21.61 |
| Lead time | 7–14 days (modelled) |

The extended-part loading fee is **30% of the whole run**: RP2040 (C2040), the crystal
(C20625731), the USB-C receptacle (C165948), the USBLC6 (C2687116) and — annoyingly —
the 27 Ω USB series resistor (C25100), a sub-cent part dragging $3 behind it.

Thin stock, checked 2026-08-10: **C25100 27 Ω — 1,738 pieces.** That is the BOM's real
fragility, and it is holding a two-cent job. C20625731 (crystal) 18k and C2040 (RP2040)
54k are comfortable for a run of 5 and worth watching for a run of 100. `parts.json`
also carries two candidate rows (`bme280`, `r-4.7k-0402`) for blocks that ship in the
project's snapshot but this board does not use — they are not on the BOM.

### Warning state, honestly

`ok: true`, **2 error / 186 warning / 334 info**, `fab.ready: false`.

The two errors are both `hole_clearance` at `J1`: a GND track 0.115 mm and a V5 conductor
0.133 mm from a drill, against KiCad's 0.2 mm default. **Do not order this board.** What
was tried against them, and why the fix is not a board-file edit, is in `DESIGN-REVIEW.md`.

Of the 186 warnings, **182 are tscircuit→KiCad converter artifacts** — `net_conflict` ×94,
`footprint_symbol_mismatch` ×42, `footprint_symbol_field_mismatch` ×40, and six
missing/extra/duplicate-footprint notes. They fire identically on a four-component probe
board, so they say nothing about this design. The four that do:

| Warning | What it means here |
|---|---|
| `isolated_copper` on zone `CAP_A` | R32's exit trace slices a sliver of floating copper off electrode A |
| `holes_co_located` ×2 | two vias drilled at the same coordinate (`V3_3`, `GND`) |
| `pcb_trace_too_long_warning` | the crystal net routes 12.81 mm against tscircuit's 10 mm crystal rule |

The 334 info items are KiCad ERC/DRC noise from the same converter: off-grid schematic
endpoints, missing symbol/footprint libraries, silk text below KiCad's 0.8 mm default.

## Enclosure interface

Board centre is the origin, +x right, +y up, viewed from the top.

- **Outline** 80 × 80 × 1.6 mm, corners rounded R16.
- **Mounting** 4 × Ø3.2 mm (M3) at (±32, ±32). Keep the screw heads under Ø5.5 mm.
- **USB-C (J1)** centred on the **bottom** edge at (0, −34.4); body 9.85 × 6.5 mm, front
  face 2.3 mm inside the board edge — the case needs a window in the bottom wall.
- **Cup plates** two 25 × 30 mm rectangles: EA from (−29, −2) to (−4, 28), EB from
  (4, −2) to (29, 28). **The mug wants to be centred at about (0, +13)**, not at the
  board centre — the electronics band owns the bottom third of the board. The lid's cup
  ring has to be offset accordingly.
- **LEDs** LED1 "PWR" at (−11, −35.5), LED2 "AGENT" at (−16, −35.5) — light pipes or a
  translucent strip along the front edge.
- **Buttons** SW1 "MUTE" at (29, −24), SW2 BOOTSEL and SW3 RESET inside the electronics
  band. All three are interior parts: SW1 needs a plunger through the lid; BOOTSEL and
  RESET are service-only and can stay sealed (see bring-up).
- **Tallest part** the USB-C receptacle. Nominal ~3.2 mm; **not read off a datasheet**.

The generator also writes `boards/main_fab/enclosure.json` with all of this in machine
form, and `board.glb` for modelling the body around it.

## Bring-up

> Plug in USB-C and **LED1 (PWR) lights immediately** — 3.30 V ±3% between C3 pin 1 and
> C3 pin 2 — that is the 3.3 V rail, hard-wired, nothing to
> configure. Then hold **SW2 (BOOTSEL)** and tap **SW3 (RESET)**: the board
> appears on the computer as the `RPI-RP2` mass-storage drive; drag a UF2 on and it
> reboots running it.

If LED1 does not light, measure **3.3 V on C3 pin 1** against **GND on C3 pin 2**, and
**5 V on C2 pin 1** — the silkscreen says `3V3 @ C3   5V @ C2` next to them. 5 V present
and 3.3 V absent is the AMS1117; neither present is the connector or the cable.

## What we could not verify

Stated plainly, because a review that hides these is worse than no review.

1. **The cup sense has never been measured.** Plate area, plate-to-mug distance through
   the lid, the useful capacitance swing between a full and an empty mug, and whether a
   1 MΩ ramp gives the RP2040 enough counts to resolve "half a glass" are all
   *unmeasured*. The topology (drive pin → 1 MΩ → plate → read pin) is the standard one
   and the geometry is sane; the sensitivity number is not knowledge we have. The first
   article decides whether the plates need to be bigger, the 1 MΩ larger, or the whole
   thing replaced by a dedicated touch controller.
2. **No golden block is hardware-verified.** Every block used here says "compile-verified,
   not yet hardware-verified, first article pending." Two named risks carry straight
   through: the TS-1187A tactile pad pairing (1+2 / 3+4 — if it is really 1+3 / 2+4, all
   three buttons are shorted and the boards are scrap) and the 12 MHz crystal's real load
   capacitance against the block's 2 × 15 pF.
3. **No ground plane.** Two layers, all pads on top, and the bottom layer carries routing.
   A bottom-side pour was tried and came back as *isolated copper* — nothing on the bottom
   layer is a GND pad, so it floats, which is worse than no pour. Return paths are
   therefore individual traces. Fine at 12 MHz and full-speed USB; it is the first thing
   to fix on a 4-layer revision.
4. **The USB pair is not length-matched or impedance-controlled.** The netlist cannot
   express either and the router does not try. Full-speed USB is forgiving over ~12 mm.
5. **No SWD header.** BOOTSEL + USB is the reflash path, so a header would only buy live
   debugging — at the cost of an extended through-hole line JLC's economy SMT service
   does not place. Deliberate; revisit if bring-up goes badly.
6. **Thermals, EMI and signal integrity are modelled, not simulated.** 0.18 W in a
   SOT-223 is far inside its envelope by arithmetic, not by measurement.
7. **Two DFM errors are open and we could not close them.** Fifteen builds; the best
   measured result is the one committed here. Moving the regulator, the LEDs, the pour
   anchors, tightening the router's clearance tolerances and going to four layers each
   made it worse, not better. The three fixes that would work — widening the C165948
   land pattern, four layers, or a hand-routed connector escape — are all outside the
   board file.
8. **Cost and current are estimates.** Prices are LCSC catalogue figures read on
   2026-08-10; the current budget is the blocks' planning figures, not a meter.

## Deviations from the shared block library

`blocks/` is this project's frozen snapshot, and one file in it differs from
`packages/golden-blocks`:

- **`blocks/rp2040-core/rp2040-core.tsx` — placement only, electrically identical.**
  As shipped, Y1 sits at `pcbX=-11` while XIN/XOUT are on the QFN's bottom edge, putting
  `Y1.pin1` **11.78 mm** from `U3.XIN`. tscircuit enforces
  `DEFAULT_CRYSTAL_MAX_TRACE_LENGTH_MM = 10` on every crystal net, so the autorouter
  **skips the entire board** and every trace comes back missing. Reproduced with the
  block alone on an empty board. Y1/C15/C16/R11 are moved into the empty slot directly
  below the chip; the longest crystal-net hop is now 8.44 mm and the load caps sit beside
  the pins they serve. The upstream fix belongs in `packages/golden-blocks`.

## Pipeline findings

Four things worth fixing in the toolchain, all reproduced:

1. **`rp2040-core` cannot route on any board as shipped** (see above) — a golden-block
   defect, not a board defect.
2. **A project inside the repo will not build without a `package.json`.** `tscircuit-cli`
   resolves its `dist/` to the nearest ancestor `package.json`; with none in the project
   it walked up to `/Users/d/code/package.json`, wrote the build there, and the pipeline
   reported a bare `COMPILE_ERROR` with a truncated stderr tail. A minimal
   `{"name":…,"private":true}` fixes it — and it must **not** set `"type": "module"`,
   which breaks React resolution inside the toolchain.
3. **`<testpoint>` is unusable on an assembled board.** It is the natural element for a
   bare probe pad or a capacitive electrode, but `circuitpy`'s BOM gate emits
   `part_not_orderable` (severity `error`) for it and **ignores `doNotPlace`**. That is
   why the electrodes here are `<copperpour>` and the probe points are component pads.
4. **`layers: 4` produces a broken packet** — the build succeeds and the router uses the
   extra layers, but `bom.lines` comes back 0, `assembly requested but no BOM rows were
   produced` fires, and the gerber export exits 1. The 4-layer path is not wired up.

Also minor: `bom.basicParts` stays 0 in the sidecar even with a populated `parts.json`,
and `minTraceToPadEdgeClearance` is applied as a general trace-to-trace clearance, so
raising it makes the checker report every ordinary gap as an error.

`package.json` is also present, which the project skeleton does not ship — without it
`tscircuit-cli` resolves its `dist/` to the nearest ancestor `package.json` (here
`/Users/d/code/package.json`), writes the build there, and the pipeline reports a bare
`COMPILE_ERROR`. It must **not** set `"type": "module"`, which breaks React resolution.

## Files

```
product.json                  the bible: name, power, 82x82 envelope, 2 layers, jlcpcb, assembly
parts.json                    the locked BOM — parts-book owns it, checked 2026-08-10
blocks/                       frozen golden-block snapshot (see deviations above)
boards/main.tsx               the board program — the only hand-written source
boards/main.circuit.json      compiled IR
boards/main.board.json        sidecar: warnings, BOM summary, fab state
boards/main_review/           _schematic.png, _pcb.png (+ svg)
boards/main_fab/              gerbers.zip, bom.csv, cpl.csv, board.glb, enclosure.json
                              (no ORDER.md — the pipeline withholds it while fab.ready is false)
DESIGN-REVIEW.md              the seven-lens panel, round by round
```

Rebuild:

```bash
CIRCUIT_PARTS_ENGINE=off python3 skills/circuitcode/scripts/circuit \
  /abs/path/examples/hydrate-coaster/boards/main.tsx --wall-clock-s 1800
```

Expect **10–20 minutes**: the local autorouter is doing real work on ~90 nets.
