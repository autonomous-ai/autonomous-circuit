import { UsbCPower } from "../blocks/usb-c-power/usb-c-power"

export default () => (
  <board width="24mm" height="30mm" thickness="1.6mm" routingDisabled>
    <UsbCPower
      layer="bottom"
      pcbX={0}
      pcbY={-6}
      vbusBoundaryRefs={{ right: "N3", left: "N4" }}
      vbusRailNodeRef="N15"
    />
  </board>
)
