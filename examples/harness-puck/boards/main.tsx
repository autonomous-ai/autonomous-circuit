/**
 * Autonomous Harness — the puck. Press it to hand work to the AI coding
 * agents you already run; the ring around the rim is the fleet, in light.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-data, ldo-3v3, rp2040-core, ws2812-level-shifter,
 *              ws2812-chain (Ws2812Pixel + the chain's own wiring rules,
 *              bent into a ring), sw-tact (x2), status-led
 *
 * Rails:
 *   VBUS_RAW  connector attach rail (authored inside usb-c-data)
 *   V5        board-owned trunk fed from the USB raw rail (5V)
 *   V3_3      U2 (AP7361C-33) logic rail for RP2040/flash + shifter
 *   V5        -> 8-pixel ring, 480mA physical peak / 280mA firmware cap
 *   U6 (SN74AHCT1G125) translates the RP2040's 3.3V data to a valid 5V
 *   WS2812 input.
 *
 * Envelope: 70 x 70 mm outline, rounded to a 70mm circle, 2 layers, 1.6mm.
 *
 * The ring helper below places the golden block's own `Ws2812Pixel` and
 * repeats the two rules the ws2812-chain block exists to enforce — one 100nF
 * per pixel adjacent to that pixel, one 330R damper on the first hop only —
 * because the block lays its pixels on a line and a puck needs them on a
 * circle.
 */

import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { Ws2812Pixel } from "../blocks/ws2812-chain/ws2812-chain"
import { Ws2812LevelShifter } from "../blocks/ws2812-level-shifter/ws2812-level-shifter"
import { SwTact } from "../blocks/sw-tact/sw-tact"
import { StatusLed } from "../blocks/status-led/status-led"
import { GndFanoutTrace, GndPlanes, MountingHole, PowerTrunk } from "../blocks/glue"

/* ---- ring geometry ------------------------------------------------------ */
const PIXELS = 8
const RING_R = 28 // mm, pixel centres
const PIXEL_VDD_LOCAL = { x: -2.475, y: 1.6 } as const
// Match the compiled reusable chain geometry: the horizontal 0402's pin1 is
// 0.51mm left of its body centre, so this placement puts that pad directly
// above VDD in pixel-local coordinates.  The resulting fixed branch is 1.8mm.
const PIXEL_CAP_CENTER_LOCAL = { x: -1.965, y: 3.4 } as const
const PIXEL_CAP_PIN1_LOCAL = { x: -2.475, y: 3.4 } as const
const START_DEG = 247.5 // first pixel: lower-left, just past the USB gap
const STEP_DEG = 45 // chain runs clockwise, so theta decreases
const START_INDEX = 10 // D10..D17 / C40..C47 — the block's refdes allocation
const LED_DATA_3V3 = "LED_DATA_3V3"
const LED_DATA_5V = "LED_DATA_5V"
const SIGNAL_TRACE_WIDTH = "0.25mm"
const LOCAL_POWER_TRACE_WIDTH = "0.2mm"
const POWER_RAIL_TRACE_WIDTH = "0.8mm"

const rad = (deg: number) => (deg * Math.PI) / 180
const r2 = (n: number) => Math.round(n * 1000) / 1000
const rotateLocal = (point: { x: number; y: number }, degrees: number) => ({
  x: point.x * Math.cos(rad(degrees)) - point.y * Math.sin(rad(degrees)),
  y: point.x * Math.sin(rad(degrees)) + point.y * Math.cos(rad(degrees)),
})

/**
 * The critical RP2040 phase regions are board-global.  They deliberately stop
 * at the central electronics cluster instead of making the clock/QSPI solver
 * search the whole 70mm puck and its LED ring.
 */
const RP_CRITICAL_ROUTING_REGION = {
  minX: -18,
  maxX: 12,
  minY: -2,
  maxY: 29,
} as const

/** USB connector-local power + data region at the bottom rim. */
const USB_ROUTING_REGION = {
  minX: -12,
  maxX: 12,
  minY: -36,
  maxY: -16,
} as const

/** Logic power corridor between the LDO and the RP2040. */
const POWER_ROUTING_REGION = {
  minX: -24,
  maxX: 24,
  minY: -22,
  maxY: 30,
} as const

/** Debug / control / GPIO corridor. */
const CONTROL_ROUTING_REGION = {
  minX: -24,
  maxX: 24,
  minY: -8,
  maxY: 22,
} as const

/** Evenly-spaced polar rows fit a circular product better than a square via
 * grid.  The inner row omits its north point because that coordinate is the
 * flash package, not because a DRC result was nudged around after routing. */
const polarRing = (radius: number, count: number, startDeg = 0) =>
  Array.from({ length: count }, (_, index) => {
    const theta = startDeg + (360 * index) / count
    return {
      x: r2(radius * Math.cos(rad(theta))),
      y: r2(radius * Math.sin(rad(theta))),
    }
  })

const GND_STITCHING_VIAS = [
  ...polarRing(34, 16),
  ...polarRing(23.5, 12).filter((_point, index) => index !== 3),
]

/**
 * The ring. One `Ws2812Pixel` per slot, rotated tangentially so each pixel's
 * DOUT faces the next pixel's DIN, each with its own 100nF continued outward
 * from the rotated VDD pad, and a single 330R damper (R30) on the first hop.
 */
const PuckRing = () => {
  const slots = Array.from({ length: PIXELS }, (_, i) => i)
  return (
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
      <trace name="TR_R30_in" from=".R30 > .pin1" to={`net.${LED_DATA_5V}`}
        thickness={SIGNAL_TRACE_WIDTH} />
      <trace name="TR_R30_out" from=".R30 > .pin2" to={`net.PX_${START_INDEX}_DIN`}
        thickness={SIGNAL_TRACE_WIDTH} />

      {slots.flatMap((i) => {
        const d = `D${START_INDEX + i}`
        const c = `C${40 + i}`
        const theta = START_DEG - i * STEP_DEG
        const rot = theta + 90 // local +x tangential; DIN faces CCW, DOUT CW
        const px = r2(RING_R * Math.cos(rad(theta)))
        const py = r2(RING_R * Math.sin(rad(theta)))
        const capCenterOffset = rotateLocal(PIXEL_CAP_CENTER_LOCAL, rot)
        const cx = r2(px + capCenterOffset.x)
        const cy = r2(py + capCenterOffset.y)
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
          // pcbPath is interpreted in the `from` component's local frame.
          <trace key={`${d}v`} name={`TR_${d}_vdd`}
            from={`.${d} > .VDD`} to={`.${c} > .pin1`}
            thickness={LOCAL_POWER_TRACE_WIDTH} maxLength="2mm"
            pcbPath={[PIXEL_VDD_LOCAL, PIXEL_CAP_PIN1_LOCAL]} />,
          <GndFanoutTrace key={`${d}g`} name={`TR_${d}_gnd`} from={`.${d} > .GND`} />,
          <trace key={`${c}v`} name={`TR_${c}_v`} from={`.${c} > .pin1`} to="net.V5"
            thickness={POWER_RAIL_TRACE_WIDTH} authoredNetTreeBoundary />,
          <GndFanoutTrace key={`${c}g`} name={`TR_${c}_g`} from={`.${c} > .pin2`} />,
          <trace
            key={`${d}i`}
            name={`TR_${d}_din`}
            from={`.${d} > .DIN`}
            to={`net.PX_${START_INDEX + i}_DIN`}
            thickness={SIGNAL_TRACE_WIDTH}
          />,
          <trace
            key={`${d}o`}
            name={`TR_${d}_dout`}
            from={`.${d} > .DOUT`}
            to={`net.PX_${START_INDEX + i + 1}_DIN`}
            thickness={SIGNAL_TRACE_WIDTH}
          />,
        ]
      })}
    </group>
  )
}

export const HarnessPuck = (props: { routingDisabled?: boolean } = {}) => (
  <board
    width="70mm"
    height="70mm"
    borderRadius={35}
    thickness={1.6}
    routingDisabled={props.routingDisabled ?? false}
    minTraceWidth="0.2mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    <autoroutingphase name="rp-clock" phaseIndex={0} region={RP_CRITICAL_ROUTING_REGION} />
    <autoroutingphase name="rp-qspi" phaseIndex={1} region={RP_CRITICAL_ROUTING_REGION} />
    <autoroutingphase name="usb-connector-pair" phaseIndex={2} region={USB_ROUTING_REGION} />
    <autoroutingphase name="usb-series-pair" phaseIndex={3} region={USB_ROUTING_REGION} />
    <autoroutingphase name="usb-cc" phaseIndex={4} region={USB_ROUTING_REGION} />
    <autoroutingphase name="usb-local-power" phaseIndex={5} region={USB_ROUTING_REGION} />
    <autoroutingphase name="rp-power" phaseIndex={6} region={POWER_ROUTING_REGION} />
    <autoroutingphase name="rp-control" phaseIndex={8} region={CONTROL_ROUTING_REGION} />
    <autoroutingphase name="mcu-io" phaseIndex={9} region={POWER_ROUTING_REGION} />
    <GndPlanes
      pours={[
        { name: "GND_TOP", layer: "top", boardEdgeMarginMm: 0.25 },
        { name: "GND_BOTTOM", layer: "bottom", boardEdgeMarginMm: 0.25 },
      ]}
      stitchingVias={GND_STITCHING_VIAS}
      viaOuterDiameterMm={0.6}
      viaHoleDiameterMm={0.3}
    />

    {/*
      Source branches are explicit trees. The USB-C raw rail is owned by
      usb-c-data (hidden nodes N1/N2 boundaries, N3 rail, N4 clamp on net
      VBUS_RAW). The board takes a 5V trunk off that raw rail node and feeds
      the whole puck's V5 net.
    */}
    <PowerTrunk
      name="V5_MAIN"
      source=".N3 > .pin1"
      net="V5"
      layer="top"
      start={{ x: 2.4, y: -25.4 }}
      end={{ x: -9.5, y: -20 }}
      startTestpoint="TP4"
      endTestpoint="TP5"
      trunkWidthMm={0.8}
      neckdownWidthMm={0.2}
    />
    <PowerTrunk
      name="V3V3_MAIN"
      source=".U2 > .VOUT"
      net="V3_3"
      layer="top"
      start={{ x: 17.5, y: -8.7 }}
      end={{ x: 9, y: -2 }}
      startTestpoint="TP6"
      endTestpoint="TP7"
      trunkWidthMm={0.8}
      neckdownWidthMm={0.2}
    />

    {/* Power + USB device: J1's cable-insertion centre lands 0.052mm beyond
        the routed bottom edge. */}
    <UsbCData
      pcbX={0}
      pcbY={-29}
      schX={-42}
      schY={4}
      vbusBoundaryRefs={{ right: "N1", left: "N2" }}
      vbusRailNodeRef="N3"
      vbusClampNodeRef="N4"
      pairRules={{ pcbTraceGapMm: 0.15, maxLengthSkewMm: 3.8, maxUncoupledLengthMm: 3 }}
      localRoutingPhaseIndex={5}
      dpConnectorRoutingPhaseIndex={2}
      dmConnectorRoutingPhaseIndex={2}
      pairRoutingPhaseIndex={2}
      connectorPairRoutingPhaseIndex={2}
      seriesPairRoutingPhaseIndex={3}
      cc1RoutingPhaseIndex={4}
      cc2RoutingPhaseIndex={4}
      powerRoutingPhaseIndex={5}
      criticalSignalWidthMm={0.15}
      signalTraceWidthMm={0.25}
    />

    {/* logic rail: V5 -> V3_3 (RP2040 + flash only) */}
    <Ldo3v3
      u="U2"
      cin="C2"
      cout="C3"
      vinNet="V5"
      voutNet="V3_3"
      externalPowerTrunkPort="VOUT"
      railWidthMm={0.8}
      pinNeckdownWidthMm={0.2}
      maxPinNeckdownLengthMm={2}
      pcbX={11}
      pcbY={-11}
      schX={-28}
      schY={12}
    />

    {/* Valid 3.3V GPIO -> 5V pixel-data boundary. /OE is hard-low and C20 is
        frozen into the sourced block; R30 remains after the buffer output. */}
    <Ws2812LevelShifter
      pcbX={-13}
      pcbY={0}
      schX={-28}
      schY={2}
      inputNet={LED_DATA_3V3}
      outputNet={LED_DATA_5V}
    />

    {/* extra bulk on V5: eight WS2812s switch three channels each */}
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
    <trace name="TR_C22_v" from=".C22 > .pin1" to="net.V5"
      thickness={POWER_RAIL_TRACE_WIDTH} authoredNetTreeBoundary />
    <GndFanoutTrace name="TR_C22_g" from=".C22 > .pin2" />
    <trace name="TR_C23_v" from=".C23 > .pin1" to="net.V5"
      thickness={POWER_RAIL_TRACE_WIDTH} authoredNetTreeBoundary />
    <GndFanoutTrace name="TR_C23_g" from=".C23 > .pin2" />

    {/* the brain: RP2040 minimal system, upper half of the disc */}
    <Rp2040Core
      pcbX={-2}
      pcbY={6}
      schX={-2}
      schY={6}
      debugPortPcbX={16}
      debugPortPcbY={1}
      debugSwclkBoundaryRef="N24"
      debugSwdBoundaryRef="N25"
      debugSignalTraceWidthMm={0.25}
      criticalSignalWidthMm={0.15}
      localPowerRoutingPhaseIndex={6}
      controlRoutingPhaseIndex={8}
      powerRailNodeRefs={{
        westUpper: "N10",
        westLower: "N11",
        south: "N12",
        eastLower: "N13",
        eastUpper: "N14",
        topRight: "N15",
        topMiddle: "N16",
        topLeft: "N17",
        bulk: "N18",
        flash: "N19",
        dvddLeft: "N20",
        dvddRight: "N21",
        dvddSouth: "N22",
        dvddJunction: "N23",
      }}
      buttonVariant="compact"
    />

    {/* the face: eight addressable pixels around the rim */}
    <PuckRing />

    {/* press-to-delegate, centre-front, under the printed key cap */}
    <SwTact name="SW1" signal="BTN_GO" pcbX={-2} pcbY={-7} schX={22} schY={10} />

    {/* mode / long-press companion, right rim */}
    <SwTact name="SW4" signal="BTN_MODE" pcbX={18.5} pcbY={2} schX={22} schY={5} />

    {/* proof of life on the logic rail — the first thing to look at on bring-up */}
    <StatusLed
      led="LED1"
      r="R20"
      rail="V3_3"
      railTraceWidthMm={0.2}
      signalTraceWidthMm={0.25}
      maxRailNeckdownLengthMm={3}
      maxSeriesTraceLengthMm={3}
      pcbX={-20}
      pcbY={-3}
      schX={22}
      schY={0}
    />

    {/* MCU I/O */}
    <trace name="TR_U3_leddata" from=".U3 > .GPIO16" to={`net.${LED_DATA_3V3}`}
      thickness={SIGNAL_TRACE_WIDTH} routingPhaseIndex={9} />
    <trace name="TR_U3_btngo" from=".U3 > .GPIO14" to="net.BTN_GO"
      thickness={SIGNAL_TRACE_WIDTH} routingPhaseIndex={9} />
    <trace name="TR_U3_btnmode" from=".U3 > .GPIO15" to="net.BTN_MODE"
      thickness={SIGNAL_TRACE_WIDTH} routingPhaseIndex={9} />

    {/* three M2 holes at 120 degrees on a 22.8mm radius — the printed body's standoffs */}
    <MountingHole name="H1" diameter={2.2} pcbX={0} pcbY={30} />
    <MountingHole name="H2" diameter={2.2} pcbX={-19.75} pcbY={-11.4} />
    <MountingHole name="H3" diameter={2.2} pcbX={19.75} pcbY={-11.4} />

    {/* silkscreen: the name, and where to put a probe */}
    <silkscreentext text="AUTONOMOUS HARNESS" pcbX={0} pcbY={19.2} fontSize={1} />
    <silkscreentext text="3V3" pcbX={6.5} pcbY={-20.5} fontSize={1} />
    <silkscreentext text="LED5V" pcbX={-8.5} pcbY={-20.5} fontSize={1} />
    <silkscreentext text="5V" pcbX={12} pcbY={-24.5} fontSize={1} />
  </board>
)

export default () => <HarnessPuck />
