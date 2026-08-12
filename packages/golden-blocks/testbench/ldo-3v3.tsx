import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { GndPlanes } from "../blocks/glue"
export default () => (
  <board width="22mm" height="20mm" thickness="1.6mm"
    minTraceToPadEdgeClearance="0.15mm" minViaEdgeToPadEdgeClearance="0.15mm">
    <GndPlanes layers={["top", "bottom"]}
      stitchingVias={[{ x: -8, y: -7 }, { x: 8, y: -7 }]} />
    <Ldo3v3 pcbX={-1} pcbY={2} />
  </board>
)
