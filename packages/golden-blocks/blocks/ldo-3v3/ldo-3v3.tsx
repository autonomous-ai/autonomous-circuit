/**
 * golden-block: ldo-3v3 (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * 3.3V logic rail from 5V: AMS1117-3.3 (LCSC C6186, JLC Basic) with 10uF
 * input and output capacitors. In: net.V5 (default). Out: net.V3_3.
 *
 * Land pattern: exact EasyEDA footprint for C6186, imported once at
 * authoring time (tscircuit-cli import C6186 --jlcpcb, 2026-08-10).
 *
 * Default refdes (global v1 allocation): U2, C2, C3.
 */

import { GndFanoutTrace } from "../glue"

const ldoPinLabels = {
  pin1: ["GND"],
  pin2: ["VOUT1", "VOUT"],
  pin3: ["VIN"],
  pin4: ["VOUT2", "TAB"],
} as const

export const Ams1117_33 = (props: {
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
    pinLabels={ldoPinLabels}
    pinAttributes={{
      VIN: { requiresPower: true },
      VOUT: { providesPower: true },
      TAB: { providesPower: true },
      GND: { requiresGround: true },
    }}
    internallyConnectedPins={[["VOUT", "TAB"]]}
    supplierPartNumbers={{ jlcpcb: ["C6186"] }}
    manufacturerPartNumber="AMS1117-3.3"
    schPinArrangement={{
      leftSide: { pins: ["VIN"], direction: "top-to-bottom" },
      rightSide: { pins: ["VOUT", "TAB"], direction: "top-to-bottom" },
      bottomSide: { pins: ["GND"], direction: "left-to-right" },
    }}
    footprint={
      <footprint>
        <smtpad portHints={["pin1"]} pcbX="2.92995985mm" pcbY="-2.29997mm" width="2.499995mm" height="1.0999978mm" shape="rect" />
        <smtpad portHints={["pin2"]} pcbX="2.92995985mm" pcbY="0mm" width="2.499995mm" height="1.0999978mm" shape="rect" />
        <smtpad portHints={["pin3"]} pcbX="2.92995985mm" pcbY="2.29997mm" width="2.499995mm" height="1.0999978mm" shape="rect" />
        <smtpad portHints={["pin4"]} pcbX="-3.00995715mm" pcbY="0mm" width="2.3400004mm" height="3.5999928mm" shape="rect" />
        <silkscreenpath route={[{ x: -1.61140775, y: -3.3262062 }, { x: -1.61140775, y: 3.3262062 }, { x: 1.33138545, y: 3.3262062 }, { x: 1.33138545, y: -3.3262062 }, { x: -1.61140775, y: -3.3262062 }]} />
        <silkscreentext text="{NAME}" pcbX="0.29178885mm" pcbY="4.3274mm" anchorAlignment="center" fontSize="1mm" />
        <courtyardoutline outline={[{ x: -4.42861115, y: 3.5774 }, { x: 5.01218885, y: 3.5774 }, { x: 5.01218885, y: -3.5774 }, { x: -4.42861115, y: -3.5774 }, { x: -4.42861115, y: 3.5774 }]} />
      </footprint>
    }
  />
)

export const Ldo3v3 = (props: {
  u?: string
  cin?: string
  cout?: string
  vinNet?: string
  voutNet?: string
  /**
   * Suppress only the C_IN-to-V5 named boundary when the board owns the
   * protected-input trunk. The local VIN-to-C_IN bypass branch remains.
   */
  externalInputPowerTrunkPort?: "VIN"
  /** Suppress the local V3_3 boundary when a board PowerTrunk starts at TAB. */
  externalPowerTrunkPort?: "TAB"
  railWidthMm?: number
  pinNeckdownWidthMm?: number
  maxPinNeckdownLengthMm?: number
  layer?: "top" | "bottom"
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const u = props.u ?? "U2"
  const cin = props.cin ?? "C2"
  const cout = props.cout ?? "C3"
  const vin = props.vinNet ?? "V5"
  const vout = props.voutNet ?? "V3_3"
  const layer = props.layer ?? "top"
  const localX = (x: number) => layer === "bottom" ? -x : x
  const localRotation = (degrees: number) =>
    layer === "bottom" ? (360 - degrees) % 360 : degrees
  const railWidthMm = props.railWidthMm ?? 0.8
  const pinNeckdownWidthMm = props.pinNeckdownWidthMm ?? 0.2
  const maxPinNeckdownLengthMm = props.maxPinNeckdownLengthMm ?? 3
  if (
    !Number.isFinite(railWidthMm) || railWidthMm <= 0 ||
    !Number.isFinite(pinNeckdownWidthMm) || pinNeckdownWidthMm <= 0 ||
    !Number.isFinite(maxPinNeckdownLengthMm) || maxPinNeckdownLengthMm <= 0 ||
    pinNeckdownWidthMm > railWidthMm
  ) {
    throw new Error("Ldo3v3 needs finite positive rail/neck dimensions with neck <= rail")
  }
  return (
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <Ams1117_33 name={u} layer={layer} pcbX={localX(0)} pcbY={0}
        pcbRotation={localRotation(0)} schX={0} schY={0} />
      <capacitor name={cin} capacitance="10uF" footprint="0805"
        pcbX={localX(6.8)} pcbY={2.3} pcbRotation={localRotation(0)} schX={-3} schY={-2}
        layer={layer} schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C15850"] }} />
      <capacitor name={cout} capacitance="10uF" footprint="0805"
        pcbX={localX(-6.25)} pcbY={0} pcbRotation={localRotation(180)} schX={3} schY={-2}
        layer={layer} schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C15850"] }} />

      {/* Both rails are authored local trees. The input pin first reaches its
          adjacent capacitor, then the capacitor owns the sole V5 boundary. */}
      <trace name={`TR_${u}_vin_${cin}`} from={`.${u} > .VIN`} to={`.${cin} > .pin1`}
        thickness={`${pinNeckdownWidthMm}mm`} maxLength={`${maxPinNeckdownLengthMm}mm`} />
      {props.externalInputPowerTrunkPort !== "VIN" ? (
        <trace name={`TR_${cin}_${vin}_boundary`} from={`.${cin} > .pin1`} to={`net.${vin}`}
          thickness={`${railWidthMm}mm`} authoredNetTreeBoundary />
      ) : null}

      {/* VOUT and TAB are internally common in the AMS1117 package. Keep one
          wide TAB-to-cap edge and exactly one possible V3_3 boundary. A board
          PowerTrunk starts at TAB and suppresses this boundary, so it cannot
          create the former VOUT/TAB duplicate cycle. */}
      <trace name={`TR_${u}_tab_${cout}`} from={`.${u} > .TAB`} to={`.${cout} > .pin1`}
        thickness={`${railWidthMm}mm`} maxLength={`${maxPinNeckdownLengthMm}mm`} />
      {props.externalPowerTrunkPort !== "TAB" ? (
        <trace name={`TR_${cout}_${vout}_boundary`} from={`.${cout} > .pin1`} to={`net.${vout}`}
          thickness={`${railWidthMm}mm`} authoredNetTreeBoundary />
      ) : null}
      <GndFanoutTrace name={`TR_${u}_gnd`} from={`.${u} > .GND`} />
      <GndFanoutTrace name={`TR_${cin}_g`} from={`.${cin} > .pin2`} />
      <GndFanoutTrace name={`TR_${cout}_g`} from={`.${cout} > .pin2`} />
    </group>
  )
}

export default Ldo3v3
