export default () => (
  <board
    width="30mm"
    height="20mm"
    minTraceWidth="0.2mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
  >
    <testpoint name="TP1" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={-11} pcbY={-5} />
    <testpoint name="TP2" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={-8} pcbY={1} />
    <testpoint name="TP3" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={-5} pcbY={-4} />
    <trace name="TR_A1_BOUNDARY" from=".TP1 > .pin1" to=".TP3 > .pin1" thickness="0.21mm" maxLength="8mm" />
    <trace name="TR_A2_BOUNDARY" from=".TP2 > .pin1" to=".TP3 > .pin1" thickness="0.22mm" maxLength="8mm" />
    <trace name="TR_A_BOUNDARY" from=".TP3 > .pin1" to="net.PWR" thickness="0.8mm" authoredNetTreeBoundary />

    <testpoint name="TP4" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={5} pcbY={-4} />
    <testpoint name="TP5" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={8} pcbY={1} />
    <testpoint name="TP6" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={11} pcbY={-5} />
    <trace name="TR_B1_BOUNDARY" from=".TP4 > .pin1" to=".TP6 > .pin1" thickness="0.23mm" maxLength="8mm" />
    <trace name="TR_B2_BOUNDARY" from=".TP5 > .pin1" to=".TP6 > .pin1" thickness="0.24mm" maxLength="8mm" />
    <trace name="TR_B_BOUNDARY" from=".TP6 > .pin1" to="net.PWR" thickness="0.7mm" authoredNetTreeBoundary />

    <testpoint name="TP7" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={0} pcbY={7} />
    <trace name="TR_UNMARKED_LOAD" from=".TP7 > .pin1" to="net.PWR" />
  </board>
)
