/**
 * dual-sensor-node — indoor/outdoor differential air node: two BME280s on
 * one I2C bus (one on-board, one meant for a short pigtail out through an
 * enclosure vent) so firmware can compare inside vs outside air.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-data, ldo-3v3, rp2040-core, i2c-bus, sensor-bme280 x2,
 * status-led x2, sw-tact, glue.tsx (MountingHole, GndPour, DebugPort,
 * PadHeader).
 * Rails: V5 (USB VBUS) -> ldo-3v3 -> V3_3 (logic, both sensors, bus pull-ups)
 * Envelope: 95.3 x 68 mm, 2 layers, 1.6mm — inside product.json's 96 x 68.
 *
 * Rail width — measured, not guessed (docs/architecture/rail-width.md):
 *   `<trace thickness="…">` reaches the router as a per-net `nominalTraceWidth`
 *   and one declaration anywhere on a net sets the whole net, so both rails are
 *   declared once on ldo-3v3, the block where both are born. Declaring blind
 *   has scrapped a board whole before (harness-puck, every rail at 0.5mm:
 *   fab.ready true -> false, 0 -> 33 blocking, two nets shorted, all of it in
 *   the RP2040's fanout), so `python -m circuitpy.netwidth
 *   products/dual-sensor-node --rails` was run against this placement first,
 *   with nothing declared:
 *     V5    ceiling 1.10mm (tightest pad U1.VBUS)     routed narrowest 0.15mm
 *     V3_3  ceiling 0.40mm (tightest pad U3.IOVDD6)   routed narrowest 0.15mm
 *   No MCU pin sits on V5 (USB VBUS, the ESD part, C1, the LDO input), so it
 *   takes 0.5mm — the jlcpcb profile's warn_power_trace_mm floor — with more
 *   than 2x margin on the measured ceiling. V3_3 lands on the RP2040's QFN-56
 *   and cannot have 0.5mm at any effort level: 0.400mm pitch with 0.200mm pads
 *   leaves `2 x (0.400 - 0.100 - 0.100) = 0.400mm`, and no placement change
 *   beats arithmetic. So V3_3 is declared at its own measured ceiling, 0.4mm.
 *   The profile still prefers 0.5mm on a power net, so `dfm_power_trace_width`
 *   stays on V3_3 by design — the geometry is the answer, not the preference.
 *   This board carries 39 V3_3 pads, the most in the fleet.
 *
 * TWO SENSORS ON ONE BUS — the point of this build
 *   `sensor-bme280` hardwires SDO to GND (address 0x76) with no prop to move
 *   it. BLOCK.md/REVIEW.md both say the second sensor needs "SDO at VDDIO
 *   (0x77), which is a block variant, not a board-level trace" — but that
 *   variant does not exist anywhere in packages/golden-blocks or any
 *   product's copied blocks/ (checked before writing this file). Without it,
 *   two on-board BME280s on the same bus is a straight address collision.
 *   Fixed here by adding one prop, `addr1`, to this project's own copy of
 *   blocks/sensor-bme280/sensor-bme280.tsx (see the DEVIATION comment
 *   there) — exactly the variant the docs already prescribe, nothing
 *   invented. Filed upstream in work/ee-feedback/dual-sensor-node.md.
 *
 *   Placement itself is the other half of the point: until 2026-08-17,
 *   `circuitlib.layout.place_row` keyed its output dict by bare block id, so
 *   a second `sensor-bme280` (or `status-led`) silently overwrote the first
 *   in the placement plan — every downstream check (overlap, board-fit,
 *   hole clearance) agreed with a plan that was one part short, because none
 *   of them ever saw the dropped instance. Fixed the same day via
 *   `INSTANCE_SEP`/`base_id()` (`status-led#2`, `sensor-bme280#2`). This
 *   board is composed with TWO sensor-bme280 instances AND two status-led
 *   instances in the same `place_board()` call specifically to exercise
 *   that fix on a block it had not yet been proven against — see
 *   work/ee-feedback/dual-sensor-node.md for the result.
 *
 * Placement intent
 *   usb-c-data / ldo-3v3 / rp2040-core / i2c-bus / sensor-bme280 x2 /
 *   status-led x2 / sw-tact placements below come straight from
 *   circuitlib.layout.place_board() on that exact 9-entry block list —
 *   zero overlap/fit warnings at 95.3 x 57.8mm. The board here is grown to
 *   95.3 x 68mm (rect centred at origin, so height grows symmetrically) to
 *   open a clear band above the RP2040/sensor row for the debug port and
 *   the bus-breakout pigtail header — furniture place_board has no slot
 *   for. usb-c-data is re-seated to hug the new, farther-away bottom edge
 *   (place_board's own edge-block formula, evaluated at the new height) so
 *   the receptacle mouth still sits at the board edge instead of floating
 *   in the extra headroom — same move i2c-sensor-hub made for the same
 *   reason.
 *
 * Pin allocation (RP2040, U3)
 *   GPIO4   I2C_SDA      to i2c-bus / both sensor-bme280 (shared SDA net)
 *   GPIO5   I2C_SCL      to i2c-bus / both sensor-bme280 (shared SCL net)
 *   GPIO2   LED_ACT      activity LED (LED2), firmware-driven
 *   GPIO3   BTN_USER     user button, active low, internal pull-up
 *   USB_DP/USB_DM        to usb-c-data (explicit trace off the chip pins,
 *                        per rp2040-core's own contract)
 *   SWCLK/SWD            landed on DebugPort, not left dangling
 *   LED1 (PWR) is hard-wired to V3_3 — proof of power the firmware can't lie
 *   about, same convention as i2c-sensor-hub/hydrate-coaster.
 *
 * Sensor allocation
 *   U5/C18/C19 — indoor sensor, on-board, address 0x76 (default: SDO->GND).
 *   U6/C20/C21 — outdoor sensor, address 0x77 (addr1: SDO->V3_3), placed
 *     next to U5 today (both physically populate this PCB — no way to model
 *     a second, separate remote board in one tscircuit source file). A
 *     builder who wants the outdoor sensor truly outside the enclosure can
 *     leave U6 unpopulated and wire a remote BME280 breakout to the
 *     PadHeader pigtail pads instead — same bus, same address story either
 *     way, so the schematic is correct under both assembly choices.
 *
 * Every part below is a golden block or glue (mounting holes, ground pour,
 * debug port, and the bus-breakout pad header are the only glue). Nothing
 * was invented from a datasheet.
 */

import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { I2cBus } from "../blocks/i2c-bus/i2c-bus"
import { SensorBme280 } from "../blocks/sensor-bme280/sensor-bme280"
import { StatusLed } from "../blocks/status-led/status-led"
import { SwTact } from "../blocks/sw-tact/sw-tact"
import { MountingHole, DebugPort, PadHeader, GndPour } from "../blocks/glue"

export default () => (
  <board
    width="95.3mm"
    height="68mm"
    thickness={1.6}
    /* 5x is the SKILL.md floor; this board pairs usb-c-data with
       rp2040-core, the exact combination i2c-sensor-hub and hydrate-coaster
       both needed 10x to clear on the USB-C data-pair fanout even after a
       favorable rotation. Declaring 10x up front rather than discovering
       the same shortfall on a second build. */
    autorouterEffortLevel="10x"
    minTraceWidth="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* ---- power entry: USB-C on the bottom edge, 5V + the USB 2.0 pair ---- */}
    <UsbCData pcbX={-1.82} pcbY={-28.83} schX={-46} schY={0} />

    {/* ---- logic rail: V5 -> V3_3 ------------------------------------------ */}
    {/* both rail widths declared here, once — see the header's measured
        ceilings. V5 0.5mm (ceiling 1.10), V3_3 0.4mm (ceiling 0.40, the
        RP2040 QFN-56's own arithmetic). */}
    <Ldo3v3 vinThickness="0.5mm" voutThickness="0.4mm"
      pcbX={-33.07} pcbY={14.61} schX={-24} schY={0} />

    {/* ---- the brain --------------------------------------------------------- */}
    <Rp2040Core pcbX={-13.15} pcbY={18.2} schX={0} schY={0} />
    <trace name="TR_USB_DP" from=".U3 > .USB_DP" to="net.USB_DP" />
    <trace name="TR_USB_DM" from=".U3 > .USB_DM" to="net.USB_DM" />

    {/* ---- I2C bus: exactly one pull-up pair, then both sensors -------------
        place_board() row order: i2c-bus, sensor-bme280, sensor-bme280#2. */}
    <I2cBus pcbX={5.66} pcbY={12.69} schX={20} schY={10} />

    {/* indoor sensor — address 0x76 (default) */}
    <SensorBme280 pcbX={14.22} pcbY={12.69} schX={20} schY={0} />
    <trace name="TR_SDA" from=".U3 > .GPIO4" to="net.SDA" />
    <trace name="TR_SCL" from=".U3 > .GPIO5" to="net.SCL" />

    {/* outdoor sensor — address 0x77 (addr1: SDO->V3_3, see block DEVIATION
        note). Same bus, same nets, different address so both can ack. */}
    <SensorBme280
      u="U6"
      cVdd="C20"
      cVddio="C21"
      addr1
      pcbX={23.78}
      pcbY={12.69}
      schX={20}
      schY={-10}
    />

    {/* ---- indicators --------------------------------------------------------
        LED1 (PWR) hard-wired to the rail: proof of power the firmware
        cannot lie about. LED2 (ACT) is firmware-driven off GPIO2. */}
    <StatusLed led="LED1" r="R20" rail="V3_3" pcbX={30.98} pcbY={11.63} schX={0} schY={-16} />
    <StatusLed led="LED2" r="R21" rail="LED_ACT" pcbX={35.82} pcbY={11.63} schX={8} schY={-16} />
    <trace name="TR_LED_ACT" from=".U3 > .GPIO2" to="net.LED_ACT" />

    {/* ---- user button, active low into GPIO3 with the internal pull-up ---- */}
    <SwTact name="SW1" signal="BTN_USER" pcbX={0} pcbY={-3.74} schX={30} schY={-16} />
    <trace name="TR_BTN_USER" from=".U3 > .GPIO3" to="net.BTN_USER" />

    {/* ---- debug port ---------------------------------------------------------
        Open board space in the headroom the taller outline opens up, clear
        of rp2040-core's own box (top edge at pcbY=24.9) — SKILL.md: pads
        inside the block's footprint route the debug pair through the
        crystal cluster and come back shorted. */}
    <DebugPort pcbX={-20} pcbY={29} schX={-10} schY={16} />

    {/* ---- bus breakout: the pigtail header for the outdoor/remote sensor ---
        PadHeader (blocks/glue) — same SDA/SCL/V3_3/GND pattern
        i2c-sensor-hub used as bare pads, now the reusable helper. Prefix
        "TP4" (-> TP41..TP44) because default "TP" would collide with
        DebugPort's TP1-3, and the BOM gate only excuses bare copper from
        needing an LCSC number when the alpha part of the refdes is exactly
        TP/FID/MH/H — confirmed against packages/circuitpy/checks.py's
        _UNSOURCED_PREFIXES before picking this prefix. */}
    <PadHeader
      prefix="TP4"
      nets={["SDA", "SCL", "V3_3", "GND"]}
      labels={["SDA", "SCL", "3V3", "GND"]}
      pcbX={15}
      pcbY={29}
      schX={30}
      schY={16}
    />

    {/* ---- ground pour, bottom layer -----------------------------------------
        glue.tsx: "pour ground on any two-layer board with a differential
        pair or an MCU" — this board has both (the USB pair, the RP2040). */}
    <GndPour layer="bottom" />

    {/* ---- mechanics: M3 in the reserved corner strip, recomputed for the
        grown 68mm height (place_board's own formula: width/2 - 3.2,
        height/2 - 3.2) ------------------------------------------------------ */}
    <MountingHole name="H1" diameter={3.2} pcbX={-44.45} pcbY={-30.8} />
    <MountingHole name="H2" diameter={3.2} pcbX={44.45} pcbY={30.8} />

    {/* ---- silkscreen -------------------------------------------------------- */}
    <silkscreentext text="DUAL SENSOR NODE" pcbX={0} pcbY={33.5} fontSize={2} />
    <silkscreentext text="INDOOR" pcbX={14.22} pcbY={16} fontSize={1} />
    <silkscreentext text="OUTDOOR" pcbX={23.78} pcbY={16} fontSize={1} />
    <silkscreentext text="I2C" pcbX={5.66} pcbY={15.3} fontSize={1} />
    <silkscreentext text="PWR" pcbX={30.98} pcbY={17.8} fontSize={1.2} />
    <silkscreentext text="ACT" pcbX={35.82} pcbY={17.8} fontSize={1.2} />
    <silkscreentext text="USER" pcbX={0} pcbY={-7.5} fontSize={1.2} />
    <silkscreentext text="USB-C 5V" pcbX={-1.82} pcbY={-14.6} fontSize={1.2} />
  </board>
)
