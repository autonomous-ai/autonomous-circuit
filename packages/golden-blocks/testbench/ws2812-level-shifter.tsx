import { Ws2812LevelShifter } from "../blocks/ws2812-level-shifter/ws2812-level-shifter"
import { GndPlanes } from "../blocks/glue"

export default () => (
  <board
    width="24mm"
    height="14mm"
    thickness="1.6mm"
    minTraceWidth="0.2mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    <GndPlanes layers={["top"]} />
    <Ws2812LevelShifter pcbX={0} pcbY={0} />
  </board>
)
