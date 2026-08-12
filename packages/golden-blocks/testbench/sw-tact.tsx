import { SwTact } from "../blocks/sw-tact/sw-tact"
export default () => (
  <board width="16mm" height="14mm" thickness="1.6mm"
    minTraceToPadEdgeClearance="0.15mm" minViaEdgeToPadEdgeClearance="0.15mm">
    <SwTact pcbX={0} pcbY={0} />
  </board>
)
