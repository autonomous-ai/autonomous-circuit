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

import { GndFanoutTrace } from "../glue"

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
  /**
   * Replace this connector pad's ordinary VBUS-to-net edge with a board-level
   * PowerTrunk. The authored VBUS1/VBUS2 crossover remains in the block, so
   * either duplicated pad can be the trunk source without closing a cycle.
   */
  externalPowerTrunkPort?: "VBUS1" | "VBUS2"
  dpNet?: string
  dmNet?: string
  layer?: "top" | "bottom"
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
  /** Board-owned phase for connector-local VBUS/CC copper. */
  localRoutingPhaseIndex?: number
  /** Board-owned phases for the two duplicated connector-side data trees. */
  dpConnectorRoutingPhaseIndex?: number
  dmConnectorRoutingPhaseIndex?: number
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
  const layer = props.layer ?? "top"
  const localRoutingPhaseIndex = props.localRoutingPhaseIndex
  const dpConnectorRoutingPhaseIndex =
    props.dpConnectorRoutingPhaseIndex ?? localRoutingPhaseIndex
  const dmConnectorRoutingPhaseIndex =
    props.dmConnectorRoutingPhaseIndex ?? localRoutingPhaseIndex
  return (
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <UsbCConnector name={j} layer={layer} pcbX={0} pcbY={0} schX={0} schY={0}
        ncPins={["SBU1", "SBU2"]} />
      <resistor name={r1} resistance="5.1k" footprint="0402" pcbX={-4} pcbY={7} schX={3} schY={-2.5}
        layer={layer} supplierPartNumbers={{ jlcpcb: ["C25905"] }} />
      <resistor name={r2} resistance="5.1k" footprint="0402" pcbX={4} pcbY={7} schX={3} schY={-3.5}
        layer={layer} supplierPartNumbers={{ jlcpcb: ["C25905"] }} />
      <resistor name={rDp} resistance="27" footprint="0402" pcbX={-1.5} pcbY={7} schX={3} schY={-4.5}
        layer={layer} supplierPartNumbers={{ jlcpcb: ["C25100"] }} />
      <resistor name={rDm} resistance="27" footprint="0402" pcbX={1.5} pcbY={7} schX={3} schY={-5.5}
        layer={layer} supplierPartNumbers={{ jlcpcb: ["C25100"] }} />
      <Usblc6 name={u} layer={layer} pcbX={0} pcbY={10} schX={7} schY={-1} />
      <capacitor name={c} capacitance="10uF" footprint="0805" pcbX={8} pcbY={7} schX={7} schY={2}
        layer={layer} supplierPartNumbers={{ jlcpcb: ["C15850"] }} />

      {/* Rails */}
      {/* The connector's two VBUS pads face each other across a field of
          signal pads. Crossing that field on top produces an endpoint-layer
          mismatch or pad clearance failure, so the tie deliberately drops
          below it and returns to each top-only SMD endpoint. */}
      <group
        pcbStyle={{
          viaPadDiameter: "0.8mm",
          viaHoleDiameter: "0.5mm",
        }}
      >
        <trace
          name={`TR_${j}_vbus_tie`}
          from={`.${j} > .VBUS1`}
          to={`.${j} > .VBUS2`}
          thickness="0.2mm"
          routingPhaseIndex={localRoutingPhaseIndex}
          pcbPath={[
            { x: 2.4, y: 3.2 },
            { x: 2.4, y: 3.2, via: true, fromLayer: "top", toLayer: "bottom" },
            { x: 2.4, y: 3.2 },
            { x: 1.5, y: 2.8 },
            { x: 1.15, y: 2.85 },
            { x: 0.55, y: 3.55 },
            { x: -0.8, y: 4.05 },
            { x: -1.1, y: 3.95 },
            { x: -2.4, y: 3.2 },
            { x: -2.4, y: 3.2, via: true, fromLayer: "bottom", toLayer: "top" },
            { x: -2.4, y: 3.2 },
          ]}
        />
      </group>
      {props.externalPowerTrunkPort === undefined ? (
        <trace name={`TR_${j}_vbus`} from={`.${j} > .VBUS1`} to={`net.${vbus}`} />
      ) : null}
      <GndFanoutTrace name={`TR_${j}_gnd1`} from={`.${j} > .GND1`} />
      <GndFanoutTrace name={`TR_${j}_gnd2`} from={`.${j} > .GND2`} />
      <GndFanoutTrace name={`TR_${j}_sh1`} from={`.${j} > .SHELL1`} />
      <GndFanoutTrace name={`TR_${j}_sh2`} from={`.${j} > .SHELL2`} />
      <GndFanoutTrace name={`TR_${j}_sh3`} from={`.${j} > .SHELL3`} />
      <GndFanoutTrace name={`TR_${j}_sh4`} from={`.${j} > .SHELL4`} />

      {/* CC pulldowns (UFP sink) */}
      <trace name={`TR_${j}_cc1r`} from={`.${j} > .CC1`} to={`.${r1} > .pin1`}
        routingPhaseIndex={localRoutingPhaseIndex} />
      <GndFanoutTrace name={`TR_${r1}_gnd`} from={`.${r1} > .pin2`} />
      <trace name={`TR_${j}_cc2r`} from={`.${j} > .CC2`} to={`.${r2} > .pin1`}
        routingPhaseIndex={localRoutingPhaseIndex} />
      <GndFanoutTrace name={`TR_${r2}_gnd`} from={`.${r2} > .pin2`} />

      {/* The USB-C orientation pads are interleaved DP/DM/DP/DM. Model each
          channel as a deliberate local tree instead of synthesizing two
          competing five-point MSTs. D+ owns the one required crossover on
          bottom copper; D- and the ESD/resistor branches remain autorouted. */}
      <group
        pcbStyle={{
          viaPadDiameter: "0.6mm",
          viaHoleDiameter: "0.3mm",
        }}
      >
        <trace
          name={`TR_${j}_dp_pair`}
          from={`.${j} > .DP1`}
          to={`.${j} > .DP2`}
          routingPhaseIndex={dpConnectorRoutingPhaseIndex}
          pcbPath={[
            { x: -0.65, y: 4.6 },
            { x: -0.65, y: 4.6, via: true, fromLayer: "top", toLayer: "bottom" },
            { x: -0.65, y: 4.6 },
            { x: 0.65, y: 4.6 },
            { x: 0.65, y: 4.6, via: true, fromLayer: "bottom", toLayer: "top" },
            { x: 0.65, y: 4.6 },
          ]}
        />
      </group>
      <trace name={`TR_${j}_dp_r`} from={`.${j} > .DP2`} to={`.${rDp} > .pin1`}
        routingPhaseIndex={dpConnectorRoutingPhaseIndex} />
      <trace name={`TR_${u}_dp_pair`} from={`.${u} > .IO1`} to={`.${u} > .IO1B`}
        routingPhaseIndex={dpConnectorRoutingPhaseIndex} />
      <trace name={`TR_${u}_dp_r`} from={`.${u} > .IO1B`} to={`.${rDp} > .pin1`}
        routingPhaseIndex={dpConnectorRoutingPhaseIndex} />
      <group
        pcbStyle={{
          viaPadDiameter: "0.6mm",
          viaHoleDiameter: "0.3mm",
        }}
      >
        <trace name={`TR_${j}_dm_pair`} from={`.${j} > .DM1`} to={`.${j} > .DM2`}
          routingPhaseIndex={dmConnectorRoutingPhaseIndex}
          pcbPath={[
            { x: 0, y: 5.7 },
            { x: 0, y: 5.7, via: true, fromLayer: "top", toLayer: "bottom" },
            { x: 0, y: 5.7 },
            { x: -1.4, y: 5.7 },
            { x: -1.4, y: 5.7, via: true, fromLayer: "bottom", toLayer: "top" },
            { x: -1.4, y: 5.7 },
          ]} />
      </group>
      <trace name={`TR_${j}_dm_r`} from={`.${j} > .DM1`} to={`.${rDm} > .pin1`}
        routingPhaseIndex={dmConnectorRoutingPhaseIndex} />
      <trace name={`TR_${u}_dm_pair`} from={`.${u} > .IO2`} to={`.${u} > .IO2B`}
        routingPhaseIndex={dmConnectorRoutingPhaseIndex} />
      <trace name={`TR_${u}_dm_r`} from={`.${u} > .IO2B`} to={`.${rDm} > .pin1`}
        routingPhaseIndex={dmConnectorRoutingPhaseIndex} />
      <trace name={`TR_${u}_vbus`} from={`.${u} > .VBUS`} to={`net.${vbus}`} />
      <GndFanoutTrace name={`TR_${u}_gnd`} from={`.${u} > .GND`} />
      <trace name={`TR_${rDp}_mcu`} from={`.${rDp} > .pin2`} to={`net.${dp}`} />
      <trace name={`TR_${rDm}_mcu`} from={`.${rDm} > .pin2`} to={`net.${dm}`} />

      {/* VBUS bulk */}
      <trace name={`TR_${c}_vbus`} from={`.${c} > .pin1`} to={`net.${vbus}`} />
      <GndFanoutTrace name={`TR_${c}_gnd`} from={`.${c} > .pin2`} />
    </group>
  )
}

export default UsbCData
