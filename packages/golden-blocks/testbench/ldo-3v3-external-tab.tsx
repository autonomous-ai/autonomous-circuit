import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"

export default () => (
  <board width="22mm" height="20mm" thickness="1.6mm"
    minTraceToPadEdgeClearance="0.15mm" minViaEdgeToPadEdgeClearance="0.15mm">
    <Ldo3v3 pcbX={-1} pcbY={2} externalPowerTrunkPort="TAB" />
    {/* Minimal board-owned wide boundary. PowerTrunk uses the same marked
        edge after its fixed corridor; this bench isolates the LDO API and
        proves TAB cannot retain a second internal V3_3 escape. */}
    <trace name="TR_BOARD_V3_TAB_BOUNDARY" from=".U2 > .TAB" to="net.V3_3"
      thickness="0.8mm" authoredNetTreeBoundary />
  </board>
)
