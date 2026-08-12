import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
export default () => (
  <board width="46mm" height="42mm" thickness="1.6mm" routingDisabled>
    <Rp2040Core
      pcbX={-3}
      pcbY={3}
      debugPortPcbX={14}
      debugPortPcbY={-2}
      debugSwclkBoundaryRef="N1"
      debugSwdBoundaryRef="N2"
      powerRailNodeRefs={{
        westUpper: "N5", westLower: "N6", south: "N7",
        eastLower: "N8", eastUpper: "N9", topRight: "N10",
        topMiddle: "N11", topLeft: "N12", bulk: "N13", flash: "N14",
        dvddLeft: "N15", dvddRight: "N16", dvddSouth: "N17",
        dvddJunction: "N18",
      }}
    />
  </board>
)
