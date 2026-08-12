export default () => (
  <board width="24mm" height="14mm" minTraceWidth="0.2mm">
    <testpoint name="TP1" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={-8} pcbY={0} />
    <testpoint name="TP2" footprintVariant="pad" padShape="circle" padDiameter="1mm" pcbX={-3} pcbY={0} />
    <trace name="TR_LOCAL" from=".TP1 > .pin1" to=".TP2 > .pin1" />
    <trace name="TR_MARKED_BOUNDARY" from=".TP2 > .pin1" to="net.V3_3" authoredNetTreeBoundary />
    <trace name="TR_SECOND_BOUNDARY" from=".TP1 > .pin1" to="net.V3_3" />
  </board>
)
