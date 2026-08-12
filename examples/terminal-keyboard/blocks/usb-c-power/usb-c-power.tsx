/**
 * golden-block: usb-c-power (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * USB-C 5V power input: TYPE-C-31-M-12 receptacle (LCSC C165948), 5.1k CC
 * pulldowns (UFP sink advertisement), USBLC6-2SC6 ESD array on the CC lines,
 * 10uF VBUS bulk. Exposes rails: net.V5 (VBUS), net.GND.
 *
 * Land pattern: exact EasyEDA footprint for C165948, imported once at
 * authoring time (tscircuit-cli import C165948 --jlcpcb, 2026-08-10) —
 * committed inline, zero network at build time.
 *
 * Default refdes (global v1 allocation): J1, R1, R2, U1, C1.
 * See BLOCK.md for the pin contract and provenance.
 */

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
        {/* The receptacle's belly is a routing keepout, and it is part of the
            footprint on purpose — it rotates and moves with J1, so a composer
            cannot place this connector wrongly.

            Why it has to exist: the two NPTH alignment holes above sit in the
            middle of an otherwise empty 7.3 x 1.2mm pocket, and the autorouter
            has no model of hole-to-copper clearance at all. Left alone it runs
            the shortest GND path straight through the pocket — measured
            0.115mm and 0.123mm from a 0.6mm drill where JLC needs 0.20mm
            (jlcpcb.com/capabilities). A drill lands inside its own positional
            tolerance of that gap, so some boards in a batch come back with the
            track cut and some do not. This one defect blocked all three
            example boards.

            The numbers. Hole centres (+/-2.899918, 0.9055672), r = 0.30.
            * Top edge 1.515 — 0.009mm under the pad row at y = 1.5240, so the
              keepout touches no pad, and a track that rides the edge still
              clears the drill by 1.515 + 0.10 - 0.9056 - 0.40 = 0.31mm.
            * Bottom edge 0.285 — same clearance on the south side.
            * Half-width 3.65 — the outer edge stops 0.075mm short of the pin-1
              shell pad at x = 3.725, which closes the 0.525mm channel between
              drill and shell that the router used to thread. Nothing legal
              fits there: a track would need x >= 3.50 for the drill and
              x <= 3.525 for the shell pad's own 0.28mm PTH rule.
            A keepout is the right tool only at this size. Sized to the holes
            instead (r = 0.65 circles) it swallows the GND pads' own corners at
            0.6186mm and every J1 tie becomes a violation; that is the trap the
            first two attempts fell into. */}
        <keepout shape="rect" pcbX="0mm" pcbY="0.90mm" width="7.30mm" height="1.23mm" layers={["top", "bottom"]} />
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
    pinAttributes={{
      VBUS: { requiresPower: true },
      GND: { requiresGround: true },
    }}
    supplierPartNumbers={{ jlcpcb: ["C2687116"] }}
    manufacturerPartNumber="USBLC6-2SC6"
    footprint="sot23_6"
  />
)

/**
 * The composed power-input block. Rails out: net.V5, net.GND.
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
  const vbus = props.vbusNet ?? "V5"
  return (
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
      <UsbCConnector name={j} pcbX={0} pcbY={0} schX={0} schY={0}
        ncPins={["DP1", "DM1", "DP2", "DM2", "SBU1", "SBU2"]} />
      <resistor name={r1} resistance="5.1k" footprint="0402" pcbX={-3} pcbY={7} schX={3} schY={-2.5}
        supplierPartNumbers={{ jlcpcb: ["C25905"] }} />
      <resistor name={r2} resistance="5.1k" footprint="0402" pcbX={3} pcbY={7} schX={3} schY={-3.5}
        supplierPartNumbers={{ jlcpcb: ["C25905"] }} />
      <Usblc6 name={u} pcbX={0} pcbY={9} schX={6} schY={-1} />
      <capacitor name={c} capacitance="10uF" footprint="0805" pcbX={7} pcbY={7} schX={6} schY={2}
        supplierPartNumbers={{ jlcpcb: ["C15850"] }} />

      {/* Rails */}
      <trace name={`TR_${j}_vbus1`} from={`.${j} > .VBUS1`} to={`net.${vbus}`} />
      <trace name={`TR_${j}_vbus2`} from={`.${j} > .VBUS2`} to={`net.${vbus}`} />
      <trace name={`TR_${j}_gnd1`} from={`.${j} > .GND1`} to="net.GND" />
      <trace name={`TR_${j}_gnd2`} from={`.${j} > .GND2`} to="net.GND" />
      <trace name={`TR_${j}_sh1`} from={`.${j} > .SHELL1`} to="net.GND" />
      <trace name={`TR_${j}_sh2`} from={`.${j} > .SHELL2`} to="net.GND" />
      <trace name={`TR_${j}_sh3`} from={`.${j} > .SHELL3`} to="net.GND" />
      <trace name={`TR_${j}_sh4`} from={`.${j} > .SHELL4`} to="net.GND" />

      {/* CC pulldowns: 5.1k to GND advertises a UFP sink (USB-C spec §4.5.1.2) */}
      <trace name={`TR_${j}_cc1r`} from={`.${j} > .CC1`} to={`.${r1} > .pin1`} />
      <trace name={`TR_${r1}_gnd`} from={`.${r1} > .pin2`} to="net.GND" />
      <trace name={`TR_${j}_cc2r`} from={`.${j} > .CC2`} to={`.${r2} > .pin1`} />
      <trace name={`TR_${r2}_gnd`} from={`.${r2} > .pin2`} to="net.GND" />

      {/* ESD: CC1 through channel 1, CC2 through channel 2 */}
      <trace name={`TR_${u}_cc1a`} from={`.${u} > .IO1`} to={`.${j} > .CC1`} />
      <trace name={`TR_${u}_cc1b`} from={`.${u} > .IO1B`} to={`.${j} > .CC1`} />
      <trace name={`TR_${u}_cc2a`} from={`.${u} > .IO2`} to={`.${j} > .CC2`} />
      <trace name={`TR_${u}_cc2b`} from={`.${u} > .IO2B`} to={`.${j} > .CC2`} />
      <trace name={`TR_${u}_vbus`} from={`.${u} > .VBUS`} to={`net.${vbus}`} />
      <trace name={`TR_${u}_gnd`} from={`.${u} > .GND`} to="net.GND" />

      {/* VBUS bulk (10uF cap keeps inrush inside the USB 2.0 limit) */}
      <trace name={`TR_${c}_vbus`} from={`.${c} > .pin1`} to={`net.${vbus}`} />
      <trace name={`TR_${c}_gnd`} from={`.${c} > .pin2`} to="net.GND" />
    </group>
  )
}

export default UsbCPower
