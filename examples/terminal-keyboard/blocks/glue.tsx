/**
 * golden-blocks glue (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Board furniture that is not a subcircuit: a mounting hole, a ground plane,
 * a mixed-width power trunk, and a bring-up port. These are here for one
 * reason — **the raw elements are
 * unsafe by default**, and a default a board author has to remember is a
 * default that gets forgotten. Use these instead of `<hole>`, `<copperpour>`,
 * and ad-hoc debug pads.
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
 * A hidden, mask-covered copper boundary for authored routing topology.
 *
 * This is deliberately not a test point: it has no paste, no silkscreen, no
 * exposed copper, and an N-prefixed identity that debug-access checks cannot
 * mistake for a probe.  Use it only as an internal trace-width/topology node.
 */
export const MaskedCopperNode = (props: {
  name: string
  layer?: "top" | "bottom"
  diameterMm?: number
  pcbX: number
  pcbY: number
  schX?: number
  schY?: number
}) => {
  const diameter = props.diameterMm ?? 0.25
  if (!/^N[1-9][0-9]*$/.test(props.name)) {
    throw new Error("MaskedCopperNode name must use a non-probe N reference such as N1")
  }
  if (
    !Number.isFinite(props.pcbX) ||
    !Number.isFinite(props.pcbY) ||
    !Number.isFinite(diameter) ||
    diameter <= 0
  ) {
    throw new Error("MaskedCopperNode needs finite coordinates and a positive diameter")
  }
  return (
    <chip
      name={props.name}
      pinLabels={{ pin1: ["NODE"] }}
      // This is an electrically-polymorphic routing node rather than an IC.
      // Marking the sole copper terminal as both rail-capable roles keeps the
      // generic chip pin checker from inventing missing-power/ground warnings;
      // no schematic or net semantics are added by these attributes.
      pinAttributes={{
        NODE: { requiresPower: true, requiresGround: true },
      }}
      manufacturerPartNumber="MASKED_COPPER_NODE"
      noSchematicRepresentation={true}
      doNotPlace={true}
      layer={props.layer ?? "top"}
      pcbX={props.pcbX}
      pcbY={props.pcbY}
      schX={props.schX}
      schY={props.schY}
      footprint={
        <footprint>
          <smtpad
            portHints={["pin1"]}
            shape="circle"
            radius={`${diameter / 2}mm`}
            coveredWithSolderMask={true}
            solderPasteMargin={`${-diameter / 2}mm`}
          />
        </footprint>
      }
    />
  )
}

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
 *
 * A trunk may change faces exactly once. Cross-layer mode is deliberately
 * explicit: the source pad's physical point and layer, the trunk layer, and
 * one off-pad via point are supplied together. The source neck is then a
 * fixed, bounded path owned by the board-side start pad; the wide trunk runs
 * from that pad to the via on `sourceLayer`, through the declared .8/.5mm
 * transition, and onward to the end pad on `trunkLayer`. Keeping the fixed
 * path relative to the board-owned pad avoids applying a transformed source
 * block's local coordinates a second time.
 *
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
  /** Legacy same-face layer. In cross-layer mode it may only repeat trunkLayer. */
  layer?: "top" | "bottom"
  /** Source-pad and start-probe face. Requires every cross-layer point/layer prop. */
  sourceLayer?: "top" | "bottom"
  /** Wide-trunk and end-probe face. Requires every cross-layer point/layer prop. */
  trunkLayer?: "top" | "bottom"
  /** Board-absolute physical centre of `source`; cross-layer mode only. */
  sourcePoint?: PcbPoint
  /** Board-absolute, off-pad layer-transition point; cross-layer mode only. */
  trunkVia?: PcbPoint
  trunkWidthMm?: number
  neckdownWidthMm?: number
  /** Maximum straight/dogleg length from sourcePoint to start. Defaults to 2mm. */
  maxNeckdownLengthMm?: number
  padDiameterMm?: number
  /** Cross-layer via copper and finished-hole diameters. Defaults to .8/.5mm. */
  viaOuterDiameterMm?: number
  viaHoleDiameterMm?: number
  /** Required via-edge to either boundary-pad edge gap. Defaults to .15mm. */
  minViaEdgeToPadEdgeClearanceMm?: number
}) => {
  const trunkWidth = props.trunkWidthMm ?? 0.8
  const neckdownWidth = props.neckdownWidthMm ?? 0.2
  const padDiameter = props.padDiameterMm ?? Math.max(1.2, trunkWidth + 0.4)
  const crossLayerFields = [
    props.sourceLayer,
    props.trunkLayer,
    props.sourcePoint,
    props.trunkVia,
  ]
  const crossLayerOptionPresent =
    crossLayerFields.some((value) => value !== undefined) ||
    props.maxNeckdownLengthMm !== undefined ||
    props.viaOuterDiameterMm !== undefined ||
    props.viaHoleDiameterMm !== undefined ||
    props.minViaEdgeToPadEdgeClearanceMm !== undefined
  const crossLayerMode = crossLayerFields.every((value) => value !== undefined)
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

  if (crossLayerOptionPresent && !crossLayerMode) {
    throw new Error(
      "PowerTrunk cross-layer mode requires sourceLayer, trunkLayer, sourcePoint, and trunkVia together",
    )
  }

  if (crossLayerMode) {
    const sourceLayer = props.sourceLayer!
    const trunkLayer = props.trunkLayer!
    const sourcePoint = props.sourcePoint!
    const trunkVia = props.trunkVia!
    const maxNeckdownLength = props.maxNeckdownLengthMm ?? 2
    const viaOuterDiameter = props.viaOuterDiameterMm ?? 0.8
    const viaHoleDiameter = props.viaHoleDiameterMm ?? 0.5
    const minViaPadClearance =
      props.minViaEdgeToPadEdgeClearanceMm ?? 0.15
    const distance = (a: PcbPoint, b: PcbPoint) =>
      Math.hypot(a.x - b.x, a.y - b.y)

    if (sourceLayer === trunkLayer) {
      throw new Error(
        "PowerTrunk cross-layer sourceLayer and trunkLayer must be different",
      )
    }
    if (props.layer !== undefined && props.layer !== trunkLayer) {
      throw new Error(
        "PowerTrunk layer must equal trunkLayer when both are supplied",
      )
    }
    if (!finitePoint(sourcePoint) || !finitePoint(trunkVia)) {
      throw new Error(
        "PowerTrunk sourcePoint and trunkVia coordinates must be finite",
      )
    }
    if (
      !Number.isFinite(maxNeckdownLength) ||
      !Number.isFinite(viaOuterDiameter) ||
      !Number.isFinite(viaHoleDiameter) ||
      !Number.isFinite(minViaPadClearance) ||
      maxNeckdownLength <= 0 ||
      viaOuterDiameter <= 0 ||
      viaHoleDiameter <= 0 ||
      viaHoleDiameter >= viaOuterDiameter ||
      viaOuterDiameter < trunkWidth ||
      minViaPadClearance <= 0
    ) {
      throw new Error(
        "PowerTrunk cross-layer dimensions require a positive max neck and via clearance, outer >= trunk, and 0 < hole < outer",
      )
    }
    if (distance(sourcePoint, props.start) > maxNeckdownLength + 1e-9) {
      throw new Error(
        "PowerTrunk sourcePoint-to-start neck exceeds maxNeckdownLengthMm",
      )
    }
    const minimumViaPadCenterDistance =
      padDiameter / 2 + viaOuterDiameter / 2 + minViaPadClearance
    if (
      distance(trunkVia, props.start) + 1e-9 <
        minimumViaPadCenterDistance ||
      distance(trunkVia, props.end) + 1e-9 < minimumViaPadCenterDistance
    ) {
      throw new Error(
        "PowerTrunk trunkVia must clear both boundary-pad edges by minViaEdgeToPadEdgeClearanceMm",
      )
    }

    const startSelector = `.${props.startTestpoint} > .pin1`
    const endSelector = `.${props.endTestpoint} > .pin1`
    const pad = (
      name: string,
      point: PcbPoint,
      padLayer: "top" | "bottom",
    ) => (
      <testpoint
        name={name}
        footprintVariant="pad"
        padShape="circle"
        padDiameter={`${padDiameter}mm`}
        doNotPlace={true}
        layer={padLayer}
        pcbX={point.x}
        pcbY={point.y}
      />
    )
    const viaOffset = {
      x: trunkVia.x - props.start.x,
      y: trunkVia.y - props.start.y,
    }

    return (
      <>
        {pad(props.startTestpoint, props.start, sourceLayer)}
        {pad(props.endTestpoint, props.end, trunkLayer)}
        <trace
          name={`TR_${props.name}_IN`}
          from={startSelector}
          to={props.source}
          thickness={`${neckdownWidth}mm`}
          maxLength={`${maxNeckdownLength}mm`}
          pcbPathRelativeTo={startSelector}
          pcbPath={[
            { x: 0, y: 0 },
            {
              x: sourcePoint.x - props.start.x,
              y: sourcePoint.y - props.start.y,
            },
          ]}
        />
        <group
          pcbStyle={{
            viaPadDiameter: `${viaOuterDiameter}mm`,
            viaHoleDiameter: `${viaHoleDiameter}mm`,
          }}
        >
          <trace
            name={`TR_${props.name}_TRUNK`}
            from={startSelector}
            to={endSelector}
            thickness={`${trunkWidth}mm`}
            pcbPathRelativeTo={startSelector}
            pcbPath={[
              { x: 0, y: 0 },
              viaOffset,
              {
                ...viaOffset,
                via: true,
                fromLayer: sourceLayer,
                toLayer: trunkLayer,
              },
              viaOffset,
              {
                x: props.end.x - props.start.x,
                y: props.end.y - props.start.y,
              },
            ]}
          />
        </group>
        <trace
          name={`TR_${props.name}_OUT`}
          from={endSelector}
          to={`net.${props.net}`}
          thickness={`${trunkWidth}mm`}
          authoredNetTreeBoundary
        />
      </>
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
        thickness={`${trunkWidth}mm`}
        authoredNetTreeBoundary
      />
    </>
  )
}

/**
 * Three through-hole pads for RP2040 SWD bring-up.
 *
 * This is board furniture rather than part of the MCU's electrical circuit:
 * the test points are deliberately DNP and carry no supplier part number.
 * Keeping the three pads in one helper makes the reachable-debug guarantee
 * structural; a board cannot expose SWCLK/SWD in its netlist while forgetting
 * the physical place where firmware and recovery tools attach.
 *
 * The 2.54mm pitch accepts ordinary pogo tooling or temporary wire leads.
 * A 1.5mm pad around a 0.8mm drill leaves a 0.35mm annular ring and 1.04mm
 * pad-to-pad clearance. Override the names when a board has more than one
 * debug port, but retain the `TP` prefix so BOM tooling recognises the DNP
 * copper feature instead of demanding a supplier part.
 */
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
  signalTraceWidthMm?: number
  /** Board-owned phase for the two outboard debug-signal routes. */
  routingPhaseIndex?: number
}) => {
  const swclkName = props.swclkName ?? "TP1"
  const swdName = props.swdName ?? "TP2"
  const gndName = props.gndName ?? "TP3"
  const swclkNet = props.swclkNet ?? "SWCLK"
  const swdNet = props.swdNet ?? "SWD"
  const gndNet = props.gndNet ?? "GND"
  const signalTraceWidthMm = props.signalTraceWidthMm ?? 0.25
  const layer = props.layer ?? "top"
  const localX = (x: number) => layer === "bottom" ? -x : x
  const schX = props.schX ?? 0
  const schY = props.schY ?? 0
  const pad = (name: string, pcbOffsetX: number, schOffsetX: number) => (
    <testpoint
      name={name}
      footprintVariant="through_hole"
      padShape="circle"
      padDiameter="1.5mm"
      holeDiameter="0.8mm"
      doNotPlace={true}
      layer={layer}
      pcbX={props.pcbX + localX(pcbOffsetX)}
      pcbY={props.pcbY}
      schX={schX + schOffsetX}
      schY={schY}
    />
  )
  // DebugPort is commonly nested in a translated board block.  A positioned
  // inner group remains eligible for child packing in the pinned core: its
  // own anchor compiles correctly while the three testpoints drift away from
  // it.  Emit explicit child coordinates in a fragment so the parent applies
  // exactly one transform.  Bottom ports mirror the physical 2.54mm pitch
  // around the same anchor; schematic ordering is intentionally unchanged.
  return (
    <>
      {pad(swclkName, -2.54, -2)}
      {pad(swdName, 0, 0)}
      {pad(gndName, 2.54, 2)}
      <trace
        name={`TR_${swclkName}`}
        from={`.${swclkName} > .pin1`}
        to={`net.${swclkNet}`}
        thickness={`${signalTraceWidthMm}mm`}
        routingPhaseIndex={props.routingPhaseIndex}
      />
      <trace
        name={`TR_${swdName}`}
        from={`.${swdName} > .pin1`}
        to={`net.${swdNet}`}
        thickness={`${signalTraceWidthMm}mm`}
        routingPhaseIndex={props.routingPhaseIndex}
      />
      <GndFanoutTrace
        name={`TR_${gndName}`}
        from={`.${gndName} > .pin1`}
        net={gndNet}
      />
    </>
  )
}

export { NPTH_TO_COPPER_MM }
