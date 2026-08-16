/**
 * Autonomous Harness — the puck. Press it to hand work to the AI coding
 * agents you already run; the ring around the rim is the fleet, in light.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-data, ldo-3v3 (x2), rp2040-core, ws2812-chain
 *              (Ws2812Pixel + the chain's own wiring rules, bent into a ring),
 *              sw-tact (x2), status-led
 *
 * Rails:
 *   V5      USB-C VBUS, 5V @ up to 1.5A budgeted
 *   V3_3    U2 (AMS1117-3.3)  -> logic only: RP2040 + flash        ~100mA
 *   V3_3_LED U5 (AMS1117-3.3) -> the 8-pixel ring only              <=480mA
 * Two regulators, not one: 8 WS2812s at the block's worst-case 60mA/pixel is
 * 480mA on its own, and 480 + 100 = 580mA through one AMS1117 is 0.99W in a
 * SOT-223 — past ldo-3v3's stated <=500mA budget. Split across two packages
 * each stays inside the block's budget and the heat lands in two places.
 * ldo-3v3's BLOCK.md sanctions exactly this: a second domain gets its own
 * net name via `voutNet`.
 *
 * Envelope: 70 x 70 mm outline, rounded to a 70mm circle, 2 layers, 1.6mm.
 *
 * Sides: every part is on the TOP side, JLC economy single-side assembly.
 * The puck sits face-up: the board mounts pixel-side-up under a printed
 * diffuser ring, the button caps press down onto SW1/SW4 through the top
 * shell, and the USB-C receptacle exits the back of the rim through the
 * 45-degree gap in the ring.
 *
 * Nothing here was invented from a datasheet. The ring helper below places
 * the golden block's own `Ws2812Pixel` and repeats the two rules the
 * ws2812-chain block exists to enforce — one 100nF per pixel adjacent to that
 * pixel, one 330R damper on the first hop only — because the block lays its
 * pixels on a line and a puck needs them on a circle.
 */

import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { Ws2812Pixel } from "../blocks/ws2812-chain/ws2812-chain"
import { SwTact } from "../blocks/sw-tact/sw-tact"
import { StatusLed } from "../blocks/status-led/status-led"
import { GndPour, MountingHole } from "../blocks/glue"

/* ---- ring geometry ------------------------------------------------------ */
const PIXELS = 8
const RING_R = 28 // mm, pixel centres
const CAP_OFFSET = 4.2 // mm outboard of each pixel, its own 100nF
const START_DEG = 247.5 // first pixel: lower-left, just past the USB gap
const STEP_DEG = 45 // chain runs clockwise, so theta decreases
const START_INDEX = 10 // D10..D17 / C40..C47 — the block's refdes allocation
const LED_RAIL = "V3_3_LED"

const rad = (deg: number) => (deg * Math.PI) / 180
const r2 = (n: number) => Math.round(n * 1000) / 1000

/**
 * The ring. One `Ws2812Pixel` per slot, rotated tangentially so each pixel's
 * DOUT faces the next pixel's DIN, each with its own 100nF just outboard of
 * it, and a single 330R damper (R30) on the first hop out of the MCU.
 */
const PuckRing = () => {
  const slots = Array.from({ length: PIXELS }, (_, i) => i)
  return (
    // Explicit coordinates on every group: an unpositioned group triggers
    // auto-layout, which repacks the ring on top of the MCU (learned the
    // hard way in round 1 — see DESIGN-REVIEW.md).
    <group pcbX={0} pcbY={0} schX={0} schY={0}>
      {/* series damping resistor, first hop only — kept near the MCU pin */}
      <resistor
        name="R30"
        resistance="330"
        footprint="0402"
        pcbX={-17}
        pcbY={3}
        schX={-22}
        schY={-12}
        supplierPartNumbers={{ jlcpcb: ["C25104"] }}
      />
      <trace name="TR_R30_in" from=".R30 > .pin1" to="net.LED_DATA" />
      <trace name="TR_R30_out" from=".R30 > .pin2" to={`net.PX_${START_INDEX}_DIN`} />

      {slots.flatMap((i) => {
        const d = `D${START_INDEX + i}`
        const c = `C${40 + i}`
        const theta = START_DEG - i * STEP_DEG
        const rot = theta + 90 // local +x tangential; DIN faces CCW, DOUT CW
        const px = r2(RING_R * Math.cos(rad(theta)))
        const py = r2(RING_R * Math.sin(rad(theta)))
        const cx = r2((RING_R + CAP_OFFSET) * Math.cos(rad(theta)))
        const cy = r2((RING_R + CAP_OFFSET) * Math.sin(rad(theta)))
        return [
          <Ws2812Pixel
            key={d}
            name={d}
            pcbX={px}
            pcbY={py}
            pcbRotation={rot}
            schX={-17 + i * 5}
            schY={-12}
          />,
          <capacitor
            key={c}
            name={c}
            capacitance="100nF"
            footprint="0402"
            pcbX={cx}
            pcbY={cy}
            pcbRotation={rot}
            schX={-17 + i * 5}
            schY={-16}
            schRotation="90deg"
            supplierPartNumbers={{ jlcpcb: ["C1525"] }}
          />,
          <trace key={`${d}v`} name={`TR_${d}_vdd`} from={`.${d} > .VDD`} to={`net.${LED_RAIL}`} />,
          <trace key={`${d}g`} name={`TR_${d}_gnd`} from={`.${d} > .GND`} to="net.GND" />,
          <trace key={`${c}v`} name={`TR_${c}_v`} from={`.${c} > .pin1`} to={`net.${LED_RAIL}`} />,
          <trace key={`${c}g`} name={`TR_${c}_g`} from={`.${c} > .pin2`} to="net.GND" />,
          <trace
            key={`${d}i`}
            name={`TR_${d}_din`}
            from={`.${d} > .DIN`}
            to={`net.PX_${START_INDEX + i}_DIN`}
          />,
          <trace
            key={`${d}o`}
            name={`TR_${d}_dout`}
            from={`.${d} > .DOUT`}
            to={`net.PX_${START_INDEX + i + 1}_DIN`}
          />,
        ]
      })}
    </group>
  )
}

/**
 * Debug testpoints: SWCLK/SWD/GND as 1.0mm pads at 2.54mm pitch, outboard of
 * the flash (U4) in open copper — the location golden-blocks measures as
 * fab.ready. Pads, not plated holes, so no drills and no BOM rows.
 */
const DebugPort = (props: { pcbX?: number; pcbY?: number }) => {
  const px = props.pcbX ?? 0
  const py = props.pcbY ?? 0
  const nets = ["SWCLK", "SWD", "GND"]
  const labels = ["CLK", "DIO", "GND"]
  const pitch = 2.54
  const diameter = 1.0
  const first = -((nets.length - 1) * pitch) / 2
  return (
    <group pcbX={px} pcbY={py} pcbRotation={0} schX={30} schY={-12}>
      {nets.flatMap((net, i) => {
        const name = `TP${i + 1}`
        return [
          <testpoint key={name} name={name} footprintVariant="pad"
            padShape="circle" padDiameter={`${diameter}mm`}
            pcbX={first + i * pitch} pcbY={0} schX={i * 2} schY={0} />,
          <trace key={`${name}_t`} name={`TR_${name}`}
            from={`.${name} > .pin1`} to={`net.${net}`} />,
          <silkscreentext key={`${name}_s`} text={labels[i] ?? net}
            pcbX={first + i * pitch} pcbY={-1.8} fontSize={1} />,
        ]
      })}
    </group>
  )
}

export default () => (
  <board
    width="70mm"
    height="70mm"
    borderRadius={35}
    thickness={1.6}
    minTraceWidth="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* power + USB device: receptacle on the back rim, in the ring's gap */}
    <UsbCData pcbX={0} pcbY={-29} schX={-42} schY={4} />

    {/*
      NOTE (2026-08-10): two <keepout shape="circle" radius="0.6mm"> at
      (+/-2.9, -28.094) — over the TYPE-C footprint's NPTH alignment holes —
      do clear the two [hole_clearance] errors those holes cause. They are NOT
      in the board today: with them in place the autorouter reroutes and comes
      back with worse defects elsewhere (a via inside U2.VOUT1's pad, plus V5
      and LED_DATA left unconnected). See DESIGN-REVIEW.md, build round 9.
    */}

    {/* logic rail: V5 -> V3_3 (RP2040 + flash only) */}
    <Ldo3v3 u="U2" cin="C2" cout="C3" voutNet="V3_3" pcbX={11} pcbY={-11} schX={-28} schY={12} />

    {/* pixel rail: V5 -> V3_3_LED (the ring only, its own SOT-223) */}
    <Ldo3v3
      u="U5"
      cin="C20"
      cout="C21"
      voutNet={LED_RAIL}
      pcbX={-12}
      pcbY={-11}
      schX={-28}
      schY={2}
    />

    {/* extra bulk on the pixel rail: eight WS2812s switch three channels each */}
    <capacitor
      name="C22"
      capacitance="10uF"
      footprint="0805"
      pcbX={-18.5}
      pcbY={-19.5}
      schX={-26}
      schY={-4}
      schRotation="90deg"
      supplierPartNumbers={{ jlcpcb: ["C15850"] }}
    />
    <capacitor
      name="C23"
      capacitance="10uF"
      footprint="0805"
      pcbX={18.5}
      pcbY={-19.5}
      schX={-23}
      schY={-4}
      schRotation="90deg"
      supplierPartNumbers={{ jlcpcb: ["C15850"] }}
    />
    <trace name="TR_C22_v" from=".C22 > .pin1" to={`net.${LED_RAIL}`} />
    <trace name="TR_C22_g" from=".C22 > .pin2" to="net.GND" />
    <trace name="TR_C23_v" from=".C23 > .pin1" to={`net.${LED_RAIL}`} />
    <trace name="TR_C23_g" from=".C23 > .pin2" to="net.GND" />

    {/* the brain: RP2040 minimal system, upper half of the disc */}
    <Rp2040Core pcbX={-2} pcbY={11} schX={-2} schY={6} />

    {/* the face: eight addressable pixels around the rim */}
    <PuckRing />

    {/* Press-to-delegate, centre-front, under the printed key cap.
        pcbY -3 -> -4.2 on 2026-08-16: at -3 the switch courtyard's top edge sits
        at -0.775 and rp2040-core's crystal Y1 reaches down to -0.95, so the two
        courtyards overlapped by 0.175mm. tscircuit treats a courtyard overlap as
        a *placement* error and then skips autorouting entirely — one 0.175mm
        conflict produced 137 blocking findings, 38 of them unconnected ports.
        Y1 cannot move: it is inside the golden block and already sits at the
        edge of the RP2040's 10mm XIN limit. Nothing occupies the board below
        SW1, so the switch moves instead, leaving 0.825mm of courtyard air. The
        cap that presses it moves 1.2mm toward the board's front edge with it. */}
    <SwTact name="SW1" signal="BTN_GO" pcbX={-2} pcbY={-4.2} schX={22} schY={10} />

    {/* mode / long-press companion, right rim */}
    <SwTact name="SW4" signal="BTN_MODE" pcbX={18.5} pcbY={2} schX={22} schY={5} />

    {/* proof of life on the logic rail — the first thing to look at on bring-up */}
    <StatusLed led="LED1" r="R20" rail="V3_3" pcbX={-20} pcbY={-3} schX={22} schY={0} />

    {/* MCU I/O */}
    <trace name="TR_U3_leddata" from=".U3 > .GPIO16" to="net.LED_DATA" />
    <trace name="TR_U3_btngo" from=".U3 > .GPIO14" to="net.BTN_GO" />
    <trace name="TR_U3_btnmode" from=".U3 > .GPIO15" to="net.BTN_MODE" />

    {/* SWD/SWCLK out to copper a probe can reach */}
    <DebugPort pcbX={18.5} pcbY={9.5} />

    {/* three M2 holes at 120 degrees on a 22.8mm radius — the printed body's standoffs */}
    <MountingHole name="H1" diameter={2.2} pcbX={0} pcbY={22} />
    <MountingHole name="H2" diameter={2.2} pcbX={-19.75} pcbY={-11.4} />
    <MountingHole name="H3" diameter={2.2} pcbX={19.75} pcbY={-11.4} />

    {/* silkscreen: the name, and where to put a probe */}
    <silkscreentext text="AUTONOMOUS HARNESS" pcbX={0} pcbY={19.2} fontSize={1} />
    <silkscreentext text="3V3" pcbX={6.5} pcbY={-20.5} fontSize={1} />
    <silkscreentext text="LED3V3" pcbX={-8.5} pcbY={-20.5} fontSize={1} />
    <silkscreentext text="5V" pcbX={12} pcbY={-24.5} fontSize={1} />

    {/* Ground plane on the bottom layer — see the same note on
        terminal-keyboard. Three of the EE's 2026-08-15 findings are the same
        missing thing: GND routed as signal-width track, a USB pair with 0%
        reference plane, and copper that reads as messy because every ground
        return is one more line in the same channels. GndPour, not a bare
        <copperpour>: three M2 drills, and the pour solver's 32-gon measures
        short at the chord midpoints (ledger #2). */}
    <GndPour layer="bottom" />
  </board>
)
