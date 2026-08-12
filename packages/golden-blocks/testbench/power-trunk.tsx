import { PowerTrunk } from "../blocks/glue"

export default () => (
  <board
    width="22mm"
    height="12mm"
    thickness="1.6mm"
    minTraceWidth="0.15mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
  >
    <testpoint
      name="TP903"
      footprintVariant="pad"
      padShape="circle"
      padDiameter="1mm"
      doNotPlace={true}
      pcbX={-9}
      pcbY={-3}
    />
    <testpoint
      name="TP904"
      footprintVariant="pad"
      padShape="circle"
      padDiameter="1mm"
      doNotPlace={true}
      pcbX={9}
      pcbY={-3}
    />
    <testpoint
      name="TP907"
      footprintVariant="pad"
      padShape="circle"
      padDiameter="1mm"
      doNotPlace={true}
      pcbX={-9}
      pcbY={3}
    />
    <testpoint
      name="TP908"
      footprintVariant="pad"
      padShape="circle"
      padDiameter="1mm"
      doNotPlace={true}
      pcbX={9}
      pcbY={3}
    />
    <PowerTrunk
      name="V5_MAIN"
      source=".TP903 > .pin1"
      net="V5"
      start={{ x: -7.5, y: -3 }}
      end={{ x: 7.5, y: -3 }}
      startTestpoint="TP901"
      endTestpoint="TP902"
      trunkWidthMm={0.8}
      neckdownWidthMm={0.2}
    />
    <trace name="TR_TP904_V5" from=".TP904 > .pin1" to="net.V5" />
    <PowerTrunk
      name="V3V3_MAIN"
      source=".TP907 > .pin1"
      net="V3_3"
      start={{ x: -7.5, y: 3 }}
      end={{ x: 7.5, y: 3 }}
      startTestpoint="TP905"
      endTestpoint="TP906"
      trunkWidthMm={0.8}
      neckdownWidthMm={0.2}
    />
    <trace name="TR_TP908_V3V3" from=".TP908 > .pin1" to="net.V3_3" />
  </board>
)
