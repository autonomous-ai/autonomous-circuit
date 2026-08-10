/**
 * <board name> — one line on what this board does.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-power, ldo-3v3, status-led
 * Rails: V5 (USB VBUS) -> V3_3 (ldo-3v3)
 * Envelope: 60 x 40 mm, 2 layers, 1.6mm — matches product.json
 *
 * Every part below either comes from a golden block or is glue (a passive,
 * an LED, a connector). Nothing here was invented from a datasheet.
 */

import { UsbCPower } from "../blocks/usb-c-power/usb-c-power"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { StatusLed } from "../blocks/status-led/status-led"

export default () => (
  <board width="40mm" height="30mm" thickness={1.6}>
    {/* power entry: USB-C -> V5 */}
    <UsbCPower pcbX={-14} pcbY={0} schX={-6} schY={0} />

    {/* logic rail: V5 -> V3_3 */}
    <Ldo3v3 pcbX={0} pcbY={6} schX={0} schY={0} />

    {/* proof of life */}
    <StatusLed rail="V3_3" pcbX={12} pcbY={-6} schX={6} schY={0} />

    {/* the enclosure needs something to hold: two holes on a known pitch */}
    <hole name="H1" diameter="3.2mm" pcbX={-17} pcbY={-12} />
    <hole name="H2" diameter="3.2mm" pcbX={17} pcbY={12} />
  </board>
)
