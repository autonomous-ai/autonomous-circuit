/**
 * golden-block: ldo-3v3 (v2)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Protected 5V to 3.3V logic rail: Diodes AP7361C-33E-13 (LCSC C500795)
 * with exact 10uF X5R input/output capacitors (LCSC C19702).
 * In: net.V5 (default). Out: net.V3_3.
 *
 * C500795's exact EasyEDA record establishes the selected part, pin mapping,
 * and 3D model. Copper deliberately uses Diodes DS37274 Rev. 5-2 page 21's
 * SOT223 recommended land exactly: 1.20x1.60mm leads, 3.30x1.60mm GND tab,
 * 2.30mm lead pitch, 6.40mm row-center distance, and 8.00mm total span.
 * This overrides the imported EasyEDA copper: 2.4649938x1.0500106mm leads
 * at row center 5.715mm and a 2.4649938x3.539998mm tab. The imported land has
 * more total area but does not meet the manufacturer's specified dimensions.
 * Default refdes remain U2, C2, C3.
 */

import { GndFanoutTrace } from "../glue"

const ldoPinLabels = {
  pin1: ["VIN"],
  pin2: ["GND1", "GND"],
  pin3: ["VOUT"],
  pin4: ["GND2", "TAB_GND"],
} as const

export const Ap7361c_33 = (props: {
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
      GND1: { requiresGround: true },
      GND2: { requiresGround: true },
    }}
    supplierPartNumbers={{ jlcpcb: ["C500795"] }}
    manufacturerPartNumber="AP7361C-33E-13"
    schPinArrangement={{
      leftSide: { pins: ["VIN"], direction: "top-to-bottom" },
      rightSide: { pins: ["VOUT"], direction: "top-to-bottom" },
      bottomSide: { pins: ["GND1", "GND2"], direction: "left-to-right" },
    }}
    footprint={
      <footprint>
        {/* Manufacturer-recommended land, rotated 90deg into block-local X. */}
        <smtpad portHints={["pin1"]} pcbX="3.2mm" pcbY="-2.3mm"
          width="1.6mm" height="1.2mm" shape="rect" />
        <smtpad portHints={["pin2"]} pcbX="3.2mm" pcbY="0mm"
          width="1.6mm" height="1.2mm" shape="rect" />
        <smtpad portHints={["pin3"]} pcbX="3.2mm" pcbY="2.3mm"
          width="1.6mm" height="1.2mm" shape="rect" />
        <smtpad portHints={["pin4"]} pcbX="-3.2mm" pcbY="0mm"
          width="1.6mm" height="3.3mm" shape="rect" />
        <silkscreenpath route={[
          { x: -1.3963904, y: -3.4012124 },
          { x: -1.3963904, y: 3.4012124 },
          { x: 1.3963904, y: 3.4012124 },
          { x: 1.3963904, y: -3.4012124 },
          { x: -1.3963904, y: -3.4012124 },
        ]} />
        <silkscreentext text="{NAME}" pcbX="0.1905mm" pcbY="4.4036mm"
          anchorAlignment="center" fontSize="1mm" />
        <courtyardoutline outline={[
          { x: -4.25, y: 3.65 }, { x: 4.25, y: 3.65 },
          { x: 4.25, y: -3.65 }, { x: -4.25, y: -3.65 },
          { x: -4.25, y: 3.65 },
        ]} />
      </footprint>
    }
    cadModel={{
      objUrl: "https://modelcdn.tscircuit.com/easyeda_models/assets/C500795.obj?uuid=e80246a9471445bfb635be848806a22e",
      stepUrl: "https://modelcdn.tscircuit.com/easyeda_models/assets/C500795.step?uuid=e80246a9471445bfb635be848806a22e",
      pcbRotationOffset: 180,
      modelOriginPosition: {
        x: 0.000012700000070253736,
        y: 0.000012700000070253736,
        z: -0.049394,
      },
    }}
  />
)

export const Ldo3v3 = (props: {
  u?: string
  cin?: string
  cout?: string
  vinNet?: string
  voutNet?: string
  /** Suppress only the C_IN-to-V5 boundary; the local bypass stays. */
  externalInputPowerTrunkPort?: "VIN"
  /** Suppress the V3_3 boundary for a board-owned trunk from physical VOUT. */
  externalPowerTrunkPort?: "VOUT"
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
  const maxPinNeckdownLengthMm = props.maxPinNeckdownLengthMm ?? 2
  if (
    !Number.isFinite(railWidthMm) || railWidthMm <= 0 ||
    !Number.isFinite(pinNeckdownWidthMm) || pinNeckdownWidthMm <= 0 ||
    !Number.isFinite(maxPinNeckdownLengthMm) || maxPinNeckdownLengthMm <= 0 ||
    pinNeckdownWidthMm > railWidthMm
  ) {
    throw new Error("Ldo3v3 needs finite positive rail/neck dimensions with neck <= rail")
  }
  return (
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0}
      schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <Ap7361c_33 name={u} layer={layer} pcbX={localX(0)} pcbY={0}
        pcbRotation={localRotation(0)} schX={0} schY={0} />
      <capacitor name={cin} capacitance="10uF" footprint="0603"
        pcbX={localX(5.75)} pcbY={-2.3} pcbRotation={localRotation(0)}
        schX={-3} schY={-2} layer={layer} schRotation="90deg"
        supplierPartNumbers={{ jlcpcb: ["C19702"] }} />
      <capacitor name={cout} capacitance="10uF" footprint="0603"
        pcbX={localX(5.75)} pcbY={2.3} pcbRotation={localRotation(0)}
        schX={3} schY={-2} layer={layer} schRotation="90deg"
        supplierPartNumbers={{ jlcpcb: ["C19702"] }} />

      {/* Each power pin first reaches its same-face ceramic capacitor. C2/C3
          then own the sole optional named-net boundary for their rail. */}
      <trace name={`TR_${u}_vin_${cin}`} from={`.${u} > .VIN`}
        to={`.${cin} > .pin1`} thickness={`${pinNeckdownWidthMm}mm`}
        maxLength={`${maxPinNeckdownLengthMm}mm`} />
      {props.externalInputPowerTrunkPort !== "VIN" ? (
        <trace name={`TR_${cin}_${vin}_boundary`} from={`.${cin} > .pin1`}
          to={`net.${vin}`} thickness={`${railWidthMm}mm`}
          authoredNetTreeBoundary />
      ) : null}

      <trace name={`TR_${u}_vout_${cout}`} from={`.${u} > .VOUT`}
        to={`.${cout} > .pin1`} thickness={`${railWidthMm}mm`}
        maxLength={`${maxPinNeckdownLengthMm}mm`} />
      {props.externalPowerTrunkPort === undefined ? (
        <trace name={`TR_${cout}_${vout}_boundary`} from={`.${cout} > .pin1`}
          to={`net.${vout}`} thickness={`${railWidthMm}mm`}
          authoredNetTreeBoundary />
      ) : null}

      {/* Both physical GND contacts must terminate into the poured face. The
          broad tab is not an output and may never be used as a rail source. */}
      <GndFanoutTrace name={`TR_${u}_gnd1`} from={`.${u} > .GND1`} />
      <GndFanoutTrace name={`TR_${u}_tab_gnd`} from={`.${u} > .GND2`} />
      <GndFanoutTrace name={`TR_${cin}_g`} from={`.${cin} > .pin2`} />
      <GndFanoutTrace name={`TR_${cout}_g`} from={`.${cout} > .pin2`} />
    </group>
  )
}

export default Ldo3v3
