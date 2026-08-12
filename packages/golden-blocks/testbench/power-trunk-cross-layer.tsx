import { GndPlanes, PowerTrunk } from "../blocks/glue"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"

export default () => (
  <board
    width="30mm"
    height="20mm"
    thickness="1.6mm"
    minTraceWidth="0.15mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
    minViaPadDiameter="0.8mm"
    minViaHoleDiameter="0.5mm"
  >
    <GndPlanes
      layers={["top", "bottom"]}
      stitchingVias={[
        { x: -12, y: -8 },
        { x: 0, y: -8 },
        { x: 12, y: -8 },
        { x: -12, y: 0 },
        { x: 12, y: 0 },
        { x: -12, y: 8 },
        { x: 0, y: 8 },
        { x: 12, y: 8 },
      ]}
      viaOuterDiameterMm={0.8}
      viaHoleDiameterMm={0.5}
    />
    <Ldo3v3 pcbX={2} pcbY={0} externalPowerTrunkPort="TAB" />
    <PowerTrunk
      name="V3V3_MAIN"
      source=".U2 > .TAB"
      net="V3_3"
      sourcePoint={{ x: -1.00995715, y: 0 }}
      start={{ x: -2.5, y: -1.2 }}
      trunkVia={{ x: -4, y: -1.8 }}
      end={{ x: -10, y: 4 }}
      startTestpoint="TP13"
      endTestpoint="TP14"
      sourceLayer="top"
      trunkLayer="bottom"
      trunkWidthMm={0.8}
      neckdownWidthMm={0.2}
      maxNeckdownLengthMm={2}
      viaOuterDiameterMm={0.8}
      viaHoleDiameterMm={0.5}
      minViaEdgeToPadEdgeClearanceMm={0.15}
    />
  </board>
)
