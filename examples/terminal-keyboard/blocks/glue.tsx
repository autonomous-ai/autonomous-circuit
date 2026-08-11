/**
 * golden-blocks glue (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Board furniture that is not a subcircuit: a mounting hole, a ground plane.
 * These are here for one reason — **the raw elements are unsafe by default**,
 * and a default a board author has to remember is a default that gets
 * forgotten. Use these instead of `<hole>` and `<copperpour>`.
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
  layer?: "top" | "bottom" | "inner1" | "inner2"
  /** Net to pour, without the `net.` prefix. Defaults to GND. */
  net?: string
}) => (
  <copperpour
    layer={props.layer ?? "bottom"}
    connectsTo={`net.${props.net ?? "GND"}`}
    cutoutMargin={`${POUR_CUTOUT_MARGIN_MM}mm`}
  />
)

export { NPTH_TO_COPPER_MM }
