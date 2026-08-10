/**
 * <board name> — one line on what this board does.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-power, ldo-3v3, status-led
 * Rails: V5 (USB VBUS) -> V3_3 (ldo-3v3)
 * Envelope: 56 x 40 mm, 2 layers, 1.6mm — inside product.json's 60 x 40
 *
 * Placement comes from circuitlib.layout.extent() — measured block footprints,
 * not eyeballed. USB-C sits on the bottom edge because that block already
 * faces y-; putting it on a side edge trips the accessibility check.
 *
 * Every part below either comes from a golden block or is glue (a passive,
 * an LED, a connector). Nothing here was invented from a datasheet.
 */

import { UsbCPower } from "../blocks/usb-c-power/usb-c-power"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { StatusLed } from "../blocks/status-led/status-led"

export default () => (
  <board
    width="56mm" height="40mm" thickness={1.6}
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

    {/* the enclosure needs something to hold: two holes on a known pitch */}
    <hole name="H1" diameter="3.2mm" pcbX={-25} pcbY={-17} />
    <hole name="H2" diameter="3.2mm" pcbX={25} pcbY={17} />
  </board>
)
