/**
 * golden-block: ws2812-chain (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * A chain of addressable RGB LEDs (WS2812B-B/T 5050, LCSC C2761795) driven
 * from one GPIO. Each pixel takes DIN from the previous pixel's DOUT, so the
 * whole strip is one signal net per link plus a shared 5V rail.
 *
 * Two rules this block exists to enforce, both of which are silent failures
 * when a composer wires WS2812s by hand:
 *   - one 100nF decoupling capacitor per pixel, adjacent to that pixel. The
 *     part switches 3 constant-current channels at high speed; shared bulk
 *     alone browns out the far end of the chain.
 *   - a series resistor on the first DIN (330R) to damp the reflection on a
 *     long first hop and protect the GPIO. Later hops are driven by the
 *     previous LED and do not need one.
 *
 * Data levels: WS2812B expects VIH >= 0.7 * VDD. Driven at 5V it wants 3.5V
 * and a 3.3V MCU pin is marginal. This block therefore defaults its rail to
 * net.V3_3, where 3.3V logic is comfortably in spec and the pixels are simply
 * dimmer. Pass rail="V5" only with a level shifter in front (not a block yet).
 *
 * Default refdes: D10..D10+n, C40..C40+n, R30 (the series resistor).
 */

const PIXEL_PITCH_MM = 7

export const Ws2812Pixel = (props: {
  name: string
  pcbX?: number
  pcbY?: number
  pcbRotation?: number | string
  schX?: number
  schY?: number
}) => (
  <chip
    {...props}
    pinLabels={{
      pin1: ["VDD"],
      pin2: ["DOUT"],
      pin3: ["GND"],
      pin4: ["DIN"],
    }}
    pinAttributes={{
      VDD: { requiresPower: true },
      GND: { requiresGround: true },
    }}
    supplierPartNumbers={{ jlcpcb: ["C2761795"] }}
    manufacturerPartNumber="WS2812B-B/T"
    schPinArrangement={{
      leftSide: { pins: ["DIN"], direction: "top-to-bottom" },
      rightSide: { pins: ["DOUT"], direction: "top-to-bottom" },
      topSide: { pins: ["VDD"], direction: "left-to-right" },
      bottomSide: { pins: ["GND"], direction: "left-to-right" },
    }}
    footprint={
      <footprint>
        {/* 5050 package, 4 pads on a 5.0 x 5.0mm body (datasheet land pattern) */}
        <smtpad portHints={["pin1"]} pcbX="-2.475mm" pcbY="1.6mm" width="1.5mm" height="1.4mm" shape="rect" />
        <smtpad portHints={["pin2"]} pcbX="-2.475mm" pcbY="-1.6mm" width="1.5mm" height="1.4mm" shape="rect" />
        <smtpad portHints={["pin3"]} pcbX="2.475mm" pcbY="-1.6mm" width="1.5mm" height="1.4mm" shape="rect" />
        <smtpad portHints={["pin4"]} pcbX="2.475mm" pcbY="1.6mm" width="1.5mm" height="1.4mm" shape="rect" />
        <silkscreenpath route={[
          { x: -2.5, y: 2.5 }, { x: 2.5, y: 2.5 }, { x: 2.5, y: -2.5 },
          { x: -2.5, y: -2.5 }, { x: -2.5, y: 2.5 },
        ]} />
        <courtyardoutline outline={[
          { x: -3.2, y: 2.8 }, { x: 3.2, y: 2.8 }, { x: 3.2, y: -2.8 },
          { x: -3.2, y: -2.8 }, { x: -3.2, y: 2.8 },
        ]} />
      </footprint>
    }
  />
)

export const Ws2812Chain = (props: {
  /** how many pixels in the chain */
  count?: number
  /** the GPIO net feeding the first pixel */
  dinNet?: string
  rail?: string
  /** first LED refdes number, e.g. 10 -> D10, D11, ... */
  startIndex?: number
  /** mm between pixel centres along x */
  pitch?: number
  r?: string
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const count = props.count ?? 1
  const din = props.dinNet ?? "LED_DATA"
  const rail = props.rail ?? "V3_3"
  const start = props.startIndex ?? 10
  const pitch = props.pitch ?? PIXEL_PITCH_MM
  const r = props.r ?? "R30"
  const pixels = Array.from({ length: count }, (_, i) => i)

  return (
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      {/* series damping resistor on the first hop only */}
      <resistor name={r} resistance="330" footprint="0402"
        pcbX={-pitch} pcbY={0} schX={-3} schY={0}
        supplierPartNumbers={{ jlcpcb: ["C25104"] }} />
      <trace name={`TR_${r}_in`} from={`.${r} > .pin1`} to={`net.${din}`} />
      <trace name={`TR_${r}_out`} from={`.${r} > .pin2`} to={`net.PX_${start}_DIN`} />

      {pixels.map((i) => {
        const d = `D${start + i}`
        const c = `C${40 + i}`
        const x = i * pitch
        return (
          // Explicit coordinates on the group: an unpositioned group triggers
          // auto-layout, which stacks the pixels instead of spacing them.
          <group key={d} pcbX={x} pcbY={0} schX={i * 4} schY={0}>
            <Ws2812Pixel name={d} pcbX={0} pcbY={0} schX={0} schY={0} />
            <capacitor name={c} capacitance="100nF" footprint="0402"
              pcbX={0} pcbY={-3.6} schX={0} schY={-2.5} schRotation="90deg"
              supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
            <trace name={`TR_${d}_vdd`} from={`.${d} > .VDD`} to={`net.${rail}`} />
            <trace name={`TR_${d}_gnd`} from={`.${d} > .GND`} to="net.GND" />
            <trace name={`TR_${c}_v`} from={`.${c} > .pin1`} to={`net.${rail}`} />
            <trace name={`TR_${c}_g`} from={`.${c} > .pin2`} to="net.GND" />
            <trace name={`TR_${d}_din`} from={`.${d} > .DIN`} to={`net.PX_${start + i}_DIN`} />
            <trace name={`TR_${d}_dout`} from={`.${d} > .DOUT`} to={`net.PX_${start + i + 1}_DIN`} />
          </group>
        )
      })}
    </group>
  )
}

export default Ws2812Chain
