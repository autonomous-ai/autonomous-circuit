/**
 * sensor-node-mini — small always-on environment node. Reads temperature,
 * humidity and pressure over I2C, shows life on one status LED, RP2040
 * brain, powered from USB-C.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-power, rp2040-core, ldo-3v3, sensor-bme280, i2c-bus,
 *   status-led
 * Rails: V5 (USB VBUS) -> V3_3 (ldo-3v3) -> MCU, sensor, LED, pull-ups
 * Envelope: 80.9 x 45.9 mm, 2 layers, 1.6mm — inside product.json's 84 x 48
 *
 * **usb-c-power, not usb-c-data** (first board in the fleet to use it): this
 * node is flashed once at manufacture time and then just runs — no host
 * ever needs the USB data pair after that. usb-c-power's own BLOCK.md says
 * plainly "usb-c-power alone leaves the MCU with no host connection," which
 * is true and is not a defect to route around with the data variant: a
 * device that never talks USB in the field should not carry a USB stack
 * for a one-time flash. The right fix is a debug interface the board
 * *does* carry — SWCLK/SWD/GND test points (`DebugPort` from `blocks/glue`)
 * wired straight to rp2040-core's exposed SWCLK/SWD nets, so the node is
 * programmed once over SWD (a Pico probe or any SWD adapter) before it ever
 * leaves the bench. Firmware updates after that are OTA or another SWD
 * session, not USB DFU — a real product constraint, recorded here rather
 * than discovered at bring-up.
 *
 * Placement is circuitlib.layout.place_board()'s output, unmodified for
 * every golden block: place_board(["usb-c-power","ldo-3v3","rp2040-core",
 * "sensor-bme280","i2c-bus","status-led"]) returned an 80.9 x 45.9mm board,
 * one inner row (ldo-3v3, rp2040-core, sensor-bme280, i2c-bus, status-led)
 * over usb-c-power on the south edge, two mounting holes in their own strip,
 * and zero warnings — no hand math needed this time (contrast
 * rgb-lamp-controller and desk-air-monitor, both hand-placed before the
 * planner fix). DebugPort is the one thing the planner does not know how to
 * size (it is board furniture, not a measured block) — it lands by hand in
 * the open strip between the row's right edge and the H2 mounting-hole
 * keepout (x ~ 30.0 to 35.4mm), rotated 90deg to stack its three pads
 * vertically in that narrow channel, well clear of rp2040-core's own box —
 * see blocks/glue's note on why a debug port inside the MCU's box shorts a
 * via into the crystal cluster.
 */

import { UsbCPower } from "../blocks/usb-c-power/usb-c-power"
import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { SensorBme280 } from "../blocks/sensor-bme280/sensor-bme280"
import { I2cBus } from "../blocks/i2c-bus/i2c-bus"
import { StatusLed } from "../blocks/status-led/status-led"
import { MountingHole, GndPour, DebugPort } from "../blocks/glue"

export default () => (
  <board
    width="80.9mm" height="45.9mm" thickness={1.6}
    autorouterEffortLevel="5x"
    minTraceWidth="0.2mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* power entry: USB-C, power-only (no D+/D-) -> V5 */}
    <UsbCPower pcbX={-1.75} pcbY={-17.78} schX={-10} schY={4} />

    {/* logic rail: V5 -> V3_3 */}
    <Ldo3v3 pcbX={-25.87} pcbY={8.66} schX={-2} schY={4} />

    {/* the brain */}
    <Rp2040Core pcbX={-5.95} pcbY={12.25} schX={0} schY={0} />

    {/* the sensor + its one pull-up pair */}
    <SensorBme280 pcbX={15.87} pcbY={6.74} schX={8} schY={0} />
    <I2cBus pcbX={22.43} pcbY={6.74} schX={12} schY={0} />

    {/* status LED — "it's alive" light, on V3_3 by default */}
    <StatusLed pcbX={28.62} pcbY={5.68} schX={16} schY={0} />

    {/* debug interface: this board's only way to get firmware onto it —
        SWCLK/SWD/GND, landed in open board space (see file header) */}
    <DebugPort pcbX={32.7} pcbY={0} pcbRotation={90} schX={0} schY={-10} />

    {/* ground return for the MCU and sensor; pour, don't route in
        0.2mm track */}
    <GndPour layer="bottom" />

    {/* mounting strip, clear of every footprint (from place_board's own
        hole plan) */}
    <MountingHole name="H1" diameter={3.2} pcbX={-37.25} pcbY={-19.75} />
    <MountingHole name="H2" diameter={3.2} pcbX={37.25} pcbY={19.75} />
  </board>
)
