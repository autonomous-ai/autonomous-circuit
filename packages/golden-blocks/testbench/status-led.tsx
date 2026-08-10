import { StatusLed } from "../blocks/status-led/status-led"
export default () => (
  <board width="12mm" height="12mm" thickness="1.6mm">
    <StatusLed pcbX={0} pcbY={-1} />
  </board>
)
