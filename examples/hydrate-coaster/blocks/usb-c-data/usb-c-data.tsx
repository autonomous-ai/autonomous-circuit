/**
 * golden-block: usb-c-data (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * USB-C power + USB 2.0 full-speed data. A SUPERSET of usb-c-power (same
 * connector, CC pulldowns, VBUS bulk — same default refdes; never place
 * both blocks): the USBLC6's two ESD channels protect D+/D− here, and 27Ω
 * series resistors (RP2040 datasheet nominal 27.4Ω) bridge the connector
 * pairs to the MCU-side nets net.USB_DP / net.USB_DM.
 *
 * Pair with rp2040-core, which drives the same net names.
 *
 * Default refdes (global v1 allocation): J1, R1, R2, U1, C1 (shared with
 * usb-c-power) + R3, R4 (series).
 */

import { UsbCConnector, Usblc6 } from "../usb-c-power/usb-c-power"

export const UsbCData = (props: {
  j?: string
  r1?: string
  r2?: string
  rDp?: string
  rDm?: string
  u?: string
  c?: string
  vbusNet?: string
  dpNet?: string
  dmNet?: string
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const j = props.j ?? "J1"
  const r1 = props.r1 ?? "R1"
  const r2 = props.r2 ?? "R2"
  const rDp = props.rDp ?? "R3"
  const rDm = props.rDm ?? "R4"
  const u = props.u ?? "U1"
  const c = props.c ?? "C1"
  const vbus = props.vbusNet ?? "V5"
  const dp = props.dpNet ?? "USB_DP"
  const dm = props.dmNet ?? "USB_DM"
  // Connector-side (pre-series-resistor) pair nets:
  const cdp = `${dp}_CONN`
  const cdm = `${dm}_CONN`
  return (
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <UsbCConnector name={j} pcbX={0} pcbY={0} schX={0} schY={0}
        ncPins={["SBU1", "SBU2"]} />
      <resistor name={r1} resistance="5.1k" footprint="0402" pcbX={-4} pcbY={7} schX={3} schY={-2.5}
        supplierPartNumbers={{ jlcpcb: ["C25905"] }} />
      <resistor name={r2} resistance="5.1k" footprint="0402" pcbX={-1.5} pcbY={7} schX={3} schY={-3.5}
        supplierPartNumbers={{ jlcpcb: ["C25905"] }} />
      <resistor name={rDp} resistance="27" footprint="0402" pcbX={1.5} pcbY={7} schX={3} schY={-4.5}
        supplierPartNumbers={{ jlcpcb: ["C25100"] }} />
      <resistor name={rDm} resistance="27" footprint="0402" pcbX={4} pcbY={7} schX={3} schY={-5.5}
        supplierPartNumbers={{ jlcpcb: ["C25100"] }} />
      <Usblc6 name={u} pcbX={0} pcbY={10} schX={7} schY={-1} />
      <capacitor name={c} capacitance="10uF" footprint="0805" pcbX={8} pcbY={7} schX={7} schY={2}
        supplierPartNumbers={{ jlcpcb: ["C15850"] }} />

      {/* Rails */}
      <trace name={`TR_${j}_vbus1`} from={`.${j} > .VBUS1`} to={`net.${vbus}`} />
      <trace name={`TR_${j}_vbus2`} from={`.${j} > .VBUS2`} to={`net.${vbus}`} />
      <trace name={`TR_${j}_gnd1`} from={`.${j} > .GND1`} to="net.GND" />
      <trace name={`TR_${j}_gnd2`} from={`.${j} > .GND2`} to="net.GND" />
      <trace name={`TR_${j}_sh1`} from={`.${j} > .SHELL1`} to="net.GND" />
      <trace name={`TR_${j}_sh2`} from={`.${j} > .SHELL2`} to="net.GND" />
      <trace name={`TR_${j}_sh3`} from={`.${j} > .SHELL3`} to="net.GND" />
      <trace name={`TR_${j}_sh4`} from={`.${j} > .SHELL4`} to="net.GND" />

      {/* CC pulldowns (UFP sink) */}
      <trace name={`TR_${j}_cc1r`} from={`.${j} > .CC1`} to={`.${r1} > .pin1`} />
      <trace name={`TR_${r1}_gnd`} from={`.${r1} > .pin2`} to="net.GND" />
      <trace name={`TR_${j}_cc2r`} from={`.${j} > .CC2`} to={`.${r2} > .pin1`} />
      <trace name={`TR_${r2}_gnd`} from={`.${r2} > .pin2`} to="net.GND" />

      {/* Data pairs: both connector orientations tied, then ESD, then 27R */}
      <trace name={`TR_${j}_dp1`} from={`.${j} > .DP1`} to={`net.${cdp}`} />
      <trace name={`TR_${j}_dp2`} from={`.${j} > .DP2`} to={`net.${cdp}`} />
      <trace name={`TR_${j}_dm1`} from={`.${j} > .DM1`} to={`net.${cdm}`} />
      <trace name={`TR_${j}_dm2`} from={`.${j} > .DM2`} to={`net.${cdm}`} />
      <trace name={`TR_${u}_dpa`} from={`.${u} > .IO1`} to={`net.${cdp}`} />
      <trace name={`TR_${u}_dpb`} from={`.${u} > .IO1B`} to={`net.${cdp}`} />
      <trace name={`TR_${u}_dma`} from={`.${u} > .IO2`} to={`net.${cdm}`} />
      <trace name={`TR_${u}_dmb`} from={`.${u} > .IO2B`} to={`net.${cdm}`} />
      <trace name={`TR_${u}_vbus`} from={`.${u} > .VBUS`} to={`net.${vbus}`} />
      <trace name={`TR_${u}_gnd`} from={`.${u} > .GND`} to="net.GND" />
      <trace name={`TR_${rDp}_conn`} from={`.${rDp} > .pin1`} to={`net.${cdp}`} />
      <trace name={`TR_${rDp}_mcu`} from={`.${rDp} > .pin2`} to={`net.${dp}`} />
      <trace name={`TR_${rDm}_conn`} from={`.${rDm} > .pin1`} to={`net.${cdm}`} />
      <trace name={`TR_${rDm}_mcu`} from={`.${rDm} > .pin2`} to={`net.${dm}`} />

      {/* VBUS bulk */}
      <trace name={`TR_${c}_vbus`} from={`.${c} > .pin1`} to={`net.${vbus}`} />
      <trace name={`TR_${c}_gnd`} from={`.${c} > .pin2`} to="net.GND" />
    </group>
  )
}

export default UsbCData
