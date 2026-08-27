/**
 * golden-block: servo-header (v1)
 * dialect: tscircuit@0.0.2279 (pinned — repo toolchain/package.json)
 *
 * A 3-pin 2.54mm male header per hobby servo: BX_PM2_54_1_3PY
 * (LCSC C18078126). One per servo, or `count` of them in a row.
 *
 * **The pin order is the whole safety argument, so it is cited, not
 * remembered.** A hobby servo lead is three wires and the order is universal
 * across Futaba, JR and Hitec:
 *
 *     pin1  GND     (black or brown)
 *     pin2  V+      (red)            <- ALWAYS the middle pin
 *     pin3  SIGNAL  (white, yellow or orange)
 *
 * rchelicopterfun.com/rc-servo-connectors.html, read 2026-08-26:
 * *"The positive (red wire) is always in the middle of 3 pin/wire servo
 * connectors."* The same page records that connectors are otherwise
 * interchangeable between brands — Futaba adds an alignment rib on the signal
 * side of the female shell and nothing else changes.
 *
 * That invariant is why V+ is on the middle pin here and must stay there: a
 * lead plugged in backwards then swaps GND and SIGNAL, which is recoverable.
 * Put V+ on an outer pin and a reversed lead feeds the rail into the servo's
 * signal input, which is not.
 *
 * The part's own numbering puts pin2 in the middle (EasyEDA pad table for
 * C18078126: pin1 at x=-2.54mm, pin2 at x=0, pin3 at x=+2.54mm), so the
 * convention and the footprint agree without a translation step — unlike
 * `sw-tact`, where they did not.
 *
 * Land pattern: the exact EasyEDA footprint for C18078126, imported with
 * `tscircuit-cli import --jlcpcb` on 2026-08-26. The tool measured
 * footprinter's best guess (`pinrow3_p2.54mm`) at **95.84% copper IoU** and
 * kept EasyEDA's on that basis. Three plated holes, 2.54mm pitch, 1.0200mm
 * hole on a 1.5748mm pad — clearance for the 0.64mm square pins a servo shell
 * expects.
 *
 * **This block is a connector, not a power supply.** It carries no bulk
 * capacitance, no regulator and no current limit; `rail` is wired straight
 * through to pin2. A hobby servo stalls at currents this repo has no table
 * for, and `ldo-3v3` budgets 500mA total, so **more than one servo does not
 * belong downstream of that block.** Feed `rail` from a supply sized for the
 * servos and treat that sizing as an open question until somebody measures it.
 *
 * Default refdes (global v1 allocation): J10, J11, … Signals default to
 * SERVO1..n. `pcbY` steps by `pitch` (default 5.08mm — the 3.04mm courtyard plus
 * clearance) so a **column** down one board edge does not overlap. A column
 * and not a row, because pin1 points x- and that is the edge this connector
 * belongs to.
 */

export const ServoHeader = (props: {
  name?: string
  signal?: string
  rail?: string
  ground?: string
  pcbX?: number
  pcbY?: number
  pcbRotation?: number | string
  schX?: number
  schY?: number
}) => {
  const j = props.name ?? "J10"
  const signal = props.signal ?? "SERVO1"
  const rail = props.rail ?? "V5"
  const ground = props.ground ?? "GND"
  return (
    <group
      pcbX={props.pcbX ?? 0}
      pcbY={props.pcbY ?? 0}
      schX={props.schX ?? 0}
      schY={props.schY ?? 0}
    >
      <connector
        name={j}
        supplierPartNumbers={{ jlcpcb: ["C18078126"] }}
        manufacturerPartNumber="BX_PM2_54_1_3PY"
        pinLabels={{ pin1: ["GND"], pin2: ["VPLUS"], pin3: ["SIG"] }}
        pinAttributes={{
          VPLUS: { requiresPower: true },
          GND: { requiresGround: true },
        }}
        footprint={
          <footprint>
            <platedhole portHints={["pin1"]} pcbX="-2.54mm" pcbY="0mm" outerDiameter="1.5748mm" holeDiameter="1.0200132mm" shape="circle" />
            <platedhole portHints={["pin2"]} pcbX="0mm" pcbY="0mm" outerDiameter="1.5748mm" holeDiameter="1.0200132mm" shape="circle" />
            <platedhole portHints={["pin3"]} pcbX="2.54mm" pcbY="0mm" outerDiameter="1.5748mm" holeDiameter="1.0200132mm" shape="circle" />
            <silkscreenpath route={[{ x: -1.27, y: 1.25 }, { x: -1.27, y: -1.25 }]} />
            <silkscreenpath route={[{ x: -4.06, y: -1.25 }, { x: 4.06, y: -1.25 }]} />
            <silkscreenpath route={[{ x: -4.06, y: 1.25 }, { x: -4.06, y: -1.25 }]} />
            <silkscreenpath route={[{ x: 4.06, y: 1.25 }, { x: -4.06, y: 1.25 }]} />
            <silkscreenpath route={[{ x: 4.06, y: -1.25 }, { x: 4.06, y: 1.25 }]} />
            <silkscreentext text="{NAME}" pcbX="0mm" pcbY="2.27mm" anchorAlignment="center" fontSize="1mm" />
            <courtyardoutline outline={[
              { x: -4.34, y: 1.52 }, { x: 4.31, y: 1.52 },
              { x: 4.31, y: -1.52 }, { x: -4.34, y: -1.52 }, { x: -4.34, y: 1.52 },
            ]} />
          </footprint>
        }
        pcbX={0}
        pcbY={0}
        pcbRotation={props.pcbRotation}
        schX={0}
        schY={0}
      />
      <trace name={`TR_${j}_gnd`} from={`.${j} > .GND`} to={`net.${ground}`} />
      <trace name={`TR_${j}_vplus`} from={`.${j} > .VPLUS`} to={`net.${rail}`} />
      <trace name={`TR_${j}_sig`} from={`.${j} > .SIG`} to={`net.${signal}`} />
    </group>
  )
}

/** `count` headers in a column, J10.. and SERVO1.. by default. */
export const ServoHeaderBank = (props: {
  count?: number
  startIndex?: number
  rail?: string
  ground?: string
  pitch?: number
  pcbRotation?: number | string
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
  const count = props.count ?? 4
  const start = props.startIndex ?? 10
  const pitch = props.pitch ?? 5.08
  return (
    <group>
      {Array.from({ length: count }, (_, i) => (
        <ServoHeader
          key={i}
          name={`J${start + i}`}
          signal={`SERVO${i + 1}`}
          rail={props.rail}
          ground={props.ground}
          pcbX={props.pcbX ?? 0}
          pcbY={(props.pcbY ?? 0) + i * pitch}
          pcbRotation={props.pcbRotation}
          schX={props.schX ?? 0}
          schY={(props.schY ?? 0) - i * 3}
        />
      ))}
    </group>
  )
}

export default ServoHeader
