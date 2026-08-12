import { SensorBme280 } from "../blocks/sensor-bme280/sensor-bme280"

export default () => (
  <board width="14mm" height="12mm" thickness="1.6mm" routingDisabled>
    <SensorBme280 layer="bottom" pcbX={0} pcbY={0} />
  </board>
)
