/**
 * bench-i2c-scanner — USB-C bench tool: plug it in, it enumerates every
 * device on an I2C bus and reports the addresses it found over serial.
 * Not a sensor board — the pad-header IS the product.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-data, ldo-3v3, rp2040-core, i2c-bus, status-led x4,
 * sw-tact x2, glue.tsx (PadHeader x2, DebugPort, MountingHole, GndPour)
 * Rails: V5 (USB VBUS) -> ldo-3v3 -> V3_3 (logic, bus pull-ups, PWR LED)
 * Envelope: 91.9 x 63.8 mm (place_board's own fit for this block set,
 * grown to 92 x 64 in product.json), 2 layers, 1.6mm.
 *
 * Only ONE i2c-bus block on the whole board (BLOCK.md: "place exactly ONE
 * i2c-bus per bus, regardless of how many sensors share it") — the brief's
 * two PadHeaders are two physical connectors on that SAME electrical bus
 * ("a bus and a pass-through"), not two separate buses. A second bus would
 * need a second i2c-bus (its own pull-up pair) and was not in the compose
 * list, so it is not here — a real finding, written up in
 * work/ee-feedback/bench-i2c-scanner.md.
 *
 * Placement
 *   ldo-3v3 / rp2040-core / i2c-bus / status-led x4 / sw-tact x2 placement
 *   is circuitlib.layout.place_board's own output for exactly this block
 *   list (zero overlap/fit warnings, 91.9 x 63.8mm). usb-c-data is the one
 *   EDGE_BLOCKS member so place_board seats it on the bottom edge, facing
 *   out. The bus breakout headers and the debug port are board furniture
 *   place_board has no slot for — they land by hand in the one place the
 *   plan leaves clear: the 5.02mm free band between the sw-tact row
 *   (bottom -5.96) and usb-c-data's top edge (-10.98), which runs the full
 *   board width because sw-tact only occupies the middle |x| <= 8.
 *
 * Pin allocation (RP2040, U3)
 *   GPIO4   I2C_SDA       to i2c-bus / both PadHeaders
 *   GPIO5   I2C_SCL       to i2c-bus / both PadHeaders
 *   GPIO6   LED_SCAN      "scanning" LED (LED2), firmware-driven
 *   GPIO7   LED_FOUND     "found a device" LED (LED3), firmware-driven
 *   GPIO8   LED_ERR       "error" LED (LED4), firmware-driven
 *   GPIO9   BTN_SCAN      scan button, active low, internal pull-up
 *   GPIO10  BTN_MODE      mode button, active low, internal pull-up
 *   USB_DP/USB_DM         to usb-c-data (explicit trace off the chip pins,
 *                         per rp2040-core's own contract)
 *   SWCLK/SWD             landed on the DebugPort, not left dangling —
 *                         `review_debug_unreachable` is escalated
 *                         warning->error (fab.py VERIFY_ESCALATED_KINDS), so
 *                         omitting it is a blocking defect, not a style
 *                         choice, even though the compose list in the brief
 *                         does not name DebugPort. Noted in the feedback file.
 *   LED1 (PWR) is hard-wired to V3_3 — proof of power the firmware can't lie
 *   about, same convention as i2c-sensor-hub / hydrate-coaster.
 *
 * Refdes for the two bus headers: PadHeader always numbers its own pads
 * from 1 (no offset prop), so two calls at the same literal prefix "TP"
 * collide. Passing `prefix="TP1"` / `prefix="TP2"` keeps the alpha-only
 * prefix at "TP" (the BOM gate's bare-copper exemption only checks the
 * alpha characters against {TP,FID,MH,H} plus "any digit present" —
 * checks.py:_is_unsourced_by_design) while producing TP11-14 / TP21-24,
 * clear of the DebugPort's own default TP1-3.
 *
 * Every part below is a golden block or glue. Nothing was invented from a
 * datasheet.
 */

import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { I2cBus } from "../blocks/i2c-bus/i2c-bus"
import { StatusLed } from "../blocks/status-led/status-led"
import { SwTact } from "../blocks/sw-tact/sw-tact"
import { MountingHole, DebugPort, PadHeader, GndPour } from "../blocks/glue"

export default () => (
  <board
    width="91.9mm"
    height="63.8mm"
    thickness={1.6}
    /* Declared "10x" after measurement, not as a guess. Built first at the
       5x floor: the pipeline's own escalation ladder (generation.py) saw a
       routing-class dfm_hole_clearance blocker in the pre-KiCad scan,
       retried at 10x, tied 2-for-2 on that same scan, and kept the cheaper
       5x build — reporting "rebuilding by hand will not clear it, the
       remaining lever is placement". But the final blocking set at 5x was
       3, not 2: 1 dfm_hole_clearance (the one the ladder saw) plus 2 KiCad
       drc_violation (a DVDD-net via clearance and a U3 hole clearance) that
       are produced ~400 lines later, after KiCad conversion — the ladder's
       own retry-decision code says as much (ROUTING_DRC_TYPES comment,
       "not one warning of this kind exists" at decide-time). The 10x
       attempt's own KiCad DRC was never run: its artifacts were discarded
       the moment the pre-KiCad scan tied, so the escalation note's claim
       was never actually tested against the errors that ended up blocking.
       Declaring 10x directly is the one case the brief calls out where that
       is still the right move; measured 2026-08-17: at 91.9x63.8mm with the
       documented 5mm rp2040-core neighbour gap (BLOCK_GAP_OVERRIDE_MM,
       layout.py) it did NOT clear them — same 3 errors, same location. Also
       tried widening that gap to 9mm on the theory the router just needed
       more room: made it WORSE (4 blocking errors, a new via-via clearance
       pair appeared), confirming layout.py's own hedge — "a floor... not a
       guarantee" — and that this specific defect lives inside
       rp2040-core's own decoupling cluster, not in board-level spacing.
       Reverted to the 5mm floor below. Full writeup in
       work/ee-feedback/bench-i2c-scanner.md. */
    autorouterEffortLevel="10x"
    minTraceWidth="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* ---- power entry: USB-C on the bottom edge, 5V + the USB 2.0 pair ---- */}
    <UsbCData pcbX={-1.82} pcbY={-26.73} schX={-46} schY={0} />

    {/* ---- logic rail: V5 -> V3_3 ------------------------------------------ */}
    <Ldo3v3 pcbX={-31.34} pcbY={17.61} schX={-24} schY={0} />

    {/* ---- the brain --------------------------------------------------------
        place_board's own placement for this block set. */}
    <Rp2040Core pcbX={-8.42} pcbY={21.2} schX={0} schY={0} />
    <trace name="TR_USB_DP" from=".U3 > .USB_DP" to="net.USB_DP" />
    <trace name="TR_USB_DM" from=".U3 > .USB_DM" to="net.USB_DM" />

    {/* ---- I2C bus: exactly one pull-up pair, shared by both headers ------- */}
    <I2cBus pcbX={13.39} pcbY={15.69} schX={20} schY={8} />
    <trace name="TR_SDA" from=".U3 > .GPIO4" to="net.SDA" />
    <trace name="TR_SCL" from=".U3 > .GPIO5" to="net.SCL" />

    {/* ---- indicators --------------------------------------------------------
        LED1 (PWR) hard-wired to the rail: proof of power the firmware
        cannot lie about. LED2/3/4 are firmware-driven status lamps. */}
    <StatusLed led="LED1" r="R20" rail="V3_3" pcbX={19.59} pcbY={14.63} schX={0} schY={-14} />
    <StatusLed led="LED2" r="R21" rail="LED_SCAN" pcbX={24.43} pcbY={14.63} schX={6} schY={-14} />
    <StatusLed led="LED3" r="R22" rail="LED_FOUND" pcbX={29.27} pcbY={14.63} schX={12} schY={-14} />
    <StatusLed led="LED4" r="R23" rail="LED_ERR" pcbX={34.11} pcbY={14.63} schX={18} schY={-14} />
    <trace name="TR_LED_SCAN" from=".U3 > .GPIO6" to="net.LED_SCAN" />
    <trace name="TR_LED_FOUND" from=".U3 > .GPIO7" to="net.LED_FOUND" />
    <trace name="TR_LED_ERR" from=".U3 > .GPIO8" to="net.LED_ERR" />

    {/* ---- buttons: scan (trigger a sweep) and mode (switch which header's
        bus view / verbosity is reported) ------------------------------------ */}
    <SwTact name="SW1" signal="BTN_SCAN" pcbX={-4.5} pcbY={-3.74} schX={30} schY={-8} />
    <SwTact name="SW4" signal="BTN_MODE" pcbX={4.5} pcbY={-3.74} schX={36} schY={-8} />
    <trace name="TR_BTN_SCAN" from=".U3 > .GPIO9" to="net.BTN_SCAN" />
    <trace name="TR_BTN_MODE" from=".U3 > .GPIO10" to="net.BTN_MODE" />

    {/* ---- debug port --------------------------------------------------------
        Open board space, clear of the RP2040's own box. Lands in the free
        band between the sw-tact row and usb-c-data's top edge, well clear
        of usb-c-data's own ESD chip (U1) so silkscreen doesn't collide. */}
    <DebugPort pcbX={-35} pcbY={-8.2} schX={-10} schY={14} />

    {/* ---- bus breakout: two PadHeaders on the SAME bus (SDA/SCL/V3_3/GND) —
        a bus port and a pass-through, same free band, straddling U1 with
        clearance on both sides. See file header for why the prefixes are
        "TP1"/"TP2" rather than a fresh letter. */}
    <PadHeader
      prefix="TP1"
      nets={["SDA", "SCL", "V3_3", "GND"]}
      labels={["SDA", "SCL", "3V3", "GND"]}
      pcbX={-15}
      pcbY={-8.2}
      schX={20}
      schY={14}
    />
    <PadHeader
      prefix="TP2"
      nets={["SDA", "SCL", "V3_3", "GND"]}
      labels={["SDA", "SCL", "3V3", "GND"]}
      pcbX={15}
      pcbY={-8.2}
      schX={32}
      schY={14}
    />

    {/* ---- ground pour, bottom layer -----------------------------------------
        glue.tsx: "pour ground on any two-layer board with a differential
        pair or an MCU" — this board has both. */}
    <GndPour layer="bottom" />

    {/* ---- mechanics: M3 in the reserved corner strip, place_board's own
        hole plan for this outline. -------------------------------------- */}
    <MountingHole name="H1" diameter={3.2} pcbX={-42.75} pcbY={-28.7} />
    <MountingHole name="H2" diameter={3.2} pcbX={42.75} pcbY={28.7} />

    {/* ---- silkscreen -------------------------------------------------------- */}
    <silkscreentext text="I2C SCANNER" pcbX={0} pcbY={30.5} fontSize={2} />
    <silkscreentext text="I2C BUS" pcbX={13.39} pcbY={18.5} fontSize={1.2} />
    <silkscreentext text="PWR" pcbX={19.59} pcbY={20} fontSize={1} />
    <silkscreentext text="SCAN" pcbX={24.43} pcbY={20} fontSize={1} />
    <silkscreentext text="FOUND" pcbX={29.27} pcbY={20} fontSize={1} />
    <silkscreentext text="ERR" pcbX={34.11} pcbY={20} fontSize={1} />
    <silkscreentext text="SCAN" pcbX={-4.5} pcbY={0.5} fontSize={1} />
    <silkscreentext text="MODE" pcbX={4.5} pcbY={0.5} fontSize={1} />
    <silkscreentext text="BUS A" pcbX={-15} pcbY={-5.2} fontSize={1} />
    <silkscreentext text="BUS B" pcbX={15} pcbY={-5.2} fontSize={1} />
    <silkscreentext text="USB-C 5V" pcbX={-1.82} pcbY={-13.7} fontSize={1.2} />
  </board>
)
