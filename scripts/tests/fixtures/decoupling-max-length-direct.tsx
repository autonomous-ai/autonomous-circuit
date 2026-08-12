export default () => (
  <board width="30mm" height="16mm" minTraceWidth="0.2mm">
    <capacitor
      name="C1"
      capacitance="100nF"
      maxDecouplingTraceLength="1mm"
      footprint="0402"
      pcbX={-8}
      pcbY={2}
    />
    <chip
      name="U1"
      pinLabels={{ pin1: ["VDD"] }}
      pinAttributes={{ VDD: { requiresPower: true } }}
      footprint={
        <footprint>
          <smtpad
            portHints={["pin1"]}
            shape="rect"
            width="1mm"
            height="1mm"
          />
        </footprint>
      }
      pcbX={-2}
      pcbY={2}
    />
    <trace
      name="TR_EXPLICIT_LOCAL"
      from=".C1 > .pin1"
      to=".U1 > .VDD"
      maxLength="2mm"
    />

    <capacitor
      name="C2"
      capacitance="100nF"
      maxDecouplingTraceLength="1mm"
      footprint="0402"
      pcbX={2}
      pcbY={-2}
    />
    <chip
      name="U2"
      pinLabels={{ pin1: ["VDD"] }}
      pinAttributes={{ VDD: { requiresPower: true } }}
      footprint={
        <footprint>
          <smtpad
            portHints={["pin1"]}
            shape="rect"
            width="1mm"
            height="1mm"
          />
        </footprint>
      }
      pcbX={8}
      pcbY={-2}
    />
    <trace name="TR_INFERRED_LOCAL" from=".C2 > .pin1" to=".U2 > .VDD" />
  </board>
)
