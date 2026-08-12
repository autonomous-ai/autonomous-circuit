export default () => (
  <board
    width="20mm"
    height="12mm"
    minTraceWidth="0.2mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    <autoroutingphase
      phaseIndex={0}
      autorouter="fanout"
      fanoutPourNetMap={{ bottom: "GND" }}
    />
    <copperpour layer="bottom" connectsTo="net.GND" cutoutMargin="0.25mm" />

    <testpoint
      name="TP_POWER"
      footprintVariant="pad"
      padShape="circle"
      padDiameter="1mm"
      pcbX={-4}
      pcbY={0}
    />
    <testpoint
      name="TP_SIGNAL"
      footprintVariant="pad"
      padShape="circle"
      padDiameter="1mm"
      pcbX={4}
      pcbY={0}
    />

    <group pcbStyle={{ viaPadDiameter: "0.8mm", viaHoleDiameter: "0.5mm" }}>
      <trace
        name="TR_POWER_FANOUT"
        from=".TP_POWER > .pin1"
        to="net.GND"
        routingPhaseIndex={0}
      />
    </group>
    <group pcbStyle={{ viaPadDiameter: "0.4mm", viaHoleDiameter: "0.2mm" }}>
      <trace
        name="TR_SIGNAL_FANOUT"
        from=".TP_SIGNAL > .pin1"
        to="net.GND"
        routingPhaseIndex={0}
      />
    </group>
  </board>
)
