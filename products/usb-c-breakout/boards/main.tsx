/**
 * usb-c-breakout — a USB-C receptacle broken out to labelled bench pads
 * (VBUS, GND, CC1, CC2, D+, D-) with a power LED, so someone can hook a
 * bench supply or a logic analyser to a USB-C cable. No MCU on this board.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-data (receptacle + ESD + CC pulldowns + series-R data
 *   pair), status-led, glue.tsx (mounting holes, ground pour). Test pads are
 *   glue too — `<testpoint footprintVariant="pad">`, same shape as
 *   `blocks/glue`'s DebugPort and examples/hydrate-coaster's debug port.
 * Rails: V5 (USB VBUS) only. There is no ldo-3v3 on this board and nothing
 *   else in the fleet's block set makes 3V3 without one, so the status LED
 *   runs off V5 directly (status-led's `rail` prop takes any net; running it
 *   from 5V instead of 3V3 is not a new circuit, just a different rail fed
 *   into the same 1k-series-resistor block). At 5V a green LED (~2.1V Vf)
 *   across a 1k resistor draws ~2.9mA — under the 0805 LED's typical 20mA
 *   rating, brighter than the block's normal 3V3 use (~1.2mA) and still
 *   frugal. No regulator was invented for this: usb-c-breakout simply does
 *   not have a 3V3 rail to offer, and that is a real gap — see
 *   work/ee-feedback/usb-c-breakout.md.
 * Rail width — measured, not guessed (docs/architecture/rail-width.md):
 *   `python -m circuitpy.netwidth products/usb-c-breakout --rails` on this
 *   placement, before anything was declared:
 *     V5  ceiling 1.10mm (tightest pad U1.VBUS)  routed narrowest 0.20mm
 *   V5 is the only rail on this board. Nothing here is placement-limited —
 *   the RP2040 QFN that caps V3_3 at 0.4000mm on the MCU boards is not on
 *   this one — so 0.5mm (the jlcpcb profile's warn_power_trace_mm floor)
 *   is declared on the V5 trace below, well inside the measured ceiling.
 * Envelope: 36 x 40.5mm, 2 layers, 1.6mm — inside product.json's 36 x 41.
 *
 * Placement: circuitlib.layout.place_board(["usb-c-data", "status-led"])
 * returned a clean 36 x 30.5mm plan with zero warnings (connector on the
 * bottom edge facing out, LED in the top row, holes on opposite corners).
 * That plan has no notion of the six breakout pads this board's whole job
 * is to expose — testpoint is glue, not a planner block, so its box isn't in
 * BLOCK_BOX_MM — so the two block placements below are the planner's
 * unmodified numbers, and a hand-reserved 8mm band was inserted between the
 * connector and the LED to hold the pad row (stacking: connector 19.42mm +
 * gap 2mm + band 8mm + gap 2mm + LED 3.52mm + margins), which is what pushed
 * the board from 30.5mm tall to 40.5mm. Re-verified with layout.board_fits(),
 * layout.overlap_warnings() and layout._hole_clearance_warnings() by hand at
 * these exact coordinates before this file was built: all three came back [].
 *
 * Breakout point for D+/D-: the MCU-side nets (net.USB_DP / net.USB_DM),
 * i.e. downstream of the USBLC6's ESD channels and the 27R series resistors
 * — the same point rp2040-core would tap. CC1/CC2 tap the connector pins
 * directly (`.J1 > .CC1` / `.J1 > .CC2`), which already carry usb-c-data's
 * built-in 5.1k pulldowns to GND (UFP/sink identification) — nothing added.
 */

import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { StatusLed } from "../blocks/status-led/status-led"
import { MountingHole, GndPour } from "../blocks/glue"

export default () => (
  <board
    width="36mm" height="40.5mm" thickness={1.6}
    /* SKILL.md floor: 5x on every board. This board has 8 parts and no MCU —
       the smallest, sparsest composition in the fleet — so 5x is a genuine
       test of whether the effort floor is doing any work on a board this
       far from the dense end of the fleet, not a copy-pasted ritual. */
    autorouterEffortLevel="5x"
    minTraceWidth="0.2mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* power + data entry: USB-C on the bottom edge, facing out */}
    <UsbCData pcbX={-1.82} pcbY={-15.08} schX={-8} schY={0} />

    {/* breakout pads: VBUS, GND, CC1, CC2, D+, D- — open board space between
        the connector and the LED, 2.54mm pitch (0.1" header pitch), 1mm
        round pads (pogo/clip-friendly, no drills, no keepouts). Labelled
        short on purpose: fontSize 1 on a 2.54mm pitch has no room for a
        5-character name without the labels overlapping (glue.tsx's own
        note on DebugPort). */}
    <testpoint name="TP1" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={-6.35} pcbY={6.67} schX={-6} schY={-6} />
    <testpoint name="TP2" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={-3.81} pcbY={6.67} schX={-3} schY={-6} />
    <testpoint name="TP3" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={-1.27} pcbY={6.67} schX={0} schY={-6} />
    <testpoint name="TP4" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={1.27} pcbY={6.67} schX={3} schY={-6} />
    <testpoint name="TP5" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={3.81} pcbY={6.67} schX={6} schY={-6} />
    <testpoint name="TP6" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={6.35} pcbY={6.67} schX={9} schY={-6} />

    {/* V5 declared at 0.5mm — measured, not guessed. `thickness` reaches the
        router as a per-net `nominalTraceWidth` and one declaration anywhere
        on a net sets the whole net (docs/architecture/rail-width.md), so this
        single trace widens every centimetre of V5 on the board. Declaring
        blind has scrapped a board before (harness-puck: fab.ready true ->
        false, 0 -> 33 blocking), so `circuitpy.netwidth` was run against this
        placement first: V5 ceiling **1.10mm**, tightest pad U1.VBUS, routed
        narrowest 0.20mm. 0.5mm is the fab profile's warn_power_trace_mm floor
        and clears the ceiling with better than 2x to spare. There is no MCU
        here, so no QFN fanout pinches this rail anywhere. */}
    <trace name="TR_TP1_VBUS" from=".TP1 > .pin1" to="net.V5" thickness="0.5mm" />
    <trace name="TR_TP2_GND" from=".TP2 > .pin1" to="net.GND" />
    <trace name="TR_TP3_CC1" from=".TP3 > .pin1" to=".J1 > .CC1" />
    <trace name="TR_TP4_CC2" from=".TP4 > .pin1" to=".J1 > .CC2" />
    <trace name="TR_TP5_DP" from=".TP5 > .pin1" to="net.USB_DP" />
    <trace name="TR_TP6_DM" from=".TP6 > .pin1" to="net.USB_DM" />

    {/* "VBUS" (4 chars) collided with the neighbouring "GND" label at this
        2.54mm pitch — measured on the first build's _pcb.png as an
        unreadable "VBUSGND". glue.tsx's DebugPort labels (CLK/DIO/GND) are
        all <=3 chars at this same pitch and don't collide, so "5V" replaces
        "VBUS" rather than shrinking every label's font past the fab's 1.0mm
        floor. */}
    <silkscreentext text="5V" pcbX={-6.35} pcbY={4.97} fontSize={1} />
    <silkscreentext text="GND" pcbX={-3.81} pcbY={4.97} fontSize={1} />
    <silkscreentext text="CC1" pcbX={-1.27} pcbY={4.97} fontSize={1} />
    <silkscreentext text="CC2" pcbX={1.27} pcbY={4.97} fontSize={1} />
    <silkscreentext text="D+" pcbX={3.81} pcbY={4.97} fontSize={1} />
    <silkscreentext text="D-" pcbX={6.35} pcbY={4.97} fontSize={1} />

    {/* proof of life: no regulator on this board, so this runs off V5
        directly — see header comment on why that's the honest choice
        rather than an invented rail. */}
    <StatusLed rail="V5" pcbX={0} pcbY={13.37} schX={0} schY={6} />

    {/* ground return for the USB differential pair — pour it, not 0.2mm
        track (blocks/glue's GndPour note). */}
    <GndPour layer="bottom" />

    {/* mounting strip, clear of every footprint and every pad */}
    <MountingHole name="H1" diameter={3.2} pcbX={-14.8} pcbY={-17.05} />
    <MountingHole name="H2" diameter={3.2} pcbX={14.8} pcbY={17.05} />

    <silkscreentext text="USB-C BREAKOUT" pcbX={0} pcbY={18.5} fontSize={1.4} />
  </board>
)
