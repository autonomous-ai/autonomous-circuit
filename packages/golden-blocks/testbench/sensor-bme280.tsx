import { SensorBme280 } from "../blocks/sensor-bme280/sensor-bme280"
export default () => (
  <board width="14mm" height="12mm" thickness="1.6mm"
    minTraceToPadEdgeClearance="0.15mm" minViaEdgeToPadEdgeClearance="0.15mm">
    <SensorBme280 pcbX={0} pcbY={0} />
  </board>
)
