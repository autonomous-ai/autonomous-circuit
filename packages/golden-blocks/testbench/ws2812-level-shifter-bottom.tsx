import { Ws2812LevelShifter } from "../blocks/ws2812-level-shifter/ws2812-level-shifter"

export default () => (
  <board
    width="24mm"
    height="14mm"
    thickness="1.6mm"
    routingDisabled
    minTraceWidth="0.2mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    <Ws2812LevelShifter layer="bottom" pcbX={0} pcbY={0} />
  </board>
)
