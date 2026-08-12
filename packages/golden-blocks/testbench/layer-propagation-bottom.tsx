import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
import { SensorBme280 } from "../blocks/sensor-bme280/sensor-bme280"
import { StatusLed } from "../blocks/status-led/status-led"
import { SwTact } from "../blocks/sw-tact/sw-tact"
import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { UsbPowerEntry } from "../blocks/usb-power-entry/usb-power-entry"
import { Ws2812LevelShifter } from "../blocks/ws2812-level-shifter/ws2812-level-shifter"
import { Ws2812Chain } from "../blocks/ws2812-chain/ws2812-chain"

export default () => (
  <board width="180mm" height="120mm" thickness="1.6mm" routingDisabled>
    <UsbCData layer="bottom" pcbX={-65} pcbY={-25}
      vbusBoundaryRefs={{ right: "N21", left: "N22" }} vbusRailNodeRef="N23"
      vbusClampNodeRef="N24"
      pairRules={{
        pcbTraceGapMm: 0.15,
        maxLengthSkewMm: 3.8,
        maxUncoupledLengthMm: 3,
      }} />
    <UsbPowerEntry layer="bottom" pcbX={-45} pcbY={-25} />
    <Ldo3v3 layer="bottom" pcbX={-25} pcbY={-25} />
    <StatusLed layer="bottom" pcbX={5} pcbY={-25} />
    <SwTact layer="bottom" pcbX={20} pcbY={-25} />
    <Ws2812LevelShifter layer="bottom" pcbX={-25} pcbY={15} />
    <SensorBme280 layer="bottom" pcbX={-5} pcbY={15} />
    <Ws2812Chain layer="bottom" count={4} pcbX={55} pcbY={-25} />
    <Rp2040Core
      layer="bottom"
      pcbX={35}
      pcbY={15}
      debugPortPcbX={-15}
      debugPortPcbY={15}
      debugSwclkBoundaryRef="N1"
      debugSwdBoundaryRef="N2"
      powerRailNodeRefs={{
        westUpper: "N5", westLower: "N6", south: "N7",
        eastLower: "N8", eastUpper: "N9", topRight: "N10",
        topMiddle: "N11", topLeft: "N12", bulk: "N13", flash: "N14",
        dvddLeft: "N15", dvddRight: "N16", dvddSouth: "N17",
        dvddJunction: "N18",
      }}
    />
  </board>
)
