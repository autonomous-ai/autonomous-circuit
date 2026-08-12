import { GndPlanes, MaskedCopperNode } from "../blocks/glue"
import { UsbPowerEntry } from "../blocks/usb-power-entry/usb-power-entry"

export default () => (
  <board width="20mm" height="12mm" minTraceWidth="0.15mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm">
    <GndPlanes layers={["top", "bottom"]}
      stitchingVias={[{ x: -8, y: -4 }, { x: 8, y: 4 }]} />
    <UsbPowerEntry pcbX={-6} externalPowerTrunkPort="OUT"
      externalRawPowerTrunkPort="IN" />
    <MaskedCopperNode name="N901" layer="top" diameterMm={0.8}
      pcbX={-3.41} pcbY={-1.4} />
    <MaskedCopperNode name="N902" layer="top" diameterMm={0.8}
      pcbX={0} pcbY={-3} />
    <trace name="TR_BOARD_RAW_NECK" from=".N901 > .pin1" to=".C24 > .pin1"
      thickness="0.2mm" maxLength="2mm" pcbPathRelativeTo=".N901 > .pin1"
      pcbPath={[{ x: 0, y: 0 }, { x: 0, y: 0.9 }]} />
    <trace name="TR_BOARD_RAW_TRUNK" from=".N901 > .pin1" to=".N902 > .pin1"
      thickness="0.8mm" pcbPathRelativeTo=".N901 > .pin1"
      pcbPath={[{ x: 0, y: 0 }, { x: 3.41, y: -1.6 }]} />
    <trace name="TR_BOARD_RAW_BOUNDARY" from=".N902 > .pin1" to="net.VBUS_RAW"
      thickness="0.8mm" authoredNetTreeBoundary />
  </board>
)
