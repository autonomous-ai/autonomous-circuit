/**
 * golden-block: usb-power-entry (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Current-limited, controlled-rise bridge from connector-side VBUS_RAW to
 * the board's V5 rail.  TPS2553DBVR is fixed at a <=500mA maximum trip point
 * with TI's documented 59k 1% ILIM network.  Its open-drain FAULT output has
 * a 100k pull-up to V3_3, a named net, and a DNP copper probe pad.
 *
 * U7 copper was imported exactly from JLCPCB/LCSC C55266 with:
 *   tscircuit-cli import C55266 --jlcpcb --use-exact-footprint
 * on 2026-08-11.  Do not replace it with an assumed generic SOT-23-6.
 *
 * Default refdes (global v1 allocation): U7, C24, R31, R32, TP10.
 */

import { GndFanoutTrace } from "../glue"

const tps2553PinLabels = {
  pin1: ["IN"],
  pin2: ["GND"],
  pin3: ["EN"],
  pin4: ["FAULT"],
  pin5: ["ILIM"],
  pin6: ["OUT"],
} as const

export const Tps2553 = (props: {
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
    pinLabels={tps2553PinLabels}
    pinAttributes={{
      IN: { requiresPower: true },
      OUT: { providesPower: true },
      GND: { requiresGround: true },
    }}
    supplierPartNumbers={{ jlcpcb: ["C55266"] }}
    manufacturerPartNumber="TPS2553DBVR"
    schPinArrangement={{
      leftSide: { pins: ["IN", "EN"], direction: "top-to-bottom" },
      rightSide: { pins: ["OUT", "FAULT", "ILIM"], direction: "top-to-bottom" },
      bottomSide: { pins: ["GND"], direction: "left-to-right" },
    }}
    footprint={
      <footprint>
        {/* Exact EasyEDA copper for C55266 (DBV / SOT-23-6), imported 2026-08-11. */}
        <smtpad portHints={["pin3"]} pcbX="1.35001mm" pcbY="0.94996mm" width="1.0999978mm" height="0.5999988mm" shape="rect" />
        <smtpad portHints={["pin2"]} pcbX="1.35001mm" pcbY="0mm" width="1.0999978mm" height="0.5999988mm" shape="rect" />
        <smtpad portHints={["pin1"]} pcbX="1.35001mm" pcbY="-0.94996mm" width="1.0999978mm" height="0.5999988mm" shape="rect" />
        <smtpad portHints={["pin6"]} pcbX="-1.35001mm" pcbY="-0.94996mm" width="1.0999978mm" height="0.5999988mm" shape="rect" />
        <smtpad portHints={["pin5"]} pcbX="-1.35001mm" pcbY="0mm" width="1.0999978mm" height="0.5999988mm" shape="rect" />
        <smtpad portHints={["pin4"]} pcbX="-1.35001mm" pcbY="0.94996mm" width="1.0999978mm" height="0.5999988mm" shape="rect" />
        <silkscreenpath route={[{ x: -0.8999982, y: 1.549908 }, { x: 0.9000236, y: 1.549908 }]} />
        <silkscreenpath route={[{ x: -0.8999982, y: -1.5501112 }, { x: 0.9000236, y: -1.5501112 }]} />
        <silkscreenpath route={[
          { x: 1.524, y: -1.651 }, { x: 1.51967258, y: -1.683870019 },
          { x: 1.506985226, y: -1.7145 }, { x: 1.486802561, y: -1.740802561 },
          { x: 1.4605, y: -1.760985226 }, { x: 1.429870019, y: -1.77367258 },
          { x: 1.397, y: -1.778 }, { x: 1.364129981, y: -1.77367258 },
          { x: 1.3335, y: -1.760985226 }, { x: 1.307197439, y: -1.740802561 },
          { x: 1.287014774, y: -1.7145 }, { x: 1.27432742, y: -1.683870019 },
          { x: 1.27, y: -1.651 }, { x: 1.27432742, y: -1.618129981 },
          { x: 1.287014774, y: -1.5875 }, { x: 1.307197439, y: -1.561197439 },
          { x: 1.3335, y: -1.541014774 }, { x: 1.364129981, y: -1.52832742 },
          { x: 1.397, y: -1.524 }, { x: 1.429870019, y: -1.52832742 },
          { x: 1.4605, y: -1.541014774 }, { x: 1.486802561, y: -1.561197439 },
          { x: 1.506985226, y: -1.5875 }, { x: 1.51967258, y: -1.618129981 },
          { x: 1.524, y: -1.651 },
        ]} />
        <courtyardoutline outline={[
          { x: -2.142554, y: 1.812354 }, { x: 2.167446, y: 1.812354 },
          { x: 2.167446, y: -2.015046 }, { x: -2.142554, y: -2.015046 },
          { x: -2.142554, y: 1.812354 },
        ]} />
      </footprint>
    }
  />
)

export const UsbPowerEntry = (props: {
  u?: string
  cIn?: string
  rIlim?: string
  rFault?: string
  faultTestpoint?: string
  rawNet?: string
  outputNet?: string
  faultNet?: string
  /** Output pad whose ordinary V5 edge is replaced by a board PowerTrunk. */
  externalPowerTrunkPort?: "OUT"
  /**
   * Suppress C24's ordinary VBUS_RAW boundary so a board-authored raw-power
   * tree can attach exactly at `.<cIn> > .pin1`.
   */
  externalRawPowerTrunkPort?: "IN"
  /**
   * Suppress R32's ordinary V3_3 leaf so a board-authored rail tree can attach
   * exactly at `.<rFault> > .pin2`.
   */
  externalFaultPullupPort?: "R32"
  /** Ordinary board-signal width after the fine-pitch FAULT escape. */
  signalTraceWidthMm?: number
  /** Package-to-probe escape width and bound for the SOT-23 FAULT pad. */
  finePitchEscapeWidthMm?: number
  maxFinePitchEscapeLengthMm?: number
  layer?: "top" | "bottom"
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const u = props.u ?? "U7"
  const cIn = props.cIn ?? "C24"
  const rIlim = props.rIlim ?? "R31"
  const rFault = props.rFault ?? "R32"
  const faultTestpoint = props.faultTestpoint ?? "TP10"
  const rawNet = props.rawNet ?? "VBUS_RAW"
  const outputNet = props.outputNet ?? "V5"
  const faultNet = props.faultNet ?? "USB_POWER_FAULT"
  const layer = props.layer ?? "top"
  const localX = (x: number) => layer === "bottom" ? -x : x
  const localRotation = (degrees: number) =>
    layer === "bottom" ? (360 - degrees) % 360 : degrees
  const signalTraceWidthMm = props.signalTraceWidthMm ?? 0.25
  const finePitchEscapeWidthMm = props.finePitchEscapeWidthMm ?? 0.15
  const maxFinePitchEscapeLengthMm = props.maxFinePitchEscapeLengthMm ?? 1
  if (
    ![signalTraceWidthMm, finePitchEscapeWidthMm, maxFinePitchEscapeLengthMm]
      .every((value) => Number.isFinite(value) && value > 0) ||
    finePitchEscapeWidthMm > signalTraceWidthMm
  ) {
    throw new Error(
      "UsbPowerEntry signal/escape dimensions must be positive with escape <= signal",
    )
  }

  return (
    <group
      pcbX={props.pcbX ?? 0}
      pcbY={props.pcbY ?? 0}
      schX={props.schX ?? 0}
      schY={props.schY ?? 0}
    >
      <Tps2553 name={u} layer={layer} pcbX={localX(0)} pcbY={0}
        pcbRotation={localRotation(0)} schX={0} schY={0} />
      <capacitor name={cIn} capacitance="100nF" footprint="0402" layer={layer}
        pcbX={localX(3.1)} pcbY={-0.5} pcbRotation={localRotation(0)} schX={-3} schY={-3}
        supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <resistor name={rIlim} resistance="59k" footprint="0402" layer={layer}
        pcbX={localX(-3.1)} pcbY={0} pcbRotation={localRotation(180)} schX={3} schY={-3}
        supplierPartNumbers={{ jlcpcb: ["C32297"] }} />
      <resistor name={rFault} resistance="100k" footprint="0402" layer={layer}
        pcbX={localX(-3.1)} pcbY={0.95} pcbRotation={localRotation(180)} schX={3} schY={3}
        supplierPartNumbers={{ jlcpcb: ["C25741"] }} />
      <testpoint name={faultTestpoint} footprintVariant="pad" padShape="circle"
        padDiameter="0.8mm" doNotPlace={true} layer={layer}
        pcbX={localX(-2.16)} pcbY={1.4} pcbRotation={localRotation(0)} schX={6} schY={1} />

      {/* Active-high EN follows the raw input. C24 is the block's required
          >=0.1uF input bypass and the sole named-net boundary for this tree. */}
      <trace name={`TR_${u}_in_${cIn}`} from={`.${u} > .IN`} to={`.${cIn} > .pin1`}
        thickness="0.3mm" maxLength="2mm" />
      <trace name={`TR_${u}_en`} from={`.${u} > .EN`} to={`.${cIn} > .pin1`}
        thickness="0.2mm" maxLength="3mm"
        pcbPath={[
          { x: localX(1.35001), y: 0.94996 },
          { x: localX(2.2), y: 0.75 },
          { x: localX(2.59), y: -0.5 },
        ]} />
      {props.externalRawPowerTrunkPort !== "IN" ? (
        <trace name={`TR_${cIn}_raw`} from={`.${cIn} > .pin1`} to={`net.${rawNet}`}
          thickness="0.3mm" maxLength="3mm" authoredNetTreeBoundary />
      ) : null}
      <GndFanoutTrace name={`TR_${cIn}_gnd`} from={`.${cIn} > .pin2`} maxLengthMm={2} />
      <GndFanoutTrace name={`TR_${u}_gnd`} from={`.${u} > .GND`} />

      {/* TI section 10.2.1.2.2 freezes 59k 1%: IOS(max) <=500mA and
          IOS(min)=400.6mA.  This is not a composer-adjustable guess. */}
      <trace name={`TR_${u}_ilim`} from={`.${u} > .ILIM`} to={`.${rIlim} > .pin1`}
        thickness="0.2mm" maxLength="2mm" />
      <GndFanoutTrace name={`TR_${rIlim}_gnd`} from={`.${rIlim} > .pin2`} />

      {props.externalPowerTrunkPort !== "OUT" ? (
        <trace name={`TR_${u}_out`} from={`.${u} > .OUT`} to={`net.${outputNet}`} thickness="0.3mm" />
      ) : null}

      {/* FAULT is open drain. It is pulled up, named, and physically
          probeable; no product may silently leave a power fault floating. */}
      <trace name={`TR_${u}_fault`} from={`.${u} > .FAULT`} to={`.${faultTestpoint} > .pin1`}
        thickness={`${finePitchEscapeWidthMm}mm`}
        maxLength={`${maxFinePitchEscapeLengthMm}mm`} />
      <trace name={`TR_${rFault}_fault`} from={`.${rFault} > .pin1`} to={`.${faultTestpoint} > .pin1`}
        thickness={`${signalTraceWidthMm}mm`} maxLength="2mm" />
      {props.externalFaultPullupPort !== "R32" ? (
        <trace name={`TR_${rFault}_pullup`} from={`.${rFault} > .pin2`} to="net.V3_3"
          thickness="0.2mm" maxLength="3mm" />
      ) : null}
      <trace name={`TR_${faultTestpoint}_fault`} from={`.${faultTestpoint} > .pin1`} to={`net.${faultNet}`}
        thickness={`${signalTraceWidthMm}mm`} maxLength="3mm"
        authoredNetTreeBoundary />
    </group>
  )
}

export default UsbPowerEntry
