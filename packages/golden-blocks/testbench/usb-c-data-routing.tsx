import { GndPlanes } from "../blocks/glue"
import { UsbCData } from "../blocks/usb-c-data/usb-c-data"

// These are board-global corridors for this isolated composition.  A product
// derives its own transformed bounds from the placed USB block; the block only
// assigns each local tree to the board-selected phase.
const USB_REGION = { minX: -10, maxX: 10, minY: -14, maxY: 13 } as const

export default () => (
  <board
    width="30mm"
    height="44mm"
    thickness="1.6mm"
    minTraceWidth="0.15mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    <autoroutingphase name="usb-connector-pair" phaseIndex={0} region={USB_REGION} />
    <autoroutingphase name="usb-series-pair" phaseIndex={1} region={USB_REGION} />
    <autoroutingphase name="usb-cc1" phaseIndex={2} region={USB_REGION} />
    <autoroutingphase name="usb-cc2" phaseIndex={3} region={USB_REGION} />
    <autoroutingphase name="usb-local-power" phaseIndex={4} region={USB_REGION} />
    <GndPlanes
      layers={["top", "bottom"]}
      fanoutLayers={["top", "bottom"]}
      stitchingVias={[
        { x: -12, y: -18 }, { x: 0, y: -18 }, { x: 12, y: -18 },
        { x: -12, y: 0 }, { x: 12, y: 0 },
        { x: -12, y: 18 }, { x: 0, y: 18 }, { x: 12, y: 18 },
      ]}
    />
    <UsbCData
      pcbX={0}
      pcbY={-7}
      vbusBoundaryRefs={{ right: "N3", left: "N4" }}
      vbusRailNodeRef="N15"
      vbusClampNodeRef="N16"
      pairRules={{
        pcbTraceGapMm: 0.15,
        maxLengthSkewMm: 3.8,
        maxUncoupledLengthMm: 3,
      }}
      localRoutingPhaseIndex={4}
      dpConnectorRoutingPhaseIndex={0}
      dmConnectorRoutingPhaseIndex={0}
      pairRoutingPhaseIndex={0}
      connectorPairRoutingPhaseIndex={0}
      seriesPairRoutingPhaseIndex={1}
      cc1RoutingPhaseIndex={2}
      cc2RoutingPhaseIndex={3}
      powerRoutingPhaseIndex={4}
    />
  </board>
)
