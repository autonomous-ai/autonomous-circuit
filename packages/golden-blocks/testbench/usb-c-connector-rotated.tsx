import { UsbCConnector } from "../blocks/usb-c-power/usb-c-power"

// Isolated mechanical transform fixture.  UsbCPower/UsbCData prove the
// electrical top/bottom contract; this fixture pins the imported connector's
// component-local alignment drills and guards through a non-zero rotation.
export default () => (
  <board width="24mm" height="24mm" thickness="1.6mm" routingDisabled>
    <UsbCConnector
      name="J90"
      pcbX={0}
      pcbY={0}
      pcbRotation={90}
      ncPins={[
        "VBUS1", "VBUS2", "CC1", "CC2", "DP1", "DM1", "DP2", "DM2",
        "SBU1", "SBU2", "GND1", "GND2",
        "SHELL1", "SHELL2", "SHELL3", "SHELL4",
      ]}
    />
  </board>
)
