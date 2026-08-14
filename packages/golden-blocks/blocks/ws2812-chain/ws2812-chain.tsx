/**
 * golden-block: ws2812-chain (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * A chain of addressable RGB LEDs (WS2812B-B/T 5050, LCSC C2761795) driven
 * from one GPIO. Each pixel takes DIN from the previous pixel's DOUT, so the
 * whole strip is one signal net per link plus a shared 5V rail.
 *
 * Two rules this block exists to enforce, both of which are silent failures
 * when a composer wires WS2812s by hand:
 *   - one 100nF decoupling capacitor per pixel, adjacent to that pixel. The
 *     part switches 3 constant-current channels at high speed; shared bulk
 *     alone browns out the far end of the chain.
 *   - a series resistor on the first DIN (330R) to damp the reflection on a
 *     long first hop and protect the GPIO. Later hops are driven by the
 *     previous LED and do not need one.
 *
 * Data levels: WS2812B expects VIH >= 0.7 * VDD. The chain therefore defaults
 * to net.V5 and consumes net.LED_DATA_5V from ws2812-level-shifter. Connecting
 * a raw 3.3V GPIO directly is outside the pixel's guaranteed input range.
 *
 * Default refdes: D10..D10+n, C40..C40+n, R30 (the series resistor).
 */

import { GndFanoutTrace, MaskedCopperNode } from "../glue"

const PIXEL_PITCH_MM = 7
const FIRST_RESISTOR_X_MM = 4.5
const CAP_PIN1_X_MM = -2.475
const CAP_PIN1_Y_MM = 3.4
const CAP_PIN1_FROM_BODY_X_MM = -0.51
const RAIL_NODE_Y_MM = 5.2

export const Ws2812Pixel = (props: {
  name: string
  layer?: "top" | "bottom"
  pcbX?: number
  pcbY?: number
  pcbRotation?: number | string
  schX?: number
  schY?: number
}) => (
  <chip
    {...props}
    pinLabels={{
      pin1: ["VDD"],
      pin2: ["DOUT"],
      pin3: ["GND"],
      pin4: ["DIN"],
    }}
    pinAttributes={{
      VDD: { requiresPower: true },
      GND: { requiresGround: true },
    }}
    supplierPartNumbers={{ jlcpcb: ["C2761795"] }}
    manufacturerPartNumber="WS2812B-B/T"
    schPinArrangement={{
      leftSide: { pins: ["DIN"], direction: "top-to-bottom" },
      rightSide: { pins: ["DOUT"], direction: "top-to-bottom" },
      topSide: { pins: ["VDD"], direction: "left-to-right" },
      bottomSide: { pins: ["GND"], direction: "left-to-right" },
    }}
    footprint={
      <footprint>
        {/* 5050 package, 4 pads on a 5.0 x 5.0mm body (datasheet land pattern) */}
        <smtpad portHints={["pin1"]} pcbX="-2.475mm" pcbY="1.6mm" width="1.5mm" height="1.4mm" shape="rect" />
        <smtpad portHints={["pin2"]} pcbX="-2.475mm" pcbY="-1.6mm" width="1.5mm" height="1.4mm" shape="rect" />
        <smtpad portHints={["pin3"]} pcbX="2.475mm" pcbY="-1.6mm" width="1.5mm" height="1.4mm" shape="rect" />
        <smtpad portHints={["pin4"]} pcbX="2.475mm" pcbY="1.6mm" width="1.5mm" height="1.4mm" shape="rect" />
        <silkscreenpath route={[
          { x: -2.5, y: 2.5 }, { x: 2.5, y: 2.5 }, { x: 2.5, y: -2.5 },
          { x: -2.5, y: -2.5 }, { x: -2.5, y: 2.5 },
        ]} />
        <courtyardoutline outline={[
          { x: -3.2, y: 2.8 }, { x: 3.2, y: 2.8 }, { x: 3.2, y: -2.8 },
          { x: -3.2, y: -2.8 }, { x: -3.2, y: 2.8 },
        ]} />
      </footprint>
    }
  />
)

const Ws2812RailEdge = (props: {
  name: string
  from: string
  to: string
  widthMm: number
  phaseIndex?: number
  deltaX: number
}) => (
  <trace
    name={props.name}
    from={props.from}
    to={props.to}
    thickness={`${props.widthMm}mm`}
    routingPhaseIndex={props.phaseIndex}
    pcbPathRelativeTo={props.from}
    pcbPath={[
      { x: 0, y: 0 },
      { x: props.deltaX, y: 0 },
    ]}
  />
)

export const Ws2812Chain = (props: {
  /** how many pixels in the chain */
  count?: number
  /** the GPIO net feeding the first pixel */
  dinNet?: string
  rail?: string
  /** first LED refdes number, e.g. 10 -> D10, D11, ... */
  startIndex?: number
  /** mm between pixel centres along x */
  pitch?: number
  /** Ordinary board-level data width; power is governed by its rail class. */
  signalTraceWidthMm?: number
  /** Fine escape from each pixel supply pad to its own bypass capacitor. */
  localPowerWidthMm?: number
  /** Width presented to the board-level pixel-supply backbone. */
  railTrunkWidthMm?: number
  /** Maximum routed pin-to-bypass path, not merely component-centre spacing. */
  maxDecouplingLengthMm?: number
  /** Maximum short bypass-capacitor escape before the wide V5 backbone. */
  maxRailNeckLengthMm?: number
  /**
   * One collision-free hidden copper ref per pixel. Defaults to N30.. in the
   * common single-chain composition; boards with more than one chain pass an
   * explicit disjoint allocation.
   */
  railNodeRefs?: readonly string[]
  /** Optional board-owned phase for the named V5 attachment. */
  railRoutingPhaseIndex?: number
  /** Optional count-aware ordered phases, one for each direct data hop. */
  dataRoutingPhaseIndices?: readonly number[]
  layer?: "top" | "bottom"
  r?: string
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const count = props.count ?? 1
  const din = props.dinNet ?? "LED_DATA_5V"
  const rail = props.rail ?? "V5"
  const start = props.startIndex ?? 10
  const pitch = props.pitch ?? PIXEL_PITCH_MM
  const signalTraceWidthMm = props.signalTraceWidthMm ?? 0.25
  const localPowerWidthMm = props.localPowerWidthMm ?? 0.2
  const railTrunkWidthMm = props.railTrunkWidthMm ?? 0.8
  const maxDecouplingLengthMm = props.maxDecouplingLengthMm ?? 2
  const maxRailNeckLengthMm = props.maxRailNeckLengthMm ?? 3
  const layer = props.layer ?? "top"
  // Mirror the complete authored block-local geometry for bottom placement;
  // mirroring footprints alone reverses pad offsets but strands their parts.
  const localX = (x: number) => layer === "bottom" ? -x : x
  const localRotation = (degrees: number) =>
    layer === "bottom" ? (360 - degrees) % 360 : degrees
  const r = props.r ?? "R30"
  const pixels = Array.from({ length: count }, (_, i) => i)
  const railNodeRefs = props.railNodeRefs
    ? [...props.railNodeRefs]
    : pixels.map((i) => `N${30 + i}`)
  const dataRoutingPhaseIndices = props.dataRoutingPhaseIndices
    ? [...props.dataRoutingPhaseIndices]
    : undefined
  if (
    !Number.isInteger(count) || count <= 0 ||
    ![pitch, signalTraceWidthMm, localPowerWidthMm, railTrunkWidthMm,
      maxDecouplingLengthMm, maxRailNeckLengthMm]
      .every((value) => Number.isFinite(value) && value > 0) ||
    localPowerWidthMm > railTrunkWidthMm
  ) {
    throw new Error(
      "Ws2812Chain needs a positive integer count, positive dimensions, and local power <= rail trunk",
    )
  }
  if (
    dataRoutingPhaseIndices && (
      dataRoutingPhaseIndices.length !== count ||
      dataRoutingPhaseIndices.some(
        (phase) => !Number.isInteger(phase) || phase < 0,
      ) ||
      new Set(dataRoutingPhaseIndices).size !== dataRoutingPhaseIndices.length
    )
  ) {
    throw new Error(
      "Ws2812Chain dataRoutingPhaseIndices must contain one unique non-negative integer per direct data hop",
    )
  }
  if (
    railNodeRefs.length !== count ||
    railNodeRefs.some((ref) => !/^N[1-9][0-9]*$/.test(ref)) ||
    new Set(railNodeRefs).size !== railNodeRefs.length
  ) {
    throw new Error(
      "Ws2812Chain railNodeRefs must contain exactly one unique non-probe N reference per pixel",
    )
  }

  return (
    <group name={`__parts_block__ws2812-chain__${r}`} pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      {/* The chain runs toward negative local X so each DOUT->DIN edge crosses
          only the gap between adjacent packages. This avoids the reversed-pad
          braid created by placing identical WS2812 footprints left-to-right. */}
      <resistor name={r} resistance="330" footprint="0402"
        layer={layer}
        pcbX={localX(FIRST_RESISTOR_X_MM)} pcbY={1.6}
        pcbRotation={localRotation(180)}
        schX={-3} schY={0}
        supplierPartNumbers={{ jlcpcb: ["C25104"] }} />
      <trace name={`TR_${r}_in`} from={`.${r} > .pin1`} to={`net.${din}`}
        thickness={`${signalTraceWidthMm}mm`} />
      <trace name={`TR_${r}_out`} from={`.${r} > .pin2`} to={`.D${start} > .DIN`}
        thickness={`${signalTraceWidthMm}mm`}
        routingPhaseIndex={dataRoutingPhaseIndices?.[0]} />

      {pixels.map((i) => {
        const d = `D${start + i}`
        const c = `C${40 + i}`
        const node = railNodeRefs[i]
        const x = -i * pitch
        return (
          // Explicit coordinates on the group: an unpositioned group triggers
          // auto-layout, which stacks the pixels instead of spacing them.
          <group key={d} pcbX={localX(x)} pcbY={0} schX={i * 4} schY={0}>
            <Ws2812Pixel name={d} layer={layer} pcbX={0} pcbY={0} schX={0} schY={0} />
            {/* The VDD pad is at (-2.475,+1.6). Pin 1 of the horizontal
                0402 lands at x=-2.475 when its body centre is x=-1.965,
                giving the supply escape a straight 1.8mm path. */}
            <capacitor name={c} capacitance="100nF" footprint="0402"
              layer={layer} pcbX={localX(-1.965)} pcbY={3.4}
              pcbRotation={localRotation(0)}
              schX={0} schY={-2.5} schRotation="90deg"
              supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
            <MaskedCopperNode
              name={node}
              diameterMm={railTrunkWidthMm}
              layer={layer}
              pcbX={localX(CAP_PIN1_X_MM)}
              pcbY={RAIL_NODE_Y_MM}
              schX={0}
              schY={-4}
            />
            <trace name={`TR_${d}_vdd`} from={`.${d} > .VDD`} to={`.${c} > .pin1`}
              thickness={`${localPowerWidthMm}mm`}
              maxLength={`${maxDecouplingLengthMm}mm`}
              pcbPath={[
                { x: localX(-2.475), y: 1.6 },
                { x: localX(-2.475), y: 3.4 },
              ]} />
            <trace
              name={`TR_${c}_${rail}_NECK`}
              from={`.${c} > .pin1`}
              to={`.${node} > .pin1`}
              thickness={`${localPowerWidthMm}mm`}
              maxLength={`${maxRailNeckLengthMm}mm`}
              pcbPath={[
                { x: localX(CAP_PIN1_FROM_BODY_X_MM), y: 0 },
                {
                  x: localX(CAP_PIN1_FROM_BODY_X_MM),
                  y: RAIL_NODE_Y_MM - CAP_PIN1_Y_MM,
                },
              ]}
            />
            <GndFanoutTrace name={`TR_${d}_gnd`} from={`.${d} > .GND`} />
            <GndFanoutTrace name={`TR_${c}_g`} from={`.${c} > .pin2`} />
            {i < count - 1 ? (
              <trace
                name={`TR_${d}_dout`}
                from={`.${d} > .DOUT`}
                to={`.D${start + i + 1} > .DIN`}
                thickness={`${signalTraceWidthMm}mm`}
                routingPhaseIndex={dataRoutingPhaseIndices?.[i + 1]}
              />
            ) : (
              <trace
                name={`TR_${d}_dout`}
                from={`.${d} > .DOUT`}
                to={`net.PX_${start + count}_DIN`}
                thickness={`${signalTraceWidthMm}mm`}
              />
            )}
          </group>
        )
      })}
      {pixels.slice(0, -1).map((i) => {
        const fromNode = railNodeRefs[i]
        const toNode = railNodeRefs[i + 1]
        return (
          <Ws2812RailEdge
            key={`rail-${i}`}
            name={`TR_${rail}_CHAIN_${start + i}_${start + i + 1}`}
            from={`.${fromNode} > .pin1`}
            to={`.${toNode} > .pin1`}
            widthMm={railTrunkWidthMm}
            phaseIndex={props.railRoutingPhaseIndex}
            deltaX={localX(-pitch)}
          />
        )
      })}
      <trace
        name={`TR_${rail}_CHAIN_ESCAPE`}
        from={`.${railNodeRefs[count - 1]} > .pin1`}
        to={`net.${rail}`}
        thickness={`${railTrunkWidthMm}mm`}
        routingPhaseIndex={props.railRoutingPhaseIndex}
        authoredNetTreeBoundary
      />
    </group>
  )
}

export default Ws2812Chain
