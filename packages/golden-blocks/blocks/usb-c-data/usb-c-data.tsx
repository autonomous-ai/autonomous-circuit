/**
 * golden-block: usb-c-data (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * USB-C power + USB 2.0 full-speed data. A SUPERSET of usb-c-power (same
 * connector, CC pulldowns, VBUS bulk — same default refdes; never place
 * both blocks): the USBLC6's two ESD channels protect D+/D− here, and 27Ω
 * series resistors (RP2040 datasheet nominal 27.4Ω) bridge the connector
 * pairs to the MCU-side nets net.USB_DP / net.USB_DM.
 * Connector power is exposed as net.VBUS_RAW with only 1uF local attach
 * capacitance; pair it with usb-power-entry before any downstream bulk load.
 *
 * Pair with rp2040-core, which drives the same net names.
 *
 * Default refdes (global v1 allocation): J1, R1, R2, U1, C1 (shared with
 * usb-c-power) + R3, R4 (series).
 */

import { GndFanoutTrace, MaskedCopperNode } from "../glue"

import {
  UsbCConnector,
  UsbRawVbusTree,
  Usblc6,
  type UsbVbusBoundaryRefs,
} from "../usb-c-power/usb-c-power"

export type UsbDifferentialPairRules = {
  /** Edge-to-edge copper gap selected for the board stack-up. */
  pcbTraceGapMm: number
  /** Maximum routed-length mismatch within this physical pair section. */
  maxLengthSkewMm: number
  /** Maximum length that the router may leave the section uncoupled. */
  maxUncoupledLengthMm: number
}

const validatePairRules = (
  rules: UsbDifferentialPairRules,
  owner: string,
) => {
  const values = [
    rules.pcbTraceGapMm,
    rules.maxLengthSkewMm,
    rules.maxUncoupledLengthMm,
  ]
  if (values.some((value) => !Number.isFinite(value) || value <= 0)) {
    throw new Error(`${owner} differential-pair rules must be finite and positive`)
  }
}

/**
 * Board-owned direct USB pair between the RP2040 package pins and the two
 * connector-block series resistors.  Both endpoint blocks must disable their
 * legacy named-net leaves when this helper is present; selecting a named net
 * here would collapse the pair back into an ambiguous aggregate connection.
 */
export const UsbDeviceDifferentialPair = (props: {
  mcu?: string
  rDp?: string
  rDm?: string
  dpTraceName?: string
  dmTraceName?: string
  routingPhaseIndex?: number
  criticalSignalWidthMm?: number
  pairRules: UsbDifferentialPairRules
}) => {
  const mcu = props.mcu ?? "U3"
  const rDp = props.rDp ?? "R3"
  const rDm = props.rDm ?? "R4"
  const dpTraceName = props.dpTraceName ?? `TR_${mcu}_${rDp}_usb_dp`
  const dmTraceName = props.dmTraceName ?? `TR_${mcu}_${rDm}_usb_dm`
  const criticalSignalWidthMm = props.criticalSignalWidthMm ?? 0.15
  if (!Number.isFinite(criticalSignalWidthMm) || criticalSignalWidthMm <= 0) {
    throw new Error(
      "UsbDeviceDifferentialPair criticalSignalWidthMm must be finite and positive",
    )
  }
  validatePairRules(props.pairRules, "UsbDeviceDifferentialPair")
  return (
    <>
      <trace name={dpTraceName}
        from={`.${mcu} > .USB_DP`} to={`.${rDp} > .pin2`}
        thickness={`${criticalSignalWidthMm}mm`}
        routingPhaseIndex={props.routingPhaseIndex} />
      <trace name={dmTraceName}
        from={`.${mcu} > .USB_DM`} to={`.${rDm} > .pin2`}
        thickness={`${criticalSignalWidthMm}mm`}
        routingPhaseIndex={props.routingPhaseIndex} />
      <differentialpair
        name={`DP_${mcu}_${rDp}_${rDm}`}
        positiveConnection={dpTraceName}
        negativeConnection={dmTraceName}
        pcbTraceGap={`${props.pairRules.pcbTraceGapMm}mm`}
        maxLengthSkew={`${props.pairRules.maxLengthSkewMm}mm`}
        maxUncoupledLength={`${props.pairRules.maxUncoupledLengthMm}mm`}
      />
    </>
  )
}

export const UsbCData = (props: {
  j?: string
  r1?: string
  r2?: string
  rDp?: string
  rDm?: string
  u?: string
  c?: string
  vbusNet?: string
  /** Globally unique hidden nodes for the reversible VBUS power tree. */
  vbusBoundaryRefs: UsbVbusBoundaryRefs
  /** Globally unique hidden endpoint for the raw rail and local clamp/cap leaves. */
  vbusRailNodeRef: string
  /** Globally unique local endpoint directly outside the clamp's VBUS pad. */
  vbusClampNodeRef: string
  dpNet?: string
  dmNet?: string
  layer?: "top" | "bottom"
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
  /** Board-owned phase for connector-local VBUS/CC copper. */
  localRoutingPhaseIndex?: number
  /** Board-owned phases for the two reversible connector-side data trees. */
  dpConnectorRoutingPhaseIndex?: number
  dmConnectorRoutingPhaseIndex?: number
  /** Board-owned phase shared by both direct physical differential sections. */
  pairRoutingPhaseIndex?: number
  /** Optional deterministic phase for the connector-to-ESD direct pair. */
  connectorPairRoutingPhaseIndex?: number
  /** Optional deterministic phase for the ESD-to-series direct pair. */
  seriesPairRoutingPhaseIndex?: number
  /** Optional deterministic phases for the two ordinary-width CC routes. */
  cc1RoutingPhaseIndex?: number
  cc2RoutingPhaseIndex?: number
  /** Optional deterministic phase for local VBUS leaves and the wide rail. */
  powerRoutingPhaseIndex?: number
  /** Explicit USB fine-pitch/controlled-pair exception to ordinary signals. */
  criticalSignalWidthMm?: number
  /** Ordinary board-signal width for the CC configuration lines. */
  signalTraceWidthMm?: number
  /** Board stack-up and routing limits for both connector-side pair sections. */
  pairRules: UsbDifferentialPairRules
  /** Disable when UsbDeviceDifferentialPair owns direct resistor-to-MCU copper. */
  emitMcuNetLeaves?: boolean
}) => {
  const j = props.j ?? "J1"
  const r1 = props.r1 ?? "R1"
  const r2 = props.r2 ?? "R2"
  const rDp = props.rDp ?? "R3"
  const rDm = props.rDm ?? "R4"
  const u = props.u ?? "U1"
  const c = props.c ?? "C1"
  const vbus = props.vbusNet ?? "VBUS_RAW"
  const dp = props.dpNet ?? "USB_DP"
  const dm = props.dmNet ?? "USB_DM"
  const layer = props.layer ?? "top"
  const oppositeLayer = layer === "top" ? "bottom" : "top"
  const localX = (x: number) => layer === "bottom" ? -x : x
  const localRotation = (degrees: number) =>
    layer === "bottom" ? (360 - degrees) % 360 : degrees
  const localRoutingPhaseIndex = props.localRoutingPhaseIndex
  const dpConnectorRoutingPhaseIndex =
    props.dpConnectorRoutingPhaseIndex ?? localRoutingPhaseIndex
  const dmConnectorRoutingPhaseIndex =
    props.dmConnectorRoutingPhaseIndex ?? localRoutingPhaseIndex
  const pairRoutingPhaseIndex = props.pairRoutingPhaseIndex ?? localRoutingPhaseIndex
  const connectorPairRoutingPhaseIndex =
    props.connectorPairRoutingPhaseIndex ?? pairRoutingPhaseIndex
  const seriesPairRoutingPhaseIndex =
    props.seriesPairRoutingPhaseIndex ?? pairRoutingPhaseIndex
  const cc1RoutingPhaseIndex =
    props.cc1RoutingPhaseIndex ?? localRoutingPhaseIndex
  const cc2RoutingPhaseIndex =
    props.cc2RoutingPhaseIndex ?? localRoutingPhaseIndex
  const powerRoutingPhaseIndex =
    props.powerRoutingPhaseIndex ?? localRoutingPhaseIndex
  const criticalSignalWidthMm = props.criticalSignalWidthMm ?? 0.15
  const criticalSignalWidth = `${criticalSignalWidthMm}mm`
  const signalTraceWidthMm = props.signalTraceWidthMm ?? 0.25
  if (
    !Number.isFinite(criticalSignalWidthMm) || criticalSignalWidthMm <= 0 ||
    !Number.isFinite(signalTraceWidthMm) || signalTraceWidthMm <= 0
  ) {
    throw new Error("UsbCData trace widths must be finite and positive")
  }
  validatePairRules(props.pairRules, "UsbCData")
  return (
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <UsbCConnector name={j} layer={layer} pcbX={localX(0)} pcbY={0}
        pcbRotation={localRotation(0)} schX={0} schY={0}
        ncPins={["SBU1", "SBU2"]} />
      <resistor name={r1} resistance="5.1k" footprint="0402"
        pcbX={localX(-5.2)} pcbY={10.5} pcbRotation={localRotation(0)} schX={3} schY={-2.5}
        layer={layer} supplierPartNumbers={{ jlcpcb: ["C25905"] }} />
      <resistor name={r2} resistance="5.1k" footprint="0402"
        pcbX={localX(5.2)} pcbY={10.5} pcbRotation={localRotation(0)} schX={3} schY={-3.5}
        layer={layer} supplierPartNumbers={{ jlcpcb: ["C25905"] }} />
      <resistor name={rDp} resistance="27" footprint="0402"
        pcbX={localX(-1.44)} pcbY={8.95} pcbRotation={localRotation(180)} schX={3} schY={-4.5}
        layer={layer} supplierPartNumbers={{ jlcpcb: ["C25100"] }} />
      <resistor name={rDm} resistance="27" footprint="0402"
        pcbX={localX(1.44)} pcbY={8.95} pcbRotation={localRotation(0)} schX={3} schY={-5.5}
        layer={layer} supplierPartNumbers={{ jlcpcb: ["C25100"] }} />
      {/* Rotate the flow-through ESD array so IO1/IO2 face the connector and
          IO1B/IO2B face a symmetric resistor row. This makes the long input
          section physically pairable instead of forcing two independent
          routes around a vertically stacked SOT-23 pin field. */}
      <Usblc6 name={u} layer={layer} pcbX={localX(0)} pcbY={6.15}
        pcbRotation={localRotation(90)} schX={7} schY={-1} />
      <capacitor name={c} capacitance="1uF" footprint="0402"
        pcbX={localX(-4.4)} pcbY={9.15} pcbRotation={localRotation(180)} schX={7} schY={2}
        layer={layer} supplierPartNumbers={{ jlcpcb: ["C52923"] }} />

      {/* Reversible connector VBUS pads form one authored .2/.8/.2 tree. */}
      <UsbRawVbusTree j={j} net={vbus} boundaryRefs={props.vbusBoundaryRefs}
        railNodeRef={props.vbusRailNodeRef} railNode={{ x: -2.8, y: 7.75 }}
        railLayerTransition={{
          startVia: { x: -5.2, y: 3.4 },
          endVia: { x: -2.8, y: 6.7 },
        }}
        layer={layer} routingPhaseIndex={powerRoutingPhaseIndex} />
      <GndFanoutTrace name={`TR_${j}_gnd1`} from={`.${j} > .GND1`} />
      <GndFanoutTrace name={`TR_${j}_gnd2`} from={`.${j} > .GND2`} />
      <GndFanoutTrace name={`TR_${j}_sh1`} from={`.${j} > .SHELL1`} />
      <GndFanoutTrace name={`TR_${j}_sh2`} from={`.${j} > .SHELL2`} />
      <GndFanoutTrace name={`TR_${j}_sh3`} from={`.${j} > .SHELL3`} />
      <GndFanoutTrace name={`TR_${j}_sh4`} from={`.${j} > .SHELL4`} />

      {/* CC pulldowns (UFP sink) */}
      <trace name={`TR_${j}_cc1r`} from={`.${j} > .CC1`} to={`.${r1} > .pin1`}
        thickness={`${signalTraceWidthMm}mm`} routingPhaseIndex={cc1RoutingPhaseIndex}
        pcbPath={[
          { x: localX(-1.25), y: 2.92 },
          { x: localX(-5.71), y: 7.38 },
        ]} />
      <GndFanoutTrace name={`TR_${r1}_gnd`} from={`.${r1} > .pin2`} />
      <trace name={`TR_${j}_cc2r`} from={`.${j} > .CC2`} to={`.${r2} > .pin1`}
        thickness={`${signalTraceWidthMm}mm`} routingPhaseIndex={cc2RoutingPhaseIndex}
        pcbPath={[
          { x: localX(1.75), y: 7.56 },
        ]} />
      <GndFanoutTrace name={`TR_${r2}_gnd`} from={`.${r2} > .pin2`} />

      {/* The USB-C orientation pads are interleaved DP/DM/DP/DM.  Model each
          channel as a connected, acyclic authored tree instead of asking the
          router to invent two competing five-point Steiner portfolios.  One
          deliberate crossover per channel moves onto the opposite face and
          back. The connector enters one USBLC6 pad and the internally common
          mate exits into the series resistor; no external copper bypasses the
          clamp package. */}
      <group pcbStyle={{ viaPadDiameter: "0.6mm", viaHoleDiameter: "0.3mm" }}>
        <trace name={`TR_${j}_dp_pair`}
          from={`.${j} > .DP1`} to={`.${j} > .DP2`}
          thickness={criticalSignalWidth}
          routingPhaseIndex={dpConnectorRoutingPhaseIndex}
          pcbPath={[
            { x: localX(-0.25), y: 3.06 },
            { x: localX(-0.38), y: 3.6 },
            { x: localX(-0.38), y: 3.6, via: true, fromLayer: layer, toLayer: oppositeLayer },
            { x: localX(-0.38), y: 3.6 },
            { x: localX(-0.38), y: 4.3 },
            { x: localX(1.6), y: 4.3 },
            { x: localX(1.6), y: 1.0 },
            { x: localX(1.6), y: 1.0, via: true, fromLayer: oppositeLayer, toLayer: layer },
            { x: localX(1.6), y: 1.0 },
            { x: localX(0.75), y: 1.0 },
          ]} />
      </group>
      <trace name={`TR_${j}_dp_esd`} from={`.${j} > .DP1`} to={`.${u} > .IO1`}
        thickness={criticalSignalWidth} routingPhaseIndex={connectorPairRoutingPhaseIndex} />
      <trace name={`TR_${u}_dp_r`} from={`.${u} > .IO1B`} to={`.${rDp} > .pin1`}
        thickness={criticalSignalWidth} routingPhaseIndex={seriesPairRoutingPhaseIndex} />

      <group pcbStyle={{ viaPadDiameter: "0.6mm", viaHoleDiameter: "0.3mm" }}>
        <trace name={`TR_${j}_dm_pair`}
          from={`.${j} > .DM1`} to={`.${j} > .DM2`}
          thickness={criticalSignalWidth}
          routingPhaseIndex={dmConnectorRoutingPhaseIndex}
          pcbPath={[
            { x: localX(0.25), y: 3.06 },
            { x: localX(0.38), y: 3.6 },
            { x: localX(0.38), y: 3.6, via: true, fromLayer: layer, toLayer: oppositeLayer },
            { x: localX(0.38), y: 3.6 },
            { x: localX(0.38), y: 2.8 },
            { x: localX(-1.6), y: 2.8 },
            { x: localX(-1.6), y: 1.0 },
            { x: localX(-1.6), y: 1.0, via: true, fromLayer: oppositeLayer, toLayer: layer },
            { x: localX(-1.6), y: 1.0 },
            { x: localX(-0.75), y: 1.0 },
          ]} />
      </group>
      <trace name={`TR_${j}_dm_esd`} from={`.${j} > .DM1`} to={`.${u} > .IO2`}
        thickness={criticalSignalWidth} routingPhaseIndex={connectorPairRoutingPhaseIndex} />
      <trace name={`TR_${u}_dm_r`} from={`.${u} > .IO2B`} to={`.${rDm} > .pin1`}
        thickness={criticalSignalWidth} routingPhaseIndex={seriesPairRoutingPhaseIndex} />
      <differentialpair
        name={`DP_${j}_${u}_connector_esd`}
        positiveConnection={`TR_${j}_dp_esd`}
        negativeConnection={`TR_${j}_dm_esd`}
        pcbTraceGap={`${props.pairRules.pcbTraceGapMm}mm`}
        maxLengthSkew={`${props.pairRules.maxLengthSkewMm}mm`}
        maxUncoupledLength={`${props.pairRules.maxUncoupledLengthMm}mm`}
      />
      <differentialpair
        name={`DP_${u}_${rDp}_${rDm}_series`}
        positiveConnection={`TR_${u}_dp_r`}
        negativeConnection={`TR_${u}_dm_r`}
        pcbTraceGap={`${props.pairRules.pcbTraceGapMm}mm`}
        maxLengthSkew={`${props.pairRules.maxLengthSkewMm}mm`}
        maxUncoupledLength={`${props.pairRules.maxUncoupledLengthMm}mm`}
      />

      <MaskedCopperNode name={props.vbusClampNodeRef} layer={layer}
        diameterMm={0.8} pcbX={localX(0)} pcbY={8.7} />
      <group pcbStyle={{ viaPadDiameter: "0.8mm", viaHoleDiameter: "0.5mm" }}>
        <trace name={`TR_${u}_vbus_backbone`}
          from={`.${props.vbusRailNodeRef} > .pin1`}
          to={`.${props.vbusClampNodeRef} > .pin1`}
          thickness="0.8mm"
          routingPhaseIndex={powerRoutingPhaseIndex}
          pcbPathRelativeTo={`.${props.vbusRailNodeRef} > .pin1`}
          pcbPath={[
            { x: 0, y: 0 },
            { x: localX(-1), y: 0 },
            { x: localX(-1), y: 0, via: true, fromLayer: layer, toLayer: oppositeLayer },
            { x: localX(-1), y: 0 },
            { x: localX(2.8), y: 1.95 },
            { x: localX(2.8), y: 1.95, via: true, fromLayer: oppositeLayer, toLayer: layer },
            { x: localX(2.8), y: 1.95 },
            { x: localX(2.8), y: 0.95 },
          ]} />
      </group>
      <trace name={`TR_${u}_vbus`} from={`.${u} > .VBUS`} to={`.${props.vbusClampNodeRef} > .pin1`}
        thickness="0.2mm" maxLength="3mm" routingPhaseIndex={powerRoutingPhaseIndex} />
      <GndFanoutTrace name={`TR_${u}_gnd`} from={`.${u} > .GND`} />
      {props.emitMcuNetLeaves !== false && (
        <>
          <trace name={`TR_${rDp}_mcu`} from={`.${rDp} > .pin2`} to={`net.${dp}`}
            thickness={criticalSignalWidth} />
          <trace name={`TR_${rDm}_mcu`} from={`.${rDm} > .pin2`} to={`net.${dm}`}
            thickness={criticalSignalWidth} />
        </>
      )}

      {/* Raw attach capacitance. Together with usb-power-entry's 100nF input
          bypass this is 1.1uF, safely below the 10uF USB attach limit. */}
      <trace name={`TR_${c}_vbus`} from={`.${c} > .pin1`} to={`.${props.vbusRailNodeRef} > .pin1`}
        thickness="0.2mm" maxLength="3mm" routingPhaseIndex={powerRoutingPhaseIndex} />
      <GndFanoutTrace name={`TR_${c}_gnd`} from={`.${c} > .pin2`} />
    </group>
  )
}

export default UsbCData
