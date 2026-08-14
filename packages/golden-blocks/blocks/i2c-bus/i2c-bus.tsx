/**
 * golden-block: i2c-bus (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * I2C bus pull-ups: 4.7k (LCSC C25900, JLC Basic) from SDA and SCL to the
 * logic rail. Place exactly ONE i2c-bus block per bus, regardless of how
 * many sensors share it. 4.7k suits 100/400kHz at 3.3V with a handful of
 * devices; that number is owned by circuitlib.tables.I2C.
 *
 * Default refdes (global v1 allocation): R8 (SDA), R9 (SCL).
 */

export const I2cBus = (props: {
  rSda?: string
  rScl?: string
  sdaNet?: string
  sclNet?: string
  rail?: string
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const rSda = props.rSda ?? "R8"
  const rScl = props.rScl ?? "R9"
  const sda = props.sdaNet ?? "SDA"
  const scl = props.sclNet ?? "SCL"
  const rail = props.rail ?? "V3_3"
  return (
    <group name={`__parts_block__i2c-bus__${rSda}`} pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <resistor name={rSda} resistance="4.7k" footprint="0402" pcbX={0} pcbY={0} schX={0} schY={0}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C25900"] }} />
      <resistor name={rScl} resistance="4.7k" footprint="0402" pcbX={2} pcbY={0} schX={1.5} schY={0}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C25900"] }} />
      <trace name={`TR_${rSda}_rail`} from={`.${rSda} > .pin1`} to={`net.${rail}`} />
      <trace name={`TR_${rSda}_sda`} from={`.${rSda} > .pin2`} to={`net.${sda}`} />
      <trace name={`TR_${rScl}_rail`} from={`.${rScl} > .pin1`} to={`net.${rail}`} />
      <trace name={`TR_${rScl}_scl`} from={`.${rScl} > .pin2`} to={`net.${scl}`} />
    </group>
  )
}

export default I2cBus
