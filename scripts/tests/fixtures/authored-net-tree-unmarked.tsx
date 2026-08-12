export default () => (
  <board
    width="24mm"
    height="18mm"
    minTraceWidth="0.2mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
  >
    <testpoint name="TP1" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={-9} pcbY={0} />
    <testpoint name="TP2" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={-3} pcbY={6} />
    <testpoint name="TP3" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={3} pcbY={0} />
    <testpoint name="TP4" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={9} pcbY={6} />
    <trace name="TR_LOCAL_1" from=".TP1 > .pin1" to=".TP3 > .pin1" thickness="0.21mm" />
    <trace name="TR_LOCAL_2" from=".TP3 > .pin1" to=".TP2 > .pin1" thickness="0.22mm" />
    <trace name="TR_LOCAL_3" from=".TP2 > .pin1" to=".TP4 > .pin1" thickness="0.23mm" />
    <trace name="TR_RAIL_BOUNDARY" from=".TP4 > .pin1" to="net.V3_3" thickness="0.3mm" />
  </board>
)
