/**
 * rgb-lamp-controller — USB-C desk lamp brain: RP2040 drives 8 on-board
 * WS2812 pixels in a line, mode + brightness buttons pick the program,
 * a status LED shows life, and a 3-pad header lets the line continue onto
 * an off-board strip.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-data, ldo-3v3, rp2040-core, ws2812-chain (count=8),
 *   sw-tact x2, status-led, glue.tsx (MountingHole, GndPour, DebugPort).
 * Rails: V5 (USB VBUS) -> V3_3 (ldo-3v3) -> MCU, pixels, buttons' pull-ups,
 *   status LED. The pixel data pad also breaks out raw V5 (see the header
 *   note below — this is a declared spec compromise, not an invented part).
 * Envelope: 84.9 x 68.6mm, 2 layers, 1.6mm — inside product.json's 90 x 75,
 *   inside JLC's 100 x 100mm $2-for-5 sample tier.
 *
 * Placement is circuitlib.layout box math run by hand (as desk-air-monitor
 * did): place_board() puts every non-connector block in ONE row, and an
 * 8-pixel ws2812-chain measures 64mm wide on its own — in one row with
 * rp2040-core/ldo-3v3/both buttons/status-led it came back 149.8mm wide.
 * So this file uses place_row() per row (bottom-to-top: usb-c-data on the
 * connector edge, then the MCU/logic row, then the pixel line on top) and
 * checks the result by hand with board_fits()/overlap_warnings() — both
 * came back clean. place_board() also has no `count` for a parametric
 * block and no way to place two instances of the same block id (its
 * placements dict is keyed by block_id), so the box for ws2812-chain at
 * count=8 and the second sw-tact were checked by temporarily patching
 * circuitlib.layout.BLOCK_BOX_MM in a scratch script, not by calling the
 * planner directly — see work/ee-feedback/rgb-lamp-controller.md.
 *
 * Every part below either comes from a golden block or is glue (the
 * off-board header pads, DebugPort, mounting holes, pour). Nothing here
 * was invented from a datasheet.
 *
 * Rail width — measured, not guessed (docs/architecture/rail-width.md):
 *   `<trace thickness="…">` reaches the router as a per-net `nominalTraceWidth`
 *   and one declaration anywhere on a net sets the whole net, so the rail is
 *   declared once on ldo-3v3, the block where both rails are born. Declaring
 *   blind has scrapped a board whole before (harness-puck, every rail at
 *   0.5mm: fab.ready true -> false, 0 -> 33 blocking, two nets shorted, all
 *   of it inside the RP2040's fanout), so `python -m circuitpy.netwidth
 *   products/rgb-lamp-controller --rails` was run against this placement first,
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
 *   nets (V3_3 and GND)* on a 1.5mm track beside U3's fanout. 40 of this
 *   board's pads are on that pin field. The rail's worst point is at a QFN
 *   pin either way, so the declaration buys almost no copper and can cost the
 *   whole board. Reverted there, and not repeated here.
 */

import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { Ws2812Chain } from "../blocks/ws2812-chain/ws2812-chain"
import { SwTact } from "../blocks/sw-tact/sw-tact"
import { StatusLed } from "../blocks/status-led/status-led"
import { MountingHole, GndPour, DebugPort } from "../blocks/glue"

export default () => (
  <board
    width="84.9mm" height="68.6mm" thickness={1.6}
    /* 5x is the floor per SKILL.md; this board has an 8-pixel chain (16
       traces of hop-to-hop DIN/DOUT plus 8 decoupling caps) and a QFN-56
       MCU on the same sheet, so escalate if build #1 comes back with
       router-clearance findings the way rp2040-core alone does at default
       effort. */
    autorouterEffortLevel="5x"
    minTraceWidth="0.2mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* power entry: USB-C + data pair -> V5, USB_DP/USB_DM to the MCU.
        Bottom edge, facing out — usb-c-data already faces y- by default. */}
    <UsbCData pcbX={-1.82} pcbY={-29.13} schX={-10} schY={6} />

    {/* logic rail: V5 -> V3_3 */}
    {/* V5 declared at 0.5mm here, once — see the header for the measured
        ceiling (1.10mm) and for why V3_3 is left alone. */}
    <Ldo3v3 vinThickness="0.5mm"
      pcbX={2.23} pcbY={2.76} schX={-2} schY={10} />

    {/* the brain. GPIO16/18/19/20 are free pins on the east side of the
        QFN, away from the crystal cluster on the south side (rp2040-core's
        own placement note: keep 10mm max to the crystal, so nothing here
        moves that cluster). */}
    <Rp2040Core pcbX={-19.98} pcbY={6.34} schX={0} schY={0} />
    <trace name="TR_led_data" from=".U3 > .GPIO16" to="net.LED_DATA" />
    <trace name="TR_btn_mode" from=".U3 > .GPIO18" to="net.BTN_MODE" />
    <trace name="TR_btn_bright" from=".U3 > .GPIO19" to="net.BTN_BRIGHT" />
    <trace name="TR_status_led" from=".U3 > .GPIO20" to="net.STATUS_LED" />

    {/* mode + brightness buttons — active low, MCU internal pull-ups per
        sw-tact's own contract (no external resistor in the block). SW1 is
        the block's global v1 default and SW2/SW3 are rp2040-core's BOOTSEL/
        RESET, so these two take SW10/SW11 per the BLOCK.md's own
        instantiate-by-override example. */}
    <SwTact name="SW10" signal="BTN_MODE" pcbX={14.15} pcbY={0.83} schX={8} schY={6} />
    <SwTact name="SW11" signal="BTN_BRIGHT" pcbX={23.15} pcbY={0.83} schX={12} schY={6} />

    {/* status LED, firmware-driven off a GPIO rather than tied to a rail —
        "shows status" per the brief, not just "board is powered" */}
    <StatusLed rail="STATUS_LED" pcbX={30.07} pcbY={-0.23} schX={16} schY={6} />

    {/* the pixel line: 8 WS2812B on one GPIO, decoupled per-pixel, damped
        on the first hop — all inside the block, per BLOCK.md. Rail stays
        the block's default V3_3 (WS2812B's VIH >= 0.7*VDD wants that at
        3.3V logic; there is no level-shifter block yet to run it at V5). */}
    <Ws2812Chain
      count={8}
      dinNet="LED_DATA"
      pcbX={-24.23}
      pcbY={18.96}
      schX={0}
      schY={-10}
    />

    {/* Off-board continuation header — DATA / V5 / GND for a longer strip
        past the 8 on-board pixels, next to the last pixel's DOUT.
        `net.PX_18_DIN` is the chain's own unused net (startIndex defaults
        to 10, so pixel 8 of 8 is D17 and its DOUT is PX_{10+8}_DIN per
        ws2812-chain/BLOCK.md) — tapping it is glue, not a new circuit.
        V5 here is raw USB VBUS, not the V3_3 the on-board pixels run on:
        this is the one place this board does NOT stay inside a block's
        contract. ws2812-chain/BLOCK.md is explicit that 3.3V data into a
        5V-powered chain is "unsupported... only with a level shifter in
        front," and no level-shifter block exists. An off-board 5V strip
        wired to these three pads gets logic that is under its 3.5V VIH
        spec by design, not by oversight — flagged here and in
        work/ee-feedback/rgb-lamp-controller.md rather than solved, because
        solving it means inventing a level-shifter circuit this shop has not
        verified. A strip run at V3_3 instead would be in spec; that pad is
        not offered because the brief asked for 5V. */}
    <testpoint name="TP4" footprintVariant="pad" padShape="circle" padDiameter="1mm"
      pcbX={21.46} pcbY={25} schX={4} schY={-14} />
    <trace name="TR_TP4" from=".TP4 > .pin1" to="net.PX_18_DIN" />
    <silkscreentext text="DATA" pcbX={21.46} pcbY={23.3} fontSize={1} />

    <testpoint name="TP5" footprintVariant="pad" padShape="circle" padDiameter="1mm"
      pcbX={24} pcbY={25} schX={6} schY={-14} />
    <trace name="TR_TP5" from=".TP5 > .pin1" to="net.V5" />
    <silkscreentext text="5V" pcbX={24} pcbY={23.3} fontSize={1} />

    <testpoint name="TP6" footprintVariant="pad" padShape="circle" padDiameter="1mm"
      pcbX={26.54} pcbY={25} schX={8} schY={-14} />
    <trace name="TR_TP6" from=".TP6 > .pin1" to="net.GND" />
    <silkscreentext text="GND" pcbX={26.54} pcbY={23.3} fontSize={1} />

    {/* debug interface: open board space between the logic row and the
        pixel line (ldo-3v3 tops out at y=5.61, the chain starts at
        y=15.04 — clear of both), not inside rp2040-core's own box per the
        glue note on why that placement shorts a via into the QFN pad field */}
    <DebugPort pcbX={10} pcbY={9.5} schX={0} schY={-18} />

    {/* ground return for the USB differential pair, the MCU's own return
        path and the pixel line's per-pixel decoupling — pour it */}
    <GndPour layer="bottom" />

    {/* mounting strip, clear of every footprint */}
    <MountingHole name="H1" diameter={3.2} pcbX={-39.25} pcbY={-31.1} />
    <MountingHole name="H2" diameter={3.2} pcbX={39.25} pcbY={31.1} />
  </board>
)
