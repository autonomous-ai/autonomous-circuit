import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { GndPlanes } from "../blocks/glue"

// Autorouting regions are board-global and are not transformed with the core.
// This isolated bench makes the complete board the critical-cluster region;
// consumers with other circuitry must choose their own global rectangle.
const RP_CLOCK_ROUTING_REGION = {
  minX: -23,
  maxX: 23,
  minY: -21,
  maxY: 21,
} as const

// Exact accumulated replay shows the five QSPI data/CS routes are
// portfolio-sensitive when offered to one solver invocation even though every
// edge is independently routable. Give IO3→IO2→IO1→IO0→CS its own ordered
// phase, all within the same endpoint-cluster +6mm board-global corridor. Each
// later phase treats every earlier edge as fixed copper. This is an explicit
// board portfolio contract, not a claim that the physical endpoints require
// 6mm of free space or a reason to nudge their packages.
const RP_QSPI_ROUTING_REGION = {
  minX: -11.605,
  maxX: 4.205,
  minY: 0.42505,
  maxY: 24.1301,
} as const

// Board-global endpoint bbox for the explicit V3_3/DVDD tree, expanded by
// 4mm.  This keeps the dense local rail portfolio near the core while still
// leaving room for the exact router to turn around every endpoint.
const RP_POWER_ROUTING_REGION = {
  minX: -12.3,
  maxX: 7.5,
  minY: -6.4,
  maxY: 22.1,
} as const

// The short package-to-cap branches are grouped by QFN edge. These bounds are
// each endpoint bbox plus a 4mm turn corridor, in board coordinates.
const RP_POWER_WEST_SOUTH_ROUTING_REGION = {
  minX: -12.3,
  maxX: 1,
  minY: -5.8,
  maxY: 9.8,
} as const
const RP_POWER_EAST_ROUTING_REGION = {
  minX: -3.1,
  maxX: 6.3,
  minY: -2,
  maxY: 9.6,
} as const
const RP_POWER_NORTH_FLASH_ROUTING_REGION = {
  minX: -10.5,
  maxX: 3.4,
  minY: 2.9,
  maxY: 22.1,
} as const

// BOOTSEL, RUN, and the deliberately outboard debug pads span most of this
// isolated board, so their board-owned region is the board routing rectangle.
const RP_CONTROL_ROUTING_REGION = RP_CLOCK_ROUTING_REGION

// The core authors one-port GND drops for plane termination. A consumer must
// own the physical pours and stitches; otherwise the aggregate GND net falls
// back into the ordinary capacity phase and this bench is not representative
// of a reusable two-layer composition.
const GND_STITCHING_VIAS = [
  { x: -20, y: -18 },
  { x: 0, y: -18 },
  { x: 20, y: -18 },
  { x: -20, y: 0 },
  { x: 20, y: 0 },
  { x: -20, y: 18 },
  { x: 0, y: 18 },
  { x: 20, y: 18 },
] as const

export default () => (
  <board
    width="46mm"
    height="42mm"
    thickness="1.6mm"
    minTraceWidth="0.15mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    <autoroutingphase
      name="rp-clock"
      phaseIndex={0}
      region={RP_CLOCK_ROUTING_REGION}
    />
    <autoroutingphase name="rp-qspi-io3" phaseIndex={1}
      region={RP_QSPI_ROUTING_REGION} />
    <autoroutingphase name="rp-qspi-io2" phaseIndex={2}
      region={RP_QSPI_ROUTING_REGION} />
    <autoroutingphase name="rp-qspi-io1" phaseIndex={3}
      region={RP_QSPI_ROUTING_REGION} />
    <autoroutingphase name="rp-qspi-io0" phaseIndex={4}
      region={RP_QSPI_ROUTING_REGION} />
    <autoroutingphase name="rp-qspi-cs" phaseIndex={5}
      region={RP_QSPI_ROUTING_REGION} />
    <autoroutingphase
      name="rp-dvdd-local"
      phaseIndex={11}
      region={RP_POWER_ROUTING_REGION}
    />
    <autoroutingphase
      name="rp-dvdd-trunk"
      phaseIndex={12}
      region={RP_POWER_ROUTING_REGION}
    />
    <autoroutingphase
      name="rp-power-west-south"
      phaseIndex={13}
      region={RP_POWER_WEST_SOUTH_ROUTING_REGION}
    />
    <autoroutingphase
      name="rp-power-east"
      phaseIndex={14}
      region={RP_POWER_EAST_ROUTING_REGION}
    />
    <autoroutingphase
      name="rp-power-north-flash"
      phaseIndex={15}
      region={RP_POWER_NORTH_FLASH_ROUTING_REGION}
    />
    <autoroutingphase
      name="rp-power-trunks"
      phaseIndex={16}
      region={RP_POWER_ROUTING_REGION}
    />
    <autoroutingphase
      name="rp-power-necks"
      phaseIndex={17}
      region={RP_POWER_ROUTING_REGION}
    />
    <autoroutingphase
      name="rp-control-debug"
      phaseIndex={18}
      region={RP_CONTROL_ROUTING_REGION}
    />
    <GndPlanes
      layers={["top", "bottom"]}
      stitchingVias={[...GND_STITCHING_VIAS]}
    />
    <Rp2040Core
      pcbX={-3}
      pcbY={3}
      debugPortPcbX={14}
      debugPortPcbY={-2}
      debugSwclkBoundaryRef="N1"
      debugSwdBoundaryRef="N2"
      criticalRoutingPhaseIndices={{
        clock: 0,
        qspiIo3: 1,
        qspiIo2: 2,
        qspiIo1: 3,
        qspiIo0: 4,
        qspiCs: 5,
      }}
      powerRoutingPhaseIndices={{
        dvddLocalBranches: 11,
        dvddTrunk: 12,
        westSouthBranches: 13,
        eastBranches: 14,
        northFlashBranches: 15,
        railTrunks: 16,
        railNecks: 17,
      }}
      controlRoutingPhaseIndex={18}
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
