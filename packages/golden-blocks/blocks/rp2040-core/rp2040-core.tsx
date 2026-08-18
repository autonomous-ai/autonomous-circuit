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
 *     1uF on VREG_VOUT (the 1.1V DVDD net), 10uF bulk on 3.3V
 *   - RUN 10k pull-up + reset button; BOOTSEL button (1k into QSPI_SS)
 *
 * Exposes: chip refdes U3 — trace GPIOs directly (`.U3 > .GPIO5`), plus
 * nets USB_DP/USB_DM (pair with usb-c-data), SWCLK/SWD (debug), and rails
 * V3_3/GND. Ported from seveibar/rp2040-module (old hooks API) to current
 * JSX, 2026-08-10.
 *
 * Default refdes (global v1 allocation): U3, U4, Y1, R11-R13, SW2, SW3,
 * C4-C16.
 */

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

export const Rp2040Core = (props: {
  u?: string
  flash?: string
  xtal?: string
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const u = props.u ?? "U3"
  const f = props.flash ?? "U4"
  const y = props.xtal ?? "Y1"
  return (
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <Rp2040Chip name={u} pcbX={0} pcbY={0} schX={0} schY={0} />
      <W25q128 name={f} pcbX={13} pcbY={0} schX={14} schY={-6} />
      {/* PLACEMENT IS LOAD-BEARING HERE (fixed 2026-08-10). v1 placed Y1 at
          pcbX={-11}, putting Y1.pin1 11.78mm from U3.XIN. tscircuit enforces a
          10mm maximum on a crystal connection and, when it cannot be met,
          SKIPS AUTOROUTING FOR THE WHOLE BOARD — so no board built on this
          block could route at all, and the error it reports names Y1 rather
          than the cause. The rule covers the load caps too. U3.XIN is the
          bottom-centre pin of the QFN-56, so the whole crystal cluster (Y1 +
          C15/C16 + the 1k XOUT series R11) sits in the strip directly below
          the chip, every endpoint within 9.1mm of XIN. Found independently by
          two boards; keep the cluster together if you move it. */}
      <crystal name={y} frequency="12MHz" loadCapacitance="10pF" pinVariant="four_pin"
        footprint="crystal" pcbX={0} pcbY={-10.5} schX={-14} schY={6}
        supplierPartNumbers={{ jlcpcb: ["C20625731"] }} />

      {/* --- Rails ------------------------------------------------------- */}
      <trace name={`TR_${u}_iovdd1`} from={`.${u} > .IOVDD1`} to="net.V3_3" />
      <trace name={`TR_${u}_iovdd2`} from={`.${u} > .IOVDD2`} to="net.V3_3" />
      <trace name={`TR_${u}_iovdd3`} from={`.${u} > .IOVDD3`} to="net.V3_3" />
      <trace name={`TR_${u}_iovdd4`} from={`.${u} > .IOVDD4`} to="net.V3_3" />
      <trace name={`TR_${u}_iovdd5`} from={`.${u} > .IOVDD5`} to="net.V3_3" />
      <trace name={`TR_${u}_iovdd6`} from={`.${u} > .IOVDD6`} to="net.V3_3" />
      <trace name={`TR_${u}_usbvdd`} from={`.${u} > .USB_VDD`} to="net.V3_3" />
      <trace name={`TR_${u}_adcavdd`} from={`.${u} > .ADC_AVDD`} to="net.V3_3" />
      <trace name={`TR_${u}_vregin`} from={`.${u} > .VREG_IN`} to="net.V3_3" />
      <trace name={`TR_${u}_vregout`} from={`.${u} > .VREG_VOUT`} to="net.DVDD" />
      <trace name={`TR_${u}_dvdd1`} from={`.${u} > .DVDD1`} to="net.DVDD" />
      <trace name={`TR_${u}_dvdd2`} from={`.${u} > .DVDD2`} to="net.DVDD" />
      <trace name={`TR_${u}_gnd`} from={`.${u} > .GND`} to="net.GND" />
      <trace name={`TR_${u}_testen`} from={`.${u} > .TESTEN`} to="net.GND" />

      {/* --- USB + debug nets out ---------------------------------------- */}
      <trace name={`TR_${u}_usbdp`} from={`.${u} > .USB_DP`} to="net.USB_DP" />
      <trace name={`TR_${u}_usbdm`} from={`.${u} > .USB_DM`} to="net.USB_DM" />
      <trace name={`TR_${u}_swclk`} from={`.${u} > .SWCLK`} to="net.SWCLK" />
      <trace name={`TR_${u}_swd`} from={`.${u} > .SWD`} to="net.SWD" />

      {/* --- Crystal: XIN direct, XOUT through 1k series ------------------ */}
      <trace name={`TR_${y}_xin`} from={`.${y} > .pin1`} to={`.${u} > .XIN`} />
      <resistor name="R11" resistance="1k" footprint="0402" pcbX={6} pcbY={-10.5} schX={-10} schY={7}
        supplierPartNumbers={{ jlcpcb: ["C11702"] }} />
      <trace name={`TR_${u}_xout_r`} from={`.${u} > .XOUT`} to=".R11 > .pin1" />
      <trace name={`TR_R11_${y}`} from=".R11 > .pin2" to={`.${y} > .pin3`} />
      {/* four_pin crystal: pin2/pin4 are the ground pads */}
      <trace name={`TR_${y}_gnd1`} from={`.${y} > .pin2`} to="net.GND" />
      <trace name={`TR_${y}_gnd2`} from={`.${y} > .pin4`} to="net.GND" />
      <capacitor name="C15" capacitance="15pF" footprint="0402" pcbX={-4.5} pcbY={-10.5} schX={-16} schY={9}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1548"] }} />
      <capacitor name="C16" capacitance="15pF" footprint="0402" pcbX={3.2} pcbY={-10.5} schX={-12} schY={9}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1548"] }} />
      <trace name={`TR_C15_xin`} from=".C15 > .pin1" to={`.${u} > .XIN`} />
      <trace name={`TR_C15_gnd`} from=".C15 > .pin2" to="net.GND" />
      <trace name={`TR_C16_xt`} from=".C16 > .pin1" to={`.${y} > .pin3`} />
      <trace name={`TR_C16_gnd`} from=".C16 > .pin2" to="net.GND" />

      {/* --- QSPI flash --------------------------------------------------- */}
      <trace name={`TR_${f}_cs`} from={`.${f} > .CS`} to={`.${u} > .QSPI_SS`} />
      <trace name={`TR_${f}_clk`} from={`.${f} > .CLK`} to={`.${u} > .QSPI_SCLK`} />
      <trace name={`TR_${f}_io0`} from={`.${f} > .IO0`} to={`.${u} > .QSPI_SD0`} />
      <trace name={`TR_${f}_io1`} from={`.${f} > .IO1`} to={`.${u} > .QSPI_SD1`} />
      <trace name={`TR_${f}_io2`} from={`.${f} > .IO2`} to={`.${u} > .QSPI_SD2`} />
      <trace name={`TR_${f}_io3`} from={`.${f} > .IO3`} to={`.${u} > .QSPI_SD3`} />
      <trace name={`TR_${f}_vcc`} from={`.${f} > .VCC`} to="net.V3_3" />
      <trace name={`TR_${f}_gnd`} from={`.${f} > .GND`} to="net.GND" />
      <capacitor name="C14" capacitance="100nF" footprint="0402" pcbX={13} pcbY={-6.5} schX={17} schY={-3}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <trace name={`TR_C14_v`} from=".C14 > .pin1" to="net.V3_3" />
      <trace name={`TR_C14_g`} from=".C14 > .pin2" to="net.GND" />

      {/* --- BOOTSEL: QSPI_SS -> 1k -> button -> GND ---------------------- */}
      <resistor name="R13" resistance="1k" footprint="0402" pcbX={8} pcbY={-6} schX={10} schY={-10}
        supplierPartNumbers={{ jlcpcb: ["C11702"] }} />
      <pushbutton name="SW2"
        supplierPartNumbers={{ jlcpcb: ["C318884"] }}
        internallyConnectedPins={[["pin1", "pin2"], ["pin3", "pin4"]]}
        footprint="dfn4_p3.6998mm_w7mm_pw0.75mm" pcbX={8} pcbY={-15.5} schX={14} schY={-10} />
      {/* Copper stays 4-tie here ON MEASUREMENT (2026-08-15): the diagonal
          rewire (pin 1 in, pin 4 out — see sw-tact) reshuffled this block's
          route and landed a via 0.0787mm from the Y1→XIN crystal trace
          against the 0.09 floor, 5/5 gauntlet runs, identical to the micron;
          the 4-tie shape routes clean. Same-group ties are safe under the
          confirmed {1,2}/{3,4} pairing (LCSC symbol for C318884, 2026-08-15;
          first-article continuity is still the final check).

          But the ties are declared through a NAMED NET, never pin-to-pin.
          `internallyConnectedPins` was supposed to fold them into the symbol;
          measured on the 2026-08-16 export it does that for the 50 keys and
          not for these two, and `schematic_symbol_short` still fired on SW2
          and SW3 on all three boards — the exact wire the EE read as a dead
          button. A trace between two pins of one symbol is what gets drawn as
          a loop across the contacts; two traces to the same net are drawn as
          two labels. Identical copper, identical connectivity, and nothing
          for a reviewer to misread. */}
      <trace name={`TR_R13_ss`} from=".R13 > .pin1" to={`.${u} > .QSPI_SS`} />
      <trace name={`TR_R13_sw`} from=".R13 > .pin2" to="net.BOOTSEL_SW" />
      <trace name={`TR_SW2_p1`} from=".SW2 > .pin1" to="net.BOOTSEL_SW" />
      <trace name={`TR_SW2_p2`} from=".SW2 > .pin2" to="net.BOOTSEL_SW" />
      <trace name={`TR_SW2_p3`} from=".SW2 > .pin3" to="net.GND" />
      <trace name={`TR_SW2_p4`} from=".SW2 > .pin4" to="net.GND" />

      {/* --- RUN: 10k pull-up + reset button ------------------------------ */}
      <resistor name="R12" resistance="10k" footprint="0402" pcbX={-8} pcbY={-6} schX={-10} schY={-8}
        supplierPartNumbers={{ jlcpcb: ["C25744"] }} />
      <pushbutton name="SW3"
        supplierPartNumbers={{ jlcpcb: ["C318884"] }}
        internallyConnectedPins={[["pin1", "pin2"], ["pin3", "pin4"]]}
        footprint="dfn4_p3.6998mm_w7mm_pw0.75mm" pcbX={-8} pcbY={-15.5} schX={-14} schY={-10} />
      <trace name={`TR_R12_v`} from=".R12 > .pin1" to="net.V3_3" />
      <trace name={`TR_U_run`} from={`.${u} > .RUN`} to="net.RUN_SW" />
      <trace name={`TR_R12_run`} from=".R12 > .pin2" to="net.RUN_SW" />
      <trace name={`TR_SW3_p1`} from=".SW3 > .pin1" to="net.RUN_SW" />
      <trace name={`TR_SW3_p2`} from=".SW3 > .pin2" to="net.RUN_SW" />
      <trace name={`TR_SW3_p3`} from=".SW3 > .pin3" to="net.GND" />
      <trace name={`TR_SW3_p4`} from=".SW3 > .pin4" to="net.GND" />

      {/* --- Decoupling (design guide: 100nF per supply pin) -------------- */}
      <capacitor name="C4" capacitance="100nF" footprint="0402" pcbX={-6} pcbY={6} schX={-6} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C5" capacitance="100nF" footprint="0402" pcbX={-3} pcbY={6} schX={-4} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C6" capacitance="100nF" footprint="0402" pcbX={0} pcbY={6} schX={-2} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C7" capacitance="100nF" footprint="0402" pcbX={3} pcbY={6} schX={0} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C8" capacitance="100nF" footprint="0402" pcbX={6} pcbY={6} schX={2} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C9" capacitance="100nF" footprint="0402" pcbX={-6} pcbY={-6} schX={4} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C10" capacitance="100nF" footprint="0402" pcbX={-3} pcbY={-6} schX={6} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <capacitor name="C11" capacitance="100nF" footprint="0402" pcbX={0} pcbY={-6} schX={8} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <trace name={`TR_C4_v`} from=".C4 > .pin1" to="net.V3_3" />
      <trace name={`TR_C4_g`} from=".C4 > .pin2" to="net.GND" />
      <trace name={`TR_C5_v`} from=".C5 > .pin1" to="net.V3_3" />
      <trace name={`TR_C5_g`} from=".C5 > .pin2" to="net.GND" />
      <trace name={`TR_C6_v`} from=".C6 > .pin1" to="net.V3_3" />
      <trace name={`TR_C6_g`} from=".C6 > .pin2" to="net.GND" />
      <trace name={`TR_C7_v`} from=".C7 > .pin1" to="net.V3_3" />
      <trace name={`TR_C7_g`} from=".C7 > .pin2" to="net.GND" />
      <trace name={`TR_C8_v`} from=".C8 > .pin1" to="net.V3_3" />
      <trace name={`TR_C8_g`} from=".C8 > .pin2" to="net.GND" />
      <trace name={`TR_C9_v`} from=".C9 > .pin1" to="net.V3_3" />
      <trace name={`TR_C9_g`} from=".C9 > .pin2" to="net.GND" />
      <trace name={`TR_C10_v`} from=".C10 > .pin1" to="net.V3_3" />
      <trace name={`TR_C10_g`} from=".C10 > .pin2" to="net.GND" />
      <trace name={`TR_C11_v`} from=".C11 > .pin1" to="net.V3_3" />
      <trace name={`TR_C11_g`} from=".C11 > .pin2" to="net.GND" />
      {/* DVDD (1.1V core, fed by the internal regulator) */}
      <capacitor name="C12" capacitance="1uF" footprint="0402" pcbX={3} pcbY={-6} schX={10} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C52923"] }} />
      <capacitor name="C13" capacitance="100nF" footprint="0402" pcbX={6} pcbY={-6} schX={12} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C1525"] }} />
      <trace name={`TR_C12_v`} from=".C12 > .pin1" to="net.DVDD" />
      <trace name={`TR_C12_g`} from=".C12 > .pin2" to="net.GND" />
      <trace name={`TR_C13_v`} from=".C13 > .pin1" to="net.DVDD" />
      <trace name={`TR_C13_g`} from=".C13 > .pin2" to="net.GND" />
      {/* 3.3V bulk */}
      <capacitor name="C17" capacitance="10uF" footprint="0805" pcbX={9} pcbY={6} schX={14} schY={12}
        schRotation="90deg" supplierPartNumbers={{ jlcpcb: ["C15850"] }} />
      <trace name={`TR_C17_v`} from=".C17 > .pin1" to="net.V3_3" />
      <trace name={`TR_C17_g`} from=".C17 > .pin2" to="net.GND" />
    </group>
  )
}

export default Rp2040Core
