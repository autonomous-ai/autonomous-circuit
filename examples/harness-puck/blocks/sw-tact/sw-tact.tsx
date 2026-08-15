/**
 * golden-block: sw-tact (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Tactile switch: TS-1187A-B-A-B (LCSC C318884, JLC Basic, $0.018).
 * 4-pad SMD; pads 1+2 are one internal terminal, 3+4 the other (LCSC's own
 * symbol for C318884 draws the internal bars — EasyEDA API, 2026-08-15;
 * first-article continuity remains the final check). The pairing is declared
 * via `internallyConnectedPins`, so every schematic export folds pins 1/2 and
 * 3/4 onto the symbol's two terminals instead of drawing a same-net tie as a
 * wire looping across the switch — which is exactly what the first human EE
 * review read as "every key is shorted" (ledger #29).
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

export const SwTact = (props: {
  name?: string
  signal?: string
  to?: string
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const sw = props.name ?? "SW1"
  const signal = props.signal ?? "BTN1"
  const to = props.to ?? "GND"
  return (
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <pushbutton
        name={sw}
        supplierPartNumbers={{ jlcpcb: ["C318884"] }}
        internallyConnectedPins={[["pin1", "pin2"], ["pin3", "pin4"]]}
        footprint="dfn4_p3.6998mm_w7mm_pw0.75mm"
        pcbX={0}
        pcbY={0}
        schX={0}
        schY={0}
      />
      <trace name={`TR_${sw}_p1`} from={`.${sw} > .pin1`} to={`net.${signal}`} />
      <trace name={`TR_${sw}_p2`} from={`.${sw} > .pin2`} to={`net.${signal}`} />
      <trace name={`TR_${sw}_p3`} from={`.${sw} > .pin3`} to={`net.${to}`} />
      <trace name={`TR_${sw}_p4`} from={`.${sw} > .pin4`} to={`net.${to}`} />
    </group>
  )
}

export default SwTact
