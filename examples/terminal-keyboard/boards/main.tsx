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
 * Envelope:    112 x 90 mm, 2 layers, 1.6mm — matches product.json
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
import { MountingHole } from "../blocks/glue"

/** Key pitch, millimetres. 10mm is the tightest the TS-1187A land pattern
 *  (dfn4_p3.6998mm_w7mm_pw0.75mm, 7.9mm across the pads) allows while still
 *  leaving a 2.1mm channel between columns for the column trace. */
const PITCH = 10

/** Board outline. 10 columns x 10mm = 100mm of key field, plus edge margin
 *  and the mounting-hole column; the electronics strip lives below the keys.
 *  90mm tall rather than 84: the last blocking errors are all crowding in the
 *  MCU escape, and 6mm more strip is the only lever left that adds room
 *  without moving a key. The spare margin lands at the top, under the screen
 *  bezel, where nothing else needs it. */
const BOARD_W = 112
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
        /* COL2ROW: column -> anode, cathode -> the per-key node */
        <diode
          key={`d${n}`}
          name={d}
          footprint="sod123"
          supplierPartNumbers={{ jlcpcb: ["C81598"] }}
          manufacturerPartNumber="1N4148W"
          pcbX={x - 3.4}
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

/** Bring-up access. There is no dedicated test-point component on this board,
 *  and that is a limitation, not a choice — see README "Honest limits".
 *  tscircuit's <testpoint> always emits a source_component, and the
 *  circuit-json BOM exporter writes it into bom.csv with an empty LCSC column
 *  even with `doNotPlace` set, which the DFM gate turns into a blocking
 *  `part_not_orderable`. A <via connectsTo="net.X"> is net-tagged but the
 *  autorouter never routes to it, so it lands as a dangling via and an
 *  `unconnected_items` DRC error. Both were tried and both blocked the fab
 *  packet, so the bring-up story rides on copper that is already here:
 *    V3_3  — U2's SOT-223 tab (2.3 x 3.6mm) and C3's high pad
 *    V5    — C2's high pad and C1 beside the connector
 *    GND   — C2/C3 low pads, the four USB shell tabs, every switch row pad
 *    ROW n — pads 3/4 of any switch in that row (0.75mm, tweezer-friendly)
 *    COL n — the anode pad of any diode in that column
 *  plus LED1, which says "3V3 is up" before a meter comes out.
 *  What is genuinely missing is SWD: SWCLK/SWD only exist on QFN pins 24/25.
 *  Reflashing goes through BOOTSEL + USB mass storage instead. */

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

    {/* proof of life, next to the port so it shows through the body's LED pipe */}
    <StatusLed rail="V3_3" led="LED1" r="R20" pcbX={-48} pcbY={-32} schX={26} schY={-16} />

    {/* ---- the printed body needs something to hold: 6x M2.5 on a 105mm x
            38mm rectangle plus mid-span pairs (a 112mm board flexes under
            thumbs; the mid holes are what stop it) ---- */}
    <MountingHole name="H1" diameter={2.7} pcbX={-52.5} pcbY={41} />
    <MountingHole name="H2" diameter={2.7} pcbX={52.5} pcbY={41} />
    <MountingHole name="H3" diameter={2.7} pcbX={-52.5} pcbY={0} />
    <MountingHole name="H4" diameter={2.7} pcbX={52.5} pcbY={0} />
    <MountingHole name="H5" diameter={2.7} pcbX={-52.5} pcbY={-41} />
    <MountingHole name="H6" diameter={2.7} pcbX={52.5} pcbY={-41} />

    <silkscreentext
      text="AUTONOMOUS TERMINAL KEYBOARD"
      layer="top"
      pcbX={0}
      pcbY={-43.4}
      fontSize="1.4mm"
      anchorAlignment="center"
    />
  </board>
)
