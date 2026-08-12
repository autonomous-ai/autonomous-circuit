import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"

export default () => (
  <board width="22mm" height="20mm" thickness="1.6mm"
    minTraceToPadEdgeClearance="0.15mm" minViaEdgeToPadEdgeClearance="0.15mm">
    <Ldo3v3 layer="bottom" pcbX={-1} pcbY={2} />
  </board>
)
