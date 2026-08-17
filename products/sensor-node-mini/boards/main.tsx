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
 *
 * Rail width — measured, not guessed (docs/architecture/rail-width.md):
 *   `<trace thickness="…">` reaches the router as a per-net `nominalTraceWidth`
 *   and one declaration anywhere on a net sets the whole net, so the rail is
 *   declared once on ldo-3v3, the block where both rails are born. Declaring
 *   blind has scrapped a board whole before (harness-puck, every rail at
 *   0.5mm: fab.ready true -> false, 0 -> 33 blocking, two nets shorted, all
 *   of it inside the RP2040's fanout), so `python -m circuitpy.netwidth
 *   products/sensor-node-mini --rails` was run against this placement first,
 *   with nothing declared:
 *     V5    ceiling 1.10mm (tightest pad U1.VBUS)     routed narrowest 0.2mm
 *     V3_3  ceiling 0.40mm (tightest pad U3.IOVDD6)   routed narrowest 0.2mm
 *   V5 is declared at 0.5mm — the jlcpcb profile's warn_power_trace_mm floor,
 *   less than half the measured ceiling. No MCU pin sits on it (USB VBUS, the
 *   ESD part, C1, the LDO input), so nothing on that net is placement-limited.
 *
 *   **V3_3 is deliberately left undeclared.** Its ceiling is exactly 0.4000mm
 *   — `2 x (0.400 pitch - 0.100 pad half-width - 0.100 clearance)` on the
 *   RP2040's QFN-56 — so 0.5mm is arithmetically impossible here, and 0.4mm
 *   was tried on this fleet and graded: on two boards it bought 0.025mm at
 *   the narrowest point, and on i2c-sensor-hub (2026-08-17) it produced
 *   fab.ready false with 3 blocking findings, including *Items shorting two
 *   nets (V3_3 and GND)* on a 1.5mm track beside U3's fanout. 32 of this
 *   board's pads are on that pin field. The rail's worst point is at a QFN
 *   pin either way, so the declaration buys almost no copper and can cost the
 *   whole board. Reverted there, and not repeated here.
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
    {/* V5 declared at 0.5mm here, once — see the header for the measured
        ceiling (1.10mm) and for why V3_3 is left alone. */}
    <Ldo3v3 vinThickness="0.5mm"
      pcbX={-25.87} pcbY={8.66} schX={-2} schY={4} />

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
