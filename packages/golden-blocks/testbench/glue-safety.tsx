import { GndPour, MountingHole } from "../blocks/glue"

/**
 * Minimal behavioral fixture for the two guarded glue primitives.
 *
 * H_RAW is deliberately bare: it isolates GndPour's own round-obstacle
 * cutout from MountingHole's routing keepout. H1 then proves the mounting-hole
 * helper independently at a translated board coordinate.
 */
export default () => (
  <board width="20mm" height="12mm" thickness="1.6mm">
    <GndPour layer="bottom" />
    <hole name="H_RAW" diameter="0.6mm" pcbX={4} pcbY={0} />
    <MountingHole name="H1" pcbX={-4} pcbY={0} />
  </board>
)
