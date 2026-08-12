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
import {
  GndPlanes,
  MountingHole,
  POUR_CUTOUT_MARGIN_MM,
  PowerTrunk,
} from "../blocks/glue"

/** Routing regions use board-global coordinates; nested block transforms do
 * not transform an autorouting phase. Keep dense local work bounded, then let
 * only the genuinely inter-block signals use their own narrow corridors. */
const RP_CLOCK_ROUTING_REGION = { minX: -24, maxX: -16, minY: -38, maxY: -12 } as const
const RP_QSPI_ROUTING_REGION = { minX: -24.5, maxX: -16, minY: -38, maxY: -23.5 } as const
const USB_CC_ROUTING_REGION = { minX: -6, maxX: 3, minY: -34, maxY: -25 } as const
const USB_DP_LOCAL_ROUTING_REGION = { minX: -3, maxX: 5, minY: -33, maxY: -22 } as const
const USB_DM_LOCAL_ROUTING_REGION = { minX: -3, maxX: 5, minY: -33, maxY: -22 } as const
const VBUS_ROUTING_REGION = { minX: -4, maxX: 19, minY: -33, maxY: -14 } as const
const RP_POWER_ROUTING_REGION = { minX: -28, maxX: 20, minY: -38, maxY: -14 } as const
const RP_DEBUG_RESET_ROUTING_REGION = { minX: -34, maxX: -2, minY: -20, maxY: -10 } as const
const SWCLK_ROUTING_REGION = { minX: -22, maxX: -2, minY: -20, maxY: -14 } as const
const SWD_ROUTING_REGION = { minX: -22.5, maxX: -4, minY: -20, maxY: -14 } as const
const UI_ROUTING_REGION = { minX: -28, maxX: 34, minY: -38, maxY: -20 } as const
const MCU_IO_ROUTING_REGION = { minX: -23, maxX: 11, minY: -29, maxY: 4 } as const

export default () => (
  <board
    width="80mm"
    height="80mm"
    thickness={1.6}
    borderRadius={16}
    minTraceWidth="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
  >
    <autoroutingphase phaseIndex={0} region={RP_CLOCK_ROUTING_REGION} />
    <autoroutingphase phaseIndex={1} region={RP_QSPI_ROUTING_REGION} />
    <autoroutingphase phaseIndex={2} region={USB_CC_ROUTING_REGION} />
    <autoroutingphase phaseIndex={3} region={USB_DM_LOCAL_ROUTING_REGION} />
    <autoroutingphase phaseIndex={4} region={USB_DP_LOCAL_ROUTING_REGION} />
    <autoroutingphase
      phaseIndex={5}
      region={VBUS_ROUTING_REGION}
      minTraceWidth="0.2mm"
      minViaPadDiameter="0.8mm"
      minViaHoleDiameter="0.5mm"
    />
    <autoroutingphase phaseIndex={6} region={RP_POWER_ROUTING_REGION} />
    <autoroutingphase
      phaseIndex={7}
      region={RP_POWER_ROUTING_REGION}
      minTraceWidth="0.2mm"
      minViaPadDiameter="0.8mm"
      minViaHoleDiameter="0.5mm"
    />
    <autoroutingphase phaseIndex={8} region={RP_DEBUG_RESET_ROUTING_REGION} />
    <autoroutingphase phaseIndex={9} region={SWCLK_ROUTING_REGION}
      minTraceWidth="0.25mm" />
    <autoroutingphase phaseIndex={11} region={SWD_ROUTING_REGION}
      minTraceWidth="0.25mm" />
    <autoroutingphase phaseIndex={12} region={UI_ROUTING_REGION}
      minTraceWidth="0.25mm" />
    <autoroutingphase phaseIndex={13} region={MCU_IO_ROUTING_REGION}
      minTraceWidth="0.25mm" />
    <autoroutingphase phaseIndex={14} region={MCU_IO_ROUTING_REGION} />

    {/* Explicit net phases keep dense local rails out of the sparse signal
        solve. The GND net is intentionally absent: direct one-port fanouts
        terminate at its plane, while authored multi-port ties keep their own
        local phase and must never synthesize an aggregate whole-board route. */}
    <net name="V5" routingPhaseIndex={5} />
    <net name="V3_3" routingPhaseIndex={7} />
    <net name="DVDD" routingPhaseIndex={6} />
    <net name="SWCLK" routingPhaseIndex={9} />
    <net name="SWD" routingPhaseIndex={11} />
    <net name="LED_NUDGE" routingPhaseIndex={12} />
    <net name="BTN_MUTE" routingPhaseIndex={12} />
    <net name="USB_DP" routingPhaseIndex={14} />
    <net name="USB_DM" routingPhaseIndex={14} />
    <net name="CAP_DRIVE" routingPhaseIndex={13} />
    <net name="CAP_A" routingPhaseIndex={14} />
    <net name="CAP_B" routingPhaseIndex={14} />
    <net name="CAP_A_SENSE" routingPhaseIndex={13} />
    <net name="CAP_B_SENSE" routingPhaseIndex={13} />

    {/* Bottom is the continuous return plane. The top bridge deliberately
        stops below y=-3 so it cannot overlap the two capacitive electrodes.
        The explicit lower-half lattice makes the two solved pour islands one
        electrical return structure at the product's 10mm density target;
        every coordinate stays inside both authored faces, and the physical
        pour gate must still prove that each via lands in solved GND copper.
        Every one-pad GND drop routes in phase 10. */}
    <GndPlanes
      pours={[
        {
          name: "GND_BRIDGE_TOP",
          layer: "top",
          boardEdgeMarginMm: 0.25,
          outline: [
            { x: -39, y: -39 },
            { x: 39, y: -39 },
            { x: 39, y: -3 },
            { x: -39, y: -3 },
          ],
        },
        { name: "GND_BOTTOM", layer: "bottom" },
      ]}
      fanoutLayers={["top", "bottom"]}
      fanoutBoundaryPaddingMm={1}
      busFanoutDirections={{
        TR_U3_gnd: "bottom_center",
        TR_C6_g: "bottom_center",
        TR_C10_g: "top_center",
      }}
      stitchingVias={[
        { x: -34, y: -34 },
        { x: -24, y: -34 },
        { x: -14, y: -34 },
        { x: -6, y: -34 },
        { x: 6, y: -34 },
        { x: 16, y: -34 },
        { x: 26, y: -34 },
        { x: 34, y: -34 },
        { x: -34, y: -24 },
        { x: -30, y: -24 },
        { x: -12, y: -24 },
        { x: -4, y: -24 },
        { x: 6, y: -24 },
        { x: 16, y: -24 },
        { x: 22, y: -24 },
        { x: 34, y: -24 },
        { x: -34, y: -14 },
        { x: -24, y: -14 },
        { x: -14, y: -14 },
        { x: -4, y: -14 },
        { x: 6, y: -14 },
        { x: 20, y: -14 },
        { x: 34, y: -14 },
        { x: 30, y: -10 },
        { x: -34, y: -4 },
        { x: -24, y: -4 },
        { x: -14, y: -4 },
        { x: -4, y: -4 },
        { x: 6, y: -4 },
        { x: 16, y: -4 },
        { x: 26, y: -4 },
        { x: 34, y: -4 },
      ]}
    />
    {/* ---- power entry: USB-C on the bottom edge, 5V + the USB 2.0 pair ---- */}
    <UsbCData
      pcbX={0}
      pcbY={-34}
      schX={-46}
      schY={0}
      localRoutingPhaseIndex={2}
      dmConnectorRoutingPhaseIndex={3}
      dpConnectorRoutingPhaseIndex={4}
      externalPowerTrunkPort="VBUS1"
    />
    <PowerTrunk
      name="V5_ENTRY"
      source=".J1 > .VBUS1"
      net="V5"
      start={{ x: 4.5, y: -30 }}
      end={{ x: 9.5, y: -22 }}
      startTestpoint="TP4"
      endTestpoint="TP5"
      trunkWidthMm={0.8}
      neckdownWidthMm={0.2}
    />

    {/* ---- logic rail: V5 -> V3_3 ----------------------------------------
        Rotating the regulator puts VIN on the connector-facing lower-left and
        VOUT on the MCU-facing left. That creates two unobstructed, measurable
        0.8mm corridors without moving the USB/data or RP critical clusters. */}
    <group pcbX={14} pcbY={-18} pcbRotation={180}>
      <Ldo3v3
        pcbX={0}
        pcbY={0}
        schX={-46}
        schY={22}
        externalPowerTrunkPort="VOUT"
      />
    </group>
    <PowerTrunk
      name="V3_3_REG"
      source=".U2 > .VOUT"
      net="V3_3"
      start={{ x: 8.5, y: -18 }}
      end={{ x: -14, y: -18 }}
      startTestpoint="TP6"
      endTestpoint="TP7"
      trunkWidthMm={0.8}
      neckdownWidthMm={0.2}
    />

    {/* ---- the brain, turned to face its neighbours ------------------------ */}
    <group pcbRotation={180} pcbX={-20} pcbY={-22} schX={0} schY={0}>
      <Rp2040Core
        pcbX={0}
        pcbY={0}
        schX={0}
        schY={0}
        debugPortPcbX={-14}
        debugPortPcbY={-6}
        debugSwclkEscapeRef="TP8"
        debugSwdEscapeRef="TP9"
        powerLocalRoutingPhaseIndex={6}
        debugResetRoutingPhaseIndex={8}
      />
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

    <trace name="TR_CAPDRV" from=".U3 > .GPIO2" to="net.CAP_DRIVE" thickness="0.25mm" />
    <trace name="TR_R30_drv" from=".R30 > .pin1" to="net.CAP_DRIVE" thickness="0.25mm" />
    <trace name="TR_R30_ea" from=".R30 > .pin2" to="net.CAP_A" />
    <trace name="TR_R31_drv" from=".R31 > .pin1" to="net.CAP_DRIVE" thickness="0.25mm" />
    <trace name="TR_R31_eb" from=".R31 > .pin2" to="net.CAP_B" />
    <trace name="TR_R32_ea" from=".R32 > .pin1" to="net.CAP_A" />
    <trace name="TR_R32_mcu" from=".R32 > .pin2" to="net.CAP_A_SENSE" thickness="0.25mm" />
    <trace name="TR_R33_eb" from=".R33 > .pin1" to="net.CAP_B" />
    <trace name="TR_R33_mcu" from=".R33 > .pin2" to="net.CAP_B_SENSE" thickness="0.25mm" />
    <trace name="TR_SENSE_A" from=".U3 > .GPIO3" to="net.CAP_A_SENSE" thickness="0.25mm" />
    <trace name="TR_SENSE_B" from=".U3 > .GPIO4" to="net.CAP_B_SENSE" thickness="0.25mm" />

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
    <StatusLed led="LED1" r="R20" rail="V3_3" pcbX={-11} pcbY={-35.5} schX={30} schY={22}
      localRoutingPhaseIndex={12} railTraceThicknessMm={0.2} />
    <StatusLed led="LED2" r="R21" rail="LED_NUDGE" pcbX={-25} pcbY={-35.5} schX={38} schY={22}
      localRoutingPhaseIndex={12} railTraceThicknessMm={0.25} />
    <trace name="TR_LED_NUDGE" from=".U3 > .GPIO0" to="net.LED_NUDGE" thickness="0.25mm" />

    {/* ---- the shut-up button, front-right corner, clear of the mug -------
        Active low into GPIO1 with the RP2040's internal pull-up. */}
    <SwTact name="SW1" signal="BTN_MUTE" pcbX={29} pcbY={-24} schX={30} schY={-14}
      localRoutingPhaseIndex={12} signalTraceThicknessMm={0.25} />
    <trace name="TR_BTN_MUTE" from=".U3 > .GPIO1" to="net.BTN_MUTE" thickness="0.25mm" />

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
    <silkscreentext text="AGENT" pcbX={-25.5} pcbY={-38} fontSize={1.2} />
    <silkscreentext text="MUTE" pcbX={29} pcbY={-19} fontSize={1.2} />
  </board>
)
