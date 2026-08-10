import { I2cBus } from "../blocks/i2c-bus/i2c-bus"
export default () => (
  <board width="12mm" height="10mm" thickness="1.6mm">
    <I2cBus pcbX={-1} pcbY={0} />
  </board>
)
