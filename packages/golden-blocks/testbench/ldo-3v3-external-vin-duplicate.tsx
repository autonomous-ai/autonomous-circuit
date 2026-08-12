import { MaskedCopperNode } from "../blocks/glue"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"

// Historical negative fixture: adding a board-owned input boundary without
// the typed opt-out leaves two independent V5 leaves. Routing is deliberately
// disabled; the behavioral regression compares this source graph with the
// valid top/bottom compositions and prevents the duplicate from returning.
export default () => (
  <board width="26mm" height="20mm" thickness="1.6mm" routingDisabled>
    <Ldo3v3 />
    <MaskedCopperNode name="N90" layer="bottom" diameterMm={0.8}
      pcbX={5.8} pcbY={-3} />
    <trace name="TR_BOARD_V5_ATTACH" from=".N90 > .pin1" to=".C2 > .pin1"
      thickness="0.8mm" />
    <trace name="TR_BOARD_V5_BOUNDARY" from=".N90 > .pin1" to="net.V5"
      thickness="0.8mm" authoredNetTreeBoundary />
  </board>
)
