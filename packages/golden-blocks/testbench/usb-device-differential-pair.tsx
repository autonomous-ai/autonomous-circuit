import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import {
  UsbCData,
  UsbDeviceDifferentialPair,
} from "../blocks/usb-c-data/usb-c-data"

const PAIR_RULES = {
  pcbTraceGapMm: 0.15,
  maxLengthSkewMm: 3.8,
  maxUncoupledLengthMm: 3,
} as const

export default () => (
  <board width="120mm" height="80mm" thickness="1.6mm" routingDisabled>
    <UsbCData
      pcbX={-25}
      pcbY={-15}
      vbusBoundaryRefs={{ right: "N3", left: "N4" }}
      vbusRailNodeRef="N15"
      vbusClampNodeRef="N16"
      pairRules={PAIR_RULES}
      emitMcuNetLeaves={false}
    />
    <Rp2040Core
      pcbX={20}
      pcbY={0}
      debugPortPcbX={15}
      debugPortPcbY={-15}
      debugSwclkBoundaryRef="N1"
      debugSwdBoundaryRef="N2"
      powerRailNodeRefs={{
        westUpper: "N5", westLower: "N6", south: "N7",
        eastLower: "N8", eastUpper: "N9", topRight: "N10",
        topMiddle: "N11", topLeft: "N12", bulk: "N13", flash: "N14",
        dvddLeft: "N17", dvddRight: "N18", dvddSouth: "N19",
        dvddJunction: "N20",
      }}
      emitUsbNetLeaves={false}
    />
    <UsbDeviceDifferentialPair
      mcu="U3"
      rDp="R3"
      rDm="R4"
      pairRules={PAIR_RULES}
    />
  </board>
)
