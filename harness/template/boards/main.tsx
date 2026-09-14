/**
 * <board name> — one line on what this board does.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-power, ldo-3v3, status-led
 * Rails: V5 (USB VBUS) -> V3_3 (ldo-3v3)
 * Envelope: 56 x 40 mm, 2 layers, 1.6mm — inside product.json's 60 x 40
 *
 * Placement comes from circuitlib.layout — measured block *boxes*, not
 * eyeballed sizes. A block's copper is not centred on its pcbX/pcbY (usb-c-power
 * sits 3.29mm above its origin), so use place_row() / min_board_for() and check
 * the result with board_fits() before building. USB-C sits on the bottom edge
 * because that block already faces y-; putting it on a side edge trips the
 * accessibility check.
 *
 * Every part below either comes from a golden block or is glue (a passive,
 * an LED, a connector). Nothing here was invented from a datasheet.
 */

import { UsbCPower } from "../blocks/usb-c-power/usb-c-power"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { StatusLed } from "../blocks/status-led/status-led"
import { MountingHole } from "../blocks/glue"

export default () => (
  <board
    width="56mm" height="40mm" thickness={1.6}
    /* Measured 2026-08-11: the same rp2040-core board is fab.ready=false with
       five blocking KiCad findings at the router's default effort and
       fab.ready=true with zero at "5x" — same design, same day, only this
       prop changed. The default effort is not good enough to order a board
       from, and the pipeline's own escalation cannot rescue it: that gate
       reads circuit.json, and these findings only exist after the KiCad
       cross-check runs. So every board declares the effort it needs. */
    autorouterEffortLevel="5x"
    minTraceWidth="0.2mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* power entry: USB-C -> V5 */}
    <UsbCPower pcbX={0} pcbY={-11} schX={-6} schY={0} />

    {/* logic rail: V5 -> V3_3 */}
    <Ldo3v3 pcbX={-19} pcbY={13} schX={0} schY={0} />

    {/* proof of life */}
    <StatusLed rail="V3_3" pcbX={20} pcbY={13} schX={6} schY={0} />

    {/* The enclosure needs something to hold: two holes on a known pitch.
        MountingHole, never a bare <hole> — the autorouter has no model of
        hole-to-copper clearance, so it will lay a track 0.1mm from a 3.2mm
        drill and the drill's own tolerance can cut it. The keepout that ships
        with MountingHole is the only thing the router reads. Same file has
        GndPour for the same reason on the pour side. */}
    <MountingHole name="H1" diameter={3.2} pcbX={-25} pcbY={-17} />
    <MountingHole name="H2" diameter={3.2} pcbX={25} pcbY={17} />

    {/* This board has no MCU, so it has no debug interface to land. Add an
        MCU block and it will: `board_plan().must_expose` names the nets, and
        `DebugPort` from blocks/glue is what lands them. An MCU whose SWCLK
        and SWD reach no pad is a board that cannot be halted or recovered
        once it is in a case — every block on it correct, the product
        useless. */}
  </board>
)
