import { ServoHeaderBank } from "../blocks/servo-header/servo-header"

/**
 * Four servo headers in a column down the left edge, pin1 pointing off the
 * board. tscircuit reads a connector's facing from pin1, not from
 * `pcbRotation`, so this is the edge these belong to — a header sitting
 * inland trips `pcb_connector_not_in_accessible_orientation`, and rightly:
 * a servo lead cannot reach a connector in the middle of a board.
 */
export default () => (
  <board width="16mm" height="26mm" thickness="1.6mm">
    <ServoHeaderBank count={4} pcbX={-3} pcbY={-7.62} schX={0} schY={0} />
  </board>
)
