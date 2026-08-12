import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { GndPour, MountingHole } from "../blocks/glue"
export default () => (
  <board width="30mm" height="40mm" thickness="1.6mm" routingDisabled>
    <UsbCData pcbX={0} pcbY={-7}
      vbusBoundaryRefs={{ right: "N3", left: "N4" }} vbusRailNodeRef="N15"
      vbusClampNodeRef="N16"
      pairRules={{
        pcbTraceGapMm: 0.15,
        maxLengthSkewMm: 3.8,
        maxUncoupledLengthMm: 3,
      }} />
    {/* GndPour, not a bare <copperpour>: the pour solver cuts a 32-gon around
        every hole, so the raw 0.2mm default lands at 0.1976mm from the USB-C
        alignment drills — under the fab floor. See blocks/glue.tsx. */}
    <GndPour layer="bottom" />
    {/* One mounting hole so the bench exercises the keepout that MountingHole
        ships; a bare <hole> is invisible to the router. */}
    <MountingHole name="H1" diameter={3.2} pcbX={-11} pcbY={15} />
  </board>
)
