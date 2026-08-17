/**
 * golden-block: sensor-bme280 (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Bosch BME280 temperature/humidity/pressure sensor (LCSC C92489) on I2C:
 * CSB tied to VDDIO (selects I2C mode), SDO tied to GND (address 0x76),
 * 100nF on VDD and VDDIO. Requires exactly one i2c-bus block on the same
 * SDA/SCL nets.
 *
 * Land pattern: footprinter LGA-8 string matched at 100% copper IoU
 * (tscircuit-cli import C92489 --jlcpcb, 2026-08-10).
 *
 * Default refdes (global v1 allocation): U5, C18, C19.
 */

const bmePinLabels = {
  pin1: ["GND1"],
  pin2: ["CSB"],
  pin3: ["SDI", "SDA"],
  pin4: ["SCK", "SCL"],
  pin5: ["SDO"],
  pin6: ["VDDIO"],
  pin7: ["GND2"],
  pin8: ["VDD"],
} as const

export const Bme280Chip = (props: {
  name: string
  pcbX?: number | string
  pcbY?: number | string
  pcbRotation?: number | string
  schX?: number
  schY?: number
}) => (
  <chip
    {...props}
    pinLabels={bmePinLabels}
    pinAttributes={{
      VDD: { requiresPower: true },
      VDDIO: { requiresPower: true },
      GND1: { requiresGround: true },
      GND2: { requiresGround: true },
    }}
    supplierPartNumbers={{ jlcpcb: ["C92489"] }}
    manufacturerPartNumber="BME280"
    footprint="lga8_grid4x0_pillpads_p0.65mm_w3.2841mm_pw0.364mm_pl0.767mm"
  />
)

export const SensorBme280 = (props: {
  u?: string
  cVdd?: string
  cVddio?: string
  sdaNet?: string
  sclNet?: string
  rail?: string
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const u = props.u ?? "U5"
  const cVdd = props.cVdd ?? "C18"
  const cVddio = props.cVddio ?? "C19"
  const sda = props.sdaNet ?? "SDA"
  const scl = props.sclNet ?? "SCL"
  const rail = props.rail ?? "V3_3"
  return (
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <Bme280Chip name={u} pcbX={0} pcbY={0} schX={0} schY={0} />
      <capacitor name={cVdd} capacitance="100nF" footprint="0402" pcbX={-3} pcbY={0} schX={-3} schY={2}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name={cVddio} capacitance="100nF" footprint="0402" pcbX={3} pcbY={0} schX={3} schY={2}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />

      <trace name={`TR_${u}_vdd`} from={`.${u} > .VDD`} to={`net.${rail}`} />
      <trace name={`TR_${u}_vddio`} from={`.${u} > .VDDIO`} to={`net.${rail}`} />
      <trace name={`TR_${u}_gnd1`} from={`.${u} > .GND1`} to="net.GND" />
      <trace name={`TR_${u}_gnd2`} from={`.${u} > .GND2`} to="net.GND" />
      {/* I2C mode + address 0x76 (both frozen — the block is the safety) */}
      <trace name={`TR_${u}_csb`} from={`.${u} > .CSB`} to={`net.${rail}`} />
      <trace name={`TR_${u}_sdo`} from={`.${u} > .SDO`} to="net.GND" />
      <trace name={`TR_${u}_sda`} from={`.${u} > .SDA`} to={`net.${sda}`} />
      <trace name={`TR_${u}_scl`} from={`.${u} > .SCL`} to={`net.${scl}`} />
      <trace name={`TR_${cVdd}_v`} from={`.${cVdd} > .pin1`} to={`net.${rail}`} />
      <trace name={`TR_${cVdd}_g`} from={`.${cVdd} > .pin2`} to="net.GND" />
      <trace name={`TR_${cVddio}_v`} from={`.${cVddio} > .pin1`} to={`net.${rail}`} />
      <trace name={`TR_${cVddio}_g`} from={`.${cVddio} > .pin2`} to="net.GND" />
    </group>
  )
}

export default SensorBme280
