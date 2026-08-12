import { Ws2812Chain } from "../blocks/ws2812-chain/ws2812-chain"
import { GndPlanes } from "../blocks/glue"
export default () => (
  <board width="40mm" height="16mm" thickness="1.6mm"
    minTraceWidth="0.2mm" minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
    minViaPadDiameter="0.6mm" minViaHoleDiameter="0.3mm">
    <autoroutingphase name="pixel-10" phaseIndex={1} />
    <autoroutingphase name="pixel-11" phaseIndex={2} />
    <autoroutingphase name="pixel-12" phaseIndex={3} />
    <autoroutingphase name="pixel-13" phaseIndex={4} />
    <GndPlanes
      layers={["top", "bottom"]}
      stitchingVias={[
        { x: -18, y: 7 },
        { x: 0, y: 7 },
        { x: 18, y: 7 },
      ]}
    />
    <Ws2812Chain
      count={4}
      pcbX={10}
      pcbY={0}
      dataRoutingPhaseIndices={[1, 2, 3, 4]}
    />
  </board>
)
