import { StatusLed } from "../blocks/status-led/status-led"
import { UsbPowerEntry } from "../blocks/usb-power-entry/usb-power-entry"
import { MaskedCopperNode } from "../blocks/glue"

export default () => (
  <board width="24mm" height="12mm" routingDisabled>
    <UsbPowerEntry
      pcbX={-6}
      externalPowerTrunkPort="OUT"
      externalRawPowerTrunkPort="IN"
      externalFaultPullupPort="R32"
    />
    <StatusLed pcbX={6} externalRailAttachmentPort="R" />
    {/* One board-owned, acyclic V3_3 tree replaces both suppressed leaves. */}
    <trace name="TR_BOARD_V3_ATTACH" from=".R32 > .pin2" to=".R20 > .pin1"
      thickness="0.2mm" />
    <trace name="TR_BOARD_V3_BOUNDARY" from=".R20 > .pin1" to="net.V3_3"
      thickness="0.8mm" authoredNetTreeBoundary />
    {/* Raw power similarly owns one narrow C24 attachment into one board
        trunk and one sole named boundary. */}
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
