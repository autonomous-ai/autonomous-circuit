/**
 * golden-block: status-led (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Rail status indicator: green 0805 LED (KT-0805G, LCSC C2297, JLC Basic)
 * with a 1k series resistor to a rail (default net.V3_3; ~1.2mA — visible,
 * frugal). For a GPIO-driven LED pass `rail` as a signal net instead.
 *
 * Default refdes (global v1 allocation): LED1, R20. Instantiate twice by
 * overriding `led` and `r`.
 */

import { GndFanoutTrace } from "../glue"

export const StatusLed = (props: {
  led?: string
  r?: string
  rail?: string
  /** Whether the source net is a supply rail or a GPIO/control signal. */
  driveKind?: "rail" | "signal"
  /**
   * Suppress the ordinary rail edge so a board-owned authored tree can attach
   * at `.<r> > .pin1` without leaving a second named-net leaf.
   */
  externalRailAttachmentPort?: "R"
  /** Short rail-to-resistor branch; package-specific and explicitly narrow. */
  railTraceWidthMm?: number
  /** Ordinary board signal width and the resistor-to-LED series width. */
  signalTraceWidthMm?: number
  maxRailNeckdownLengthMm?: number
  maxSeriesTraceLengthMm?: number
  layer?: "top" | "bottom"
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const led = props.led ?? "LED1"
  const r = props.r ?? "R20"
  const rail = props.rail ?? "V3_3"
  const layer = props.layer ?? "top"
  const localX = (x: number) => layer === "bottom" ? -x : x
  const localRotation = (degrees: number) =>
    layer === "bottom" ? (360 - degrees) % 360 : degrees
  const driveKind = props.driveKind ?? "rail"
  const railTraceWidthMm = props.railTraceWidthMm ?? 0.2
  const signalTraceWidthMm = props.signalTraceWidthMm ?? 0.25
  const maxRailNeckdownLengthMm = props.maxRailNeckdownLengthMm ?? 3
  const maxSeriesTraceLengthMm = props.maxSeriesTraceLengthMm ?? 3
  if (
    !Number.isFinite(railTraceWidthMm) || railTraceWidthMm <= 0 ||
    !Number.isFinite(signalTraceWidthMm) || signalTraceWidthMm <= 0 ||
    !Number.isFinite(maxRailNeckdownLengthMm) || maxRailNeckdownLengthMm <= 0 ||
    !Number.isFinite(maxSeriesTraceLengthMm) || maxSeriesTraceLengthMm <= 0
  ) {
    throw new Error("StatusLed trace widths must be finite and positive")
  }
  const sourceTraceWidthMm =
    driveKind === "signal" ? signalTraceWidthMm : railTraceWidthMm
  return (
    <group name={`__parts_block__status-led__${led}`} pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <led name={led} footprint="0805" color="green" pcbX={localX(0)} pcbY={0}
        pcbRotation={localRotation(0)} schX={0} schY={0}
        layer={layer} schRotation="270deg" supplierPartNumbers={{ jlcpcb: ["C2297"] }} />
      <resistor name={r} resistance="1k" footprint="0402" pcbX={localX(0)} pcbY={2.2}
        pcbRotation={localRotation(0)} schX={0} schY={2}
        layer={layer} schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C11702"] }} />
      {props.externalRailAttachmentPort !== "R" ? (
        <trace name={`TR_${r}_rail`} from={`.${r} > .pin1`} to={`net.${rail}`}
          thickness={`${sourceTraceWidthMm}mm`}
          maxLength={driveKind === "rail" ? `${maxRailNeckdownLengthMm}mm` : undefined} />
      ) : null}
      <trace name={`TR_${r}_led`} from={`.${r} > .pin2`} to={`.${led} > .anode`}
        thickness={`${signalTraceWidthMm}mm`} maxLength={`${maxSeriesTraceLengthMm}mm`} />
      <GndFanoutTrace name={`TR_${led}_gnd`} from={`.${led} > .cathode`} />
    </group>
  )
}

export default StatusLed
