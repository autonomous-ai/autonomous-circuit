/**
 * golden-block: usb-c-power (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * USB-C 5V raw power input: TYPE-C-31-M-12 receptacle (LCSC C165948), 5.1k
 * CC pulldowns (UFP sink advertisement), USBLC6-2SC6 ESD on the CC lines,
 * and 1uF attach capacitance. Exposes net.VBUS_RAW and net.GND; pair with
 * usb-power-entry before the LDO or any downstream bulk capacitance.
 *
 * Land pattern: exact EasyEDA footprint for C165948, imported once at
 * authoring time (tscircuit-cli import C165948 --jlcpcb, 2026-08-10) —
 * committed inline, zero network at build time.
 *
 * Default refdes (global v1 allocation): J1, R1, R2, U1, C1.
 * See BLOCK.md for the pin contract and provenance.
 */

import { GndFanoutTrace, MaskedCopperNode } from "../glue"

const usbcPinLabels = {
  pin1: ["EH2", "SHELL2"],
  pin2: ["EH1", "SHELL1"],
  pin3: ["EH4", "SHELL4"],
  pin4: ["EH3", "SHELL3"],
  pin5: ["B8", "SBU2"],
  pin6: ["A5", "CC1"],
  pin7: ["B7", "DM2"],
  pin8: ["A6", "DP1"],
  pin9: ["A7", "DM1"],
  pin10: ["B6", "DP2"],
  pin11: ["A8", "SBU1"],
  pin12: ["B5", "CC2"],
  pin13: ["A1B12", "GND1"],
  pin14: ["B1A12", "GND2"],
  pin15: ["B4A9", "VBUS1"],
  pin16: ["A4B9", "VBUS2"],
} as const

export const UsbCConnector = (props: {
  name: string
  layer?: "top" | "bottom"
  /** pins intentionally left unconnected in the enclosing block */
  ncPins?: string[]
  pcbX?: number | string
  pcbY?: number | string
  pcbRotation?: number | string
  schX?: number
  schY?: number
}) => {
  const { ncPins, ...rest } = props
  const pinAttributes: Record<string, object> = {
    VBUS1: { providesPower: true },
    VBUS2: { providesPower: true },
    GND1: { requiresGround: true },
    GND2: { requiresGround: true },
  }
  for (const p of ncPins ?? []) pinAttributes[p] = { doNotConnect: true }
  return (
  <connector
    {...rest}
    pinLabels={usbcPinLabels}
    pinAttributes={pinAttributes}
    supplierPartNumbers={{ jlcpcb: ["C165948"] }}
    manufacturerPartNumber="TYPE-C-31-M-12"
    schPinArrangement={{
      rightSide: {
        pins: [
          "VBUS1", "VBUS2", "CC1", "CC2", "DP1", "DM1", "DP2", "DM2",
          "SBU1", "SBU2", "GND1", "GND2",
        ],
        direction: "top-to-bottom",
      },
      leftSide: {
        pins: ["SHELL1", "SHELL2", "SHELL3", "SHELL4"],
        direction: "top-to-bottom",
      },
    }}
    footprint={
      <footprint>
        <hole pcbX="-2.899918mm" pcbY="0.9055672mm" diameter="0.5999988mm" />
        <hole pcbX="2.899918mm" pcbY="0.9055672mm" diameter="0.5999988mm" />
        {/* The autorouter does not model NPTH drill-to-copper clearance, so
            each 0.6mm alignment drill carries its own component-local guard.
            A 1.02mm square extends 0.21mm beyond the 0.30mm drill radius on
            every cardinal edge, exceeding the 0.20mm fabrication rule while
            remaining 0.108mm below the closest imported pad copper. Keeping
            these guards local to the drills preserves the legal central
            routing pocket; a single footprint-wide rectangle incorrectly
            erased that pocket and made the reversible USB data trees
            non-planar. */}
        <keepout shape="rect" pcbX="-2.899918mm" pcbY="0.9055672mm"
          width="1.02mm" height="1.02mm" layers={["top", "bottom"]} />
        <keepout shape="rect" pcbX="2.899918mm" pcbY="0.9055672mm"
          width="1.02mm" height="1.02mm" layers={["top", "bottom"]} />
        <platedhole portHints={["pin2"]} pcbX="4.325112mm" pcbY="-2.7741308mm" holeWidth="0.7999984mm" holeHeight="1.3999972mm" outerWidth="1.1999976mm" outerHeight="1.7999964mm" shape="pill" />
        <platedhole portHints={["pin1"]} pcbX="4.325112mm" pcbY="1.4056932mm" holeWidth="0.7999984mm" holeHeight="1.5999968mm" outerWidth="1.1999976mm" outerHeight="1.999996mm" shape="pill" />
        <platedhole portHints={["pin4"]} pcbX="-4.325112mm" pcbY="1.4056932mm" holeWidth="0.7999984mm" holeHeight="1.5999968mm" outerWidth="1.1999976mm" outerHeight="1.999996mm" shape="pill" />
        <platedhole portHints={["pin3"]} pcbX="-4.325112mm" pcbY="-2.7741308mm" holeWidth="0.7999984mm" holeHeight="1.3999972mm" outerWidth="1.1999976mm" outerHeight="1.7999964mm" shape="pill" />
        <smtpad portHints={["pin5"]} pcbX="-1.75006mm" pcbY="2.1740432mm" width="0.2999994mm" height="1.2999974mm" shape="rect" />
        <smtpad portHints={["pin6"]} pcbX="-1.249934mm" pcbY="2.1740432mm" width="0.2999994mm" height="1.2999974mm" shape="rect" />
        <smtpad portHints={["pin7"]} pcbX="-0.750062mm" pcbY="2.1740432mm" width="0.2999994mm" height="1.2999974mm" shape="rect" />
        <smtpad portHints={["pin8"]} pcbX="-0.249936mm" pcbY="2.1740432mm" width="0.2999994mm" height="1.2999974mm" shape="rect" />
        <smtpad portHints={["pin9"]} pcbX="0.249936mm" pcbY="2.1740432mm" width="0.2999994mm" height="1.2999974mm" shape="rect" />
        <smtpad portHints={["pin10"]} pcbX="0.750062mm" pcbY="2.1740432mm" width="0.2999994mm" height="1.2999974mm" shape="rect" />
        <smtpad portHints={["pin11"]} pcbX="1.24968mm" pcbY="2.1740432mm" width="0.2999994mm" height="1.2999974mm" shape="rect" />
        <smtpad portHints={["pin12"]} pcbX="1.75006mm" pcbY="2.1740432mm" width="0.2999994mm" height="1.2999974mm" shape="rect" />
        <smtpad portHints={["pin13"]} points={[{ x: "-2.8999688mm", y: "1.524108mm" }, { x: "-2.8999688mm", y: "2.8241308mm" }, { x: "-3.1999682mm", y: "2.8241308mm" }, { x: "-3.1999682mm", y: "2.8239784mm" }, { x: "-3.4999422mm", y: "2.8239784mm" }, { x: "-3.4999422mm", y: "1.5239556mm" }, { x: "-3.1999428mm", y: "1.5239556mm" }, { x: "-3.1999428mm", y: "1.524108mm" }, { x: "-2.8999688mm", y: "1.524108mm" }]} shape="polygon" />
        <smtpad portHints={["pin14"]} points={[{ x: "2.8999942mm", y: "2.8241308mm" }, { x: "2.8999942mm", y: "1.5241588mm" }, { x: "3.1999936mm", y: "1.5241588mm" }, { x: "3.5000184mm", y: "1.5241588mm" }, { x: "3.5000184mm", y: "2.8241308mm" }, { x: "3.200019mm", y: "2.8241308mm" }, { x: "2.8999942mm", y: "2.8241308mm" }]} shape="polygon" />
        <smtpad portHints={["pin15"]} points={[{ x: "2.7001724mm", y: "1.5241588mm" }, { x: "2.7001724mm", y: "2.8241308mm" }, { x: "2.400173mm", y: "2.8241308mm" }, { x: "2.1001482mm", y: "2.8241308mm" }, { x: "2.1001482mm", y: "1.5241588mm" }, { x: "2.4001476mm", y: "1.5241588mm" }, { x: "2.7001724mm", y: "1.5241588mm" }]} shape="polygon" />
        <smtpad portHints={["pin16"]} points={[{ x: "-2.0999704mm", y: "1.5240064mm" }, { x: "-2.0999704mm", y: "2.8239784mm" }, { x: "-2.3999952mm", y: "2.8239784mm" }, { x: "-2.6999438mm", y: "2.823953mm" }, { x: "-2.6999438mm", y: "1.523981mm" }, { x: "-2.399919mm", y: "1.523981mm" }, { x: "-2.0999704mm", y: "1.5240064mm" }]} shape="polygon" />
        <silkscreenpath route={[{ x: -4.4689776, y: -1.6757586 }, { x: -4.4689776, y: 0.1871536 }]} />
        <silkscreenpath route={[{ x: 4.4710096, y: -5.3941408 }, { x: -4.4689776, y: -5.3941408 }, { x: -4.4689776, y: -3.9128382 }]} />
        <silkscreenpath route={[{ x: 4.4710096, y: -1.6761142 }, { x: 4.4710096, y: 0.1875092 }]} />
        <silkscreenpath route={[{ x: 4.4710096, y: -5.3941408 }, { x: 4.4710096, y: -3.9124826 }]} />
        <silkscreentext text="{NAME}" pcbX="0.002794mm" pcbY="3.8286012mm" anchorAlignment="center" fontSize="1mm" />
        <courtyardoutline outline={[{ x: -5.174806, y: 3.0786012 }, { x: 5.180394, y: 3.0786012 }, { x: 5.180394, y: -5.6509988 }, { x: -5.174806, y: -5.6509988 }, { x: -5.174806, y: 3.0786012 }]} />
      </footprint>
    }
  />
  )
}
/* NOTE: rendered as <connector /> (not <chip />) so the refdes convention
 * and connector-facing placement checks apply. */

const esdPinLabels = {
  pin1: ["IO1"],
  pin2: ["GND"],
  pin3: ["IO2"],
  pin4: ["IO2B"],
  pin5: ["VBUS"],
  pin6: ["IO1B"],
} as const

export const Usblc6 = (props: {
  name: string
  layer?: "top" | "bottom"
  pcbX?: number | string
  pcbY?: number | string
  pcbRotation?: number | string
  schX?: number
  schY?: number
}) => (
  <chip
    {...props}
    pinLabels={esdPinLabels}
    internallyConnectedPins={[["IO1", "IO1B"], ["IO2", "IO2B"]]}
    pinAttributes={{
      VBUS: { requiresPower: true },
      GND: { requiresGround: true },
    }}
    supplierPartNumbers={{ jlcpcb: ["C2687116"] }}
    manufacturerPartNumber="USBLC6-2SC6"
    footprint="sot23_6"
  />
)

export type UsbVbusBoundaryRefs = {
  /** Hidden node beside connector pad VBUS1 (the +X pad). */
  right: string
  /** Hidden node beside connector pad VBUS2 (the -X pad). */
  left: string
}

/**
 * Reversible USB-C VBUS pads joined by a measured power tree.
 *
 * Each 0.6mm-wide connector pad leaves through a short 0.2mm neck into a
 * mask-covered 1mm node.  A 0.8/0.5mm via at each node carries the 0.8mm
 * trunk onto the opposite copper face, below the interleaved signal pads,
 * then returns to the connector face.  Exactly one marked boundary attaches
 * the complete local tree to VBUS_RAW.  This avoids both the former long
 * narrow crossover and the two redundant connector-pad-to-net aggregates.
 */
export const UsbRawVbusTree = (props: {
  j: string
  net: string
  boundaryRefs: UsbVbusBoundaryRefs
  railNodeRef: string
  railNode: { x: number; y: number }
  layer?: "top" | "bottom"
  routingPhaseIndex?: number
  trunkWidthMm?: number
  neckdownWidthMm?: number
  viaPadDiameterMm?: number
  viaHoleDiameterMm?: number
  maxNeckdownLengthMm?: number
  /**
   * Optional top-authored block-local via pair for the rail edge.  Use this
   * when the connector face has no legal 0.8mm corridor between the left
   * reversible node and the rail node.  The helper mirrors both X coordinates
   * and reverses the physical layer transition for a bottom-side block, so
   * both endpoints always return to the component face.
   */
  railLayerTransition?: {
    startVia: { x: number; y: number }
    endVia: { x: number; y: number }
  }
}) => {
  const layer = props.layer ?? "top"
  const oppositeLayer = layer === "top" ? "bottom" : "top"
  // Bottom footprints mirror their pad offsets, so mirror the authored
  // block-local tree as well.  The caller continues to provide one stable
  // top-authored coordinate set for either face.
  const localX = (x: number) => layer === "bottom" ? -x : x
  const trunkWidthMm = props.trunkWidthMm ?? 0.8
  const neckdownWidthMm = props.neckdownWidthMm ?? 0.2
  const viaPadDiameterMm = props.viaPadDiameterMm ?? 0.8
  const viaHoleDiameterMm = props.viaHoleDiameterMm ?? 0.5
  const maxNeckdownLengthMm = props.maxNeckdownLengthMm ?? 2
  const refs = [props.boundaryRefs?.right, props.boundaryRefs?.left, props.railNodeRef]
  if (
    refs.some((ref) => !/^N[1-9][0-9]*$/.test(ref ?? "")) ||
    new Set(refs).size !== 3
  ) {
    throw new Error("UsbRawVbusTree needs three distinct hidden N boundary refs")
  }
  const railLayerTransition = props.railLayerTransition
  if (
    railLayerTransition &&
    [
      railLayerTransition.startVia.x,
      railLayerTransition.startVia.y,
      railLayerTransition.endVia.x,
      railLayerTransition.endVia.y,
    ].some((value) => !Number.isFinite(value))
  ) {
    throw new Error("UsbRawVbusTree rail-layer transition points must be finite")
  }
  if (!Number.isFinite(props.railNode?.x) || !Number.isFinite(props.railNode?.y)) {
    throw new Error("UsbRawVbusTree rail-node coordinates must be finite")
  }
  if (
    ![trunkWidthMm, neckdownWidthMm, viaPadDiameterMm, viaHoleDiameterMm,
      maxNeckdownLengthMm].every((value) => Number.isFinite(value) && value > 0) ||
    neckdownWidthMm > trunkWidthMm ||
    viaHoleDiameterMm >= viaPadDiameterMm ||
    viaPadDiameterMm > 1
  ) {
    throw new Error(
      "UsbRawVbusTree dimensions must be positive with neck <= trunk and hole < via pad <= node",
    )
  }

  const rightRef = props.boundaryRefs.right
  const leftRef = props.boundaryRefs.left
  const railRef = props.railNodeRef
  const rightNode = `.${rightRef} > .pin1`
  const leftNode = `.${leftRef} > .pin1`
  const railNode = `.${railRef} > .pin1`
  // Keep the two VBUS nodes outside the interleaved signal-pad escapes.
  // x=+/-3.2 leaves a routable .15mm clearance corridor around each 0.8mm
  // node. The necks dogleg first above the adjacent GND pad, staying about
  // 1.78mm long and safely inside the 2mm contract.
  const right = { x: localX(3.2), y: 3.4 }
  const left = { x: localX(-3.2), y: 3.4 }
  const rail = { x: localX(props.railNode.x), y: props.railNode.y }
  const railStartVia = railLayerTransition && {
    x: localX(railLayerTransition.startVia.x),
    y: railLayerTransition.startVia.y,
  }
  const railEndVia = railLayerTransition && {
    x: localX(railLayerTransition.endVia.x),
    y: railLayerTransition.endVia.y,
  }
  return (
    <>
      <MaskedCopperNode name={rightRef} layer={layer} diameterMm={viaPadDiameterMm}
        pcbX={right.x} pcbY={right.y} />
      <MaskedCopperNode name={leftRef} layer={layer} diameterMm={viaPadDiameterMm}
        pcbX={left.x} pcbY={left.y} />
      <MaskedCopperNode name={railRef} layer={layer} diameterMm={viaPadDiameterMm}
        pcbX={rail.x} pcbY={rail.y} />
      <trace name={`TR_${props.j}_vbus1_neck`}
        from={`.${props.j} > .VBUS1`} to={rightNode}
        thickness={`${neckdownWidthMm}mm`}
        maxLength={`${maxNeckdownLengthMm}mm`}
        routingPhaseIndex={props.routingPhaseIndex}
        pcbPath={[
          { x: localX(2.4), y: 3.1 },
          { x: right.x, y: right.y },
        ]} />
      <group pcbStyle={{
        viaPadDiameter: `${viaPadDiameterMm}mm`,
        viaHoleDiameter: `${viaHoleDiameterMm}mm`,
      }}>
        <trace name={`TR_${props.j}_vbus_trunk`}
          from={rightNode} to={leftNode}
          thickness={`${trunkWidthMm}mm`}
          routingPhaseIndex={props.routingPhaseIndex}
          pcbPathRelativeTo={rightNode}
          pcbPath={[
            { x: 0, y: 0 },
            { x: localX(1), y: 0 },
            { x: localX(1), y: 0, via: true, fromLayer: layer, toLayer: oppositeLayer },
            { x: localX(1), y: 0 },
            { x: localX(1), y: 1.6 },
            { x: left.x - right.x - localX(1), y: left.y - right.y + 1.6 },
            { x: left.x - right.x - localX(1), y: left.y - right.y },
            { x: left.x - right.x - localX(1), y: left.y - right.y,
              via: true, fromLayer: oppositeLayer, toLayer: layer },
            { x: left.x - right.x - localX(1), y: left.y - right.y },
            { x: left.x - right.x, y: left.y - right.y },
          ]} />
      </group>
      <trace name={`TR_${props.j}_vbus2_neck`}
        from={`.${props.j} > .VBUS2`} to={leftNode}
        thickness={`${neckdownWidthMm}mm`}
        maxLength={`${maxNeckdownLengthMm}mm`}
        routingPhaseIndex={props.routingPhaseIndex}
        pcbPath={[
          { x: localX(-2.4), y: 3.1 },
          { x: left.x, y: left.y },
        ]} />
      {railStartVia && railEndVia ? (
        <group pcbStyle={{
          viaPadDiameter: `${viaPadDiameterMm}mm`,
          viaHoleDiameter: `${viaHoleDiameterMm}mm`,
        }}>
          <trace name={`TR_${props.j}_vbus_rail`}
            from={leftNode} to={railNode}
            thickness={`${trunkWidthMm}mm`}
            routingPhaseIndex={props.routingPhaseIndex}
            pcbPathRelativeTo={leftNode}
            pcbPath={[
              { x: 0, y: 0 },
              { x: railStartVia.x - left.x, y: railStartVia.y - left.y },
              { x: railStartVia.x - left.x, y: railStartVia.y - left.y, via: true,
                fromLayer: layer, toLayer: oppositeLayer },
              { x: railStartVia.x - left.x, y: railStartVia.y - left.y },
              { x: railEndVia.x - left.x, y: railEndVia.y - left.y },
              { x: railEndVia.x - left.x, y: railEndVia.y - left.y, via: true,
                fromLayer: oppositeLayer, toLayer: layer },
              { x: railEndVia.x - left.x, y: railEndVia.y - left.y },
              { x: rail.x - left.x, y: rail.y - left.y },
            ]} />
        </group>
      ) : (
        <trace name={`TR_${props.j}_vbus_rail`}
          from={leftNode} to={railNode}
          thickness={`${trunkWidthMm}mm`}
          routingPhaseIndex={props.routingPhaseIndex} />
      )}
      <trace name={`TR_${props.j}_vbus_boundary`}
        from={railNode} to={`net.${props.net}`}
        thickness={`${trunkWidthMm}mm`}
        authoredNetTreeBoundary />
    </>
  )
}

/**
 * The composed connector block. Rails out: net.VBUS_RAW, net.GND.
 * CC lines run through the USBLC6's two ESD channels (ch1 = IO1/IO1B,
 * ch2 = IO2/IO2B) so the externally exposed CC pins are protected.
 */
export const UsbCPower = (props: {
  j?: string
  r1?: string
  r2?: string
  u?: string
  c?: string
  vbusNet?: string
  /** Globally unique hidden nodes for the reversible VBUS power tree. */
  vbusBoundaryRefs: UsbVbusBoundaryRefs
  /** Globally unique hidden endpoint for the raw rail and local clamp/cap leaves. */
  vbusRailNodeRef: string
  /** Board-owned phase for the connector-local VBUS and CC copper. */
  localRoutingPhaseIndex?: number
  /** Ordinary board-signal width for the protected CC lines. */
  signalTraceWidthMm?: number
  layer?: "top" | "bottom"
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const j = props.j ?? "J1"
  const r1 = props.r1 ?? "R1"
  const r2 = props.r2 ?? "R2"
  const u = props.u ?? "U1"
  const c = props.c ?? "C1"
  const vbus = props.vbusNet ?? "VBUS_RAW"
  const layer = props.layer ?? "top"
  const localX = (x: number) => layer === "bottom" ? -x : x
  const localRotation = (degrees: number) =>
    layer === "bottom" ? (360 - degrees) % 360 : degrees
  const signalTraceWidthMm = props.signalTraceWidthMm ?? 0.25
  if (!Number.isFinite(signalTraceWidthMm) || signalTraceWidthMm <= 0) {
    throw new Error("UsbCPower signalTraceWidthMm must be finite and positive")
  }
  return (
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <UsbCConnector name={j} layer={layer} pcbX={localX(0)} pcbY={0}
        pcbRotation={localRotation(0)} schX={0} schY={0}
        ncPins={["DP1", "DM1", "DP2", "DM2", "SBU1", "SBU2"]} />
      <resistor name={r1} resistance="5.1k" footprint="0402"
        pcbX={localX(-3)} pcbY={7} pcbRotation={localRotation(0)} schX={3} schY={-2.5}
        layer={layer} supplierPartNumbers={{ jlcpcb: ["C25905"] }} />
      <resistor name={r2} resistance="5.1k" footprint="0402"
        pcbX={localX(3)} pcbY={7} pcbRotation={localRotation(0)} schX={3} schY={-3.5}
        layer={layer} supplierPartNumbers={{ jlcpcb: ["C25905"] }} />
      <Usblc6 name={u} layer={layer} pcbX={localX(0)} pcbY={14.5}
        pcbRotation={localRotation(0)} schX={6} schY={-1} />
      <capacitor name={c} capacitance="1uF" footprint="0402"
        pcbX={localX(-1.4)} pcbY={12} pcbRotation={localRotation(0)} schX={6} schY={2}
        layer={layer} supplierPartNumbers={{ jlcpcb: ["C52923"] }} />

      {/* Reversible connector VBUS pads form one authored power tree. */}
      <UsbRawVbusTree j={j} net={vbus} boundaryRefs={props.vbusBoundaryRefs}
        railNodeRef={props.vbusRailNodeRef} railNode={{ x: 0.5, y: 12.2 }}
        layer={layer} routingPhaseIndex={props.localRoutingPhaseIndex} />
      <GndFanoutTrace name={`TR_${j}_gnd1`} from={`.${j} > .GND1`} />
      <GndFanoutTrace name={`TR_${j}_gnd2`} from={`.${j} > .GND2`} />
      <GndFanoutTrace name={`TR_${j}_sh1`} from={`.${j} > .SHELL1`} />
      <GndFanoutTrace name={`TR_${j}_sh2`} from={`.${j} > .SHELL2`} />
      <GndFanoutTrace name={`TR_${j}_sh3`} from={`.${j} > .SHELL3`} />
      <GndFanoutTrace name={`TR_${j}_sh4`} from={`.${j} > .SHELL4`} />

      {/* CC flow-through: connector enters one USBLC6 channel pad and exits
          the internally common mate into the 5.1k UFP pulldown. */}
      <trace name={`TR_${j}_cc1_esd`} from={`.${j} > .CC1`} to={`.${u} > .IO1`}
        thickness={`${signalTraceWidthMm}mm`} routingPhaseIndex={props.localRoutingPhaseIndex} />
      <trace name={`TR_${u}_cc1_r`} from={`.${u} > .IO1B`} to={`.${r1} > .pin1`}
        thickness={`${signalTraceWidthMm}mm`} routingPhaseIndex={props.localRoutingPhaseIndex} />
      <GndFanoutTrace name={`TR_${r1}_gnd`} from={`.${r1} > .pin2`} />
      <trace name={`TR_${j}_cc2_esd`} from={`.${j} > .CC2`} to={`.${u} > .IO2`}
        thickness={`${signalTraceWidthMm}mm`} routingPhaseIndex={props.localRoutingPhaseIndex} />
      <trace name={`TR_${u}_cc2_r`} from={`.${u} > .IO2B`} to={`.${r2} > .pin1`}
        thickness={`${signalTraceWidthMm}mm`} routingPhaseIndex={props.localRoutingPhaseIndex} />
      <GndFanoutTrace name={`TR_${r2}_gnd`} from={`.${r2} > .pin2`} />
      <trace name={`TR_${u}_vbus`} from={`.${u} > .VBUS`} to={`.${props.vbusRailNodeRef} > .pin1`}
        thickness="0.2mm" maxLength="3mm" routingPhaseIndex={props.localRoutingPhaseIndex} />
      <GndFanoutTrace name={`TR_${u}_gnd`} from={`.${u} > .GND`} />

      {/* Raw attach cap; downstream bulk belongs after UsbPowerEntry. */}
      <trace name={`TR_${c}_vbus`} from={`.${c} > .pin1`} to={`.${props.vbusRailNodeRef} > .pin1`}
        thickness="0.2mm" maxLength="3mm" routingPhaseIndex={props.localRoutingPhaseIndex} />
      <GndFanoutTrace name={`TR_${c}_gnd`} from={`.${c} > .pin2`} />
    </group>
  )
}

export default UsbCPower
