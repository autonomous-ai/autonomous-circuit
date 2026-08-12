export default () => (
  <board width="24mm" height="14mm" minTraceWidth="0.2mm">
    <testpoint name="TP1" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={-8} pcbY={0} />
    <testpoint name="TP2" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={0} pcbY={0} />
    <testpoint name="TP3" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={8} pcbY={0} />
    <trace name="TR_EDGE_1" from=".TP1 > .pin1" to=".TP2 > .pin1" />
    <trace name="TR_EDGE_2" from=".TP2 > .pin1" to=".TP3 > .pin1" />
    <trace name="TR_EDGE_3" from=".TP3 > .pin1" to=".TP1 > .pin1" />
    <trace name="TR_RAIL_BOUNDARY" from=".TP3 > .pin1" to="net.V3_3" authoredNetTreeBoundary />
  </board>
)
