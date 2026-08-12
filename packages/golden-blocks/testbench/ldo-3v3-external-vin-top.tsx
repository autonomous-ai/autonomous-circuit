import { GndPlanes, MaskedCopperNode } from "../blocks/glue"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"

export default () => (
  <board width="26mm" height="20mm" thickness="1.6mm"
    minTraceWidth="0.15mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
    minViaPadDiameter="0.6mm" minViaHoleDiameter="0.3mm">
    <GndPlanes layers={["top", "bottom"]}
      stitchingVias={[{ x: -10, y: -7 }, { x: 10, y: -7 }]} />
    <Ldo3v3 externalInputPowerTrunkPort="VIN" maxPinNeckdownLengthMm={2} />
    <MaskedCopperNode name="N90" layer="bottom" diameterMm={0.8}
      pcbX={4.925} pcbY={-7} />
    <group pcbStyle={{ viaPadDiameter: "0.8mm", viaHoleDiameter: "0.5mm" }}>
      <trace name="TR_BOARD_V5_ATTACH" from=".N90 > .pin1" to=".C2 > .pin1"
        thickness="0.8mm" maxLength="10mm"
        pcbPathRelativeTo=".N90 > .pin1"
        pcbPath={[
          { x: 0, y: 0 },
          { x: 0, y: 2.5 },
          { x: 0, y: 2.5, via: true, fromLayer: "bottom", toLayer: "top" },
          { x: 0, y: 2.5 },
          { x: 0, y: 4.7 },
        ]} />
    </group>
    <trace name="TR_BOARD_V5_BOUNDARY" from=".N90 > .pin1" to="net.V5"
      thickness="0.8mm" authoredNetTreeBoundary />
  </board>
)
