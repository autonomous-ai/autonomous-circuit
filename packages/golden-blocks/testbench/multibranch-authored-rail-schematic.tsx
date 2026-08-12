import { Rp2040Chip, W25q128 } from "../blocks/rp2040-core/rp2040-core"
import { MaskedCopperNode } from "../blocks/glue"
import { TactileButton } from "../blocks/sw-tact/sw-tact"

type RailLeaf = {
  node: string
  capacitor: string
  pin: string
  x: number
  y: number
  capX: number
  capY: number
}

const V3_HUB = { x: -35, y: 0 } as const
const DVDD_HUB = { x: 35, y: 0 } as const
const V3_PINS = [
  "IOVDD6", "IOVDD5", "IOVDD4", "IOVDD3", "IOVDD2",
  "ADC_AVDD", "IOVDD1", "USB_VDD", "VREG_IN",
] as const
const DVDD_PINS = ["DVDD1", "VREG_VOUT", "DVDD2"] as const

const radialLeaves = (
  pins: readonly string[],
  startRef: number,
  hub: { x: number; y: number },
  radius: number,
): RailLeaf[] => pins.map((pin, index) => {
  const angle = (index / pins.length) * Math.PI * 2
  return {
    node: `N${startRef + index}`,
    capacitor: `C${startRef + index}`,
    pin,
    x: hub.x + Math.cos(angle) * radius,
    y: hub.y + Math.sin(angle) * radius,
    capX: hub.x + Math.cos(angle) * (radius + 2.4),
    capY: hub.y + Math.sin(angle) * (radius + 2.4),
  }
})

const V3_LEAVES = radialLeaves(V3_PINS, 1, V3_HUB, 9)
const DVDD_LEAVES = radialLeaves(DVDD_PINS, 11, DVDD_HUB, 9)

const FixedSpoke = (props: {
  leaf: RailLeaf
  hubRef: string
  hub: { x: number; y: number }
  rail: string
}) => {
  const dx = props.leaf.x - props.hub.x
  const dy = props.leaf.y - props.hub.y
  const length = Math.hypot(dx, dy)
  const via = { x: (dx / length) * 0.8, y: (dy / length) * 0.8 }
  return <trace
    name={`TR_${props.leaf.node}_${props.rail}_HUB`}
    from={`.${props.leaf.node} > .pin1`}
    to={`.${props.hubRef} > .pin1`}
    thickness="0.8mm"
    pcbPathRelativeTo={`.${props.leaf.node} > .pin1`}
    pcbPath={[
      { x: 0, y: 0 }, via,
      { ...via, via: true, fromLayer: "top", toLayer: "bottom" }, via,
      { x: props.hub.x - props.leaf.x, y: props.hub.y - props.leaf.y },
    ]}
  />
}

const LocalBranch = (props: { leaf: RailLeaf }) => <>
  <capacitor name={props.leaf.capacitor} capacitance="100nF" footprint="0402"
    layer="top" pcbX={props.leaf.capX} pcbY={props.leaf.capY} />
  <MaskedCopperNode name={props.leaf.node} diameterMm={0.4} layer="top"
    pcbX={props.leaf.x} pcbY={props.leaf.y} />
  <trace name={`TR_U3_${props.leaf.pin}_${props.leaf.capacitor}`}
    from={`.U3 > .${props.leaf.pin}`} to={`.${props.leaf.capacitor} > .pin1`}
    thickness="0.2mm" />
  <trace name={`TR_${props.leaf.capacitor}_${props.leaf.node}_NECK`}
    from={`.${props.leaf.capacitor} > .pin1`} to={`.${props.leaf.node} > .pin1`}
    thickness="0.2mm" />
  <trace name={`TR_${props.leaf.capacitor}_GND`}
    from={`.${props.leaf.capacitor} > .pin2`} to="net.GND" thickness="0.2mm" />
</>

export const MultibranchAuthoredRailSchematic = ({
  schAutoLayoutEnabled = true,
}: {
  schAutoLayoutEnabled?: boolean
}) => <board width="110mm" height="70mm" routingDisabled
  schAutoLayoutEnabled={schAutoLayoutEnabled}
  minTraceWidth="0.15mm"
  minTraceToPadEdgeClearance="0.15mm"
  minViaEdgeToPadEdgeClearance="0.15mm"
  minViaPadDiameter="0.6mm"
  minViaHoleDiameter="0.3mm">
  <Rp2040Chip name="U3" layer="top" pcbX={0} pcbY={0} />
  <W25q128 name="U4" layer="top" pcbX={0} pcbY={18} />
  <trace name="TR_QSPI_CS" from=".U3 > .QSPI_SS" to=".U4 > .CS" />
  <trace name="TR_QSPI_CLK" from=".U3 > .QSPI_SCLK" to=".U4 > .CLK" />
  <trace name="TR_QSPI_IO0" from=".U3 > .QSPI_SD0" to=".U4 > .IO0" />
  <trace name="TR_QSPI_IO1" from=".U3 > .QSPI_SD1" to=".U4 > .IO1" />
  <trace name="TR_QSPI_IO2" from=".U3 > .QSPI_SD2" to=".U4 > .IO2" />
  <trace name="TR_QSPI_IO3" from=".U3 > .QSPI_SD3" to=".U4 > .IO3" />
  <trace name="TR_U4_VCC" from=".U4 > .VCC" to="net.V3_3" />
  <trace name="TR_U4_GND" from=".U4 > .GND" to="net.GND" />
  <capacitor name="C14" capacitance="100nF" footprint="0402" pcbX={-8} pcbY={27} />
  <trace name="TR_C14_V3" from=".C14 > .pin1" to="net.V3_3" />
  <trace name="TR_C14_GND" from=".C14 > .pin2" to="net.GND" />
  <capacitor name="C17" capacitance="10uF" footprint="0805" pcbX={8} pcbY={27} />
  <trace name="TR_C17_V3" from=".C17 > .pin1" to="net.V3_3" />
  <trace name="TR_C17_GND" from=".C17 > .pin2" to="net.GND" />
  <resistor name="R13" resistance="1k" footprint="0402" pcbX={-6} pcbY={-18} />
  <TactileButton name="SW2" variant="standard" pcbX={10} pcbY={-18} />
  <trace name="TR_R13_V3" from=".R13 > .pin1" to="net.V3_3" />
  <trace name="TR_R13_SW2" from=".R13 > .pin2" to=".SW2 > .pin1" />
  <trace name="TR_SW2_GND2" from=".SW2 > .pin2" to="net.GND" />
  <trace name="TR_SW2_GND3" from=".SW2 > .pin3" to="net.GND" />
  <trace name="TR_SW2_GND4" from=".SW2 > .pin4" to="net.GND" />
  <trace name="TR_U3_GND" from=".U3 > .GND" to="net.GND" />
  <trace name="TR_U3_TESTEN" from=".U3 > .TESTEN" to="net.GND" />
  <trace name="TR_U3_RUN" from=".U3 > .RUN" to="net.V3_3" />
  {V3_LEAVES.map((leaf) => <LocalBranch key={leaf.node} leaf={leaf} />)}
  {DVDD_LEAVES.map((leaf) => <LocalBranch key={leaf.node} leaf={leaf} />)}
  <MaskedCopperNode name="N10" diameterMm={0.8} layer="bottom"
    pcbX={V3_HUB.x} pcbY={V3_HUB.y} />
  <MaskedCopperNode name="N14" diameterMm={0.8} layer="bottom"
    pcbX={DVDD_HUB.x} pcbY={DVDD_HUB.y} />
  <group pcbStyle={{ viaPadDiameter: "0.8mm", viaHoleDiameter: "0.5mm" }}>
    {V3_LEAVES.map((leaf) => <FixedSpoke key={leaf.node} leaf={leaf}
      hubRef="N10" hub={V3_HUB} rail="V3" />)}
    {DVDD_LEAVES.map((leaf) => <FixedSpoke key={leaf.node} leaf={leaf}
      hubRef="N14" hub={DVDD_HUB} rail="DVDD" />)}
  </group>
  <trace name="TR_V3_ESCAPE" from=".N10 > .pin1" to="net.V3_3"
    thickness="0.8mm" authoredNetTreeBoundary />
  <trace name="TR_DVDD_ESCAPE" from=".N14 > .pin1" to="net.DVDD"
    thickness="0.8mm" authoredNetTreeBoundary />
</board>

export default () => <MultibranchAuthoredRailSchematic />
