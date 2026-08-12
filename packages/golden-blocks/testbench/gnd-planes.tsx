import { GndFanoutTrace, GndPlanes } from "../blocks/glue"

export default () => (
  <board
    width="24mm"
    height="16mm"
    thickness="1.6mm"
    minTraceWidth="0.15mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    <GndPlanes
      layers={["top", "bottom"]}
      stitchingVias={[
        { x: 0, y: -5 },
        { x: 0, y: 5 },
      ]}
    />
    <resistor name="R901" resistance="1k" footprint="0402" pcbX={-5} pcbY={0} />
    <resistor name="R902" resistance="1k" footprint="0402" pcbX={5} pcbY={0} />
    <GndFanoutTrace name="TR_R901_GND" from=".R901 > .pin1" />
    <GndFanoutTrace name="TR_R902_GND" from=".R902 > .pin2" />
    <trace name="TR_R901_R902" from=".R901 > .pin2" to=".R902 > .pin1" />
  </board>
)
