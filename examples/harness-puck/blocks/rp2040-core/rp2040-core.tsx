/**
 * golden-block: rp2040-core (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * RP2040 minimal system per the Raspberry Pi hardware design guide
 * ("Hardware design with RP2040", minimal design example):
 *   - RP2040 (LCSC C2040), exact land pattern imported 2026-08-10
 *   - W25Q128JVSIQ 16MB QSPI flash (C97521, JLC Basic)
 *   - ABM8-272-T3 12MHz crystal (C20625731) + 15pF loads + 1k XOUT series
 *   - full decoupling: 100nF per IOVDD/USB_VDD/ADC_AVDD/DVDD pin,
 *     independent 1uF capacitors on VREG_IN and VREG_VOUT, 10uF bulk on 3.3V
 *   - RUN 10k pull-up + reset button; BOOTSEL button (1k into QSPI_SS)
 *
 * Exposes: chip refdes U3 — trace GPIOs directly (`.U3 > .GPIO5`), plus
 * nets USB_DP/USB_DM (pair with usb-c-data), SWCLK/SWD (debug), and rails
 * V3_3/GND. Ported from seveibar/rp2040-module (old hooks API) to current
 * JSX, 2026-08-10.
 *
 * Default refdes (global v1 allocation): U3, U4, Y1, R11-R13, SW2, SW3,
 * TP1-TP3, C4-C16.
 */

import { DebugPort, GndFanoutTrace, MaskedCopperNode } from "../glue"
import {
  TactileButton,
  type TactileSwitchVariant,
} from "../sw-tact/sw-tact"

const rp2040PinLabels = {
  pin1: ["IOVDD6"],
  pin2: ["GPIO0"],
  pin3: ["GPIO1"],
  pin4: ["GPIO2"],
  pin5: ["GPIO3"],
  pin6: ["GPIO4"],
  pin7: ["GPIO5"],
  pin8: ["GPIO6"],
  pin9: ["GPIO7"],
  pin10: ["IOVDD5"],
  pin11: ["GPIO8"],
  pin12: ["GPIO9"],
  pin13: ["GPIO10"],
  pin14: ["GPIO11"],
  pin15: ["GPIO12"],
  pin16: ["GPIO13"],
  pin17: ["GPIO14"],
  pin18: ["GPIO15"],
  pin19: ["TESTEN"],
  pin20: ["XIN"],
  pin21: ["XOUT"],
  pin22: ["IOVDD4"],
  pin23: ["DVDD2"],
  pin24: ["SWCLK"],
  pin25: ["SWD"],
  pin26: ["RUN"],
  pin27: ["GPIO16"],
  pin28: ["GPIO17"],
  pin29: ["GPIO18"],
  pin30: ["GPIO19"],
  pin31: ["GPIO20"],
  pin32: ["GPIO21"],
  pin33: ["IOVDD3"],
  pin34: ["GPIO22"],
  pin35: ["GPIO23"],
  pin36: ["GPIO24"],
  pin37: ["GPIO25"],
  pin38: ["GPIO26_ADC0", "GPIO26"],
  pin39: ["GPIO27_ADC1", "GPIO27"],
  pin40: ["GPIO28_ADC2", "GPIO28"],
  pin41: ["GPIO29_ADC3", "GPIO29"],
  pin42: ["IOVDD2"],
  pin43: ["ADC_AVDD"],
  pin44: ["VREG_IN"],
  pin45: ["VREG_VOUT"],
  pin46: ["USB_DM"],
  pin47: ["USB_DP"],
  pin48: ["USB_VDD"],
  pin49: ["IOVDD1"],
  pin50: ["DVDD1"],
  pin51: ["QSPI_SD3"],
  pin52: ["QSPI_SCLK"],
  pin53: ["QSPI_SD0"],
  pin54: ["QSPI_SD2"],
  pin55: ["QSPI_SD1"],
  pin56: ["QSPI_SS"],
  pin57: ["GND", "thermalpad"],
} as const

export const Rp2040Chip = (props: {
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
    pinLabels={rp2040PinLabels}
    pinAttributes={{
      IOVDD1: { requiresPower: true },
      IOVDD2: { requiresPower: true },
      IOVDD3: { requiresPower: true },
      IOVDD4: { requiresPower: true },
      IOVDD5: { requiresPower: true },
      IOVDD6: { requiresPower: true },
      USB_VDD: { requiresPower: true },
      ADC_AVDD: { requiresPower: true },
      VREG_IN: { requiresPower: true },
      VREG_VOUT: { providesPower: true },
      DVDD1: { requiresPower: true },
      DVDD2: { requiresPower: true },
      GND: { requiresGround: true },
      RUN: { mustBeConnected: true },
      TESTEN: { mustBeConnected: true },
    }}
    supplierPartNumbers={{ jlcpcb: ["C2040"] }}
    manufacturerPartNumber="RP2040"
    footprint="qfn56_thermalpad3.1mmx3.1mm_p0.4mm_w7.8999mm_h7.9001mm_pw0.2mm_pl0.85mm"
  />
)

const flashPinLabels = {
  pin1: ["CS"],
  pin2: ["DO", "IO1"],
  pin3: ["WP", "IO2"],
  pin4: ["GND"],
  pin5: ["DI", "IO0"],
  pin6: ["CLK"],
  pin7: ["HOLD", "IO3"],
  pin8: ["VCC"],
} as const

export const W25q128 = (props: {
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
    pinLabels={flashPinLabels}
    pinAttributes={{
      VCC: { requiresPower: true },
      GND: { requiresGround: true },
    }}
    supplierPartNumbers={{ jlcpcb: ["C97521"] }}
    manufacturerPartNumber="W25Q128JVSIQ"
    footprint="soic8_pillpads_w9.3102mm_pw0.63mm_pl2.25mm_pin1location(leftside,bottom)"
  />
)

export type Rp2040PowerRailNodeRefs = {
  westUpper: string
  westLower: string
  south: string
  eastLower: string
  eastUpper: string
  topRight: string
  topMiddle: string
  topLeft: string
  bulk: string
  flash: string
  /** Mask-covered component-face rail node beside the DVDD1 bypass. */
  dvddLeft: string
  /** Mask-covered component-face rail node beside the VREG_OUT bypass. */
  dvddRight: string
  /** Mask-covered component-face rail node beside the DVDD2 bypass. */
  dvddSouth: string
  /** Opposite-side junction for the explicit 0.8/0.5mm DVDD rail. */
  dvddJunction: string
}

export type Rp2040PowerRoutingPhaseIndices = {
  /** West and south QFN supply-pin escapes into their local bypass caps. */
  westSouthBranches: number
  /** East QFN supply-pin escapes into their local bypass caps. */
  eastBranches: number
  /** North QFN supply-pin escapes plus the flash local supply branch. */
  northFlashBranches: number
  /** Short VREG_OUT/DVDD-pin branches into their local bypass capacitors. */
  dvddLocalBranches: number
  /** Wide, via-scoped internal core-rail links and sole DVDD boundary. */
  dvddTrunk: number
  /** Short capacitor/load necks into the mask-covered rail nodes. */
  railNecks: number
  /** Wide node-to-node rail tree and its sole named-net boundary. */
  railTrunks: number
}

export type Rp2040CriticalRoutingPhaseIndices = {
  /** Crystal, flash clock, and their local load/series branches. */
  clock: number
  /** Ordered QSPI edges. Consumers give each edge its own accumulated phase. */
  qspiIo3: number
  qspiIo2: number
  qspiIo1: number
  qspiIo0: number
  qspiCs: number
}

export const Rp2040Core = (props: {
  u?: string
  flash?: string
  xtal?: string
  layer?: "top" | "bottom"
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
  /** Required board-local placement for the outboard SWD pads. */
  debugPortPcbX: number
  debugPortPcbY: number
  debugPortSchX?: number
  debugPortSchY?: number
  /** Globally unique hidden copper boundaries for the two fixed QFN escapes. */
  debugSwclkBoundaryRef: string
  debugSwdBoundaryRef: string
  /** Globally unique, mask-covered waypoints for the authored 0.8mm rail. */
  powerRailNodeRefs: Rp2040PowerRailNodeRefs
  /** Boundary-to-debug-pad width; the QFN toe escape remains fixed at 0.15mm. */
  debugSignalTraceWidthMm?: number
  /** Explicit fine-pitch width for crystal, QSPI, and USB package escapes. */
  criticalSignalWidthMm?: number
  /** Optional board-owned clock and per-edge QSPI phase ordering. */
  criticalRoutingPhaseIndices?: Rp2040CriticalRoutingPhaseIndices
  /** Board-owned phase for the explicit V3_3 and DVDD local trees. */
  localPowerRoutingPhaseIndex?: number
  /** Optional board-owned subdivision of the already-authored power tree. */
  powerRoutingPhaseIndices?: Rp2040PowerRoutingPhaseIndices
  /** Board-owned phase for BOOTSEL, RUN, and outboard SWD copper. */
  controlRoutingPhaseIndex?: number
  /** Disable when UsbDeviceDifferentialPair owns direct MCU-to-series copper. */
  emitUsbNetLeaves?: boolean
  buttonVariant?: TactileSwitchVariant
}) => {
  const u = props.u ?? "U3"
  const f = props.flash ?? "U4"
  const y = props.xtal ?? "Y1"
  const layer = props.layer ?? "top"
  const oppositeLayer = layer === "top" ? "bottom" : "top"
  // A bottom-side footprint mirrors its pad offsets. Mirror every authored
  // block-local center too (and complement rotations) so the whole placement,
  // not only each isolated package, remains the same electrical geometry.
  const localX = (x: number) => layer === "bottom" ? -x : x
  const localRotation = (degrees: number) =>
    layer === "bottom" ? (360 - degrees) % 360 : degrees
  const buttonVariant = props.buttonVariant ?? "standard"
  const debugSignalTraceWidthMm = props.debugSignalTraceWidthMm ?? 0.25
  const criticalSignalWidthMm = props.criticalSignalWidthMm ?? 0.15
  const criticalSignalWidth = `${criticalSignalWidthMm}mm`
  const clockRoutingPhaseIndex = props.criticalRoutingPhaseIndices?.clock ?? 0
  const qspiIo3RoutingPhaseIndex = props.criticalRoutingPhaseIndices?.qspiIo3 ?? 1
  const qspiIo2RoutingPhaseIndex = props.criticalRoutingPhaseIndices?.qspiIo2 ?? 1
  const qspiIo1RoutingPhaseIndex = props.criticalRoutingPhaseIndices?.qspiIo1 ?? 1
  const qspiIo0RoutingPhaseIndex = props.criticalRoutingPhaseIndices?.qspiIo0 ?? 1
  const qspiCsRoutingPhaseIndex = props.criticalRoutingPhaseIndices?.qspiCs ?? 1
  const localPowerRoutingPhaseIndex = props.localPowerRoutingPhaseIndex
  const westSouthPowerRoutingPhaseIndex =
    props.powerRoutingPhaseIndices?.westSouthBranches ?? localPowerRoutingPhaseIndex
  const eastPowerRoutingPhaseIndex =
    props.powerRoutingPhaseIndices?.eastBranches ?? localPowerRoutingPhaseIndex
  const northFlashPowerRoutingPhaseIndex =
    props.powerRoutingPhaseIndices?.northFlashBranches ?? localPowerRoutingPhaseIndex
  const powerNeckRoutingPhaseIndex =
    props.powerRoutingPhaseIndices?.railNecks ?? localPowerRoutingPhaseIndex
  const powerTrunkRoutingPhaseIndex =
    props.powerRoutingPhaseIndices?.railTrunks ?? localPowerRoutingPhaseIndex
  const dvddLocalRoutingPhaseIndex =
    props.powerRoutingPhaseIndices?.dvddLocalBranches ?? localPowerRoutingPhaseIndex
  const dvddTrunkRoutingPhaseIndex =
    props.powerRoutingPhaseIndices?.dvddTrunk ?? localPowerRoutingPhaseIndex
  const controlRoutingPhaseIndex = props.controlRoutingPhaseIndex
  const localPowerWidth = "0.2mm"
  const localRailWidth = "0.8mm"
  if (!Number.isFinite(props.debugPortPcbX) || !Number.isFinite(props.debugPortPcbY)) {
    throw new Error(
      "Rp2040Core requires finite debugPortPcbX/debugPortPcbY coordinates; " +
      "the board must place its outboard SWD pads explicitly",
    )
  }
  if (!Number.isFinite(criticalSignalWidthMm) || criticalSignalWidthMm <= 0) {
    throw new Error("Rp2040Core criticalSignalWidthMm must be finite and positive")
  }
  if (props.criticalRoutingPhaseIndices) {
    const criticalPhases = Object.values(props.criticalRoutingPhaseIndices)
    if (
      criticalPhases.length !== 6 ||
      criticalPhases.some((phase) => !Number.isInteger(phase) || phase < 0) ||
      new Set(criticalPhases).size !== criticalPhases.length
    ) {
      throw new Error(
        "Rp2040Core criticalRoutingPhaseIndices must contain six distinct non-negative integers",
      )
    }
  }
  const powerRailNodeRefs = Object.values(props.powerRailNodeRefs ?? {})
  const debugBoundaryRefs = [props.debugSwclkBoundaryRef, props.debugSwdBoundaryRef]
  const allInternalNodeRefs = [...debugBoundaryRefs, ...powerRailNodeRefs]
  if (
    powerRailNodeRefs.length !== 14 ||
    allInternalNodeRefs.some((ref) => !/^N[1-9][0-9]*$/.test(ref)) ||
    new Set(allInternalNodeRefs).size !== allInternalNodeRefs.length
  ) {
    throw new Error(
      "Rp2040Core debug and power boundary refs must be distinct non-probe N references",
    )
  }
  const debugEscape = (
    ref: string,
    signal: "SWCLK" | "SWD",
    pcbX: number,
    schY: number,
  ) => (
    <>
      {/* Copper-only boundary: leave the 0.4mm-pitch QFN perpendicular at
          0.15mm, then widen before the general route turns toward the probe. */}
      <MaskedCopperNode
        name={ref}
        diameterMm={0.25}
        layer={layer}
        pcbX={localX(pcbX)}
        pcbY={-4.25}
        schX={-12}
        schY={schY}
      />
      <trace
        name={`TR_${u}_${signal.toLowerCase()}_escape`}
        from={`.${u} > .${signal}`}
        to={`.${ref} > .pin1`}
        thickness="0.15mm"
        routingPhaseIndex={controlRoutingPhaseIndex}
        pcbPath={[
          { x: localX(pcbX), y: -3.6 },
          { x: localX(pcbX), y: -4.25 },
        ]}
      />
      <trace
        name={`TR_${ref}`}
        from={`.${ref} > .pin1`}
        to={`net.${signal}`}
        thickness={`${debugSignalTraceWidthMm}mm`}
        routingPhaseIndex={controlRoutingPhaseIndex}
      />
    </>
  )
  return (
    <group name={`__parts_block__rp2040-core__${u}`} pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <Rp2040Chip name={u} layer={layer} pcbX={0} pcbY={0} schX={0} schY={0} />
      {/* Bring-up is part of the reusable block contract, not board-author
          advice. Placement is deliberately required from the board: a
          coordinate that is outboard before a parent-group rotation can land
          inside a connector afterwards. The block owns the nets and geometry;
          the composition owns the collision-free board coordinate. */}
      <DebugPort
        layer={layer}
        pcbX={props.debugPortPcbX}
        pcbY={props.debugPortPcbY}
        schX={props.debugPortSchX ?? -20}
        schY={props.debugPortSchY ?? -4}
        signalTraceWidthMm={debugSignalTraceWidthMm}
        routingPhaseIndex={controlRoutingPhaseIndex}
      />
      {/* RP2040 pins 51-56 all leave the QFN's north edge. Keep the flash on
          that edge as a bus cluster instead of making every QSPI trace cross
          the package from a part on its east side. Crystal and flash clock
          route in phase 0; the remaining QSPI bus follows in phase 1, then
          later rail/GPIO routing treats all of their copper as fixed. */}
      <W25q128 name={f} layer={layer} pcbX={localX(-0.7)} pcbY={11.6} schX={14} schY={-6} />
      {/* XIN/XOUT leave the south edge. Rotating Y1 puts pin1 toward XIN and
          pin3 toward R11/C16; the load capacitors then branch at the crystal
          pads rather than doubling back to the QFN. This is a route topology,
          not just a distance nudge: both oscillator nets compile under 10mm
          with no layer changes in the routed regression bench. */}
      <crystal name={y} frequency="12MHz" loadCapacitance="10pF" pinVariant="four_pin"
        footprint="crystal" layer={layer} pcbX={localX(-0.2)} pcbY={-7.6} pcbRotation={localRotation(270)} schX={-14} schY={6}
        supplierPartNumbers={{ jlcpcb: ["C20625731"] }} />

      {/* --- Rails -------------------------------------------------------
          Power is an authored tree, not a many-point named-net portfolio.
          Every QFN supply toe first reaches its own nearby bypass capacitor;
          the capacitor rail sides then form one acyclic local bus with one
          named-net escape.  Besides matching the RP2040 layout guidance,
          this prevents an autorouter from inventing a long, conflicting
          Steiner tree among all supply pins. */}
      <GndFanoutTrace name={`TR_${u}_gnd`} from={`.${u} > .GND`} />
      <GndFanoutTrace name={`TR_${u}_testen`} from={`.${u} > .TESTEN`} />

      {/* --- USB + debug nets out ---------------------------------------- */}
      {props.emitUsbNetLeaves !== false && (
        <>
          <trace name={`TR_${u}_usbdp`} from={`.${u} > .USB_DP`} to="net.USB_DP" thickness={criticalSignalWidth} />
          <trace name={`TR_${u}_usbdm`} from={`.${u} > .USB_DM`} to="net.USB_DM" thickness={criticalSignalWidth} />
        </>
      )}
      {debugEscape(props.debugSwclkBoundaryRef, "SWCLK", 1, -4)}
      {debugEscape(props.debugSwdBoundaryRef, "SWD", 1.4, -6)}

      {/* --- Crystal: XIN direct, XOUT through 1k series ------------------ */}
      <trace name={`TR_${y}_xin`} from={`.${y} > .pin1`} to={`.${u} > .XIN`} thickness={criticalSignalWidth} routingPhaseIndex={clockRoutingPhaseIndex} />
      <resistor name="R11" resistance="1k" footprint="0402" layer={layer} pcbX={localX(3)} pcbY={-4.9} schX={-10} schY={7}
        supplierPartNumbers={{ jlcpcb: ["C11702"] }} />
      <trace name={`TR_${u}_xout_r`} from={`.${u} > .XOUT`} to=".R11 > .pin1" thickness={criticalSignalWidth} routingPhaseIndex={clockRoutingPhaseIndex} />
      <trace name={`TR_R11_${y}`} from=".R11 > .pin2" to={`.${y} > .pin3`} thickness={criticalSignalWidth} routingPhaseIndex={clockRoutingPhaseIndex} />
      {/* four_pin crystal: pin2/pin4 are the ground pads */}
      <GndFanoutTrace name={`TR_${y}_gnd1`} from={`.${y} > .pin2`} />
      <GndFanoutTrace name={`TR_${y}_gnd2`} from={`.${y} > .pin4`} />
      <capacitor name="C15" capacitance="15pF" footprint="0402" layer={layer} pcbX={localX(-3.1)} pcbY={-6.5} pcbRotation={localRotation(180)} schX={-16} schY={9}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1548"] }} />
      <capacitor name="C16" capacitance="15pF" footprint="0402" layer={layer} pcbX={localX(2.7)} pcbY={-8.7} schX={-12} schY={9}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1548"] }} />
      <trace name={`TR_C15_xin`} from=".C15 > .pin1" to={`.${y} > .pin1`} thickness={criticalSignalWidth} routingPhaseIndex={clockRoutingPhaseIndex} />
      <GndFanoutTrace name="TR_C15_gnd" from=".C15 > .pin2" />
      <trace name={`TR_C16_xt`} from=".C16 > .pin1" to={`.${y} > .pin3`} thickness={criticalSignalWidth} routingPhaseIndex={clockRoutingPhaseIndex} />
      <GndFanoutTrace name="TR_C16_gnd" from=".C16 > .pin2" />

      {/* --- QSPI flash --------------------------------------------------- */}
      <trace name={`TR_${f}_cs`} from={`.${f} > .CS`} to={`.${u} > .QSPI_SS`} thickness={criticalSignalWidth} routingPhaseIndex={qspiCsRoutingPhaseIndex} />
      <trace name={`TR_${f}_clk`} from={`.${f} > .CLK`} to={`.${u} > .QSPI_SCLK`} thickness={criticalSignalWidth} routingPhaseIndex={clockRoutingPhaseIndex} />
      <trace name={`TR_${f}_io0`} from={`.${f} > .IO0`} to={`.${u} > .QSPI_SD0`} thickness={criticalSignalWidth} routingPhaseIndex={qspiIo0RoutingPhaseIndex} />
      <trace name={`TR_${f}_io1`} from={`.${f} > .IO1`} to={`.${u} > .QSPI_SD1`} thickness={criticalSignalWidth} routingPhaseIndex={qspiIo1RoutingPhaseIndex} />
      <trace name={`TR_${f}_io2`} from={`.${f} > .IO2`} to={`.${u} > .QSPI_SD2`} thickness={criticalSignalWidth} routingPhaseIndex={qspiIo2RoutingPhaseIndex} />
      <trace name={`TR_${f}_io3`} from={`.${f} > .IO3`} to={`.${u} > .QSPI_SD3`} thickness={criticalSignalWidth} routingPhaseIndex={qspiIo3RoutingPhaseIndex} />
      <GndFanoutTrace name={`TR_${f}_gnd`} from={`.${f} > .GND`} />
      <capacitor name="C14" capacitance="100nF" footprint="0402" layer={layer} pcbX={localX(-4.6)} pcbY={15.1} pcbRotation={localRotation(180)} schX={17} schY={-3}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <GndFanoutTrace name="TR_C14_g" from=".C14 > .pin2" />

      {/* --- BOOTSEL: QSPI_SS -> 1k -> button -> GND ---------------------- */}
      <resistor name="R13" resistance="1k" footprint="0402" layer={layer} pcbX={localX(-4.7)} pcbY={7.4} pcbRotation={localRotation(180)} schX={10} schY={-10}
        supplierPartNumbers={{ jlcpcb: ["C11702"] }} />
      {buttonVariant === "compact" ? (
        <TactileButton name="SW2"
          variant="compact"
          supplierPartNumbers={{ jlcpcb: ["C2828561"] }}
          layer={layer}
          pcbX={localX(-11)} pcbY={7} schX={14} schY={-10} />
      ) : (
        <TactileButton name="SW2"
          variant="standard"
          supplierPartNumbers={{ jlcpcb: ["C318884"] }}
          layer={layer}
          pcbX={localX(-11)} pcbY={7} schX={14} schY={-10} />
      )}
      <trace name={`TR_R13_ss`} from=".R13 > .pin1" to={`.${u} > .QSPI_SS`}
        routingPhaseIndex={controlRoutingPhaseIndex} />
      <trace name={`TR_R13_sw`} from=".R13 > .pin2" to=".SW2 > .pin1"
        routingPhaseIndex={controlRoutingPhaseIndex} />
      {buttonVariant === "standard" ? (
        <>
          <trace name="TR_SW2_p2" from=".SW2 > .pin2" to=".SW2 > .pin1"
            routingPhaseIndex={controlRoutingPhaseIndex} />
          <GndFanoutTrace name="TR_SW2_p3" from=".SW2 > .pin3" />
          <GndFanoutTrace name="TR_SW2_p4" from=".SW2 > .pin4" />
        </>
      ) : (
        <GndFanoutTrace name="TR_SW2_p2" from=".SW2 > .pin2" />
      )}

      {/* --- RUN: 10k pull-up + reset button ------------------------------ */}
      <resistor name="R12" resistance="10k" footprint="0402" layer={layer} pcbX={localX(6)} pcbY={-4} pcbRotation={localRotation(180)} schX={-10} schY={-8}
        supplierPartNumbers={{ jlcpcb: ["C25744"] }} />
      {buttonVariant === "compact" ? (
        <TactileButton name="SW3"
          variant="compact"
          supplierPartNumbers={{ jlcpcb: ["C2828561"] }}
          layer={layer}
          pcbX={localX(9)} pcbY={-8} schX={-14} schY={-10} />
      ) : (
        <TactileButton name="SW3"
          variant="standard"
          supplierPartNumbers={{ jlcpcb: ["C318884"] }}
          layer={layer}
          pcbX={localX(9)} pcbY={-8} schX={-14} schY={-10} />
      )}
      <trace name={`TR_R12_run`} from=".R12 > .pin2" to={`.${u} > .RUN`}
        routingPhaseIndex={controlRoutingPhaseIndex} />
      <trace name={`TR_SW3_p1`} from=".SW3 > .pin1" to={`.${u} > .RUN`}
        routingPhaseIndex={controlRoutingPhaseIndex} />
      {buttonVariant === "standard" ? (
        <>
          <trace name="TR_SW3_p2" from=".SW3 > .pin2" to={`.${u} > .RUN`}
            routingPhaseIndex={controlRoutingPhaseIndex} />
          <GndFanoutTrace name="TR_SW3_p3" from=".SW3 > .pin3" />
          <GndFanoutTrace name="TR_SW3_p4" from=".SW3 > .pin4" />
        </>
      ) : (
        <GndFanoutTrace name="TR_SW3_p2" from=".SW3 > .pin2" />
      )}

      {/* --- Decoupling (design guide: 100nF per supply pin) -------------- */}
      <capacitor name="C4" capacitance="100nF" footprint="0402" layer={layer} pcbX={localX(-5.3)} pcbY={2.8} pcbRotation={localRotation(180)} schX={-6} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C5" capacitance="100nF" footprint="0402" layer={layer} pcbX={localX(-5.3)} pcbY={-1} pcbRotation={localRotation(180)} schX={-4} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C6" capacitance="100nF" footprint="0402" layer={layer} pcbX={localX(-1.6)} pcbY={-4.8} pcbRotation={localRotation(180)} schX={-2} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C7" capacitance="100nF" footprint="0402" layer={layer} pcbX={localX(5.3)} pcbY={-1} schX={0} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C8" capacitance="100nF" footprint="0402" layer={layer} pcbX={localX(5.3)} pcbY={2.6} schX={2} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C9" capacitance="100nF" footprint="0402" layer={layer} pcbX={localX(4.05)} pcbY={5.2} pcbRotation={localRotation(90)} schX={4} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C10" capacitance="100nF" footprint="0402" layer={layer} pcbX={localX(-0.15)} pcbY={5.2} pcbRotation={localRotation(90)} schX={6} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C11" capacitance="100nF" footprint="0402" layer={layer} pcbX={localX(0.9)} pcbY={5.2} pcbRotation={localRotation(90)} schX={8} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <GndFanoutTrace name="TR_C4_g" from=".C4 > .pin2" />
      <GndFanoutTrace name="TR_C5_g" from=".C5 > .pin2" />
      <GndFanoutTrace name="TR_C6_g" from=".C6 > .pin2" />
      <GndFanoutTrace name="TR_C7_g" from=".C7 > .pin2" />
      <GndFanoutTrace name="TR_C8_g" from=".C8 > .pin2" />
      <GndFanoutTrace name="TR_C9_g" from=".C9 > .pin2" />
      <GndFanoutTrace name="TR_C10_g" from=".C10 > .pin2" />
      <GndFanoutTrace name="TR_C11_g" from=".C11 > .pin2" />
      {/* DVDD (1.1V core, fed by the internal regulator) */}
      <capacitor name="C12" capacitance="100nF" footprint="0402" layer={layer} pcbX={localX(1.11)} pcbY={-4.9} schX={10} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C13" capacitance="100nF" footprint="0402" layer={layer} pcbX={localX(-1.2)} pcbY={5.2} pcbRotation={localRotation(90)} schX={12} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C25" capacitance="1uF" footprint="0402" layer={layer} pcbX={localX(1.95)} pcbY={5.2} pcbRotation={localRotation(90)} schX={14} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C52923"] }} />
      <capacitor name="C26" capacitance="1uF" footprint="0402" layer={layer} pcbX={localX(3)} pcbY={5.2} pcbRotation={localRotation(90)} schX={16} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C52923"] }} />
      <GndFanoutTrace name="TR_C12_g" from=".C12 > .pin2" />
      <GndFanoutTrace name="TR_C13_g" from=".C13 > .pin2" />
      <GndFanoutTrace name="TR_C25_g" from=".C25 > .pin2" />
      <GndFanoutTrace name="TR_C26_g" from=".C26 > .pin2" />
      {/* 3.3V bulk */}
      <capacitor name="C17" capacitance="10uF" footprint="0805" layer={layer} pcbX={localX(6.5)} pcbY={5.8} schX={14} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C15850"] }} />
      <GndFanoutTrace name="TR_C17_g" from=".C17 > .pin2" />

      {/* Mask-covered rail waypoints keep the 0.8mm trunk away from the
          narrow 0402 pads. Each capacitor reaches its waypoint through a
          short 0.2mm neck; only waypoint-to-waypoint edges carry the rail. */}
      <MaskedCopperNode name={props.powerRailNodeRefs.westUpper} diameterMm={0.4} layer={layer} pcbX={localX(-5)} pcbY={3.8} />
      <MaskedCopperNode name={props.powerRailNodeRefs.westLower} diameterMm={0.4} layer={layer} pcbX={localX(-4.8)} pcbY={-2.3} />
      <MaskedCopperNode name={props.powerRailNodeRefs.south} diameterMm={0.4} layer={layer} pcbX={localX(-3.2)} pcbY={-5.4} />
      <MaskedCopperNode name={props.powerRailNodeRefs.eastLower} diameterMm={0.4} layer={layer} pcbX={localX(4.8)} pcbY={-2.2} />
      <MaskedCopperNode name={props.powerRailNodeRefs.eastUpper} diameterMm={0.4} layer={layer} pcbX={localX(5)} pcbY={3.7} />
      <MaskedCopperNode name={props.powerRailNodeRefs.topRight} diameterMm={0.4} layer={layer} pcbX={localX(4.5)} pcbY={4.7} />
      <MaskedCopperNode name={props.powerRailNodeRefs.topMiddle} diameterMm={0.4} layer={layer} pcbX={localX(1.2)} pcbY={4.2} />
      <MaskedCopperNode name={props.powerRailNodeRefs.topLeft} diameterMm={0.4} layer={layer} pcbX={localX(0.35)} pcbY={4.1} />
      <MaskedCopperNode name={props.powerRailNodeRefs.bulk} diameterMm={0.4} layer={layer} pcbX={localX(5)} pcbY={5.8} />
      <MaskedCopperNode name={props.powerRailNodeRefs.flash} diameterMm={0.4} layer={layer} pcbX={localX(-3.5)} pcbY={15.1} />
      <MaskedCopperNode name={props.powerRailNodeRefs.dvddLeft}
        diameterMm={0.8} layer={layer} pcbX={localX(-3.25)} pcbY={4.7} />
      <MaskedCopperNode name={props.powerRailNodeRefs.dvddRight}
        diameterMm={0.8} layer={layer} pcbX={localX(3.25)} pcbY={3.85} />
      <MaskedCopperNode name={props.powerRailNodeRefs.dvddSouth}
        diameterMm={0.8} layer={layer} pcbX={localX(2.85)} pcbY={-6.45} />
      <MaskedCopperNode name={props.powerRailNodeRefs.dvddJunction}
        diameterMm={0.8} layer={oppositeLayer} pcbX={localX(-3.25)} pcbY={0} />

      {/* Short package-to-bypass leaves.  These are intentionally separate
          two-port edges so routing and verification can measure each loop. */}
      <trace name={`TR_${u}_iovdd6_C4`} from={`.${u} > .IOVDD6`} to=".C4 > .pin1" thickness={localPowerWidth} maxLength="2mm" routingPhaseIndex={westSouthPowerRoutingPhaseIndex} />
      <trace name={`TR_${u}_iovdd5_C5`} from={`.${u} > .IOVDD5`} to=".C5 > .pin1" thickness={localPowerWidth} maxLength="2mm" routingPhaseIndex={westSouthPowerRoutingPhaseIndex} />
      <trace name={`TR_${u}_iovdd4_C6`} from={`.${u} > .IOVDD4`} to=".C6 > .pin1" thickness={localPowerWidth} maxLength="2mm" routingPhaseIndex={westSouthPowerRoutingPhaseIndex} />
      <trace name={`TR_${u}_iovdd3_C7`} from={`.${u} > .IOVDD3`} to=".C7 > .pin1" thickness={localPowerWidth} maxLength="2mm" routingPhaseIndex={eastPowerRoutingPhaseIndex} />
      <trace name={`TR_${u}_iovdd2_C8`} from={`.${u} > .IOVDD2`} to=".C8 > .pin1" thickness={localPowerWidth} maxLength="2mm" routingPhaseIndex={eastPowerRoutingPhaseIndex} />
      <trace name={`TR_${u}_adcavdd_C9`} from={`.${u} > .ADC_AVDD`} to=".C9 > .pin1" thickness={localPowerWidth} maxLength="2mm" routingPhaseIndex={northFlashPowerRoutingPhaseIndex} />
      <trace name={`TR_${u}_iovdd1_C10`} from={`.${u} > .IOVDD1`} to=".C10 > .pin1" thickness={localPowerWidth} maxLength="2mm" routingPhaseIndex={northFlashPowerRoutingPhaseIndex} />
      <trace name={`TR_${u}_usbvdd_C11`} from={`.${u} > .USB_VDD`} to=".C11 > .pin1" thickness={localPowerWidth} maxLength="2mm" routingPhaseIndex={northFlashPowerRoutingPhaseIndex} />
      <trace name={`TR_${u}_vregin_C26`} from={`.${u} > .VREG_IN`} to=".C26 > .pin1" thickness={localPowerWidth} maxLength="2mm" routingPhaseIndex={northFlashPowerRoutingPhaseIndex} />
      <trace name={`TR_${f}_vcc_rail`} from={`.${f} > .VCC`} to={`.${props.powerRailNodeRefs.flash} > .pin1`} thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={northFlashPowerRoutingPhaseIndex} />

      {/* Each rail load has an explicit bounded neck into the wide tree. */}
      <trace name="TR_V3_C4_NECK" from=".C4 > .pin1" to={`.${props.powerRailNodeRefs.westUpper} > .pin1`} thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={powerNeckRoutingPhaseIndex} />
      <trace name="TR_V3_C5_NECK" from=".C5 > .pin1" to={`.${props.powerRailNodeRefs.westLower} > .pin1`} thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={powerNeckRoutingPhaseIndex} />
      <trace name="TR_V3_C6_NECK" from=".C6 > .pin1" to={`.${props.powerRailNodeRefs.south} > .pin1`} thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={powerNeckRoutingPhaseIndex} />
      <trace name="TR_V3_C7_NECK" from=".C7 > .pin1" to={`.${props.powerRailNodeRefs.eastLower} > .pin1`} thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={powerNeckRoutingPhaseIndex} />
      <trace name="TR_V3_C8_NECK" from=".C8 > .pin1" to={`.${props.powerRailNodeRefs.eastUpper} > .pin1`} thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={powerNeckRoutingPhaseIndex} />
      <trace name="TR_V3_C9_NECK" from=".C9 > .pin1" to={`.${props.powerRailNodeRefs.topRight} > .pin1`} thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={powerNeckRoutingPhaseIndex} />
      <trace name="TR_V3_C11_NECK" from=".C11 > .pin1" to={`.${props.powerRailNodeRefs.topMiddle} > .pin1`} thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={powerNeckRoutingPhaseIndex} />
      <trace name="TR_V3_C10_NECK" from=".C10 > .pin1" to={`.${props.powerRailNodeRefs.topLeft} > .pin1`} thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={powerNeckRoutingPhaseIndex} />
      <trace name="TR_V3_C26_NECK" from=".C26 > .pin1" to={`.${props.powerRailNodeRefs.topRight} > .pin1`} thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={powerNeckRoutingPhaseIndex} />
      <trace name="TR_V3_C17_NECK" from=".C17 > .pin1" to={`.${props.powerRailNodeRefs.bulk} > .pin1`} thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={powerNeckRoutingPhaseIndex} />
      <trace name="TR_V3_C14_NECK" from=".C14 > .pin1" to={`.${props.powerRailNodeRefs.flash} > .pin1`} thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={powerNeckRoutingPhaseIndex} />
      <trace name="TR_V3_R12_NECK" from=".R12 > .pin1" to={`.${props.powerRailNodeRefs.eastLower} > .pin1`} thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={powerNeckRoutingPhaseIndex} />

      {/* One acyclic 0.8mm 3V3 tree surrounds the dense QFN. C17's node is
          the sole escape; no other core port directly touches net.V3_3. */}
      <trace name="TR_V3_BULK_TOPRIGHT" from={`.${props.powerRailNodeRefs.bulk} > .pin1`} to={`.${props.powerRailNodeRefs.topRight} > .pin1`} thickness={localRailWidth} routingPhaseIndex={powerTrunkRoutingPhaseIndex} />
      <trace name="TR_V3_TOPRIGHT_TOPMIDDLE" from={`.${props.powerRailNodeRefs.topRight} > .pin1`} to={`.${props.powerRailNodeRefs.topMiddle} > .pin1`} thickness={localRailWidth} routingPhaseIndex={powerTrunkRoutingPhaseIndex} />
      <trace name="TR_V3_TOPMIDDLE_TOPLEFT" from={`.${props.powerRailNodeRefs.topMiddle} > .pin1`} to={`.${props.powerRailNodeRefs.topLeft} > .pin1`} thickness={localRailWidth} routingPhaseIndex={powerTrunkRoutingPhaseIndex} />
      <trace name="TR_V3_TOPLEFT_WESTUPPER" from={`.${props.powerRailNodeRefs.topLeft} > .pin1`} to={`.${props.powerRailNodeRefs.westUpper} > .pin1`} thickness={localRailWidth} routingPhaseIndex={powerTrunkRoutingPhaseIndex} />
      <trace name="TR_V3_WESTUPPER_WESTLOWER" from={`.${props.powerRailNodeRefs.westUpper} > .pin1`} to={`.${props.powerRailNodeRefs.westLower} > .pin1`} thickness={localRailWidth} routingPhaseIndex={powerTrunkRoutingPhaseIndex} />
      <trace name="TR_V3_WESTLOWER_SOUTH" from={`.${props.powerRailNodeRefs.westLower} > .pin1`} to={`.${props.powerRailNodeRefs.south} > .pin1`} thickness={localRailWidth} routingPhaseIndex={powerTrunkRoutingPhaseIndex} />
      <trace name="TR_V3_TOPRIGHT_EASTUPPER" from={`.${props.powerRailNodeRefs.topRight} > .pin1`} to={`.${props.powerRailNodeRefs.eastUpper} > .pin1`} thickness={localRailWidth} routingPhaseIndex={powerTrunkRoutingPhaseIndex} />
      <trace name="TR_V3_EASTUPPER_EASTLOWER" from={`.${props.powerRailNodeRefs.eastUpper} > .pin1`} to={`.${props.powerRailNodeRefs.eastLower} > .pin1`} thickness={localRailWidth} routingPhaseIndex={powerTrunkRoutingPhaseIndex} />
      <trace name="TR_V3_TOPLEFT_FLASH" from={`.${props.powerRailNodeRefs.topLeft} > .pin1`} to={`.${props.powerRailNodeRefs.flash} > .pin1`} thickness={localRailWidth} routingPhaseIndex={powerTrunkRoutingPhaseIndex} />
      <trace name="TR_V3_ESCAPE" from={`.${props.powerRailNodeRefs.bulk} > .pin1`} to="net.V3_3"
        thickness={localRailWidth} routingPhaseIndex={powerTrunkRoutingPhaseIndex}
        authoredNetTreeBoundary />

      {/* The internal 1.1V rail uses one same-face capacitor per remote QFN
          supply toe.  C13 (100nF) belongs only to DVDD1; C25 (1uF) belongs
          only to VREG_OUT; C12 (100nF) belongs only to DVDD2. C26 is the
          separate 1uF VREG_IN bypass on V3_3. The short .2mm branches are
          placement-owned copper and never cross another package escape. */}
      <trace name={`TR_${u}_vregout_C25`} from={`.${u} > .VREG_VOUT`} to=".C25 > .pin1"
        thickness={localPowerWidth} maxLength="2mm"
        routingPhaseIndex={dvddLocalRoutingPhaseIndex}
        pcbPath={[
          { x: localX(1.8), y: 3.42505 },
          { x: localX(1.8), y: 3.98 },
          { x: localX(1.95), y: 4.69 },
        ]} />
      <trace name={`TR_${u}_dvdd1_C13`} from={`.${u} > .DVDD1`} to=".C13 > .pin1"
        thickness={localPowerWidth} maxLength="2mm"
        routingPhaseIndex={dvddLocalRoutingPhaseIndex}
        pcbPath={[
          { x: localX(-0.2), y: 3.42505 },
          { x: localX(-0.2), y: 4.16 },
          { x: localX(-0.65), y: 4.16 },
          { x: localX(-0.65), y: 4.22 },
          { x: localX(-1.2), y: 4.69 },
        ]} />
      <trace name={`TR_${u}_dvdd2_C12`} from={`.${u} > .DVDD2`} to=".C12 > .pin1"
        thickness={localPowerWidth} maxLength="2mm"
        routingPhaseIndex={dvddLocalRoutingPhaseIndex}
        pcbPath={[
          { x: localX(0.6), y: -3.42505 },
          { x: localX(0.6), y: -4.9 },
        ]} />

      {/* Each small capacitor has a bounded .2mm neck into a mask-covered
          component-face node. A continuous .8mm fixed path leaves that node,
          crosses one scoped .8/.5mm via, and reaches the opposite-face N18
          junction. The via is real copper inside the same source trace; it is
          not a source-disconnected assignable waypoint. */}
      <trace name="TR_DVDD_C13_NECK" from=".C13 > .pin1" to={`.${props.powerRailNodeRefs.dvddLeft} > .pin1`}
        thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={dvddTrunkRoutingPhaseIndex} />
      <trace name="TR_DVDD_C25_NECK" from=".C25 > .pin1" to={`.${props.powerRailNodeRefs.dvddRight} > .pin1`}
        thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={dvddTrunkRoutingPhaseIndex} />
      <trace name="TR_DVDD_C12_NECK" from=".C12 > .pin1" to={`.${props.powerRailNodeRefs.dvddSouth} > .pin1`}
        thickness={localPowerWidth} maxLength="3mm" routingPhaseIndex={dvddTrunkRoutingPhaseIndex} />
      <group pcbStyle={{ viaPadDiameter: "0.8mm", viaHoleDiameter: "0.5mm" }}>
        <trace name="TR_DVDD_LEFT_JUNCTION"
          from={`.${props.powerRailNodeRefs.dvddLeft} > .pin1`}
          to={`.${props.powerRailNodeRefs.dvddJunction} > .pin1`}
          thickness={localRailWidth} routingPhaseIndex={dvddTrunkRoutingPhaseIndex}
          pcbPathRelativeTo={`.${props.powerRailNodeRefs.dvddLeft} > .pin1`}
          pcbPath={[
            { x: 0, y: 0 },
            { x: localX(-1), y: 0 },
            { x: localX(-1), y: 0, via: true, fromLayer: layer, toLayer: oppositeLayer },
            { x: localX(-1), y: 0 },
            { x: localX(-1), y: -4.7 },
            { x: 0, y: -4.7 },
          ]} />
        <trace name="TR_DVDD_RIGHT_JUNCTION"
          from={`.${props.powerRailNodeRefs.dvddRight} > .pin1`}
          to={`.${props.powerRailNodeRefs.dvddJunction} > .pin1`}
          thickness={localRailWidth} routingPhaseIndex={dvddTrunkRoutingPhaseIndex}
          pcbPathRelativeTo={`.${props.powerRailNodeRefs.dvddRight} > .pin1`}
          pcbPath={[
            { x: 0, y: 0 },
            { x: localX(1), y: 0 },
            { x: localX(1), y: 0, via: true, fromLayer: layer, toLayer: oppositeLayer },
            { x: localX(1), y: 0 },
            { x: localX(1), y: -3.85 },
            { x: localX(-6.5), y: -3.85 },
          ]} />
        <trace name="TR_DVDD_SOUTH_JUNCTION"
          from={`.${props.powerRailNodeRefs.dvddSouth} > .pin1`}
          to={`.${props.powerRailNodeRefs.dvddJunction} > .pin1`}
          thickness={localRailWidth} routingPhaseIndex={dvddTrunkRoutingPhaseIndex}
          pcbPathRelativeTo={`.${props.powerRailNodeRefs.dvddSouth} > .pin1`}
          pcbPath={[
            { x: 0, y: 0 },
            { x: localX(1), y: 0 },
            { x: localX(1), y: 0, via: true, fromLayer: layer, toLayer: oppositeLayer },
            { x: localX(1), y: 0 },
            { x: localX(1), y: 6.45 },
            { x: localX(-6.1), y: 6.45 },
          ]} />
      </group>
      <trace name="TR_DVDD_ESCAPE" from={`.${props.powerRailNodeRefs.dvddJunction} > .pin1`} to="net.DVDD"
        thickness={localRailWidth} routingPhaseIndex={dvddTrunkRoutingPhaseIndex}
        authoredNetTreeBoundary />
    </group>
  )
}

export default Rp2040Core