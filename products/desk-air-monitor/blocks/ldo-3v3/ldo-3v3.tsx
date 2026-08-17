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

const ldoPinLabels = {
  pin1: ["GND"],
  pin2: ["VOUT1", "VOUT"],
  pin3: ["VIN"],
  pin4: ["VOUT2", "TAB"],
} as const

export const Ams1117_33 = (props: {
  name: string
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
      GND: { requiresGround: true },
    }}
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
   * Track width for the input rail (V5) and the output rail (V3_3), e.g.
   * `"0.5mm"`. Default: unset, i.e. the board's `minTraceWidth`.
   *
   * This block is where both rails of an MCU board are born, which makes it
   * the one place a per-net width can be declared once and reach all of it.
   * Per `docs/architecture/rail-width.md`, `<trace thickness="…">` arrives at
   * the router as a per-net `nominalTraceWidth` and **one declaration
   * anywhere on a net sets the whole net** — so this widens what the router
   * searches for rather than stamping copper on afterwards, and it widens
   * every consumer of the rail, not just the LDO's own legs.
   *
   * **Measure before you pass anything.** Declaring a width the placement
   * cannot take scrapped a board whole (harness-puck, every rail at 0.5mm:
   * `fab.ready` true → false, 0 → 33 blocking findings, two nets shorted).
   * `python -m circuitpy.netwidth <project> --rails` reports the widest track
   * each net's own pads can escape at; pass only what that clears, and never
   * round up. On an RP2040 board the QFN-56's 0.400mm pitch caps V3_3 at
   * exactly 0.4000mm — `2 × (0.400 − 0.100 − 0.100)` — which no effort level
   * or placement change can beat.
   */
  vinThickness?: string
  voutThickness?: string
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
  const vinW = props.vinThickness
  const voutW = props.voutThickness
  return (
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <Ams1117_33 name={u} pcbX={0} pcbY={0} schX={0} schY={0} />
      <capacitor name={cin} capacitance="10uF" footprint="0805" pcbX={-2} pcbY={-6} schX={-3} schY={-2}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C15850"] }} />
      <capacitor name={cout} capacitance="10uF" footprint="0805" pcbX={5} pcbY={-6} schX={3} schY={-2}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C15850"] }} />

      <trace name={`TR_${u}_vin`} from={`.${u} > .VIN`} to={`net.${vin}`} thickness={vinW} />
      <trace name={`TR_${u}_vout`} from={`.${u} > .VOUT`} to={`net.${vout}`} thickness={voutW} />
      <trace name={`TR_${u}_tab`} from={`.${u} > .TAB`} to={`net.${vout}`} thickness={voutW} />
      <trace name={`TR_${u}_gnd`} from={`.${u} > .GND`} to="net.GND" />
      <trace name={`TR_${cin}_v`} from={`.${cin} > .pin1`} to={`net.${vin}`} thickness={vinW} />
      <trace name={`TR_${cin}_g`} from={`.${cin} > .pin2`} to="net.GND" />
      <trace name={`TR_${cout}_v`} from={`.${cout} > .pin1`} to={`net.${vout}`} thickness={voutW} />
      <trace name={`TR_${cout}_g`} from={`.${cout} > .pin2`} to="net.GND" />
    </group>
  )
}

export default Ldo3v3
