/**
 * golden-blocks glue (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Board furniture that is not a subcircuit: a mounting hole, connected ground
 * planes, and a bring-up port. These are here for one reason — **the raw
 * elements are unsafe by default**, and a default a board author has to
 * remember is a default that gets forgotten. Use these instead of ad-hoc
 * holes, pours, fanouts, and debug pads.
 *
 * Both defects below are geometry the autorouter and the pour solver have no
 * model of, so nothing warns you until the fab gate at the end of a build.
 */

/** JLC's non-plated-hole-to-copper floor, jlcpcb.com/capabilities (2026-08-11). */
const NPTH_TO_COPPER_MM = 0.2

/**
 * `@tscircuit/copper-pour-solver` draws every round obstacle as a 32-sided
 * polygon (`circleToPolygon(center, radius, numSegments = 32)`), and a 32-gon
 * whose vertices sit on a circle of radius R passes within `R * cos(pi/32)`
 * = `R * 0.995185` of the centre at each chord midpoint. So a pour told to keep
 * `m` from a hole of radius `r` really keeps
 *
 *     (r + m) * cos(pi/32) - r
 *
 * and at the 0.2mm default that is 0.1976mm around a 0.6mm USB-C alignment
 * drill and 0.193mm around a 3.2mm mounting hole — both under the 0.2mm floor,
 * both reported by the fab gate as a violation on a board nobody drew wrong.
 *
 * Inverting it, a hole of radius `r` needs `m >= (r + 0.2) / cos(pi/32) - r`:
 * 0.2025mm at r = 0.3, 0.2087mm at r = 1.6. The constant below covers every
 * hole up to r = 10mm; past that, compute it. If the solver's segment count
 * ever changes, this is the number that changes with it.
 */
export const POUR_CUTOUT_MARGIN_MM = 0.25

/**
 * The keepout radius a drill of `holeDiameter` needs so the router keeps
 * tracks legal. The clearance check measures trace **copper** against the
 * keepout and wants 0.1mm, so a keepout of `r + 0.25` leaves at least 0.25mm
 * of drill-to-copper even if a track rides the boundary — above the 0.2mm
 * floor with room for the router's own rounding.
 */
export const keepoutRadiusForHole = (holeDiameterMm: number) =>
  Math.round((holeDiameterMm / 2 + 0.25) * 1000) / 1000

export type PcbPoint = { x: number; y: number }

/**
 * A mounting hole that the router will not run a track past.
 *
 * A bare `<hole>` is invisible to the autorouter: it has no hole-to-copper
 * model at all, so it will happily lay a track 0.1mm from a 3.2mm drill, and
 * the drill's own positional tolerance can then cut it — some boards in the
 * batch work and some do not. The keepout is the only thing the router reads.
 */
export const MountingHole = (props: {
  name: string
  /** Drill diameter in mm. 3.2 clears an M3; 2.7 clears an M2.5. */
  diameter?: number
  pcbX: number
  pcbY: number
}) => {
  const diameter = props.diameter ?? 3.2
  return (
    <group pcbX={props.pcbX} pcbY={props.pcbY}>
      <hole name={props.name} diameter={`${diameter}mm`} pcbX={0} pcbY={0} />
      <keepout
        shape="circle"
        radius={`${keepoutRadiusForHole(diameter)}mm`}
        pcbX={0}
        pcbY={0}
        layers={["top", "bottom"]}
      />
    </group>
  )
}

/**
 * A ground plane on one layer, with a pour margin that survives the solver's
 * 32-gon (see `POUR_CUTOUT_MARGIN_MM`). Prefer this over a bare
 * `<copperpour>` on any board that has a hole in it — which is every board
 * with a USB-C receptacle or a mounting point.
 */
export const GndPour = (props: {
  name?: string
  layer?: "top" | "bottom" | "inner1" | "inner2"
  /** Net to pour, without the `net.` prefix. Defaults to GND. */
  net?: string
  outline?: PcbPoint[]
  boardEdgeMarginMm?: number
}) => (
  <copperpour
    name={props.name}
    layer={props.layer ?? "bottom"}
    connectsTo={`net.${props.net ?? "GND"}`}
    outline={props.outline}
    boardEdgeMargin={
      props.boardEdgeMarginMm === undefined
        ? undefined
        : `${props.boardEdgeMarginMm}mm`
    }
    cutoutMargin={`${POUR_CUTOUT_MARGIN_MM}mm`}
  />
)

type PlaneLayer = "top" | "bottom"
type FanoutDirection =
  | "top_left"
  | "top_center"
  | "top_right"
  | "center_left"
  | "center"
  | "center_right"
  | "bottom_left"
  | "bottom_center"
  | "bottom_right"

/** Reserve low phase numbers for crystal, memory, USB, and other critical buses. */
export const GND_FANOUT_PHASE_INDEX = 10

/**
 * One source pad's short drop to a poured plane in the reserved GND phase.
 *
 * Use this only for a one-port-to-net connection. A deliberate local GND tie
 * such as `U4.GND -> C14.pin2` is ordinary routed copper and must remain a
 * normal `<trace>` (or explicitly `routingPhaseIndex={null}`). The pinned
 * fanout router converts exactly one-port traces into plane terminations; a
 * multi-port trace cannot be converted.
 */
export const GndFanoutTrace = (props: {
  name: string
  from: string
  net?: string
  thicknessMm?: number
  maxLengthMm?: number
}) => (
  <trace
    name={props.name}
    from={props.from}
    to={`net.${props.net ?? "GND"}`}
    routingPhaseIndex={GND_FANOUT_PHASE_INDEX}
    thickness={
      props.thicknessMm === undefined ? undefined : `${props.thicknessMm}mm`
    }
    maxLength={
      props.maxLengthMm === undefined ? undefined : `${props.maxLengthMm}mm`
    }
  />
)

/**
 * Plane geometry, its dedicated fanout phase, and explicit stitching vias.
 *
 * By default every poured layer is a fanout target. The exact-pinned core
 * chooses each one-port trace's physical pad layer when that layer has a pour:
 * a same-layer pad becomes an explicit zero-length plane contact, while a pad
 * not on any mapped face keeps the existing dogbone/via behavior. Real
 * same-net stitching vias join the faces, and verifylib binds every contact to
 * its source pad before proving it reaches solved material connected copper.
 * `fanoutLayer` remains as a compatibility escape hatch for a deliberate
 * single-face design; new callers should use `fanoutLayers` or the default.
 *
 * Every source-only GND connection that should use the plane must be authored
 * with `GndFanoutTrace`. Do not add a phased `<net>` primitive: the patched
 * planner suppresses the redundant aggregate rail only for the explicitly
 * mapped plane net and leaves deliberate multi-port local ties in later phases.
 */
export const GndPlanes = (props: {
  layers?: PlaneLayer[]
  pours?: Array<{
    name?: string
    layer: PlaneLayer
    outline?: PcbPoint[]
    boardEdgeMarginMm?: number
  }>
  fanoutLayer?: PlaneLayer
  fanoutLayers?: PlaneLayer[]
  net?: string
  fanoutBoundaryPaddingMm?: number
  busFanoutDirections?: Record<string, FanoutDirection>
  stitchingVias?: PcbPoint[]
  viaHoleDiameterMm?: number
  viaOuterDiameterMm?: number
}) => {
  if (props.layers && props.pours) {
    throw new Error("GndPlanes accepts either layers or pours, not both")
  }
  const pours =
    props.pours ??
    (props.layers ?? ["top", "bottom"]).map((layer) => ({ layer }))
  const layers = [...new Set(pours.map((pour) => pour.layer))]
  if (props.fanoutLayer && props.fanoutLayers) {
    throw new Error("GndPlanes accepts either fanoutLayer or fanoutLayers, not both")
  }
  const fanoutLayers =
    props.fanoutLayers ?? (props.fanoutLayer ? [props.fanoutLayer] : layers)
  const net = props.net ?? "GND"
  const stitchingVias = props.stitchingVias ?? []
  const hole = props.viaHoleDiameterMm ?? 0.3
  const outer = props.viaOuterDiameterMm ?? 0.6
  if (!layers.length || new Set(layers).size !== layers.length) {
    throw new Error("GndPlanes layers must be a non-empty unique list")
  }
  if (
    pours.some(
      (pour) =>
        pour.outline !== undefined &&
        (pour.outline.length < 3 ||
          !pour.outline.every(
            (point) => Number.isFinite(point.x) && Number.isFinite(point.y),
          )),
    )
  ) {
    throw new Error("GndPlanes pour outlines need at least three finite points")
  }
  if (
    fanoutLayers.length === 0 ||
    new Set(fanoutLayers).size !== fanoutLayers.length ||
    fanoutLayers.some((layer) => !layers.includes(layer))
  ) {
    throw new Error(
      "GndPlanes fanoutLayers must be a non-empty unique subset of its poured layers",
    )
  }
  if (layers.length > 1 && stitchingVias.length === 0) {
    throw new Error(
      "GndPlanes on multiple layers requires explicit stitchingVias coordinates",
    )
  }
  if (
    !Number.isFinite(hole) ||
    !Number.isFinite(outer) ||
    hole <= 0 ||
    outer <= hole
  ) {
    throw new Error(
      "GndPlanes needs an outer via diameter greater than its positive hole",
    )
  }
  if (!stitchingVias.every((point) => Number.isFinite(point.x) && Number.isFinite(point.y))) {
    throw new Error("GndPlanes stitching-via coordinates must be finite")
  }
  const fanoutPourNetMap = Object.fromEntries(
    fanoutLayers.map((layer) => [layer, net]),
  )

  return (
    <>
      <autoroutingphase
        phaseIndex={GND_FANOUT_PHASE_INDEX}
        autorouter="fanout"
        fanoutBoundaryPadding={`${props.fanoutBoundaryPaddingMm ?? 1}mm`}
        fanoutPourNetMap={fanoutPourNetMap}
        busFanoutDirections={props.busFanoutDirections}
      />
      {pours.map((pour, index) => (
        <GndPour
          key={`${pour.layer}_${index}`}
          name={pour.name}
          layer={pour.layer}
          net={net}
          outline={pour.outline}
          boardEdgeMarginMm={pour.boardEdgeMarginMm}
        />
      ))}
      {stitchingVias.map((point, index) => (
        <via
          key={`GND_STITCH_${index}`}
          name={`VIA_GND_STITCH_${index + 1}`}
          pcbX={point.x}
          pcbY={point.y}
          fromLayer={layers[0]}
          toLayer={layers[layers.length - 1]}
          holeDiameter={`${hole}mm`}
          outerDiameter={`${outer}mm`}
          connectsTo={`net.${net}`}
        />
      ))}
    </>
  )
}

/**
 * A wide power-source branch with short, separately routed neck-downs at its
 * endpoints.
 *
 * Setting one `<trace thickness="0.8mm">` on a shared power net is not a safe
 * substitute: the pinned router can promote that width across the whole net,
 * including 0.4mm-pitch IC and 0402 escapes where 0.8mm copper cannot fit.
 * This helper inserts two DNP copper test pads as explicit width boundaries,
 * fixes only the unobstructed trunk between them, and leaves the two short
 * endpoint escapes to the autorouter at `neckdownWidthMm`. It deliberately
 * connects one physical source pad to one named rail. Both endpoints must not
 * already belong to that rail: doing that closes a redundant connectivity
 * cycle. Two such cycles in one board are enough to stall the pinned core
 * before the first routing phase, even with routing disabled.
 *
 * The board author still owns the corridor. Place each boundary pad close to
 * its endpoint, keep the straight trunk out of component/trace keepouts, and
 * declare the same widths in `product.json.layout.netClasses`; parsed DRC and
 * the layout-intent gate are what prove the result. The pads intentionally
 * remain exposed so the boundary nodes double as useful rail probe points.
 * The source block must therefore omit its ordinary source-to-net trace while
 * this helper owns the branch. `UsbCData`/`UsbCPower` and `Ldo3v3` expose an
 * `external-trunk` mode for exactly that purpose. Test-point names are explicit
 * because reference designators must be unique in the consuming board.
 */
export const PowerTrunk = (props: {
  name: string
  /** Physical source-pad selector, for example `.J1 > .VBUS1`. */
  source: string
  /** Bare destination rail name, for example `V5` (not `net.V5`). */
  net: string
  start: PcbPoint
  end: PcbPoint
  startTestpoint: string
  endTestpoint: string
  layer?: "top" | "bottom"
  trunkWidthMm?: number
  neckdownWidthMm?: number
  padDiameterMm?: number
}) => {
  const trunkWidth = props.trunkWidthMm ?? 0.8
  const neckdownWidth = props.neckdownWidthMm ?? 0.2
  const padDiameter = props.padDiameterMm ?? Math.max(1.2, trunkWidth + 0.4)
  const finitePoint = (point: PcbPoint) =>
    Number.isFinite(point.x) && Number.isFinite(point.y)
  if (!props.name || !props.source || !props.net) {
    throw new Error("PowerTrunk requires non-empty name/source/net values")
  }
  if (!props.source.startsWith(".") || props.net.startsWith("net.")) {
    throw new Error(
      "PowerTrunk source must be a component-pad selector and net must be a bare rail name",
    )
  }
  for (const ref of [props.startTestpoint, props.endTestpoint]) {
    if (!/^TP[1-9][0-9]*$/.test(ref)) {
      throw new Error(`PowerTrunk test point ${ref} must look like TP1 or TP42`)
    }
  }
  if (props.startTestpoint === props.endTestpoint) {
    throw new Error("PowerTrunk start/end test-point names must be different")
  }
  if (!finitePoint(props.start) || !finitePoint(props.end)) {
    throw new Error("PowerTrunk start/end coordinates must be finite")
  }
  if (props.start.x === props.end.x && props.start.y === props.end.y) {
    throw new Error("PowerTrunk start/end coordinates must be different")
  }
  if (
    !Number.isFinite(trunkWidth) ||
    !Number.isFinite(neckdownWidth) ||
    !Number.isFinite(padDiameter) ||
    trunkWidth <= 0 ||
    neckdownWidth <= 0 ||
    neckdownWidth > trunkWidth ||
    padDiameter < trunkWidth
  ) {
    throw new Error(
      "PowerTrunk widths must be positive, neckdown <= trunk, and pad >= trunk",
    )
  }

  const layer = props.layer ?? "top"
  const startSelector = `.${props.startTestpoint} > .pin1`
  const endSelector = `.${props.endTestpoint} > .pin1`
  const pad = (name: string, point: PcbPoint) => (
    <testpoint
      name={name}
      footprintVariant="pad"
      padShape="circle"
      padDiameter={`${padDiameter}mm`}
      doNotPlace={true}
      layer={layer}
      pcbX={point.x}
      pcbY={point.y}
    />
  )

  // These coordinates are board-absolute. An unnamed <group> without its own
  // pcbX/pcbY is eligible for tscircuit's automatic group placement, which
  // silently moves both boundary pads while leaving the fixed pcbPath at the
  // authored coordinates. That stretches the "fixed" trunk across unrelated
  // parts. A fragment preserves the board-owned corridor exactly.
  return (
    <>
      {pad(props.startTestpoint, props.start)}
      {pad(props.endTestpoint, props.end)}
      <trace
        name={`TR_${props.name}_IN`}
        from={props.source}
        to={startSelector}
        thickness={`${neckdownWidth}mm`}
      />
      <trace
        name={`TR_${props.name}_TRUNK`}
        from={startSelector}
        to={endSelector}
        thickness={`${trunkWidth}mm`}
        pcbPathRelativeTo={startSelector}
        pcbPath={[
          { x: 0, y: 0 },
          {
            x: props.end.x - props.start.x,
            y: props.end.y - props.start.y,
          },
        ]}
      />
      <trace
        name={`TR_${props.name}_OUT`}
        from={endSelector}
        to={`net.${props.net}`}
        thickness={`${neckdownWidth}mm`}
      />
    </>
  )
}

/** Three reachable, DNP through-hole pads for RP2040 SWD bring-up. */
export const DebugPort = (props: {
  pcbX: number
  pcbY: number
  layer?: "top" | "bottom"
  schX?: number
  schY?: number
  swclkName?: string
  swdName?: string
  gndName?: string
  swclkNet?: string
  swdNet?: string
  gndNet?: string
}) => {
  const swclkName = props.swclkName ?? "TP1"
  const swdName = props.swdName ?? "TP2"
  const gndName = props.gndName ?? "TP3"
  const swclkNet = props.swclkNet ?? "SWCLK"
  const swdNet = props.swdNet ?? "SWD"
  const gndNet = props.gndNet ?? "GND"
  const pad = (name: string, pcbX: number, schX: number) => (
    <testpoint
      name={name}
      footprintVariant="through_hole"
      padShape="circle"
      padDiameter="1.5mm"
      holeDiameter="0.8mm"
      doNotPlace={true}
      layer={props.layer ?? "top"}
      pcbX={pcbX}
      pcbY={0}
      schX={schX}
      schY={0}
    />
  )
  return (
    <group
      pcbX={props.pcbX}
      pcbY={props.pcbY}
      schX={props.schX ?? 0}
      schY={props.schY ?? 0}
    >
      {pad(swclkName, -2.54, -2)}
      {pad(swdName, 0, 0)}
      {pad(gndName, 2.54, 2)}
      <trace name={`TR_${swclkName}`} from={`.${swclkName} > .pin1`} to={`net.${swclkNet}`} />
      <trace name={`TR_${swdName}`} from={`.${swdName} > .pin1`} to={`net.${swdNet}`} />
      <trace
        name={`TR_${gndName}`}
        from={`.${gndName} > .pin1`}
        to={`net.${gndNet}`}
        routingPhaseIndex={GND_FANOUT_PHASE_INDEX}
      />
    </group>
  )
}

export { NPTH_TO_COPPER_MM }
