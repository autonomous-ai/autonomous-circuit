/**
 * pixel-badge — a wearable conference badge: 8 addressable RGB pixels across
 * the top edge (read at eye level, worn pinned to a shirt), two buttons to
 * step through patterns, USB-C for power and firmware.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-data, rp2040-core, ldo-3v3 (x2), ws2812-chain (x8),
 *              sw-tact (x2), status-led, plus glue (MountingHole, DebugPort,
 *              GndPour) for mounting holes and the debug interface.
 *
 * Rails:
 *   V5        USB-C VBUS, 5V @ up to 1.5A budgeted
 *   V3_3      U2 (AMS1117-3.3) -> logic only: RP2040 + flash      ~100mA
 *   V3_3_LED  U5 (AMS1117-3.3) -> the 8-pixel row only            <=480mA
 * Two regulators, not one, for the same reason harness-puck (examples/) uses
 * two: 8 WS2812s at the ws2812-chain block's worst-case 60mA/pixel is 480mA
 * on its own, and 480 + 100 = 580mA through a single AMS1117 is 0.99W in a
 * SOT-223 — circuitlib.helpers.regulator_thermal() calls that "over-temperature"
 * at a 70degC ambient. Split across two packages, U2 (logic, ~100mA) reports
 * "ok" and U5 (LEDs alone, <=480mA) reports "marginal" (a warning, not an
 * error) — accepted and reported, not chased away, per SKILL.md's rule that
 * fab.ready is never traded for a tidier warning count.
 *
 * Envelope: 82 x 58mm, 2 layers, 1.6mm. This is bigger than the 70 x 40mm
 * example in the brief — see work/ee-feedback/pixel-badge.md for the
 * measured reason: rp2040-core's own box is 27.5 x 24.4mm and usb-c-data's
 * is 15.2 x 19.4mm (circuitlib.layout.BLOCK_BOX_MM), and place_board()'s
 * placements dict is keyed by block id, so it silently collapses two
 * sw-tact instances into one — every coordinate below was placed by hand
 * from circuitlib.layout.box()/extent(), not from place_board(), because of
 * that gap. All gaps between block bounding boxes are >=2.5mm (BLOCK_GAP_MM
 * floor is 2.0mm); all margins to the outline are >=4mm (ROUTER_HALO_MM) —
 * both hand-verified against the measured boxes before this ever went to a
 * build, the same discipline layout.place_board() would have applied if it
 * could take more than one instance of a block.
 *
 * Sides: every part on the TOP side, JLC economy single-side assembly.
 * Nothing here was invented from a datasheet — every IC is a golden block,
 * and the pixel row is the golden ws2812-chain block used exactly as
 * documented (its own internal .map() over `count` pixels — see the
 * feedback doc for the note on why that loop matters to the app).
 */

import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { Ws2812Chain } from "../blocks/ws2812-chain/ws2812-chain"
import { SwTact } from "../blocks/sw-tact/sw-tact"
import { StatusLed } from "../blocks/status-led/status-led"
import { GndPour, MountingHole, DebugPort } from "../blocks/glue"

const PIXELS = 8

export default () => (
  <board
    width="82mm"
    height="58mm"
    thickness={1.6}
    /* Measured 2026-08-11 on rp2040-core: fab.ready=false with five blocking
       KiCad findings at the router's default effort, fab.ready=true with
       zero at "5x" — same design, only this prop changed. Every board
       declares the effort it needs; this is the floor. */
    autorouterEffortLevel="5x"
    minTraceWidth="0.2mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* power + USB device entry, bottom edge, facing out. Paired with
        rp2040-core to its left, which drives the same USB_DP/USB_DM net
        names — no explicit trace needed, same net by name.
        pcbX moved +2.49mm (2026-08-17, ledger #52/#53): the old x=13.2 left
        only 2.51mm to rp2040-core's right edge; pair_gap() now wants 5mm
        from rp2040-core on every side (BLOCK_GAP_OVERRIDE_MM), so this
        opens that gap to 5.0mm exactly. Still 3.47mm clear of status-led
        to its right — floor (2mm) is fine there, it has no override. */}
    <UsbCData pcbX={15.69} pcbY={-23.83} schX={-30} schY={0} />

    {/* the brain, bottom-left, sharing the bottom band with usb-c-data
        rather than stacking above it — stacking the two vertically (as
        place_board() would do by default) needs ~46mm of board height on
        its own; side by side needs ~25mm, which is why they are laid out
        this way instead of top-to-bottom. Position unchanged — its
        neighbours moved away from it instead, see each one's own note. */}
    <Rp2040Core pcbX={-11.12} pcbY={-7.28} schX={0} schY={0} />

    {/* logic rail: V5 -> V3_3 (RP2040 + flash only)
        pcbY moved +2.5mm (2026-08-17): old y=8.62 left rp2040-core only
        2.5mm below (needs 5mm now) and 2.5mm below the pixel row above it
        (unchanged, still needs the 2mm floor — see Ws2812Chain's own note
        for where that room came from). */}
    <Ldo3v3 u="U2" cin="C2" cout="C3" voutNet="V3_3" pcbX={-7.67} pcbY={11.12} schX={-15} schY={6} />

    {/* pixel rail: V5 -> V3_3_LED (the row only, its own SOT-223 —
        see the header note on why this is a second regulator, not a
        second use of U2). Same +2.5mm y move as U2, same reason. */}
    <Ldo3v3 u="U5" cin="C20" cout="C21" voutNet="V3_3_LED" pcbX={5.43} pcbY={11.12} schX={-15} schY={-6} />

    {/* extra bulk on the pixel rail: 8 WS2812s switching three channels
        each is a fast, spiky load the two per-regulator 10uF caps alone
        don't fully flatten (harness-puck carries the same two-cap pattern
        on its ring; one is enough at this pixel count and this proximity
        to U5). Moved with U5, same +2.5mm. */}
    <capacitor
      name="C22"
      capacitance="10uF"
      footprint="0805"
      pcbX={16}
      pcbY={11.12}
      schX={-8}
      schY={-6}
      schRotation="90deg"
      supplierPartNumbers={{ jlcpcb: ["C15850"] }}
    />
    <trace name="TR_C22_v" from=".C22 > .pin1" to="net.V3_3_LED" />
    <trace name="TR_C22_g" from=".C22 > .pin2" to="net.GND" />

    {/* the face: 8 addressable pixels across the top edge — read at eye
        level on a badge pinned to a shirt. This is the golden ws2812-chain
        block used exactly as shipped (count=8), not a hand-rolled ring like
        harness-puck's — a straight row is exactly what this block already
        does, so composing it needed no board-level pixel wiring at all.
        This is the one geometry this board may NOT move sideways or
        reshape — it stays a straight line across the top edge. pcbY moved
        +2.5mm (17.89 -> 20.39, 2026-08-17): that is purely to hand U2/U5
        the room they needed against rp2040-core's wider gap below them —
        the row itself is untouched otherwise, and this still leaves
        6.3mm clear of the board's top edge (more than the router's own
        4mm halo). */}
    <Ws2812Chain
      count={PIXELS}
      dinNet="LED_DATA"
      rail="V3_3_LED"
      pcbX={-24.23}
      pcbY={20.39}
      schX={10}
      schY={16}
    />

    {/* Test point on the chain's own unused net (2026-08-17): floating_net_
        warnings() (packages/circuitpy/src/circuitpy/checks.py) flagged
        `PX_18_DIN` reaching only D17 — the last pixel's DOUT, per
        ws2812-chain/BLOCK.md's PX_{start+count}_DIN convention (start=10,
        count=8 -> PX_18_DIN), same defect and same fix as
        rgb-lamp-controller's TP4: land the chain's own trailing net on a
        pad instead of leaving it a dead end. This is glue, not a new
        circuit — the net already exists inside the block. */}
    <testpoint name="TP4" footprintVariant="pad" padShape="circle" padDiameter="1mm"
      pcbX={31} pcbY={20.39} schX={18} schY={16} />
    <trace name="TR_TP4" from=".TP4 > .pin1" to="net.PX_18_DIN" />
    <silkscreentext text="DATA" pcbX={31} pcbY={18.7} fontSize={1} />

    {/* pattern controls, right edge, thumb reach. SW1/SW4 per sw-tact's own
        convention — rp2040-core already owns SW2 (BOOTSEL) and SW3 (RESET). */}
    <SwTact name="SW1" signal="BTN_MODE" pcbX={30} pcbY={-15} schX={22} schY={4} />
    <SwTact name="SW4" signal="BTN_BRIGHT" pcbX={30} pcbY={-3} schX={22} schY={0} />

    {/* proof of life on the logic rail */}
    <StatusLed led="LED1" r="R20" rail="V3_3" pcbX={30} pcbY={-22.5} schX={22} schY={-4} />

    {/* MCU I/O */}
    <trace name="TR_U3_leddata" from=".U3 > .GPIO16" to="net.LED_DATA" />
    <trace name="TR_U3_btnmode" from=".U3 > .GPIO14" to="net.BTN_MODE" />
    <trace name="TR_U3_btnbright" from=".U3 > .GPIO15" to="net.BTN_BRIGHT" />

    {/* SWD/SWCLK out to copper a probe can reach — left strip, open board
        space, well clear of rp2040-core's own footprint (see rp2040-core's
        BLOCK.md and glue.tsx's DebugPort doc: landing this inside the MCU
        block's box routes the pair through the crystal cluster).
        pcbX moved -30 -> -31 (2026-08-17): the old spot cleared
        rp2040-core's left edge by only ~4.3mm; -31 opens that to >=5mm,
        matching the same BLOCK_GAP_OVERRIDE_MM this board's other
        rp2040-core neighbours now honour. */}
    <DebugPort pcbX={-31} pcbY={-15} schX={-30} schY={-16} />

    {/* two mounting holes, left/right edge, clear of every footprint */}
    <MountingHole name="H1" diameter={3.2} pcbX={37.8} pcbY={0} />
    <MountingHole name="H2" diameter={3.2} pcbX={-37.8} pcbY={0} />

    {/* silkscreen: the name, and the rails at a glance. V3_3/LED3_3 labels
        moved +2.5mm in y with their regulators. */}
    <silkscreentext text="PIXEL BADGE" pcbX={0} pcbY={-27.5} fontSize={1.4} />
    <silkscreentext text="V3_3" pcbX={-7.67} pcbY={6} fontSize={0.9} />
    <silkscreentext text="LED3_3" pcbX={5.43} pcbY={6} fontSize={0.9} />

    {/* Ground plane, bottom layer — the same reasoning as harness-puck and
        terminal-keyboard: GND on signal-width track, an unreferenced USB
        pair, and copper that reads as tangled are three symptoms of the same
        missing plane. GndPour, not a bare <copperpour> — see blocks/glue.tsx
        for the margin math the pour solver's 32-gon needs. */}
    <GndPour layer="bottom" />
  </board>
)
