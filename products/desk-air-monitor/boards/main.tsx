/**
 * desk-air-monitor — USB-C desk device reading temperature, humidity and
 * pressure on I2C, showing status on two LEDs, with a tactile button.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-data, rp2040-core, ldo-3v3, sensor-bme280, i2c-bus,
 *   status-led x2, sw-tact
 * Rails: V5 (USB VBUS) -> V3_3 (ldo-3v3) -> MCU, sensor, LEDs, pull-ups
 * Envelope: 61 x 64.8 mm, 2 layers, 1.6mm — inside product.json's 62 x 66
 *
 * Placement is from circuitlib.layout box math (place_row's algorithm run
 * by hand): two LEDs collapse to one key in place_board()'s dict, so this
 * board uses a hand-computed two-row-plus-debug-band layout instead of the
 * planner directly. Verified overlap-free with layout.overlap_warnings(),
 * board_fits() and price_tier_warnings() before this file was ever built.
 *
 * usb-c-data sits on the bottom edge, facing out (south) — connector
 * blocks must be accessible, not buried inline. rp2040-core + ldo-3v3 sit
 * in the top row; sensor-bme280 + i2c-bus + both status-leds + sw-tact sit
 * in the middle row; the debug port lands in its own reserved band between
 * the middle row and the connector — open board space, not inside
 * rp2040-core's own box, per the block's own warning about routing the
 * debug pair through the crystal cluster.
 *
 * Every part below either comes from a golden block or is glue (LEDs,
 * pull-ups, holes, pour, debug pads all ship inside their blocks or
 * blocks/glue). Nothing here was invented from a datasheet.
 */

import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { SensorBme280 } from "../blocks/sensor-bme280/sensor-bme280"
import { I2cBus } from "../blocks/i2c-bus/i2c-bus"
import { StatusLed } from "../blocks/status-led/status-led"
import { SwTact } from "../blocks/sw-tact/sw-tact"
import { MountingHole, GndPour, DebugPort } from "../blocks/glue"

export default () => (
  <board
    width="61mm" height="64.8mm" thickness={1.6}
    /* Measured 2026-08-11: the same rp2040-core board is fab.ready=false with
       five blocking KiCad findings at the router's default effort and
       fab.ready=true with zero at "5x" — same design, same day, only this
       prop changed. Every board declares the effort it needs.
       Build #1 on this board (7 blocks, "5x") came back fab.ready=false with
       exactly one blocking finding: a DVDD via clearance violation (0.0900mm
       required, 0.0722mm actual) entirely inside rp2040-core's own footprint
       — a net this board never touches. Escalating to "10x" per SKILL.md's
       own guidance ("go to 10x while the router is still missing its own
       clearances") for build #2. */
    autorouterEffortLevel="10x"
    minTraceWidth="0.2mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* power entry: USB-C + data pair -> V5, USB_DP/USB_DM to the MCU */}
    <UsbCData pcbX={-1.82} pcbY={-27.23} schX={-8} schY={8} />

    {/* logic rail: V5 -> V3_3 */}
    <Ldo3v3 pcbX={13.64} pcbY={18.11} schX={-2} schY={4} />

    {/* the brain */}
    <Rp2040Core pcbX={-8.57} pcbY={21.7} schX={0} schY={0} />

    {/* the sensor + its one pull-up pair */}
    <SensorBme280 pcbX={-12.12} pcbY={-0.24} schX={0} schY={-6} />
    <I2cBus pcbX={-5.56} pcbY={-0.24} schX={4} schY={-6} />

    {/* status LEDs, board state on one and sensor-alert on the other —
        firmware's call which is which; both wired to V3_3 by default so
        either can be repointed at a GPIO net without a board change */}
    <StatusLed rail="V3_3" pcbX={0.64} pcbY={-1.3} schX={8} schY={-6} />
    <StatusLed
      rail="V3_3"
      led="LED2"
      r="R21"
      pcbX={5.48}
      pcbY={-1.3}
      schX={10}
      schY={-6}
    />

    {/* user button */}
    <SwTact pcbX={12.4} pcbY={-0.24} schX={12} schY={-6} />

    {/* debug interface: open board space between the middle row and the
        USB connector, not inside rp2040-core's own box — see
        blocks/glue's DebugPort note on why that placement shorts a via
        into the QFN pad field */}
    <DebugPort pcbX={0} pcbY={-6.96} schX={0} schY={-10} />

    {/* ground return for the USB differential pair and the MCU's own
        return path — pour it, don't route it in 0.2mm track */}
    <GndPour layer="bottom" />

    {/* mounting strip, clear of every footprint */}
    <MountingHole name="H1" diameter={3.2} pcbX={-27.3} pcbY={-29.2} />
    <MountingHole name="H2" diameter={3.2} pcbX={27.3} pcbY={29.2} />
  </board>
)
