/**
 * terminal-keyboard — the thumb keyboard for the Autonomous Terminal.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * A 5-row x 10-column diode-isolated key matrix on an RP2040, which enumerates
 * over USB-C as a native HID keyboard for the Terminal's mobile Linux side.
 *
 * Blocks used: rp2040-core, usb-c-data, ldo-3v3, status-led, sw-tact (x50)
 * Glue:        50x 1N4148W (SOD-123, LCSC C81598), 6x M2.5 mounting holes
 * Rails:       V5 (USB VBUS) -> V3_3 (ldo-3v3)
 * Envelope:    100 x 90 mm, 2 layers, 1.6mm — matches product.json
 *
 * Matrix wiring is COL2ROW: net.COL<c> -> D<n> anode, D<n> cathode -> the
 * per-key node net K<r><c>, node -> SW<n> -> net.ROW<r>. Firmware drives one
 * ROW low at a time and reads the ten COL inputs on their internal pull-ups,
 * so current only ever flows column -> row. The diode is what stops a third
 * phantom key appearing when two real keys share a row and a column.
 *
 * Pin allocation: GPIO0..GPIO4 = ROW0..ROW4 (outputs, 5 scan steps),
 * GPIO5..GPIO14 = COL0..COL9 (inputs, internal pull-ups). GPIO15..GPIO29 free.
 *
 * Every part below either comes from a golden block or is glue (a diode, a
 * hole). Nothing here was invented from a datasheet.
 */

import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { StatusLed } from "../blocks/status-led/status-led"
import { SwTact } from "../blocks/sw-tact/sw-tact"
import { GndPour, MountingHole } from "../blocks/glue"

/** Key pitch, millimetres. 10mm is the tightest the TS-1187A land pattern
 *  (dfn4_p3.6998mm_w7mm_pw0.75mm, 7.9mm across the pads) allows while still
 *  leaving a 2.1mm channel between columns for the column trace. */
const PITCH = 10

/** Board outline. 10 columns x 10mm = 100mm of key field, plus edge margin;
 *  the electronics strip lives below the keys. 90mm tall rather than 84: the
 *  last blocking errors are all crowding in the MCU escape, and 6mm more
 *  strip is the only lever left that adds room without moving a key. The
 *  spare margin lands at the top, under the screen bezel, where nothing else
 *  needs it.
 *
 *  Why 100 wide, and what it cost. 112x90 missed the <=100x100mm $2 sample
 *  tier by 12mm and quoted $8.90 for five bare boards — 4.5x, for margin
 *  (ledger #30, EE review 2026-08-15 finding 5). Dee approved the physical
 *  change on 2026-08-16; it is landed here and the printed body changes with
 *  it. What 112 was actually made of, measured on the built board rather
 *  than estimated:
 *
 *      switch pads      -48.500 .. +48.500   97.00mm
 *      all copper       -50.500 .. +48.500   99.00mm  (diode column is the -x end)
 *      M2.5 drills      -53.850 .. +53.850  107.70mm
 *      drill keepouts   -54.100 .. +54.100  108.20mm
 *
 *  Read that table again: the widest thing on the board was a screw. The
 *  circuit fitted under 100mm all along; the mounting column did not. 112 is
 *  2 x (52.5 screw centre + 3.5 to the edge), and 52.5 is 7.5mm outboard of
 *  the last key centre — a screw column parked beside the keycaps. The
 *  earlier note here said content spans 107.7mm and treated that as a wall;
 *  107.7 IS the mounting holes.
 *
 *  And no screw goes back beside the key field at this size. A 2.7mm drill
 *  carries a 1.6mm keepout, the switch copper reaches 4.5*PITCH + 3.5, so a
 *  flank screw needs x >= 4.5*PITCH + 5.1 and the board allows x <= 48.4.
 *  Those cross at PITCH = 9.62 — and that is with the keepout touching both
 *  the copper and the board edge. Nothing above a 9.6mm pitch has a flank
 *  screw on a 100mm board, so this was never a margin trim.
 *
 *  Three moves get to 100x90 with the 10mm pitch untouched: the diodes tuck
 *  2.0mm right (below), the status LED shifts 2.2mm east (below), and all six
 *  screws leave the side rails for the top and bottom rails (below). Copper
 *  now stops at +-48.500 and courtyards at +-48.750, so the board keeps
 *  1.50mm of copper-to-edge and 1.25mm of body-to-edge on each flank. */
const BOARD_W = 100
const BOARD_H = 90

const colX = (c: number) => -45 + PITCH * c
const rowY = (r: number) => 36 - PITCH * r

/** The legend on the real prototype, row by row, left to right.
 *  Rows are 10 wide except where an oversized keycap covers two positions:
 *  R4C9 sits under the tall ENTER cap, R5C4/R5C5 under the wide SPACE cap.
 *  R5C9 is a wired spare with no cap in the current body. */
const LEGEND: string[][] = [
  ["ESC", "FN+", "CTL@", "F1", "F2", "F3", "F4", "CTL", "FN-", "BKSP"],
  ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
  ["A", "S", "D", "F", "G", "H", "J", "K", "L", "ENT"],
  ["TAB", "Z", "X", "C", "V", "B", "N", "M", "PGUP", "ENT"],
  ["SHFT", "ALT", "MIC", "SPC", "SPC", "SPC", "HOME", "PGDN", "END", "SPR"],
]

const ROWS = 5
const COLS = 10

/** One key cell: the diode (COL2ROW, anode on the column side) and the switch.
 *  Index n runs 0..49 row-major, so refdes D1..D50 / SW10..SW59 read straight
 *  off the grid: D1..D10 is the top row, D41..D50 the bottom. */
const keyCells = () => {
  const out: JSX.Element[] = []
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const n = r * COLS + c
      const d = `D${n + 1}`
      const sw = `SW${10 + n}`
      const node = `K${r}${c}`
      const x = colX(c)
      const y = rowY(r)
      // schematic: one cell per key on a 9 x 6 grid, same shape as the board,
      // sitting directly above the block row so the drawing reads as one page
      const sx = -40 + 9 * c
      const sy = 24 - 6 * r
      // NOTE: these are emitted as flat siblings, never wrapped in a bare
      // <group>. A group with no pcbX/pcbY hands the board to the auto-pack
      // solver, which throws away every coordinate computed here.
      out.push(
        /* COL2ROW: column -> anode, cathode -> the per-key node.
           x - 1.4, not x - 3.4: at -3.4 the SOD-123 body reached 2.0mm past
           its own switch's left pad, so column 0's five diodes hung 0.75mm
           off a 100mm board. Tucked to -1.4 the diode's copper (x -3.5 ..
           x +0.7) sits inside the switch's own 7.0mm land, so the flank is
           set by the switches alone. The channel between columns at the
           diode row goes from 2.1mm split around an anode pad to a clear
           3.0mm, which is the routing the tuck buys back. */
        <diode
          key={`d${n}`}
          name={d}
          footprint="sod123"
          supplierPartNumbers={{ jlcpcb: ["C81598"] }}
          manufacturerPartNumber="1N4148W"
          pcbX={x - 1.4}
          pcbY={y - 4.4}
          schX={sx - 3}
          schY={sy}
        />,
        <trace key={`dc${n}`} name={`TR_${d}_col`} from={`.${d} > .anode`} to={`net.COL${c}`} />,
        <trace key={`dn${n}`} name={`TR_${d}_node`} from={`.${d} > .cathode`} to={`net.${node}`} />,
        /* the key itself: node -> switch -> row */
        <SwTact
          key={`sw${n}`}
          name={sw}
          signal={node}
          to={`ROW${r}`}
          pcbX={x}
          pcbY={y}
          schX={sx + 3}
          schY={sy}
        />,
        <silkscreentext
          key={`lg${n}`}
          text={LEGEND[r][c]}
          layer="top"
          pcbX={x}
          pcbY={y + 3.3}
          fontSize="1mm"
          anchorAlignment="center"
        />,
      )
    }
  }
  return out
}

/** Matrix nets to the MCU: rows on GPIO0-4, columns on GPIO5-14. */
const matrixToMcu = () => {
  const out: JSX.Element[] = []
  for (let r = 0; r < ROWS; r++) {
    out.push(
      <trace
        key={`row${r}`}
        name={`TR_ROW${r}`}
        from={`.U3 > .GPIO${r}`}
        to={`net.ROW${r}`}
      />,
    )
  }
  for (let c = 0; c < COLS; c++) {
    out.push(
      <trace
        key={`col${c}`}
        name={`TR_COL${c}`}
        from={`.U3 > .GPIO${5 + c}`}
        to={`net.COL${c}`}
      />,
    )
  }
  return out
}

/**
 * Bring-up access: a 3-pin SWD header (SWCLK/SWD/GND) as 1.0mm test pads at
 * 2.54mm pitch, in open copper east of the flash (U4). Pads, not plated
 * holes, so no drills and no BOM rows — measured on harness-puck: a
 * <testpoint> needs `footprintVariant="pad"` to stay out of the BOM's LCSC
 * column (the part_not_orderable claim in the earlier README "Honest limits"
 * is stale for this idiom). Reflash stays BOOTSEL + USB mass storage; these
 * pads give a debugger somewhere to bite once a probe needs SWD.
 */

const DebugPort = (props: { pcbX?: number; pcbY?: number }) => {
  const px = props.pcbX ?? 0
  const py = props.pcbY ?? 0
  const nets = ["SWCLK", "SWD", "GND"]
  const labels = ["CLK", "DIO", "GND"]
  const pitch = 2.54
  const diameter = 1.0
  const first = -((nets.length - 1) * pitch) / 2
  return (
    <group pcbX={px} pcbY={py} pcbRotation={0} schX={30} schY={-12}>
      {nets.flatMap((net, i) => {
        const name = `TP${i + 1}`
        return [
          <testpoint key={name} name={name} footprintVariant="pad"
            padShape="circle" padDiameter={`${diameter}mm`}
            pcbX={first + i * pitch} pcbY={0} schX={i * 2} schY={0} />,
          <trace key={`${name}_t`} name={`TR_${name}`}
            from={`.${name} > .pin1`} to={`net.${net}`} />,
          <silkscreentext key={`${name}_s`} text={labels[i] ?? net}
            pcbX={first + i * pitch} pcbY={-1.8} fontSize={1} />,
        ]
      })}
    </group>
  )
}

export default () => (
  <board
    width={`${BOARD_W}mm`}
    height={`${BOARD_H}mm`}
    thickness={1.6}
    autorouterEffortLevel="5x"
    minTraceWidth="0.2mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
    /* Measured, not assumed: raising minTraceToPadEdgeClearance /
       minViaEdgeToPadEdgeClearance to 0.15mm does NOT make the router route
       wider — it lays copper at ~0.115mm either way and the checkers simply
       report more failures (7 via-clearance errors at the 0.1mm default
       became 125 at 0.15mm). Those props are a *check* threshold here, not a
       routing constraint, so the board declares only the fab rules it means. */
  >
    {/* ---- the key field: 50 switches, 50 diodes, on a strict 10mm grid ---- */}
    {keyCells()}

    {/* ---- the electronics strip, below the keys ---- */}

    {/* The brain: RP2040 minimal system, native USB HID. Centred under the key
        field on purpose — the fifteen matrix nets fan out symmetrically, so the
        longest column run is half the board instead of the whole board. */}
    <Rp2040Core pcbX={0} pcbY={-25} schX={0} schY={-16} />
    {matrixToMcu()}

    {/* Power entry + USB 2.0 data, on the bottom edge (the cable runs down,
        away from the thumbs and out from under the screen). Pulled in to
        x=-23 so the D+/D- pair has a short, clear diagonal to U3's top edge:
        at x=-34 the pair had to cross the whole strip and the router shorted
        USB_DP to USB_DM in three places. Nothing else lives on that diagonal. */}
    <UsbCData pcbX={-23} pcbY={-40.5} schX={-46} schY={-16} />

    {/* Logic rail: V5 -> V3_3. Parked left of the port so it is close to VBUS
        and out of the USB pair's lane; V3_3 leaves as one wide net eastward. */}
    <Ldo3v3 pcbX={-40} pcbY={-27} schX={-24} schY={-16} />

    {/* proof of life, next to the port so it shows through the body's LED pipe.
        x=-45.8, not -48: at -48 LED1's courtyard reached -49.680, which is
        0.320mm from a 100mm edge — inside JLCPCB's 1.0mm conveyor rail. 2.2mm
        east puts it at -47.480, clear of the 2.5mm body-to-edge band, and
        still leaves 0.44mm of courtyard between R20 and the LDO. The body's
        LED pipe moves 2.2mm with it. */}
    <StatusLed rail="V3_3" led="LED1" r="R20" pcbX={-45.8} pcbY={-32} schX={26} schY={-16} />

    {/* SWD access: outboard of the flash in open copper (measured clear 2026-08-14) */}
    <DebugPort pcbX={38} pcbY={-33} />

    {/* ---- the printed body needs something to hold: 6x M2.5, all six now on
            the top and bottom rails. THIS CHANGES THE PRINTED BODY. The
            screws used to live in the two side rails at x=+-52.5 (y=+41/0/-41);
            on a 100mm board there is no side rail left to put them in. The key
            field's copper already reaches +-48.500, and a 2.7mm drill needs
            1.6mm of keepout, so the innermost legal screw centre beside the
            keys is x=50.35 — 0.35mm outside a 100mm board. That is true at
            every pitch above 9.40mm, so this is not a margin trim: the side
            bosses are gone and the body carries that load on the bare
            underside instead.

            Where they went, and how much clear board each one has, measured
            on the post-shrink placement (keepout edge to the nearest pad or
            courtyard; the router's own vias are excluded because it replaces
            them every build):

              (-47, +42)  1.70mm    (0, +42)  1.93mm    (+47, +42)  1.70mm
              (-47, -42)  7.11mm    (0, -42)  2.50mm    (+47, -42)  8.78mm

            Every drill keeps 1.65mm of FR4 to the board edge on both axes,
            and 1.40mm from its keepout. The mid-span pair moved from the
            middle of the side edges to the middle of the top and bottom
            edges, which is the right swap now that 100mm is the long span:
            a thumb press at the centre of the key field is 42mm from a screw
            instead of 63mm.

            Two things the mechanical side has to hold, because the board
            cannot check them: the top row's keycaps must not reach above
            y=+40.65 (the top drills start there), which on a 10mm pitch means
            a cap no taller than 9.3mm; and a boss on the top or bottom rail
            has 1.65mm of board outboard of its drill, so it wants a washer
            face, not a countersink.

            Declare them in H-number order. A <hole>'s name never reaches
            circuit.json, so enclosure.json numbers the holes by the order
            they appear in the file — write them out of order and the body's
            H3 is a different screw from this file's H3. ---- */}
    <MountingHole name="H1" diameter={2.7} pcbX={-47} pcbY={42} />
    <MountingHole name="H2" diameter={2.7} pcbX={0} pcbY={42} />
    <MountingHole name="H3" diameter={2.7} pcbX={47} pcbY={42} />
    <MountingHole name="H4" diameter={2.7} pcbX={-47} pcbY={-42} />
    <MountingHole name="H5" diameter={2.7} pcbX={0} pcbY={-42} />
    <MountingHole name="H6" diameter={2.7} pcbX={47} pcbY={-42} />

    {/* moved off centre: H5's drill now occupies y -43.35 .. -41.15 at x=0,
        which is where this line used to sit. x=24 is the empty stretch of the
        bottom rail between H5 and H6. */}
    <silkscreentext
      text="AUTONOMOUS TERMINAL KEYBOARD"
      layer="top"
      pcbX={24}
      pcbY={-42.6}
      fontSize="1.4mm"
      anchorAlignment="center"
    />

    {/* Ground plane on the bottom layer.

        Three of the EE's findings are the same missing thing (review
        2026-08-15). GND was routed as 0.2mm track, the same copper as a key
        signal — a plane is what a rail's current path is supposed to be, and
        a poured net is exempt from the width rule for that reason. The USB
        pair had 0% reference: a differential pair over no plane has undefined
        impedance and its return current goes wherever it can. And the copper
        read as "messy" partly because every ground return was drawn as one
        more line competing for the same channels.

        GndPour, not a bare <copperpour>: the pour solver draws every round
        obstacle as a 32-gon, so a nominal margin measures short at the chord
        midpoints, and this board has six drills (ledger #2). */}
    <GndPour layer="bottom" />
  </board>
)
