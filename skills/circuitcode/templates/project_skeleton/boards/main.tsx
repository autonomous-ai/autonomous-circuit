/**
 * Generated protected-usb-indicator-v1 starter.
 * dialect: tscircuit@0.0.2279 (pinned by the project toolchain)
 *
 * Planner blocks: usb-power-entry, status-led, usb-c-data, ldo-3v3
 * Protected topology: VBUS_RAW -> U7 -> V5 -> U2 -> V3_3
 * Schematic policy: explicit, left-to-right block anchors.
 */

import { GndPlanes, MaskedCopperNode, MountingHole, PowerTrunk } from "../blocks/glue"
import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { UsbPowerEntry } from "../blocks/usb-power-entry/usb-power-entry"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { StatusLed } from "../blocks/status-led/status-led"

const GND_STITCHES = [
  { x: -17, y: -14 },
  { x: -9, y: -14 },
  { x: 9, y: -14 },
  { x: 18, y: -14 },
  { x: -20, y: -7 },
  { x: -10, y: -7 },
  { x: 10, y: -7 },
  { x: 20, y: -7 },
  { x: -20, y: 0 },
  { x: -10, y: 0 },
  { x: 10, y: 0 },
  { x: 20, y: 0 },
  { x: -20, y: 5 },
  { x: -10, y: 5 },
  { x: 0, y: 5 },
  { x: 10, y: 5 },
  { x: 20, y: 5 },
  { x: -16, y: 15 },
  { x: -9, y: 17 },
  { x: 0, y: 15 },
  { x: 9, y: 17 },
  { x: 16, y: 15 },
] as const

const USB_LOCAL_REGION = {
  minX: -10, maxX: 10,
  minY: -18.4, maxY: 1.7,
} as const

export default () => (
  <board width="46.9mm" height="36.8mm" thickness="1.6mm"
    minTraceWidth="0.15mm" minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
    minViaPadDiameter="0.6mm" minViaHoleDiameter="0.3mm">
    <autoroutingphase name="usb-connector-pair" phaseIndex={0} region={USB_LOCAL_REGION} />
    <autoroutingphase name="usb-series-pair" phaseIndex={1} region={USB_LOCAL_REGION} />
    <autoroutingphase name="usb-cc1" phaseIndex={2} region={USB_LOCAL_REGION} />
    <autoroutingphase name="usb-cc2" phaseIndex={3} region={USB_LOCAL_REGION} />
    <autoroutingphase name="usb-local-power" phaseIndex={4} region={USB_LOCAL_REGION} />
    <net name="VBUS_RAW" routingPhaseIndex={5} />
    <net name="V5" routingPhaseIndex={6} />
    <net name="V3_3" routingPhaseIndex={7} />
    <net name="USB_POWER_FAULT" routingPhaseIndex={8} />

    <UsbCData pcbX={0} pcbY={-11.3} schX={-12} schY={0}
      vbusBoundaryRefs={{ right: "N3", left: "N4" }} vbusRailNodeRef="N15"
      vbusClampNodeRef="N16"
      pairRules={{ pcbTraceGapMm: 0.15, maxLengthSkewMm: 3.8, maxUncoupledLengthMm: 3 }}
      localRoutingPhaseIndex={4} dpConnectorRoutingPhaseIndex={0}
      dmConnectorRoutingPhaseIndex={0} connectorPairRoutingPhaseIndex={0}
      seriesPairRoutingPhaseIndex={1} cc1RoutingPhaseIndex={2}
      cc2RoutingPhaseIndex={3} powerRoutingPhaseIndex={4}
      criticalSignalWidthMm={0.15} signalTraceWidthMm={0.25} />
    <UsbPowerEntry pcbX={-9.16} pcbY={10.47} schX={-4} schY={0}
      externalPowerTrunkPort="OUT" externalRawPowerTrunkPort="IN"
      externalFaultPullupPort="R32" signalTraceWidthMm={0.25}
      finePitchEscapeWidthMm={0.15} />
    <Ldo3v3 pcbX={5.81} pcbY={10.75} schX={4} schY={0}
      externalPowerTrunkPort="VOUT" externalInputPowerTrunkPort="VIN"
      railWidthMm={0.8} pinNeckdownWidthMm={0.2}
      maxPinNeckdownLengthMm={2} />
    <StatusLed layer="bottom" pcbX={-1.86} pcbY={9.69}
      schX={12} schY={0} externalRailAttachmentPort="R"
      railTraceWidthMm={0.2} signalTraceWidthMm={0.25}
      maxRailNeckdownLengthMm={2} />

    <group pcbX={0} pcbY={0}>
    <MountingHole name="H1" diameter={3.2} pcbX={-20.25} pcbY={-15.2} />
    <MountingHole name="H2" diameter={3.2} pcbX={20.25} pcbY={15.2} />
    <GndPlanes layers={["top", "bottom"]} stitchingVias={[...GND_STITCHES]}
      viaOuterDiameterMm={0.6} viaHoleDiameterMm={0.3} />

    <PowerTrunk name="V5_MAIN" source=".U7 > .OUT" net="V5"
      sourcePoint={{ x: -10.51001, y: 9.52004 }}
      start={{ x: -11.99, y: 8.22 }}
      trunkVia={{ x: -13.44, y: 6.77 }}
      end={{ x: 10.6675, y: 6.77 }}
      startTestpoint="TP11" endTestpoint="TP12"
      sourceLayer="top" trunkLayer="bottom" trunkWidthMm={0.8}
      neckdownWidthMm={0.2} maxNeckdownLengthMm={2}
      viaOuterDiameterMm={0.8} viaHoleDiameterMm={0.5} />

    <PowerTrunk name="V3V3_MAIN" source=".U2 > .VOUT" net="V3_3"
      sourcePoint={{ x: 9.01, y: 13.05 }}
      start={{ x: 9.01, y: 15.05 }}
      trunkVia={{ x: 7.41, y: 15.75 }}
      end={{ x: -0.24, y: 13.48 }}
      startTestpoint="TP13" endTestpoint="TP14"
      sourceLayer="top" trunkLayer="bottom" trunkWidthMm={0.8}
      neckdownWidthMm={0.2} maxNeckdownLengthMm={2}
      viaOuterDiameterMm={0.8} viaHoleDiameterMm={0.5} />

    <MaskedCopperNode name="N20" layer="top" diameterMm={0.8}
      pcbX={-14.24} pcbY={11.42} />
    <MaskedCopperNode name="N21" layer="top" diameterMm={0.8}
      pcbX={-6.57} pcbY={8.57} />
    <trace name="TR_RAW_ATTACH_NECK" from=".N21 > .pin1" to=".C24 > .pin1"
      thickness="0.2mm" maxLength="2mm" pcbPathRelativeTo=".N21 > .pin1"
      pcbPath={[{ x: 0, y: 0 }, { x: 0, y: 1.4 }]} />
    <trace name="TR_RAW_ATTACH_TRUNK" from=".N15 > .pin1" to=".N21 > .pin1"
      thickness="0.8mm" maxLength="24mm" pcbPathRelativeTo=".N15 > .pin1"
      pcbPath={[
        { x: 0, y: 0 },
        { x: 0, y: 5.85 },
        { x: -3.77, y: 12.12 },
      ]} />
    <group pcbStyle={{ viaPadDiameter: "0.8mm", viaHoleDiameter: "0.5mm" }}>
      <trace name="TR_V5_ATTACH_LDO" from=".TP12 > .pin1" to=".C2 > .pin1"
        thickness="0.8mm" maxLength="12mm" pcbPathRelativeTo=".TP12 > .pin1"
        pcbPath={[
          { x: 0, y: 0 },
          { x: 0.328, y: 4.05 },
          { x: 0.328, y: 4.05, via: true, fromLayer: "bottom", toLayer: "top" },
          { x: 0.328, y: 4.05 },
          { x: 0.328, y: 1.42 },
          { x: 0.0675, y: 1.68 },
        ]} />
    </group>
    <trace name="TR_V3_ATTACH_LED" from=".TP14 > .pin1" to=".R20 > .pin1"
      thickness="0.2mm" maxLength="2mm" pcbPathRelativeTo=".TP14 > .pin1"
      pcbPath={[{ x: 0, y: 0 }, { x: -1.11, y: -1.59 }]} />
    <group pcbStyle={{ viaPadDiameter: "0.8mm", viaHoleDiameter: "0.5mm" }}>
      <trace name="TR_V3_ATTACH_BRANCH" from=".TP14 > .pin1" to=".N20 > .pin1"
        thickness="0.8mm" maxLength="20mm" pcbPathRelativeTo=".TP14 > .pin1"
        pcbPath={[
          { x: 0, y: 0 },
          { x: -2.5, y: 1.6 },
          { x: -2.5, y: 1.6, via: true, fromLayer: "bottom", toLayer: "top" },
          { x: -2.5, y: 1.6 },
          { x: -14, y: 1.6 },
          { x: -14, y: -2.06 },
        ]} />
    </group>
    <trace name="TR_V3_ATTACH_FAULT" from=".N20 > .pin1" to=".R32 > .pin2"
      thickness="0.2mm" maxLength="2mm" pcbPathRelativeTo=".N20 > .pin1"
      pcbPath={[{ x: 0, y: 0 }, { x: 1.47, y: 0 }]} />
    </group>
  </board>
)
