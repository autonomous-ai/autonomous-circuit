export default () => (
  <board
    width="20mm"
    height="12mm"
    thickness="1.6mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    <testpoint
      name="TP1"
      footprintVariant="pad"
      padShape="circle"
      padDiameter="1mm"
      pcbX={-5}
      pcbY={-3}
    />
    <testpoint
      name="TP2"
      footprintVariant="pad"
      padShape="circle"
      padDiameter="1mm"
      pcbX={5}
      pcbY={-3}
    />
    <testpoint
      name="TP3"
      footprintVariant="pad"
      padShape="circle"
      padDiameter="1mm"
      pcbX={-5}
      pcbY={3}
    />
    <testpoint
      name="TP4"
      footprintVariant="pad"
      padShape="circle"
      padDiameter="1mm"
      pcbX={5}
      pcbY={3}
    />
    <testpoint
      name="TP5"
      footprintVariant="pad"
      padShape="circle"
      padDiameter="1mm"
      pcbX={-5}
      pcbY={0}
    />
    <testpoint
      name="TP6"
      footprintVariant="pad"
      padShape="circle"
      padDiameter="1mm"
      pcbX={5}
      pcbY={0}
    />

    {/* pcbStyle is the existing typed, trace-local sizing API: the wrapper
        scopes the larger power-via requirement without inflating every board
        via. Core still floors both values at the board manufacturing minima. */}
    <group
      pcbStyle={{ viaPadDiameter: "0.8mm", viaHoleDiameter: "0.5mm" }}
    >
      <trace
        name="TR_POWER_FIXED"
        from=".TP1 > .pin1"
        to=".TP2 > .pin1"
        thickness="0.6mm"
        pcbPathRelativeTo=".TP1 > .pin1"
        pcbPath={[
          { x: 0, y: 0 },
          { x: 2, y: 0 },
          { x: 2, y: 0, via: true, fromLayer: "top", toLayer: "bottom" },
          { x: 2, y: 0 },
          { x: 8, y: 0 },
          { x: 8, y: 0, via: true, fromLayer: "bottom", toLayer: "top" },
          { x: 8, y: 0 },
          { x: 10, y: 0 },
        ]}
      />
    </group>

    <group pcbStyle={{ viaPadDiameter: "0.4mm", viaHoleDiameter: "0.2mm" }}>
      <trace
        name="TR_SIGNAL_FIXED"
        from=".TP3 > .pin1"
        to=".TP4 > .pin1"
        thickness="0.2mm"
        pcbPathRelativeTo=".TP3 > .pin1"
        pcbPath={[
          { x: 0, y: 0 },
          { x: 2, y: 0 },
          { x: 2, y: 0, via: true, fromLayer: "top", toLayer: "bottom" },
          { x: 2, y: 0 },
          { x: 8, y: 0 },
          { x: 8, y: 0, via: true, fromLayer: "bottom", toLayer: "top" },
          { x: 8, y: 0 },
          { x: 10, y: 0 },
        ]}
      />
    </group>

    {/* This ordinary connection forces the fixed traces through the async
        autorouter's preload/output reinsertion path. */}
    <trace
      name="TR_AUTOROUTE"
      from=".TP5 > .pin1"
      to=".TP6 > .pin1"
      thickness="0.2mm"
    />
  </board>
)
