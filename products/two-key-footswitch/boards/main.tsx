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
       shortfall a third time.

       At 10x this board still comes back fab.ready=false with 3 blocking
       findings (dfm_hole_clearance + 2x drc_violation), all attributed to
       U3 and geometrically inside rp2040-core's own footprint (a via near
       its C4-C8 decoupling row, ~2.7mm off the chip's own origin) — same
       signature as desk-air-monitor's documented DVDD via finding, on a
       net this board never touches. Tried 100x once (this board's part
       count is small, so the ladder's own "not something a chat loop can
       absorb" warning seemed worth testing anyway): killed after ~28
       minutes with no result and no partial verdict, well past the
       terminal-keyboard 5x reference point of ~17 minutes for a much
       busier board. Reverted to 10x — see
       work/ee-feedback/two-key-footswitch.md. This is reported as a
       library finding, not patched here: the defect sits inside a golden
       block's own internal routing, not in anything this board placed. */
    autorouterEffortLevel="10x"
    minTraceWidth="0.2mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* ---- power entry: USB-C on the bottom edge, 5V + the USB 2.0 pair ---- */}
    <UsbCData pcbX={-1.82} pcbY={-29.28} schX={-8} schY={10} />

    {/* ---- logic rail: V5 -> V3_3 -------------------------------------- */}
    <Ldo3v3 pcbX={-12.54} pcbY={-1.82} schX={-2} schY={4} />

    {/* ---- the brain ------------------------------------------------------
        Explicit USB_DP/USB_DM traces off the chip pins: rp2040-core's own
        contract ("GPIOs are not netted by the block") and both reference
        boards in this fleet do the pairing this way.
        pcbY re-derived 2026-08-17 (ledger #52/#53) from
        circuitlib.layout.place_board(["rp2040-core","ldo-3v3","sw-tact",
        "status-led","sw-tact","usb-c-data"], mounting_holes=False,
        max_width_mm=47.5), which now routes every gap through pair_gap()
        and so gives rp2040-core BLOCK_GAP_OVERRIDE_MM's 5mm from every
        neighbour automatically — up from the ad hoc ~2.9mm this board
        shipped with (17.74 -> 23.75, +6.01mm). warnings: []. */}
    <Rp2040Core pcbX={-2.27} pcbY={23.75} schX={0} schY={0} />
    <trace name="TR_USB_DP" from=".U3 > .USB_DP" to="net.USB_DP" />
    <trace name="TR_USB_DM" from=".U3 > .USB_DM" to="net.USB_DM" />

    {/* ---- the two keys: push-to-talk (left), mute (right) --------------
        Active low into the RP2040's internal pull-ups, same convention as
        every sw-tact instance in this fleet (sw-tact/BLOCK.md). */}
    <SwTact name="SW1" signal="PTT_BTN" pcbX={-0.62} pcbY={-3.74} schX={8} schY={-6} />
    <trace name="TR_PTT" from=".U3 > .GPIO2" to="net.PTT_BTN" />
    <SwTact name="SW10" signal="MUTE_BTN" pcbX={13.22} pcbY={-3.74} schX={14} schY={-6} />
    <trace name="TR_MUTE" from=".U3 > .GPIO3" to="net.MUTE_BTN" />

    {/* ---- status LED: hard-wired to V3_3, proof of power --------------- */}
    <StatusLed rail="V3_3" pcbX={6.3} pcbY={-4.8} schX={11} schY={-10} />

    {/* ---- debug port -----------------------------------------------------
        rp2040-core's new top edge (30.45) now sits exactly on the router
        halo boundary the wider gap consumes, leaving no headroom above the
        block — so the debug header moves beside rp2040-core instead of
        above it, rotated 90 (pads run in y) the same way macropad-6's does
        for the same reason. ~2.2mm clear of rp2040-core's left edge in x,
        at the block's own pcbY, well clear of H3/H4 in both axes. */}
    <DebugPort pcbX={-17} pcbY={23.75} pcbRotation={90} schX={0} schY={16} />

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

    {/* ---- silkscreen ----------------------------------------------------
        Title moved from y=24.8 (2026-08-17): that sat just above
        rp2040-core's OLD top edge (24.44); the block's new top edge is
        30.45, so the old spot is now on top of the block itself. Moved
        above rp2040-core and the mounting holes, inside the top margin. */}
    <silkscreentext text="TWO-KEY FOOTSWITCH" pcbX={0} pcbY={33} fontSize={1.4} />
    <silkscreentext text="PTT" pcbX={-0.62} pcbY={-8.6} fontSize={1.2} />
    <silkscreentext text="MUTE" pcbX={13.22} pcbY={-8.6} fontSize={1.2} />
    <silkscreentext text="USB-C 5V" pcbX={-1.82} pcbY={-16} fontSize={1.2} />
  </board>
)
