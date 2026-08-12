/**
 * golden-block: sw-tact (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Tactile switch: TS-1187A-B-A-B (LCSC C318884, JLC Basic, $0.018).
 * 4-pad SMD; pads 1+2 are one internal terminal, 3+4 the other (standard
 * TS-1187A pairing — hardware-verify on first article). The block ties each
 * pair so a single cracked joint never opens the circuit.
 *
 * Wiring: `signal` net → pins 1/2, pins 3/4 → `to` net (default GND).
 * Active-low with a pull-up on the signal side (MCU internal pull-ups
 * suffice on RP2040/ESP32).
 *
 * Land pattern: footprinter "dfn4_p3.6998mm_w7mm_pw0.75mm" — 98.81% copper
 * IoU against the EasyEDA pattern (tscircuit-cli import C318884, 2026-08-10).
 *
 * Default refdes (global v1 allocation): SW1. Instantiate per key by
 * overriding `name` (SW10, SW11, … for key grids).
 */

import { GndFanoutTrace } from "../glue"

export type TactileSwitchVariant = "standard" | "compact"

/**
 * The orderable physical switch without board-level net wiring.
 *
 * `compact` is ROCPU TPT-2C1 / JLCPCB C2828561: a 3x2mm, two-terminal
 * SPST tactile switch. Its committed footprinter string is the 100% copper-IoU
 * result from the pinned `tscircuit-cli import C2828561 --jlcpcb` run on
 * 2026-08-11. It is Extended (not Basic), so use it where area matters rather
 * than silently replacing a high-count key field.
 */
export const TactileButton = (props: {
  name: string
  variant?: TactileSwitchVariant
  layer?: "top" | "bottom"
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const variant = props.variant ?? "standard"
  if (variant === "compact") {
    return (
      <pushbutton
        name={props.name}
        pinLabels={{ pin1: ["pin1"], pin2: ["pin2"] } as const}
        supplierPartNumbers={{ jlcpcb: ["C2828561"] }}
        manufacturerPartNumber="TPT-2C1"
        footprint="res_p3.1999mm_pw1mm_ph1.524mm"
        layer={props.layer ?? "top"}
        pcbX={props.pcbX ?? 0}
        pcbY={props.pcbY ?? 0}
        schX={props.schX ?? 0}
        schY={props.schY ?? 0}
      />
    )
  }
  return (
    <pushbutton
      name={props.name}
      supplierPartNumbers={{ jlcpcb: ["C318884"] }}
      manufacturerPartNumber="TS-1187A-B-A-B"
      footprint="dfn4_p3.6998mm_w7mm_pw0.75mm"
      layer={props.layer ?? "top"}
      pcbX={props.pcbX ?? 0}
      pcbY={props.pcbY ?? 0}
      schX={props.schX ?? 0}
      schY={props.schY ?? 0}
    />
  )
}

export const SwTact = (props: {
  name?: string
  signal?: string
  to?: string
  signalTraceWidthMm?: number
  variant?: TactileSwitchVariant
  layer?: "top" | "bottom"
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const sw = props.name ?? "SW1"
  const signal = props.signal ?? "BTN1"
  const to = props.to ?? "GND"
  const signalTraceWidthMm = props.signalTraceWidthMm ?? 0.25
  const variant = props.variant ?? "standard"
  const layer = props.layer ?? "top"
  return (
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <TactileButton
        name={sw}
        variant={variant}
        layer={layer}
        pcbX={0}
        pcbY={0}
        schX={0}
        schY={0}
      />
      <trace name={`TR_${sw}_p1`} from={`.${sw} > .pin1`} to={`net.${signal}`}
        thickness={`${signalTraceWidthMm}mm`} />
      {variant === "standard" ? (
        <>
          <trace name={`TR_${sw}_p2`} from={`.${sw} > .pin2`} to={`net.${signal}`}
            thickness={`${signalTraceWidthMm}mm`} />
          {to === "GND" ? (
            <>
              <GndFanoutTrace name={`TR_${sw}_p3`} from={`.${sw} > .pin3`} />
              <GndFanoutTrace name={`TR_${sw}_p4`} from={`.${sw} > .pin4`} />
            </>
          ) : (
            <>
              <trace name={`TR_${sw}_p3`} from={`.${sw} > .pin3`} to={`net.${to}`}
                thickness={`${signalTraceWidthMm}mm`} />
              <trace name={`TR_${sw}_p4`} from={`.${sw} > .pin4`} to={`net.${to}`}
                thickness={`${signalTraceWidthMm}mm`} />
            </>
          )}
        </>
      ) : to === "GND" ? (
        <GndFanoutTrace name={`TR_${sw}_p2`} from={`.${sw} > .pin2`} />
      ) : (
        <trace name={`TR_${sw}_p2`} from={`.${sw} > .pin2`} to={`net.${to}`}
          thickness={`${signalTraceWidthMm}mm`} />
      )}
    </group>
  )
}

export default SwTact
