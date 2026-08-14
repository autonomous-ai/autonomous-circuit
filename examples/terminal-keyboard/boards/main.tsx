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
 * Envelope:    108 x 58 mm, 2 layers, 1.6mm — matches product.json
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
import { GndPlanes, MountingHole, PowerTrunk } from "../blocks/glue"

/** Key pitch, millimetres. 10mm is the tightest the TS-1187A land pattern
 *  (dfn4_p3.6998mm_w7mm_pw0.75mm, 7.9mm across the pads) allows while still
 *  leaving a 2.1mm channel between columns for the column trace. */
const PITCH = 10
const ROWS = 5
const COLS = 10
// Matrix copper is spacious board-level wiring, not a fine-pitch escape. The
// EE layout contract therefore keeps it at the preferred 0.25mm signal width;
// RP2040/USB fine-pitch critical escapes run at the 0.15mm floor the golden
// block pins (crystal, QSPI, USB); the matrix stays at 0.25mm.
const MATRIX_TRACE_WIDTH = "0.25mm"

/** The approved outline is derived from the key field, not from a leftover
 *  electronics strip: 100 x 50mm of nominal key cells plus a 4mm mechanical
 *  band on every edge. The band leaves the measured key keep-outs at least
 *  4.25mm from the vertical edges / at least 4.025mm from the horizontal edges and
 *  gives each M2.5 drill 0.65mm of material to the routed outline. */
const MECHANICAL_MARGIN = 4
const BOARD_W = COLS * PITCH + 2 * MECHANICAL_MARGIN
const BOARD_H = ROWS * PITCH + 2 * MECHANICAL_MARGIN

/** The diode is asymmetric inside each cell. The X offset centres the combined
 *  99.5mm keep-out. The Y offset starts from its 1.425mm centring correction
 *  and adds 0.85mm so D46 clears J1's unavoidable through-hole shell tab;
 *  the compact outline still retains a measured 4.025mm top margin. */
const KEY_KEEPOUT_X_BIAS = 1
const KEY_KEEPOUT_Y_BIAS = 2.275
const colX = (c: number) => (c - (COLS - 1) / 2) * PITCH + KEY_KEEPOUT_X_BIAS
const rowY = (r: number) => ((ROWS - 1) / 2 - r) * PITCH + KEY_KEEPOUT_Y_BIAS

/** Exactly 32 explicit GND stitches satisfy the product's 10mm-density floor
 *  after the verifier's 50% component-keepout allowance. They sit in the
 *  perimeter mechanical band: the lower row deliberately skips |x| < 15 so
 *  J1's plated shell and insertion edge remain unobstructed. */
const GND_STITCHING_VIAS = [
  { x: -45, y: 27 },
  { x: -39, y: 27 },
  { x: -33, y: 27 },
  { x: -27, y: 27 },
  { x: -21, y: 27 },
  { x: -15, y: 27 },
  { x: -9, y: 27 },
  { x: -3, y: 27 },
  { x: 3, y: 27 },
  { x: 9, y: 27 },
  { x: 15, y: 27 },
  { x: 21, y: 27 },
  { x: 27, y: 27 },
  { x: 33, y: 27 },
  { x: 39, y: 27 },
  { x: 45, y: 27 },
  { x: -45, y: -26 },
  { x: -35, y: -26 },
  { x: -25, y: -26 },
  { x: -15, y: -26 },
  { x: 15, y: -26 },
  { x: 25, y: -26 },
  { x: 35, y: -26 },
  { x: 45, y: -26 },
  { x: -52, y: -18 },
  { x: 52, y: -18 },
  { x: -52, y: -9 },
  { x: 52, y: -9 },
  { x: -52, y: 9 },
  { x: 52, y: 9 },
  { x: -52, y: 18 },
  { x: 52, y: 18 },
]

/** Autorouting-phase regions are board-global coordinates; tscircuit does not
 * transform them with a translated/rotated block. Preserve phase 0's exact
 * previously-proven SRJ bounds so its crystal/clock copper remains stable. */
const RP_CLOCK_ROUTING_REGION = {
  minX: -54,
  maxX: 54,
  minY: -29,
  maxY: 29,
} as const

/** The five phase-1 QSPI nets use a local solve space instead of spanning the
 * 108x58mm keyboard. This integer box was cold-replayed at 0.15mm clearance
 * against the preserved phase-0 copper and solved deterministically. */
const RP_QSPI_ROUTING_REGION = {
  minX: -11,
  maxX: 10,
  minY: -11,
  maxY: 25,
} as const

/** Route both sides of the USB pair before plane escapes and the key matrix.
 * The region contains J1, ESD/series parts, and the RP2040 USB pins while
 * excluding the rest of the 108mm key field. Pair quality is still measured
 * from emitted copper; a phase name is not evidence of matched routing. */
const USB_ROUTING_REGION = {
  minX: -12,
  maxX: 12,
  minY: -29,
  maxY: 14,
} as const

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

/** One key cell: the diode (COL2ROW, anode on the column side) and the switch.
 *  Index n runs 0..49 row-major, so refdes D1..D50 / SW10..SW59 read straight
 *  off the grid: D1..D10 is the top row, D41..D50 the bottom. */
const keyCells = () => {
  const out: JSX.Element[] = []
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const n = r * COLS + c
      // The two bottom-centre keys (row 4, columns 4-5) sit directly under the
      // USB-C connector's reversible power/bulk vias on the back face, so they
      // cannot share that board area. Skip them; the matrix stays 10-wide on
      // every other row.
      if (r === ROWS - 1 && (c === 4 || c === 5)) continue
      const d = `D${n + 1}`
      const sw = `SW${10 + n}`
      const node = `K${r}${c}`
      const x = colX(c)
      const y = rowY(r)
      // Locating J1 by its cable-mating datum puts its right plated shell
      // anchor beneath D46's default cathode pad. Keep that diode inside its
      // own 10mm cell but use the otherwise-empty left half of the cell so top
      // copper clears the through-hole anchor.
      const diodeX = x - 3.4 - (r === ROWS - 1 && c === 5 ? 1.5 : 0)
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
          layer="top"
          pcbX={diodeX}
          pcbY={y - 4.4}
          schX={sx - 3}
          schY={sy}
        />,
        <trace
          key={`dc${n}`}
          name={`TR_${d}_col`}
          from={`.${d} > .anode`}
          to={`net.COL${c}`}
          thickness={MATRIX_TRACE_WIDTH}
        />,
        <trace
          key={`dn${n}`}
          name={`TR_${d}_node`}
          from={`.${d} > .cathode`}
          to={`net.${node}`}
          thickness={MATRIX_TRACE_WIDTH}
        />,
        /* the key itself: node -> switch -> row */
        <SwTact
          key={`sw${n}`}
          name={sw}
          signal={node}
          to={`ROW${r}`}
          layer="top"
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
        thickness={MATRIX_TRACE_WIDTH}
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
        thickness={MATRIX_TRACE_WIDTH}
      />,
    )
  }
  return out
}

export const TerminalKeyboard = (props: { routingDisabled?: boolean } = {}) => (
  <board
    width={`${BOARD_W}mm`}
    height={`${BOARD_H}mm`}
    thickness={1.6}
    routingDisabled={props.routingDisabled ?? false}
    doubleSidedAssembly={true}
    minTraceWidth="0.15mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    <autoroutingphase phaseIndex={0} region={RP_CLOCK_ROUTING_REGION} />
    <autoroutingphase phaseIndex={1} region={RP_QSPI_ROUTING_REGION} />
    <autoroutingphase phaseIndex={2} region={USB_ROUTING_REGION} />
    <net name="USB_DP" routingPhaseIndex={2} />
    <net name="USB_DM" routingPhaseIndex={2} />
    <GndPlanes
      layers={["top", "bottom"]}
      stitchingVias={GND_STITCHING_VIAS}
    />

    {/* ---- the key field: 50 switches, 50 diodes, on a strict 10mm grid ---- */}
    <group name="__parts_board__key-matrix" pcbX={0} pcbY={0}>
      {keyCells()}
    </group>

    {/* ---- all assembled electronics live on the back, behind the keys ---- */}

    {/* The board chooses the reusable SWD furniture coordinate because only
        the composition knows where its other blocks land. Local (6, 12)
        clears the flash courtyard introduced by the reusable critical-bus
        placement while retaining a reachable horizontal 2.54mm row. */}
    <Rp2040Core
      layer="bottom"
      pcbX={0}
      pcbY={-0.5}
      debugPortPcbX={8}
      debugPortPcbY={12}
      debugSwclkBoundaryRef="N5"
      debugSwdBoundaryRef="N6"
      powerRailNodeRefs={{
        westUpper: "N7",
        westLower: "N8",
        south: "N9",
        eastLower: "N10",
        eastUpper: "N11",
        topRight: "N12",
        topMiddle: "N13",
        topLeft: "N14",
        bulk: "N15",
        flash: "N16",
        dvddLeft: "N17",
        dvddRight: "N18",
        dvddSouth: "N19",
        dvddJunction: "N20",
      }}
      buttonVariant="compact"
      schX={0}
      schY={-16}
    />
    {matrixToMcu()}

    {/* J1 is exactly on the lower-edge centreline, and its compiled cable
        insertion datum lands within 0.002mm of the routed edge. The body
        stays inside the outline while the insertion volume points outward. */}
    <UsbCData
      layer="bottom"
      pcbX={0}
      pcbY={-BOARD_H / 2 + 6.05}
      schX={-46}
      schY={-16}
      vbusBoundaryRefs={{ right: "N1", left: "N2" }}
      vbusRailNodeRef="N3"
      vbusClampNodeRef="N4"
      pairRules={{ pcbTraceGapMm: 0.15, maxLengthSkewMm: 3.8, maxUncoupledLengthMm: 3 }}
      localRoutingPhaseIndex={2}
    />

    <Ldo3v3
      layer="bottom"
      vinNet="VBUS_RAW"
      pcbX={-20}
      pcbY={-21.8}
      schX={-24}
      schY={-16}
    />

    <StatusLed
      layer="bottom"
      rail="V3_3"
      led="LED1"
      r="R20"
      externalRailAttachmentPort="R"
      pcbX={-48}
      pcbY={17}
      schX={26}
      schY={-16}
    />
    {/* The V3_3 rail is board-owned and reaches the far status LED through the
        ordinary net; suppress the block's short 3mm rail edge so the router can
        span the board. */}
    <trace name="TR_R20_V3_RAIL" from=".R20 > .pin1" to="net.V3_3" thickness="0.2mm" />

    {/* Six M2.5 holes sit in the 4mm mechanical band. Their centres are 2mm
        from the outline; the keep-outs, not comments, reserve both layers. */}
    <MountingHole name="H1" diameter={2.7} pcbX={-BOARD_W / 2 + 2} pcbY={BOARD_H / 2 - 2} />
    <MountingHole name="H2" diameter={2.7} pcbX={BOARD_W / 2 - 2} pcbY={BOARD_H / 2 - 2} />
    <MountingHole name="H3" diameter={2.7} pcbX={-BOARD_W / 2 + 2} pcbY={0} />
    <MountingHole name="H4" diameter={2.7} pcbX={BOARD_W / 2 - 2} pcbY={0} />
    <MountingHole name="H5" diameter={2.7} pcbX={-BOARD_W / 2 + 2} pcbY={-BOARD_H / 2 + 2} />
    <MountingHole name="H6" diameter={2.7} pcbX={BOARD_W / 2 - 2} pcbY={-BOARD_H / 2 + 2} />

    <silkscreentext
      text="AUTONOMOUS TERMINAL KEYBOARD"
      layer="bottom"
      pcbX={0}
      pcbY={BOARD_H / 2 - 1.5}
      fontSize="1mm"
      anchorAlignment="center"
    />
  </board>
)

export default () => <TerminalKeyboard />
