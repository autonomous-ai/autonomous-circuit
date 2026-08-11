import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { DebugPort } from "../blocks/glue"

/**
 * The block plus the one piece of board furniture it obliges the board to
 * carry. `Rp2040Core` *creates* the SWCLK and SWD nets and terminates
 * neither; land them or the assembled board cannot be halted or recovered.
 *
 * The port is here and not inside the block on measurement: three pads inside
 * the block's own box put the router through the crystal cluster and it came
 * back with a via shorted into U3's pad field. Outboard of the flash it
 * routes clean. A debug port is board furniture, like a mounting hole — which
 * is why it lives in `glue.tsx` beside `MountingHole`.
 */
export default () => (
  <board width="46mm" height="42mm" thickness="1.6mm">
    <Rp2040Core pcbX={-3} pcbY={3} />
    <DebugPort pcbX={17} pcbY={-6} pcbRotation={90} schX={22} schY={4} />
  </board>
)
