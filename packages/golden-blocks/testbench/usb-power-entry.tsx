import { UsbPowerEntry } from "../blocks/usb-power-entry/usb-power-entry"
import { GndPlanes } from "../blocks/glue"

export default () => (
  <board
    width="20mm"
    height="14mm"
    thickness="1.6mm"
    minTraceWidth="0.2mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    <GndPlanes
      layers={["top", "bottom"]}
      stitchingVias={[
        { x: -8, y: -5 },
        { x: 8, y: -5 },
        { x: -8, y: 5 },
        { x: 8, y: 5 },
      ]}
    />
    <UsbPowerEntry pcbX={0} pcbY={0} />
  </board>
)
