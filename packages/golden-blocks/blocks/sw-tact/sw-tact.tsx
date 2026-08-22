/**
 * golden-block: sw-tact (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Tactile switch: TS-1187A-B-A-B (LCSC C318884, JLC Basic, $0.018).
 *
 * **The pairing is by ROW, and our footprint numbers by COLUMN.** This block
 * had it ninety degrees out until an outside hardware review read it off the
 * board image (2026-08-21: "the two terminals are tied together, so it reads
 * as permanently pressed"). Both halves measured rather than argued:
 *
 *   the part, LCSC's own footprint for C318884 (EasyEDA API, 2026-08-21):
 *     pad1 ── pad2   one row, 6.00mm apart   = one terminal
 *     pad3 ── pad4   the other row           = the other terminal
 *     and the datasheet's circuit diagram joins each row with a bar.
 *
 *   our land pattern, `dfn4_p3.6998mm_w7mm_pw0.75mm`, measured off a built
 *   board's own pad coordinates:
 *     pin1 top-left      pin4 top-right      <- one row
 *     pin2 bottom-left   pin3 bottom-right   <- the other row
 *     numbered down the left column and up the right, DFN convention.
 *
 * So `pin1`/`pin2` are one *column*: one pad from each terminal. Tying them
 * to the same net ties signal to ground through the switch body, and the
 * button can never do anything. The declaration below therefore pairs by row
 * — `{pin1,pin4}` and `{pin2,pin3}` — which is the same physical pairing the
 * datasheet draws, expressed in the numbering the footprint actually uses.
 *
 * Wiring: both pads of each terminal, `{pin1,pin4}` → `signal` and
 * `{pin2,pin3}` → `to` (default GND). Active-low with a pull-up on the
 * signal side (MCU internal pull-ups suffice on RP2040/ESP32).
 *
 * **First-article continuity is still the final check**, and it is the only
 * thing that can catch a footprint whose numbering changes upstream: a meter
 * across pin1-pin4 should read closed with the button up, pin1-pin2 open.
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
        internallyConnectedPins={[["pin1", "pin4"], ["pin2", "pin3"]]}
        footprint="dfn4_p3.6998mm_w7mm_pw0.75mm"
        pcbX={0}
        pcbY={0}
        schX={0}
        schY={0}
      />
      <trace name={`TR_${sw}_p1`} from={`.${sw} > .pin1`} to={`net.${signal}`} />
      <trace name={`TR_${sw}_p4`} from={`.${sw} > .pin4`} to={`net.${signal}`} />
      <trace name={`TR_${sw}_p2`} from={`.${sw} > .pin2`} to={`net.${to}`} />
      <trace name={`TR_${sw}_p3`} from={`.${sw} > .pin3`} to={`net.${to}`} />
    </group>
  )
}

export default SwTact
