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
  layer?: "top" | "bottom"
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
  /** Board-owned phase for the short resistor-to-LED local tie. */
  localRoutingPhaseIndex?: number
  /** Width of the outboard rail/signal branch into the resistor. */
  railTraceThicknessMm?: number
}) => {
  const led = props.led ?? "LED1"
  const r = props.r ?? "R20"
  const rail = props.rail ?? "V3_3"
  const layer = props.layer ?? "top"
  return (
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <led name={led} footprint="0805" color="green" pcbX={0} pcbY={0} schX={0} schY={0}
        layer={layer} schRotation="270deg" supplierPartNumbers={{ jlcpcb: ["C2297"] }} />
      <resistor name={r} resistance="1k" footprint="0402" pcbX={0} pcbY={2.5} schX={0} schY={2}
        layer={layer} schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C11702"] }} />
      <trace name={`TR_${r}_rail`} from={`.${r} > .pin1`} to={`net.${rail}`}
        thickness={props.railTraceThicknessMm === undefined
          ? undefined
          : `${props.railTraceThicknessMm}mm`} />
      <trace name={`TR_${r}_led`} from={`.${r} > .pin2`} to={`.${led} > .anode`}
        routingPhaseIndex={props.localRoutingPhaseIndex} />
      <GndFanoutTrace name={`TR_${led}_gnd`} from={`.${led} > .cathode`} />
    </group>
  )
}

export default StatusLed
