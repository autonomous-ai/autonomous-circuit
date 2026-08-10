import { Ws2812Chain } from "../blocks/ws2812-chain/ws2812-chain"
export default () => (
  <board width="40mm" height="16mm" thickness="1.6mm"
    minTraceWidth="0.2mm" minViaPadDiameter="0.6mm" minViaHoleDiameter="0.3mm">
    <Ws2812Chain count={4} pcbX={-10} pcbY={0} />
  </board>
)
