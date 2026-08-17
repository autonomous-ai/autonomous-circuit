/**
 * macropad-6 — a six-key USB-C macropad: 3x2 grid of tactile switches on
 * 19.05mm keycap pitch, RP2040 brain, one status LED.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-data, rp2040-core, ldo-3v3, sw-tact (x6), status-led
 * Glue:        blocks/glue — MountingHole (x4), DebugPort, GndPour
 * Rails:       V5 (USB VBUS, usb-c-data) -> V3_3 (ldo-3v3) -> rp2040-core
 *
 * ldo-3v3 was not in the block list the brief named (usb-c-data,
 * rp2040-core, sw-tact, status-led) — it is added here anyway.
 * rp2040-core.requires includes net.V3_3, and ldo-3v3 is the only golden
 * block that provides it (circuitlib.blocks: CAPABILITY_INDEX["rail-3v3"] =
 * ("ldo-3v3",)); board_plan() would pull it in for the same reason. Leaving
 * it out is not "fewer blocks", it is an MCU with no logic supply. Composing
 * the block the MCU actually needs is not inventing a circuit; hand-drawing
 * a regulator from a datasheet would have been.
 *
 * Envelope: 66 x 84 mm, 2 layers, 1.6mm — inside product.json's 66 x 84,
 * and inside the 100 x 100mm JLCPCB $2-for-5 sample tier with room to spare.
 *
 * Six keys wired straight to GPIOs — no matrix, no diodes. A diode matrix
 * earns its keep past ~20 keys (it trades N+M GPIOs for N*M keys); six keys
 * on an RP2040 with 30 GPIOs is not that trade, so each switch gets its own
 * GPIO with the MCU's internal pull-up, exactly as sw-tact/BLOCK.md assumes.
 *
 * Pin allocation: GPIO0..GPIO5 = KEY0..KEY5 (inputs, internal pull-ups,
 * active-low, switch shorts to GND). GPIO6..GPIO29 free for firmware use
 * (a rotary encoder, RGB underglow, etc.) — not part of this board.
 *
 * Key layout, row-major, top row first (matches the printed legend 1-2-3 /
 * 4-5-6 and the GPIO order):
 *   KEY0 KEY1 KEY2   (top row,    y = KEY_CY + 9.525)
 *   KEY3 KEY4 KEY5   (bottom row, y = KEY_CY - 9.525)
 *   columns x = -19.05, 0, +19.05mm  (19.05mm pitch both axes — standard
 *   mechanical keycap spacing, so a real 1u cap fits every position).
 *
 * Placement is computed, not eyeballed, from circuitlib.layout's measured
 * block boxes (each block's copper is not centred on its pcbX/pcbY — e.g.
 * ldo-3v3 sits from -6.7 to +2.85mm around its own origin). The bottom
 * cluster (usb-c-data, rp2040-core, status-led, ldo-3v3) is exactly the
 * output of `layout.place_board(["usb-c-data","rp2040-core","status-led",
 * "ldo-3v3"])`, which came back with `warnings: []` at 65.8 x 51.4mm; this
 * file uses those same relative positions, uniformly shifted +14.3mm in y to
 * make room for the key field above (place_board's own two default holes
 * are not used — this board's mounting-hole strategy differs, see below).
 * On top of that validated cluster:
 *   - the 3x2 key field sits 6mm above rp2040-core's box (the tallest block
 *     in the row), with a 4mm router halo (ROUTER_HALO_MM) above it — key
 *     field spans x=[-22.55,+22.55], y=[+11.4,+34.9] on this board.
 *   - the SWD debug port sits 2mm clear of rp2040-core's own box, in open
 *     board space rather than inside it — three pads inside that box
 *     measured a via shorted into the QFN pad field on 2026-08-11 — rotated
 *     90 degrees so its pad span (5.08mm) runs in y, where the gap between
 *     rp2040-core and the board's left halo boundary (only 4.52mm in x) has
 *     room for the pads' 1mm diameter but not for them laid sideways.
 *   - four M2.5 mounting holes: two flanking the USB shoulder at the bottom,
 *     two above the key field at the top, each measured >=1.85mm clear of
 *     the nearest switch pad and >=2.6mm clear of the board edge before this
 *     file was written (the same clearance formula `_hole_clearance_
 *     warnings()` uses), not discovered after a failed build.
 *
 * Why 6mm, not the 2mm BLOCK_GAP_MM floor: the first full build (gap=2mm,
 * 66x80mm) came back fab.ready=false with 64 KiCad errors, every one of them
 * *inside* rp2040-core's own U3/U4 QSPI-flash cluster — nothing to do with
 * the keys, USB, LED or LDO. rp2040-core's own decoupling row (C4-C8) sits
 * at group-relative y=+6, i.e. only 2.8mm short of the key field's bottom
 * edge at the 2mm gap — so six GPIO escape traces plus the SWD pair from
 * DebugPort all had to funnel through the same narrow corridor as the
 * block's own via-dense decoupling. BLOCK_GAP_MM=2mm is sized to keep
 * *courtyards* from touching; it says nothing about whether there is room
 * left over to *route through* that gap once a board adds its own traffic
 * on top — which is exactly what happened here. Confirmed non-placement:
 * re-running the identical file (same effort level) gave 11 errors, then 64,
 * then 64 again — the router's solution in that corridor is unstable run to
 * run, and a wider corridor is the only lever available from this file (see
 * work/ee-feedback/macropad-6.md for the full sequence and the "10x" attempt
 * that was tried and reverted).
 *
 * Every part below either comes from a golden block or is glue (silkscreen,
 * mounting holes). Nothing here was invented from a datasheet.
 */

import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { StatusLed } from "../blocks/status-led/status-led"
import { SwTact } from "../blocks/sw-tact/sw-tact"
import { GndPour, MountingHole, DebugPort } from "../blocks/glue"

const BOARD_W = 66
const BOARD_H = 84

/** Standard mechanical-keycap pitch. */
const PITCH = 19.05

/** Measured placements — see the header for how each was derived. */
const USB_X = -1.82
const USB_Y = -36.83
const RP_X = -10.98
const RP_Y = -1.3
const LED_X = 8.47
const LED_Y = -7.87
const LDO_X = 16.07
const LDO_Y = -4.89
const DEBUG_X = -26.7
const DEBUG_Y = -1.3
/** Vertical centre of the 2x3 key field. */
const KEY_CY = 23.15

const KEY_LEGEND = ["1", "2", "3", "4", "5", "6"]

/** One key cell: switch only (no diode — direct GPIO wiring). Index n runs
 *  0..5 row-major (top row first), so GPIO<n> / KEY<n> / SW<10+n> all read
 *  straight off the grid. */
const keyCells = () => {
  const out: JSX.Element[] = []
  for (let r = 0; r < 2; r++) {
    for (let c = 0; c < 3; c++) {
      const n = r * 3 + c
      const sw = `SW${10 + n}`
      const net = `KEY${n}`
      const x = (c - 1) * PITCH
      const y = KEY_CY + (0.5 - r) * PITCH
      // schematic: same 3x2 shape, above the MCU block, so the drawing reads
      // as one page in the same layout as the board.
      const sx = -10 + 10 * c
      const sy = 20 - 10 * r
      out.push(
        <SwTact
          key={`sw${n}`}
          name={sw}
          signal={net}
          pcbX={x}
          pcbY={y}
          schX={sx}
          schY={sy}
        />,
        // sw-tact only traces pin1 (signal) and pin4 (GND); pin3 is the
        // switch's own internal partner of pin4 (internallyConnectedPins:
        // [[pin1,pin2],[pin3,pin4]]), physically the same GND node, but the
        // block leaves it electrically unrouted on the PCB. Left that way, a
        // GndPour on this board floods right up to that floating pad and the
        // fast check came back with a drc_violation:shorting_items error
        // against "" and GND on 3 of the 6 switches (SW11, SW12, SW14 —
        // which ones depends on local pour geometry, not on anything wrong
        // with those particular keys). Naming pin3 onto the net it is
        // already tied to inside the switch package removes the floating
        // copper without changing what the switch does — not a block edit,
        // just declaring a net the block's own doc already claims is true.
        // (pin2, the signal-side partner, is left unrouted: nothing pours
        // around a signal net, so a bare pin2 pad never triggered this class
        // of error, and wiring it on the first attempt was what introduced a
        // new V3_3/KEY0 short elsewhere on the board — reverted.)
        <trace
          key={`tr${n}_p3`}
          name={`TR_${net}_p3`}
          from={`.${sw} > .pin3`}
          to="net.GND"
        />,
        <trace
          key={`tr${n}`}
          name={`TR_${net}`}
          from={`.U3 > .GPIO${n}`}
          to={`net.${net}`}
        />,
        <silkscreentext
          key={`lg${n}`}
          text={KEY_LEGEND[n]}
          layer="top"
          pcbX={x}
          pcbY={y + 5.5}
          fontSize="1.4mm"
          anchorAlignment="center"
        />,
      )
    }
  }
  return out
}

export default () => (
  <board
    width={`${BOARD_W}mm`}
    height={`${BOARD_H}mm`}
    thickness={1.6}
    /* Measured 2026-08-11: the same rp2040-core board is fab.ready=false
       with five blocking KiCad findings at the router's default effort and
       fab.ready=true with zero at "5x" — same design, only this prop
       changed. Declared, not left to the pipeline's own escalation, because
       that escalation reads circuit.json and these findings only exist
       after the KiCad cross-check has already run.
       Tried "10x" on this board (SKILL.md: "go to 10x while the router is
       still missing its own clearances") to chase a DVDD via clearance
       error and a U3 hole clearance error seen at "5x". It made the same
       class of finding worse, not better: the RP2040/flash QSPI cluster
       went from 2 clearance-class errors to a dozen (new pcb_trace_error /
       pcb_pad_trace_clearance_error entries inside U3/U4, plus a new
       USB_DP/USB_DM via short that was not present at "5x"). Reverted to
       the "5x" floor — the DVDD/U3 clearance findings are carried as an
       accepted, reported defect rather than chased further (see
       work/ee-feedback/macropad-6.md). */
    autorouterEffortLevel="5x"
    minTraceWidth="0.2mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* ---- the key field: 6 switches, direct GPIO, no matrix, no diodes ---- */}
    {keyCells()}

    {/* The brain: RP2040 minimal system, native USB HID. */}
    <Rp2040Core pcbX={RP_X} pcbY={RP_Y} schX={0} schY={-10} />

    {/* Power entry + USB 2.0 data, bottom edge, facing out. */}
    <UsbCData pcbX={USB_X} pcbY={USB_Y} schX={-26} schY={-10} />

    {/* Logic rail: V5 -> V3_3, feeding rp2040-core. */}
    <Ldo3v3 pcbX={LDO_X} pcbY={LDO_Y} schX={20} schY={-16} />

    {/* proof of life */}
    <StatusLed rail="V3_3" pcbX={LED_X} pcbY={LED_Y} schX={20} schY={-4} />

    {/* SWD access, left of the MCU in open board space — not inside
        rp2040-core's own footprint. Rotated 90 degrees: the gap between
        rp2040-core and the board's left halo boundary is only 4.52mm wide,
        enough for the pads' 1mm copper stacked in y but not for the pads'
        5.08mm span laid out in x. */}
    <DebugPort
      pcbX={DEBUG_X}
      pcbY={DEBUG_Y}
      pcbRotation={90}
      schX={-26}
      schY={0}
    />

    {/* The enclosure needs something to hold: four M2.5 holes, two flanking
        the USB shoulder, two above the key field. Each keeps >=1.85mm clear
        of the nearest switch pad and >=2.6mm clear of the board edge.
        MountingHole, never a bare <hole> — the autorouter has no
        hole-to-copper model, so it will lay a track 0.1mm from the drill and
        the drill's own tolerance can cut it; MountingHole's keepout is what
        the router actually reads. */}
    <MountingHole name="H1" diameter={2.7} pcbX={-22} pcbY={-36} />
    <MountingHole name="H2" diameter={2.7} pcbX={22} pcbY={-36} />
    <MountingHole name="H3" diameter={2.7} pcbX={-22} pcbY={38} />
    <MountingHole name="H4" diameter={2.7} pcbX={22} pcbY={38} />

    <silkscreentext
      text="MACROPAD-6"
      layer="top"
      pcbX={0}
      pcbY={-40}
      fontSize="1.4mm"
      anchorAlignment="center"
    />

    {/* Ground plane, bottom layer. Every margin set explicitly — see
        blocks/glue's GndPour comment: a bare <copperpour> with only
        cutoutMargin measured straight into KiCad DRC on terminal-keyboard
        (2026-08-16), zone clearance down to 0.0000mm at one drill. */}
    <GndPour layer="bottom" />
  </board>
)
