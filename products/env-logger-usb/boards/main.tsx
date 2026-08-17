/**
 * env-logger-usb — USB-C environment logger. Streams temperature, humidity
 * and pressure from a BME280 over USB serial; three LEDs show power,
 * logging and error state; a start-stop button; a bench breakout header
 * exposes the I2C bus and the SWD pins for anyone probing the board.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-data, ldo-3v3, rp2040-core, sensor-bme280, i2c-bus,
 * status-led x3, sw-tact, glue.tsx (PadHeader, MountingHole, GndPour)
 * Rails: V5 (USB VBUS) -> ldo-3v3 -> V3_3 (logic, sensor, bus pull-ups,
 * PWR LED)
 * Envelope: 99.6 x 64 mm, 2 layers, 1.6mm — inside product.json's 99.6 x 64
 *
 * Placement: circuitlib.layout.place_board() on
 * ["usb-c-data","ldo-3v3","rp2040-core","sensor-bme280","i2c-bus",
 * "status-led"x3,"sw-tact"] came back 99.6 x 51.4mm, zero warnings, one row
 * (place_board's own dict — status-led#2/#3 kept apart automatically,
 * no hand-copied-key bug this time). Every placement below except
 * usb-c-data's pcbY and the board height is that plan verbatim.
 *
 * The plan's own 51.4mm height leaves rp2040-core's crystal/BOOTSEL/RUN
 * cluster (the block's box reaches pcbY=-2.72 at this row) only 2.06mm
 * above usb-c-data's inward edge — not enough room for the breakout header
 * PadHeader needs (glue.tsx: land it in open board space, not inside the
 * MCU's own box, and 2mm+ clear of neighbours). Grew the board to 64mm tall
 * (place_board has no "just add height" knob — it derives height from
 * content) and re-seated usb-c-data at the new bottom edge using
 * place_board's own edge-placement formula evaluated at height=64
 * (box_center_y = -height/2 + EDGE_MARGIN_MM + edge_h/2, then subtract the
 * block's origin_offset — same arithmetic place_board runs internally).
 * That opened an 8.36mm gap (row floor -2.72 to connector top -11.08); the
 * header sits at its centre, pcbY=-6.9, with ~4.2mm clear on each side —
 * comfortably past the 2mm floor every other gap in this library uses.
 * Mounting holes recomputed at the new height with place_board's own
 * hole-inset formula (unchanged width, so H1/H2 only move in y).
 * Preflight (routingDisabled compile + fastcheck) confirmed zero placement
 * warnings before this ever reached a real build.
 *
 * Pin allocation (RP2040, U3)
 *   GPIO4   SDA          i2c-bus / sensor-bme280 default net
 *   GPIO5   SCL          i2c-bus / sensor-bme280 default net
 *   GPIO2   LED_LOG      logging-active LED (LED2), firmware-driven
 *   GPIO3   LED_ERR      error LED (LED3), firmware-driven
 *   GPIO6   BTN_STARTSTOP start/stop button, active low, internal pull-up
 *   USB_DP/USB_DM        to usb-c-data (explicit trace off the chip pins,
 *                        per rp2040-core's own contract and the
 *                        hydrate-coaster reference board)
 *   SWCLK/SWD            landed on the breakout header, not left dangling
 *   LED1 (PWR) hard-wired to V3_3 — proof of power the firmware can't lie
 *   about, same convention as every other board in the fleet.
 *
 * Bench breakout: PadHeader (blocks/glue) carrying SDA, SCL, SWCLK, SWD,
 * GND — one header, so a probe on the bench can tap either the I2C bus or
 * the SWD pins without hunting for two headers. Bare 1mm pads at 2.54mm
 * pitch, same convention as every debug port in the fleet: no drills, no
 * keepouts, solders a header when someone wants one. Refdes TP1-TP5 (the
 * BOM gate excuses bare copper from needing an LCSC number only when the
 * refdes prefix is TP/FID/MH/H with a digit — i2c-sensor-hub hit this as
 * part_not_orderable with a "PAD_" prefix on 2026-08-17; PadHeader's own
 * "TP" default sidesteps it).
 *
 * Every part below is a golden block or glue (mounting holes, the ground
 * pour, and the breakout pads). Nothing here was invented from a
 * datasheet.
 */

import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { SensorBme280 } from "../blocks/sensor-bme280/sensor-bme280"
import { I2cBus } from "../blocks/i2c-bus/i2c-bus"
import { StatusLed } from "../blocks/status-led/status-led"
import { SwTact } from "../blocks/sw-tact/sw-tact"
import { MountingHole, GndPour, PadHeader } from "../blocks/glue"

export default () => (
  <board
    width="99.6mm"
    height="64mm"
    thickness={1.6}
    /* SKILL.md floor. Starting here per the assignment brief rather than at
       the 10x this board's usb-c-data + rp2040-core pairing needed on
       i2c-sensor-hub — see the build note this file's sibling feedback
       doc carries once the gauntlet has actually run. */
    autorouterEffortLevel="5x"
    minTraceWidth="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* ---- power entry: USB-C on the bottom edge, 5V + the USB 2.0 pair ----
        pcbY re-seated for the grown board (see file header) —
        place_board's own edge formula at height=64, not the 51.4mm value
        the raw plan returned. */}
    <UsbCData pcbX={-1.82} pcbY={-26.83} schX={-46} schY={0} />

    {/* ---- logic rail: V5 -> V3_3 ------------------------------------------ */}
    <Ldo3v3 pcbX={-35.2} pcbY={11.41} schX={-24} schY={0} />

    {/* ---- the brain --------------------------------------------------------
        place_board's placement verbatim. */}
    <Rp2040Core pcbX={-15.28} pcbY={15.0} schX={0} schY={0} />
    <trace name="TR_USB_DP" from=".U3 > .USB_DP" to="net.USB_DP" />
    <trace name="TR_USB_DM" from=".U3 > .USB_DM" to="net.USB_DM" />

    {/* ---- I2C bus: exactly one pull-up pair, then the sensor -------------- */}
    <I2cBus pcbX={13.09} pcbY={9.49} schX={20} schY={8} />
    <SensorBme280 pcbX={6.53} pcbY={9.49} schX={20} schY={0} />
    <trace name="TR_SDA" from=".U3 > .GPIO4" to="net.SDA" />
    <trace name="TR_SCL" from=".U3 > .GPIO5" to="net.SCL" />

    {/* ---- indicators --------------------------------------------------------
        LED1 (PWR) hard-wired to the rail: proof of power the firmware
        cannot lie about. LED2 (LOG) and LED3 (ERR) are firmware-driven. */}
    <StatusLed led="LED1" r="R20" rail="V3_3" pcbX={19.29} pcbY={8.43} schX={0} schY={-14} />
    <StatusLed led="LED2" r="R21" rail="LED_LOG" pcbX={24.12} pcbY={8.43} schX={4} schY={-14} />
    <StatusLed led="LED3" r="R22" rail="LED_ERR" pcbX={28.97} pcbY={8.43} schX={8} schY={-14} />
    <trace name="TR_LED_LOG" from=".U3 > .GPIO2" to="net.LED_LOG" />
    <trace name="TR_LED_ERR" from=".U3 > .GPIO3" to="net.LED_ERR" />

    {/* ---- start/stop button, active low into GPIO6 with the internal pull-up ---- */}
    <SwTact name="SW1" signal="BTN_STARTSTOP" pcbX={35.89} pcbY={9.49} schX={30} schY={-8} />
    <trace name="TR_BTN_STARTSTOP" from=".U3 > .GPIO6" to="net.BTN_STARTSTOP" />

    {/* ---- bench breakout: I2C bus + SWD on one header ----------------------
        Open board space between the row and the connector — not inside
        rp2040-core's own box, per glue.tsx's note on the crystal cluster
        (see file header for the height/placement math that opened this
        gap). One PadHeader instead of a separate DebugPort + bare pads:
        the brief asks for a bench header that taps "the I2C bus or the
        SWD pins", and PadHeader takes an arbitrary net list, so it is one
        header rather than two components doing overlapping jobs. */}
    <PadHeader
      prefix="TP"
      nets={["SDA", "SCL", "SWCLK", "SWD", "GND"]}
      labels={["SDA", "SCL", "CLK", "DIO", "GND"]}
      pcbX={0}
      pcbY={-6.9}
      schX={0}
      schY={14}
    />

    {/* ---- ground pour, bottom layer -----------------------------------------
        glue.tsx: "pour ground on any two-layer board with a differential
        pair or an MCU" — this board has both. */}
    <GndPour layer="bottom" />

    {/* ---- mechanics: M3 in the reserved corner strip, recomputed for the
        grown (64mm) board height — place_board's own hole-inset formula;
        width is unchanged so only the y coordinates moved. */}
    <MountingHole name="H1" diameter={3.2} pcbX={-46.6} pcbY={-28.8} />
    <MountingHole name="H2" diameter={3.2} pcbX={46.6} pcbY={28.8} />

    {/* ---- silkscreen -------------------------------------------------------- */}
    <silkscreentext text="ENV LOGGER USB" pcbX={0} pcbY={30} fontSize={2} />
    <silkscreentext text="BME280" pcbX={6.53} pcbY={13.5} fontSize={1.2} />
    <silkscreentext text="I2C BUS" pcbX={13.09} pcbY={12} fontSize={1} />
    <silkscreentext text="PWR" pcbX={19.29} pcbY={14.5} fontSize={1.2} />
    <silkscreentext text="LOG" pcbX={24.12} pcbY={14.5} fontSize={1.2} />
    <silkscreentext text="ERR" pcbX={28.97} pcbY={14.5} fontSize={1.2} />
    <silkscreentext text="START/STOP" pcbX={35.89} pcbY={13.9} fontSize={1} />
    <silkscreentext text="USB-C 5V" pcbX={-1.82} pcbY={-21} fontSize={1.2} />
  </board>
)
