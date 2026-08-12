export default () => (
  <board
    width="30mm"
    height="20mm"
    minTraceWidth="0.2mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
  >
    <autoroutingphase
      phaseIndex={10}
      autorouter="fanout"
      fanoutPourNetMap={{ top: "GND" }}
    />
    <copperpour layer="top" connectsTo="net.GND" cutoutMargin="0.25mm" />

    <capacitor
      name="C1"
      capacitance="100nF"
      maxDecouplingTraceLength="1mm"
      footprint="0402"
      pcbX={-5}
      pcbY={0}
    />
    <capacitor
      name="C2"
      capacitance="100nF"
      maxDecouplingTraceLength="1mm"
      footprint="0402"
      pcbX={5}
      pcbY={0}
    />
    <testpoint
      name="TP1"
      footprintVariant="pad"
      padShape="circle"
      padDiameter="1mm"
      pcbX={10}
      pcbY={-5}
    />
    <testpoint
      name="TP2"
      footprintVariant="pad"
      padShape="circle"
      padDiameter="1mm"
      pcbX={0}
      pcbY={7}
    />

    {/* This fixture isolates max-length inference.  Keep capacitor-terminal
        edges pad-compatible; the separate explicit-width regression owns the
        0.8mm trunk/0.2mm neck contract. */}
    <trace
      name="TR_CAP_RAIL"
      from=".C1 > .pin1"
      to=".C2 > .pin1"
      thickness="0.2mm"
    />
    <trace
      name="TR_RAIL_BOUNDARY"
      from=".C2 > .pin1"
      to="net.VDD"
      thickness="0.2mm"
      authoredNetTreeBoundary
    />
    <trace name="TR_VDD_LOAD" from=".TP1 > .pin1" to="net.VDD" />
    <trace
      name="TR_C1_GND"
      from=".C1 > .pin2"
      to="net.GND"
      routingPhaseIndex={10}
      maxLength="1mm"
    />
    <trace
      name="TR_C2_GND"
      from=".C2 > .pin2"
      to="net.GND"
      routingPhaseIndex={10}
      maxLength="1mm"
    />
    <trace name="TR_REMOTE_GND" from=".TP2 > .pin1" to="net.GND" />
  </board>
)
