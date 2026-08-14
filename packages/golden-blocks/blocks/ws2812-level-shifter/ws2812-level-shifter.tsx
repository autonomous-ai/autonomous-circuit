/**
 * golden-block: ws2812-level-shifter (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * A 3.3V-to-5V data translator for a 5V WS2812 chain.  The AHCT input has
 * a 2.0V minimum VIH at VCC=4.5..5.5V, so an RP2040's 3.3V output is valid;
 * the buffer then presents a full 5V-domain signal to the first pixel.
 * /OE is hard-low (always enabled) and the required 100nF bypass capacitor
 * is part of the block.  The chain's 330R series damper stays downstream of
 * this block, adjacent to the first data hop.
 *
 * U6 copper was imported exactly from JLCPCB/LCSC C7484 with:
 *   tscircuit-cli import C7484 --jlcpcb --use-exact-footprint
 * on 2026-08-11.  Do not replace it with an assumed generic SOT-23-5.
 *
 * Default refdes (global v1 allocation): U6, C20.
 */

import { GndFanoutTrace } from "../glue"

const ahct125PinLabels = {
  pin1: ["OE"], // active-low; frozen to GND below
  pin2: ["A"],
  pin3: ["GND"],
  pin4: ["Y"],
  pin5: ["VCC"],
} as const

export const Sn74ahct1g125 = (props: {
  name: string
  layer?: "top" | "bottom"
  pcbX?: number | string
  pcbY?: number | string
  pcbRotation?: number | string
  schX?: number
  schY?: number
}) => (
  <chip
    {...props}
    pinLabels={ahct125PinLabels}
    pinAttributes={{
      VCC: { requiresPower: true },
      GND: { requiresGround: true },
    }}
    supplierPartNumbers={{ jlcpcb: ["C7484"] }}
    manufacturerPartNumber="SN74AHCT1G125DBVR"
    schPinArrangement={{
      leftSide: { pins: ["OE", "A"], direction: "top-to-bottom" },
      rightSide: { pins: ["Y"], direction: "top-to-bottom" },
      topSide: { pins: ["VCC"], direction: "left-to-right" },
      bottomSide: { pins: ["GND"], direction: "left-to-right" },
    }}
    footprint={
      <footprint>
        {/* Exact EasyEDA copper for C7484 (DBV / SOT-23-5), imported 2026-08-11. */}
        <smtpad portHints={["pin5"]} pcbX="-1.300099mm" pcbY="-0.94996mm" width="1.0999978mm" height="0.5999988mm" shape="rect" />
        <smtpad portHints={["pin4"]} pcbX="-1.300099mm" pcbY="0.94996mm" width="1.0999978mm" height="0.5999988mm" shape="rect" />
        <smtpad portHints={["pin3"]} pcbX="1.300099mm" pcbY="0.94996mm" width="1.0999978mm" height="0.5999988mm" shape="rect" />
        <smtpad portHints={["pin2"]} pcbX="1.300099mm" pcbY="0mm" width="1.0999978mm" height="0.5999988mm" shape="rect" />
        <smtpad portHints={["pin1"]} pcbX="1.300099mm" pcbY="-0.94996mm" width="1.0999978mm" height="0.5999988mm" shape="rect" />
        <silkscreenpath route={[{ x: 0.8999728, y: -1.404112 }, { x: 0.8999728, y: -1.5500604 }]} />
        <silkscreenpath route={[{ x: 0.8999728, y: -0.4541266 }, { x: 0.8999728, y: -0.495808 }]} />
        <silkscreenpath route={[{ x: 0.8999728, y: 0.495808 }, { x: 0.8999728, y: 0.454152 }]} />
        <silkscreenpath route={[{ x: 0.8999728, y: 1.5499588 }, { x: 0.8999728, y: 1.404112 }]} />
        <silkscreenpath route={[{ x: -0.900049, y: -1.404112 }, { x: -0.900049, y: -1.5500604 }]} />
        <silkscreenpath route={[{ x: -0.900049, y: 0.495808 }, { x: -0.900049, y: -0.4958334 }]} />
        <silkscreenpath route={[{ x: -0.900049, y: 1.5499588 }, { x: -0.900049, y: 1.4040866 }]} />
        <silkscreenpath route={[{ x: -0.900049, y: 1.5499588 }, { x: 0.8999728, y: 1.5499588 }]} />
        <silkscreenpath route={[{ x: -0.900049, y: -1.5500604 }, { x: 0.8999728, y: -1.5500604 }]} />
        <courtyardoutline outline={[
          { x: -2.091881, y: 1.812354 },
          { x: 2.116519, y: 1.812354 },
          { x: 2.116519, y: -1.786446 },
          { x: -2.091881, y: -1.786446 },
          { x: -2.091881, y: 1.812354 },
        ]} />
      </footprint>
    }
  />
)

export const Ws2812LevelShifter = (props: {
  u?: string
  c?: string
  inputNet?: string
  outputNet?: string
  signalTraceWidthMm?: number
  localPowerWidthMm?: number
  railTrunkWidthMm?: number
  maxDecouplingLengthMm?: number
  layer?: "top" | "bottom"
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const u = props.u ?? "U6"
  const c = props.c ?? "C20"
  const input = props.inputNet ?? "LED_DATA_3V3"
  const output = props.outputNet ?? "LED_DATA_5V"
  const signalTraceWidthMm = props.signalTraceWidthMm ?? 0.25
  const localPowerWidthMm = props.localPowerWidthMm ?? 0.2
  const railTrunkWidthMm = props.railTrunkWidthMm ?? 0.8
  const maxDecouplingLengthMm = props.maxDecouplingLengthMm ?? 2
  const layer = props.layer ?? "top"
  // Bottom copper mirrors package-local pad offsets. Apply the same transform
  // to every authored center, rotation, and path so the bypass stays pin-facing.
  const localX = (x: number) => layer === "bottom" ? -x : x
  const localRotation = (degrees: number) =>
    layer === "bottom" ? (360 - degrees) % 360 : degrees
  if (
    ![signalTraceWidthMm, localPowerWidthMm, railTrunkWidthMm,
      maxDecouplingLengthMm].every((value) => Number.isFinite(value) && value > 0) ||
    localPowerWidthMm > railTrunkWidthMm
  ) {
    throw new Error(
      "Ws2812LevelShifter dimensions must be positive with local power <= rail trunk",
    )
  }

  return (
    <group
      name={`__parts_block__ws2812-level-shifter__${u}`}
      pcbX={props.pcbX ?? 0}
      pcbY={props.pcbY ?? 0}
      schX={props.schX ?? 0}
      schY={props.schY ?? 0}
    >
      <Sn74ahct1g125 name={u} layer={layer} pcbX={0} pcbY={0} schX={0} schY={0} />
      {/* C20 is 1.9mm from the VCC pad centre and belongs to this IC, not to
          a board-level bulk-cap budget. */}
      <capacitor
        name={c}
        capacitance="100nF"
        footprint="0402"
        layer={layer}
        pcbX={localX(-3.2)}
        pcbY={-0.95}
        pcbRotation={localRotation(180)}
        schX={0}
        schY={-2.5}
        schRotation="90deg"
        supplierPartNumbers={{ jlcpcb: ["C1525"] }}
      />

      <GndFanoutTrace name={`TR_${u}_oe`} from={`.${u} > .OE`} />
      <trace name={`TR_${u}_a`} from={`.${u} > .A`} to={`net.${input}`}
        thickness={`${signalTraceWidthMm}mm`} />
      <GndFanoutTrace name={`TR_${u}_gnd`} from={`.${u} > .GND`} />
      <trace name={`TR_${u}_y`} from={`.${u} > .Y`} to={`net.${output}`}
        thickness={`${signalTraceWidthMm}mm`} />
      <trace name={`TR_${u}_vcc`} from={`.${u} > .VCC`} to={`.${c} > .pin1`}
        thickness={`${localPowerWidthMm}mm`}
        maxLength={`${maxDecouplingLengthMm}mm`}
        pcbPath={[
          { x: localX(-1.300099), y: -0.94996 },
          { x: localX(-2.69), y: -0.95 },
        ]} />
      <trace name={`TR_${c}_v`} from={`.${c} > .pin1`} to="net.V5"
        thickness={`${railTrunkWidthMm}mm`} authoredNetTreeBoundary />
      <GndFanoutTrace name={`TR_${c}_g`} from={`.${c} > .pin2`} />
    </group>
  )
}

export default Ws2812LevelShifter
