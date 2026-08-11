import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
export default () => (
  <board width="30mm" height="40mm" thickness="1.6mm" routingDisabled>
    <UsbCData pcbX={0} pcbY={-7} />
    <copperpour layer="bottom" connectsTo="net.GND" />
  </board>
)
