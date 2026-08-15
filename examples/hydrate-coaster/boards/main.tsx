/**
 * hydrate-coaster — the brain and the senses of the Autonomous Hydrate coaster.
 *
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * Blocks used: usb-c-data, ldo-3v3, rp2040-core, status-led x2, sw-tact
 * Rails: V5 (USB VBUS, 5V) -> ldo-3v3 -> V3_3 (logic)
 * Envelope: 80 x 80 mm squircle, 2 layers, 1.6mm — inside product.json's 82 x 82
 *
 * The cup sense is two mask-covered copper electrodes (copper pours EA/EB) on
 * the top layer, under where the mug sits. Each is driven from one shared GPIO
 * through a 1M series resistor and read back on its own GPIO — the standard
 * RC charge-time self-capacitance trick. Sum of the two reads tracks the water;
 * difference says the mug is actually on the coaster and roughly centred.
 * Nothing here is invented: every part is a golden block or glue (resistors,
 * LEDs, copper, holes).
 *
 * Layout intent
 *   y >= -2   electrode zone, under the mug. Copper and two anchor resistors only.
 *   y <= -4   electronics band. USB-C on the BOTTOM edge, centred.
 *   The RP2040 block is rotated 180deg so the chip's USB/QSPI side faces the
 *   USB connector 12mm below it. Without this the USB pair had to cross the
 *   whole chip and the router filled the 0.4mm-pitch fanout with shorts.
 *
 * Pin allocation (RP2040, U3; sides are AFTER the 180deg rotation)
 *   GPIO2   CAP_DRIVE     shared drive for both electrodes   (chip right side)
 *   GPIO3   CAP_A_SENSE   left electrode read-back           (chip right side)
 *   GPIO4   CAP_B_SENSE   right electrode read-back          (chip right side)
 *   GPIO0   LED_NUDGE     the agent's light                  (chip right side)
 *   GPIO1   BTN_MUTE      shut-up button, active low         (chip right side)
 *   USB_DP/USB_DM         to usb-c-data                      (chip bottom side)
 * Every signal the board adds leaves the QFN on its RIGHT side and runs up the
 * empty corridor at x = -8..9. The chip's top side is left entirely to the
 * crystal, RUN and SWD: the first cut put the cup-sense nets up there and the
 * router squeezed vias into the 2.8mm gaps beside Y1, which is where the last
 * three clearance errors were.
 */

import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { StatusLed } from "../blocks/status-led/status-led"
import { SwTact } from "../blocks/sw-tact/sw-tact"
import { MountingHole, POUR_CUTOUT_MARGIN_MM } from "../blocks/glue"

export default () => (
  <board
    width="80mm"
    height="80mm"
    thickness={1.6}
    borderRadius={16}
    /* 5x (the SKILL.md floor) cleared 14/16 errors but left the USB-C
       data-pair fanout with a via inside J1.B4A9's pad and one at 0.07mm —
       the 5x router's ceiling on a 16-pin 0.5mm-pitch connector. 10x per the
       idiom doc ("go to 10x while the router is still missing its own
       clearances") to clear the last two. */
    autorouterEffortLevel="10x"
    minTraceWidth="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    {/* ---- power entry: USB-C on the bottom edge, 5V + the USB 2.0 pair ---- */}
    <UsbCData pcbX={0} pcbY={-34} schX={-46} schY={0} />

    {/* ---- logic rail: V5 -> V3_3 ----------------------------------------
        Up and right of the connector. Tried tucking it beside C1 (the USB
        bulk cap) to shorten V5; that put the LDO's own input cap inside C1's
        courtyard and, once moved clear, pushed the D- pair across the VBUS
        pad instead. This position is the best of the three measured. */}
    <Ldo3v3 pcbX={14} pcbY={-18} schX={-46} schY={22} />

    {/* ---- the brain, turned to face its neighbours ------------------------ */}
    <group pcbRotation={180} pcbX={-20} pcbY={-22} schX={0} schY={0}>
      <Rp2040Core pcbX={0} pcbY={0} schX={0} schY={0} />
    </group>
    <trace name="TR_USB_DP" from=".U3 > .USB_DP" to="net.USB_DP" />
    <trace name="TR_USB_DM" from=".U3 > .USB_DM" to="net.USB_DM" />

    {/* ---- cup sense: one drive pin, two electrodes ------------------------
        GPIO2 --[1M R30]--> CAP_A --[1k R32]--> GPIO3   (left electrode)
        GPIO2 --[1M R31]--> CAP_B --[1k R33]--> GPIO4   (right electrode)
        The 1M sets the charge ramp the firmware times; the 1k is series
        protection on the pin that owns the electrode. R32/R33 sit INSIDE their
        pour so the electrode copper has a same-net pad to bond to — a pour with
        no pad of its own net inside it comes out as isolated copper. They sit
        near the inner edge of each plate, 4mm from it: moving them to the
        middle of the plate (x = +/-16) cost 26 extra routing errors, because
        the two sense nets then had to climb the crowded left half of the
        corridor. The price of x = +/-8 is one sliver of plate copper cut off
        by R32's exit trace -- see DESIGN-REVIEW.md, layout lens. */}
    <resistor name="R30" resistance="1M" footprint="0402" pcbX={-2} pcbY={-6} schX={30} schY={10}
      supplierPartNumbers={{ jlcpcb: ["C26083"] }} />
    <resistor name="R31" resistance="1M" footprint="0402" pcbX={2} pcbY={-6} schX={30} schY={-2}
      supplierPartNumbers={{ jlcpcb: ["C26083"] }} />
    <resistor name="R32" resistance="1k" footprint="0402" pcbX={-8} pcbY={2} schX={40} schY={12}
      supplierPartNumbers={{ jlcpcb: ["C11702"] }} />
    <resistor name="R33" resistance="1k" footprint="0402" pcbX={8} pcbY={2} schX={40} schY={0}
      supplierPartNumbers={{ jlcpcb: ["C11702"] }} />

    <trace name="TR_CAPDRV" from=".U3 > .GPIO2" to="net.CAP_DRIVE" />
    <trace name="TR_R30_drv" from=".R30 > .pin1" to="net.CAP_DRIVE" />
    <trace name="TR_R30_ea" from=".R30 > .pin2" to="net.CAP_A" />
    <trace name="TR_R31_drv" from=".R31 > .pin1" to="net.CAP_DRIVE" />
    <trace name="TR_R31_eb" from=".R31 > .pin2" to="net.CAP_B" />
    <trace name="TR_R32_ea" from=".R32 > .pin1" to="net.CAP_A" />
    <trace name="TR_R32_mcu" from=".R32 > .pin2" to="net.CAP_A_SENSE" />
    <trace name="TR_R33_eb" from=".R33 > .pin1" to="net.CAP_B" />
    <trace name="TR_R33_mcu" from=".R33 > .pin2" to="net.CAP_B_SENSE" />
    <trace name="TR_SENSE_A" from=".U3 > .GPIO3" to="net.CAP_A_SENSE" />
    <trace name="TR_SENSE_B" from=".U3 > .GPIO4" to="net.CAP_B_SENSE" />

    {/* Electrode A — left half of the mug footprint. Mask-covered: no bare
        copper under a glass that will one day get knocked over. */}
    <copperpour
      name="EA"
      layer="top"
      connectsTo="net.CAP_A"
      coveredWithSolderMask
      boardEdgeMargin="1mm"
      cutoutMargin={`${POUR_CUTOUT_MARGIN_MM}mm`}
      outline={[
        { x: -29, y: -2 },
        { x: -4, y: -2 },
        { x: -4, y: 28 },
        { x: -29, y: 28 },
      ]}
    />
    {/* Electrode B — right half. The 8mm gap to EA keeps their mutual
        capacitance small next to the mug-to-plate capacitance we want. */}
    <copperpour
      name="EB"
      layer="top"
      connectsTo="net.CAP_B"
      coveredWithSolderMask
      boardEdgeMargin="1mm"
      cutoutMargin={`${POUR_CUTOUT_MARGIN_MM}mm`}
      outline={[
        { x: 4, y: -2 },
        { x: 29, y: -2 },
        { x: 29, y: 28 },
        { x: 4, y: 28 },
      ]}
    />

    {/* ---- indicators, front edge left of the USB -------------------------
        Also tried them on the right, past the regulator, on the theory that
        their GND return was what crowded the connector's drills. It was not:
        the count went 2 -> 4. Left is the measured best.
        LED1 is hard-wired to the rail: proof of life the firmware cannot lie
        about. LED2 is the agent's own light, on GPIO0. */}
    <StatusLed led="LED1" r="R20" rail="V3_3" pcbX={-11} pcbY={-35.5} schX={30} schY={22} />
    <StatusLed led="LED2" r="R21" rail="LED_NUDGE" pcbX={-16} pcbY={-35.5} schX={38} schY={22} />
    <trace name="TR_LED_NUDGE" from=".U3 > .GPIO0" to="net.LED_NUDGE" />

    {/* ---- the shut-up button, front-right corner, clear of the mug -------
        Active low into GPIO1 with the RP2040's internal pull-up. */}
    <SwTact name="SW1" signal="BTN_MUTE" pcbX={29} pcbY={-24} schX={30} schY={-14} />
    <trace name="TR_BTN_MUTE" from=".U3 > .GPIO1" to="net.BTN_MUTE" />

    {/* ---- debug port ------------------------------------------------------
        SWCLK/SWD are brought out by the rp2040-core block but reach nothing —
        the board assembles, powers, and cannot be programmed or halted. Three
        pads (CLK/DIO/GND) in the corridor below the electrodes, 2.54mm pitch so
        a 3-pin header solders on when one is wanted; 1mm round pads take a pogo
        or a clip when none is. Pads not plated holes — no drills, no keepouts. */}
    <testpoint name="TP1" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={-2.54} pcbY={-12} />
    <testpoint name="TP2" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={0} pcbY={-12} />
    <testpoint name="TP3" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={2.54} pcbY={-12} />
    <trace name="TR_TP1_SWCLK" from=".TP1 > .pin1" to="net.SWCLK" />
    <trace name="TR_TP2_SWD" from=".TP2 > .pin1" to="net.SWD" />
    <trace name="TR_TP3_GND" from=".TP3 > .pin1" to="net.GND" />
    <silkscreentext text="CLK" pcbX={-2.54} pcbY={-13.8} fontSize={1} />
    <silkscreentext text="DIO" pcbX={0} pcbY={-13.8} fontSize={1} />
    <silkscreentext text="GND" pcbX={2.54} pcbY={-13.8} fontSize={1} />

    {/* ---- mechanics: M3 on a 64 x 64 square, one at each corner ---------- */}
    <MountingHole name="H1" diameter={3.2} pcbX={-32} pcbY={-32} />
    <MountingHole name="H2" diameter={3.2} pcbX={32} pcbY={-32} />
    <MountingHole name="H3" diameter={3.2} pcbX={-32} pcbY={32} />
    <MountingHole name="H4" diameter={3.2} pcbX={32} pcbY={32} />

    {/* ---- silkscreen ------------------------------------------------------ */}
    <silkscreentext text="AUTONOMOUS HYDRATE" pcbX={0} pcbY={35} fontSize={2} />
    <silkscreentext text="CUP SENSE A" pcbX={-16} pcbY={13} fontSize={1.6} />
    <silkscreentext text="CUP SENSE B" pcbX={16} pcbY={13} fontSize={1.6} />
    <silkscreentext text="3V3 @ C3   5V @ C2" pcbX={0} pcbY={-14} fontSize={1.2} />
    <silkscreentext text="USB-C 5V" pcbX={0} pcbY={-19} fontSize={1.2} />
    <silkscreentext text="PWR" pcbX={-11} pcbY={-38} fontSize={1.2} />
    <silkscreentext text="AGENT" pcbX={-16.5} pcbY={-38} fontSize={1.2} />
    <silkscreentext text="MUTE" pcbX={29} pcbY={-19} fontSize={1.2} />
  </board>
)
