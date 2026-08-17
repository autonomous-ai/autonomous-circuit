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
 * How far a pour keeps from copper it does not own, from a drill, and from
 * the board edge.
 *
 * **Every one of these has to be set explicitly, and the default is not
 * enough.** Measured 2026-08-16: `<copperpour>` with only `cutoutMargin` set
 * put terminal-keyboard and harness-puck straight into KiCad DRC — *zone
 * clearance 0.1500mm, actual 0.1016mm*, one at **0.0000mm** (touching), and
 * hole clearance 0.1486mm against a 0.2mm rule. `cutoutMargin` governs board
 * cutouts; other-net copper is `clearance`/`padMargin`/`traceMargin` and
 * drills fall under `clearance`, and leaving those at their defaults fills
 * closer than the zone's own declared clearance.
 *
 * 0.3mm clears every rule the profile carries with room to spare: 0.15mm zone
 * clearance, 0.20mm via-hole-to-copper, 0.28mm plated-hole-to-copper, and the
 * solver's 32-gon, which measures `R * 0.995185` at the chord midpoints. A
 * ground plane loses a little copper to this and loses nothing that matters —
 * a fragmented plane is still a plane, and a shorted one is a scrap board.
 */
export const POUR_COPPER_MARGIN_MM = 0.3

/**
 * A ground plane on one layer, with every margin set — see
 * `POUR_COPPER_MARGIN_MM` for why "every" is the operative word, and
 * `POUR_CUTOUT_MARGIN_MM` for the 32-gon.
 *
 * Prefer this over a bare `<copperpour>` on any board that has a hole in it —
 * which is every board with a USB-C receptacle or a mounting point.
 *
 * **Pour ground on any two-layer board with a differential pair or an MCU.**
 * Three separate findings in the first human EE review (2026-08-15) were this
 * one absence wearing different clothes: ground returning through 0.2mm track
 * instead of a plane, a USB pair with 0% reference under it, and copper that
 * reads as tangled because every return path is one more line competing for
 * the same channels.
 */
export const GndPour = (props: {
  layer?: "top" | "bottom" | "inner1" | "inner2"
  /** Net to pour, without the `net.` prefix. Defaults to GND. */
  net?: string
}) => (
  <copperpour
    layer={props.layer ?? "bottom"}
    connectsTo={`net.${props.net ?? "GND"}`}
    clearance={`${POUR_COPPER_MARGIN_MM}mm`}
    padMargin={`${POUR_COPPER_MARGIN_MM}mm`}
    traceMargin={`${POUR_COPPER_MARGIN_MM}mm`}
    boardEdgeMargin={`${POUR_COPPER_MARGIN_MM}mm`}
    cutoutMargin={`${POUR_CUTOUT_MARGIN_MM}mm`}
  />
)

/**
 * The debug interface, landed on copper a probe can reach.
 *
 * An MCU block brings SWCLK and SWD out as nets. If nothing terminates them
 * the board is assembled, powered, and **cannot be programmed or halted** —
 * every block on it is individually correct and the finished device is a
 * paperweight. That was true of all three example boards on 2026-08-11
 * (`review_debug_unreachable`), which is the signature of a defect that has to
 * be closed by construction rather than by a note in a README.
 *
 * Three pads, not two: a probe needs a ground reference, so GND ships with the
 * pair. 2.54mm pitch means a 3-pin header solders straight on when somebody
 * wants one, and 1mm circular pads take a pogo or a clip when nobody does.
 * Pads, not plated holes — no drills, no keepouts, no assembly line cost.
 *
 * **Put it in open board space, not inside the MCU block.** Measured both ways
 * on 2026-08-11: three pads inside `rp2040-core`'s own box send the debug pair
 * through the crystal cluster and the router comes back with a via shorted
 * into the QFN pad field; outboard of the flash, the same design is
 * `fab.ready`. A debug port is board furniture, which is why it lives here
 * beside `MountingHole` rather than inside a block.
 *
 * Default refdes `TP1`-`TP3`, reserved for this in the global v1 allocation.
 */
/**
 * A row of labelled pads on named nets — a header without a header.
 *
 * This is the component two engineers copied the internals of on the same day
 * rather than import, because it was only ever called `DebugPort` and a board
 * that wants "DATA / 5V / GND out to a strip" does not go looking for a debug
 * port. It was always general: `nets`, `labels`, `pitch`, `padDiameter`,
 * `prefix` and a rotation. `DebugPort` is now this with the SWD trio as
 * defaults, so every board that already uses it is unchanged.
 *
 * Bare pads rather than a plated header on purpose: no drills means no
 * keepouts and nothing for the router to squeeze around, and a 0.1" pitch
 * still takes a soldered header when someone wants one.
 *
 *     <PadHeader prefix="J" nets={["LED_DATA", "V5", "GND"]}
 *                labels={["DAT", "5V", "GND"]} pcbX={0} pcbY={20} />
 */
export const PadHeader = (props: {
  /** Refdes for the first pad; the rest count up. */
  prefix?: string
  /** Nets to land, in order. Defaults to the SWD trio. */
  nets?: string[]
  /**
   * Silkscreen beside each pad. Short on purpose: the fab floor is a 1.0mm
   * character height and a 5-character label at that size is wider than the
   * 2.54mm pitch, so full net names would print over each other.
   */
  labels?: string[]
  /** Pad spacing in mm. 2.54 takes a 0.1" header. */
  pitch?: number
  padDiameter?: number
  pcbX?: number
  pcbY?: number
  /** 90 lays the pads up the board instead of across it. */
  pcbRotation?: number
  schX?: number
  schY?: number
  /**
   * Where the numbering starts. Two headers on one board both counted from 1
   * and collided, and the workaround an engineer reached for was
   * `prefix="TP4"` — which yields `TP41`-`TP44` and reads as a mistake in the
   * BOM (dual-sensor-node, 2026-08-17). `startIndex={4}` gives `TP4`-`TP6`.
   */
  startIndex?: number
  /**
   * Per-pad trace width, same order as `nets`. `undefined` (or a shorter
   * array) leaves that pad's trace at the board's own `minTraceWidth`.
   *
   * Added for dual-rail-psu (2026-08-17), a board with no MCU, where this
   * header is where a rail's *only* copper ends and there was no way to ask
   * for more than the board-wide default. Per `docs/architecture/rail-width.md`
   * a `<trace thickness>` reaches the router as a per-net `nominalTraceWidth`
   * — one declaration anywhere on a net sets the whole net — so it widens what
   * the router searches for rather than stamping copper on afterwards.
   * Declaring a width the placement cannot take has scrapped a board before
   * (`fab.ready` true → false, 33 blocking): measure the ceiling with
   * `python -m circuitpy.netwidth <project>` and pass only what it clears.
   */
  thickness?: (string | undefined)[]
}) => {
  const prefix = props.prefix ?? "TP"
  const nets = props.nets ?? ["SWCLK", "SWD", "GND"]
  const labels = props.labels ?? ["CLK", "DIO", "GND"]
  const pitch = props.pitch ?? 2.54
  const diameter = props.padDiameter ?? 1.0
  const startIndex = props.startIndex ?? 1
  const thickness = props.thickness ?? []
  const first = -((nets.length - 1) * pitch) / 2
  return (
    <group
      pcbX={props.pcbX ?? 0}
      pcbY={props.pcbY ?? 0}
      pcbRotation={props.pcbRotation ?? 0}
      schX={props.schX ?? 0}
      schY={props.schY ?? 0}
    >
      {nets.flatMap((net, i) => {
        const name = `${prefix}${startIndex + i}`
        return [
          <testpoint
            key={name}
            name={name}
            footprintVariant="pad"
            padShape="circle"
            padDiameter={`${diameter}mm`}
            pcbX={first + i * pitch}
            pcbY={0}
            schX={i * 2}
            schY={0}
          />,
          <trace
            key={`${name}_t`}
            name={`TR_${name}`}
            from={`.${name} > .pin1`}
            to={`net.${net}`}
            {...(thickness[i] ? { thickness: thickness[i] } : {})}
          />,
          <silkscreentext
            key={`${name}_s`}
            text={labels[i] ?? net}
            pcbX={first + i * pitch}
            pcbY={-1.7}
            fontSize={1}
          />,
        ]
      })}
    </group>
  )
}

/**
 * The SWD trio, which is what most boards want a pad row for. Kept as its own
 * name because every board in the fleet already imports it.
 */
export const DebugPort = (props: Parameters<typeof PadHeader>[0]) => <PadHeader {...props} />

export { NPTH_TO_COPPER_MM }
