import { Rp2040Core } from "../blocks/rp2040-core/rp2040-core"
export default () => (
  <board width="46mm" height="42mm" thickness="1.6mm" routingDisabled>
    <Rp2040Core pcbX={-3} pcbY={3} />
  </board>
)
