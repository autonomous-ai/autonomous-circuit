/**
 * two-key-footswitch — USB-C RP2040 footswitch with two keys (push-to-talk,
 * mute) and one status LED. Deliberately the small end of the product range:
 * fewest blocks, smallest board, cheapest BOM in the fleet.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-data, rp2040-core, ldo-3v3, sw-tact x2, status-led,
 *   glue.tsx (MountingHole x4, DebugPort, GndPour)
 * Rails: V5 (USB VBUS) -> V3_3 (ldo-3v3) -> MCU, switches' pull-ups, LED
 * Envelope: 47.5 x 68.9mm, 2 layers, 1.6mm — inside product.json's 48 x 70mm
 *
 * ldo-3v3 is NOT in the brief's block list but IS load-bearing: rp2040-core
 * requires net.V3_3 (circuitlib.blocks: requires=("V3_3","GND","USB_DP",
 * "USB_DM")) and usb-c-data only ever provides V5. circuitlib.helpers.
 * board_plan(capabilities=["usb-data","mcu","button","indicator"]) confirms
 * it: the planner auto-pulls ldo-3v3 in to satisfy the unmet V3_3 requirement
 * and comes back buildable with zero unmet/unavailable. This is a finding
 * about the brief, not an invented circuit — ldo-3v3 is itself a golden
 * block, just missing from this board's composition list.
 *
 * Placement is circuitlib.layout.place_row's algorithm run by hand, in two
 * inner rows plus the usb-c-data edge row — the same reason
 * desk-air-monitor's own header cites: two sw-tact instances collapse onto
 * one key in place_row()'s placements dict, so place_board() itself can't
 * lay out two identical blocks in one call. Verified overlap-free with
 * circuitlib.layout's own box/extent math (overlap check, board-fits check
 * for both the edge and inner margins, hole-clearance check, price-tier
 * check) run by hand before this file existed — see
 * work/ee-feedback/two-key-footswitch.md for the exact numbers.
 *
 * Row 1 (top, inner): rp2040-core alone (27.53 x 24.42mm, the single biggest
 *   block on the board).
 * Row 2 (below row 1): ldo-3v3, SW1 (PTT), status-led, SW10 (MUTE).
 * Row 3 (bottom edge): usb-c-data, facing out (south).
 * Four mounting holes, one per corner, in the router-halo-plus-hole-strip
 * band — "a footswitch gets stepped on" per the brief, so two opposite
 * corners (the desk-air-monitor default) is not enough; all four take the
 * mechanical load evenly whichever corner a boot lands on.
 *
 * Pin allocation (RP2040, U3)
 *   GPIO2   PTT_BTN    push-to-talk key, active low, internal pull-up
 *   GPIO3   MUTE_BTN   mute key, active low, internal pull-up
 *   USB_DP/USB_DM      to usb-c-data (explicit trace off the chip pins, per
 *                      rp2040-core's own contract and the hydrate-coaster /
 *                      i2c-sensor-hub reference boards)
 *   SWCLK/SWD          landed on DebugPort, not left dangling
 *   status-led is hard-wired to V3_3 (power-on indicator) — the same
 *   always-on convention every other board in the fleet uses for its one
 *   unconditional LED; a firmware-driven PTT/MUTE state light would be a
 *   second LED this brief did not ask for.
 *
 * FINDING: sw-tact is a TS-1187A-B-A-B SMD tactile switch (5.1 x 5.1mm,
 * finger-press rated) — the only "button" capability the golden library
 * has. A real foot switch wants a panel-mount momentary switch or a 3.5mm
 * jack to an external pedal, both taking real mechanical force; nothing like
 * that exists as a block. This board is composed exactly as instructed
 * (sw-tact x2) and is therefore a control PCB for two foot-actuated keys,
 * not two SMD buttons a boot presses directly — the enclosure has to carry
 * the mechanical load down to these tiny pads. Recorded in
 * work/ee-feedback/two-key-footswitch.md as a library gap, not patched here.
 *
 * Every part below is a golden block or glue (mounting holes, debug port,
 * ground pour, silkscreen, and the two GPIO traces the block's own contract
 * calls for). Nothing here was invented from a datasheet.
 */

import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { StatusLed } from "../blocks/status-led/status-led"
import { SwTact } from "../blocks/sw-tact/sw-tact"
import { MountingHole, DebugPort, GndPour } from "../blocks/glue"

export default () => (
  <board
    width="47.5mm" height="68.9mm" thickness={1.6}
    /* 5x is the SKILL.md/brief floor. Escalating straight to 10x on the
       evidence already on file in this fleet: desk-air-monitor measured the
       same rp2040-core board fab.ready=false at the default effort and
       fab.ready=true at 5x; i2c-sensor-hub and examples/hydrate-coaster both
       measured usb-c-data + rp2040-core specifically — this board's exact
       power/MCU pairing — needing 10x even after a favorable rotation, with
       5x left short on the USB-C data-pair fanout. Declaring 10x up front
       instead of spending a full 20-40 minute build discovering the same
       shortfall a third time. */
    autorouterEffortLevel="100x"
    minTraceWidth="0.2mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* ---- power entry: USB-C on the bottom edge, 5V + the USB 2.0 pair ---- */}
    <UsbCData pcbX={-1.82} pcbY={-29.28} schX={-8} schY={10} />

    {/* ---- logic rail: V5 -> V3_3 -------------------------------------- */}
    <Ldo3v3 pcbX={-12.54} pcbY={-4.83} schX={-2} schY={4} />

    {/* ---- the brain ------------------------------------------------------
        Explicit USB_DP/USB_DM traces off the chip pins: rp2040-core's own
        contract ("GPIOs are not netted by the block") and both reference
        boards in this fleet do the pairing this way. */}
    <Rp2040Core pcbX={-2.27} pcbY={17.74} schX={0} schY={0} />
    <trace name="TR_USB_DP" from=".U3 > .USB_DP" to="net.USB_DP" />
    <trace name="TR_USB_DM" from=".U3 > .USB_DM" to="net.USB_DM" />

    {/* ---- the two keys: push-to-talk (left), mute (right) --------------
        Active low into the RP2040's internal pull-ups, same convention as
        every sw-tact instance in this fleet (sw-tact/BLOCK.md). */}
    <SwTact name="SW1" signal="PTT_BTN" pcbX={-0.62} pcbY={-6.76} schX={8} schY={-6} />
    <trace name="TR_PTT" from=".U3 > .GPIO2" to="net.PTT_BTN" />
    <SwTact name="SW10" signal="MUTE_BTN" pcbX={13.22} pcbY={-6.76} schX={14} schY={-6} />
    <trace name="TR_MUTE" from=".U3 > .GPIO3" to="net.MUTE_BTN" />

    {/* ---- status LED: hard-wired to V3_3, proof of power --------------- */}
    <StatusLed rail="V3_3" pcbX={6.3} pcbY={-7.82} schX={11} schY={-10} />

    {/* ---- debug port -----------------------------------------------------
        Open board space, clear of rp2040-core's own box (SKILL.md: pads
        inside the block's footprint route the debug pair through the
        crystal cluster and come back shorted). 4.6mm clear of the RP2040's
        top edge at this y, clear of both top mounting holes in x. */}
    <DebugPort pcbX={0} pcbY={29} schX={0} schY={16} />

    {/* ---- ground pour, bottom layer --------------------------------------
        glue.tsx: "pour ground on any two-layer board with a differential
        pair or an MCU" — this board has both (the USB pair, the RP2040).
        Every margin set explicitly; a bare <copperpour> with only
        cutoutMargin set is what put other boards in this fleet straight
        into KiCad DRC (measured 2026-08-16). */}
    <GndPour layer="bottom" />

    {/* ---- mechanics: four M3 holes, one per corner ----------------------
        "a footswitch gets stepped on" — two opposite corners (every other
        board in this fleet's default) leaves the other diagonal unsupported
        under a boot. All four sit in the router-halo-plus-hole-strip band,
        >=2.15mm of keepout-to-edge and >=3mm of keepout-to-nearest-block
        clearance (hand-checked against circuitlib.layout's own hole/overlap
        math — see work/ee-feedback/two-key-footswitch.md). */}
    <MountingHole name="H1" diameter={3.2} pcbX={-19.75} pcbY={-30.45} />
    <MountingHole name="H2" diameter={3.2} pcbX={19.75} pcbY={-30.45} />
    <MountingHole name="H3" diameter={3.2} pcbX={-19.75} pcbY={30.45} />
    <MountingHole name="H4" diameter={3.2} pcbX={19.75} pcbY={30.45} />

    {/* ---- silkscreen ---------------------------------------------------- */}
    <silkscreentext text="TWO-KEY FOOTSWITCH" pcbX={0} pcbY={24.8} fontSize={1.6} />
    <silkscreentext text="PTT" pcbX={-0.62} pcbY={-11.6} fontSize={1.2} />
    <silkscreentext text="MUTE" pcbX={13.22} pcbY={-11.6} fontSize={1.2} />
    <silkscreentext text="USB-C 5V" pcbX={-1.82} pcbY={-16} fontSize={1.2} />
  </board>
)
