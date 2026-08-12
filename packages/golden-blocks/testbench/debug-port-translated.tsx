import { DebugPort } from "../blocks/glue"

/**
 * Reproduces the RP2040 composition shape: DebugPort is nested in a block at
 * a nonzero board coordinate, and its own coordinates are block-local.  The
 * bottom instance is the exact X-mirror of the top instance.
 */
export default () => (
  <board width="40mm" height="30mm" thickness="1.6mm" routingDisabled>
    <group pcbX={-3} pcbY={3} schX={-10} schY={5}>
      <DebugPort
        layer="top"
        pcbX={15}
        pcbY={-14}
        schX={4}
        schY={-2}
        swclkName="TP_TOP_CLK"
        swdName="TP_TOP_DIO"
        gndName="TP_TOP_GND"
        swclkNet="TOP_SWCLK"
        swdNet="TOP_SWD"
      />
    </group>
    <group pcbX={3} pcbY={3} schX={10} schY={5}>
      <DebugPort
        layer="bottom"
        pcbX={-15}
        pcbY={-14}
        schX={-4}
        schY={-2}
        swclkName="TP_BOTTOM_CLK"
        swdName="TP_BOTTOM_DIO"
        gndName="TP_BOTTOM_GND"
        swclkNet="BOTTOM_SWCLK"
        swdNet="BOTTOM_SWD"
      />
    </group>
  </board>
)
