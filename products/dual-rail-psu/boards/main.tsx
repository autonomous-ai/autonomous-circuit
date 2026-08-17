/**
 * dual-rail-psu — a bench-supply brick: USB-C in, 5V and 3V3 broken out on
 * pad headers with a power LED per rail, so someone can run a breadboard off
 * a laptop charger without a bench PSU. No MCU on this board — the rails are
 * the entire product, so they are the thing this file pushes on.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-power (5V in, no data pair — this board never touches
 *   D+/D-), ldo-3v3 (5V -> 3V3), status-led x2 (one per rail), glue.tsx
 *   (PadHeader x2, MountingHole, GndPour). Do not invent circuits: every part
 *   here is a golden block wired the way its BLOCK.md describes.
 *
 * Power budget (full arithmetic in product.json's `powerBudget`):
 *   - V5 header: fed straight off VBUS, budgeted 1.5A (usb-c-power's own
 *     board budget, not the connector's ceiling — no PD negotiation here).
 *   - LDO (AMS1117-3.3, SOT-223): budgeted at its own rated ceiling, 500mA
 *     continuous. Dissipation (5 - 3.3) x 0.5A = 0.85W on the tab — BLOCK.md's
 *     stated limit for a poured-tab 1oz 2-layer board, not a margin above it.
 *     No extra V3_3 pour added on top of the LDO's own footprint copper; if a
 *     future revision wants more than 500mA continuous, that pour is the
 *     first thing to add, not a wider trace.
 *   - V3_3 header: budgeted 500mA, the LDO's ceiling.
 *   - Both status LEDs (~4.1mA combined) round to nothing against either
 *     budget.
 *
 * Rail width — measured, not guessed (docs/architecture/rail-width.md):
 *   `<trace thickness="…">` reaches the router as a per-net width and one
 *   declaration anywhere on a net sets the whole net, but declaring blind has
 *   scrapped a board before (harness-puck, every rail at once: fab.ready
 *   true -> false, 0 -> 33 blocking, all inside an RP2040's QFN fanout this
 *   board doesn't have). So: build once undeclared, run
 *   `python -m circuitpy.netwidth` against the placement, then declare only
 *   what the measured ceiling clears.
 *
 *   Measured on this placement's first clean build (2026-08-17,
 *   `circuitpy.netwidth --net V5 --net V3_3 --net GND`, no rail declared):
 *     V5    ceiling 1.10mm (tightest pad U1.VBUS)    routed narrowest 0.20mm
 *     V3_3  ceiling 1.30mm (tightest pad R20.pin1)   routed narrowest 0.25mm
 *     GND   ceiling 1.10mm (tightest pad U1.GND)     routed narrowest 0.20mm
 *   No QFN on this board, so nothing pinches either rail anywhere near the
 *   fab profile's 0.5mm warn floor — both V5 and V3_3 clear it with roughly
 *   2x to spare, so 0.5mm is declared on both (glue.tsx's `PadHeader` grew a
 *   `thickness` prop for this — see its header comment). GND left
 *   undeclared; it pours to the bottom layer. See
 *   work/ee-feedback/dual-rail-psu.md for the rebuild's verdict.
 *
 * Placement: circuitlib.layout.place_board(["usb-c-power", "ldo-3v3",
 * "status-led", "status-led"]) returned a clean 41.1 x 31.0mm plan with zero
 * warnings (connector on the bottom edge facing out, ldo-3v3 and both LEDs in
 * the row above, holes on opposite corners). PadHeader is glue, not a planner
 * block — its box isn't in BLOCK_BOX_MM, same reason usb-c-breakout's
 * testpoint row was hand-reserved — so the plan's own 31.0mm height was too
 * short to hold two header rows on top of it. Grown by 20mm (31.0 -> 51.0)
 * to make room, and every planner-placed block plus both mounting holes was
 * shifted down by half that (10mm) so the connector stays exactly on the
 * bottom edge and the entire 20mm lands as new space above the original
 * content — re-verified at these exact shifted coordinates with
 * layout.board_fits(), layout.overlap_warnings() and
 * layout._hole_clearance_warnings() before this file was built: all three
 * came back [].
 * Envelope: 41.1 x 51.0mm, 2 layers, 1.6mm — inside product.json's 42 x 52.
 */

import { UsbCPower } from "../blocks/usb-c-power/usb-c-power"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { StatusLed } from "../blocks/status-led/status-led"
import { MountingHole, GndPour, PadHeader } from "../blocks/glue"

export default () => (
  <board
    width="41.1mm" height="51mm" thickness={1.6}
    /* SKILL.md floor: 5x on every board — a floor, not a hand-picked number;
       the pipeline climbs to 10x on its own if a routing-class blocker shows
       up and keeps the harder result only if it is strictly better. */
    autorouterEffortLevel="5x"
    minTraceWidth="0.2mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* power entry: USB-C on the bottom edge, facing out. No data pair on
        this board — usb-c-power (not usb-c-data) is the correct block, it
        just doesn't expose D+/D-. */}
    <UsbCPower pcbX={-1.75} pcbY={-20.33} schX={-12} schY={0} />

    {/* 5V -> 3V3. Default refdes U2/C2/C3 (global v1 allocation). */}
    <Ldo3v3 pcbX={-5.96} pcbY={-1.35} schX={-10} schY={9} />

    {/* one status LED per rail. Default instance runs off V3_3 (the block's
        own default rail); the second overrides led/r/rail to run off V5
        directly — same 1k-series-resistor circuit, different rail fed in,
        not a new circuit (usb-c-breakout's precedent for feeding this block
        from 5V instead of 3V3). */}
    {/* shifted +3mm right of place_board's own numbers (3.88 -> 6.88,
        8.72 -> 11.72): the first full build put one blocking finding on this
        board — a router-placed via 0.164mm from U2's own GND pad (needs
        0.2mm), unchanged at both 5x and the pipeline's own 10x escalation,
        so it was the placement's own tightness rather than a router-quality
        problem. Widening ldo-3v3's clearance from its neighbour row gave the
        router more room on that side; re-verified at these coordinates with
        layout.board_fits() / overlap_warnings() / hole_clearance() (all [])
        before rebuilding. */}
    <StatusLed pcbX={6.88} pcbY={-4.34} schX={0} schY={9} />
    <StatusLed led="LED2" r="R21" rail="V5" pcbX={11.72} pcbY={-4.34} schX={8} schY={9} />

    {/* the whole point of the board: 5V and 3V3 broken out on labelled pad
        headers, 2.54mm pitch (0.1" header pitch), 1mm round pads. Both rail
        traces declared at 0.5mm — the fab's warn_power_trace_mm floor —
        after circuitpy.netwidth confirmed this placement's V5 and V3_3 pads
        can escape wider than that everywhere (see header comment). GND left
        undeclared; it pours to the bottom layer. */}
    <PadHeader prefix="TP2" nets={["V5", "GND"]} labels={["5V", "GND"]}
      thickness={["0.5mm", undefined]}
      pcbX={0} pcbY={6} schX={-5} schY={18} />
    <PadHeader prefix="TP3" nets={["V3_3", "GND"]} labels={["3V3", "GND"]}
      thickness={["0.5mm", undefined]}
      pcbX={0} pcbY={13} schX={5} schY={18} />

    <silkscreentext text="DUAL RAIL PSU" pcbX={0} pcbY={19} fontSize={1.4} />

    {/* ground return and the LDO's own thermal path both want a plane, not
        0.2mm track (glue.tsx's own GndPour note). */}
    <GndPour layer="bottom" />

    {/* mounting strip, clear of every footprint and both header rows —
        verified with layout._hole_clearance_warnings() before this file was
        built. */}
    <MountingHole name="H1" diameter={3.2} pcbX={-17.35} pcbY={-22.3} />
    <MountingHole name="H2" diameter={3.2} pcbX={17.35} pcbY={22.3} />
  </board>
)
