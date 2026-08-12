const SourceBlock = () => (
  <group pcbX={-8} pcbY={0}>
    <chip
      name="U_SRC"
      pinLabels={{ pin1: ["DP"], pin2: ["DM"] }}
      footprint={
        <footprint>
          <smtpad portHints={["pin1"]} pcbX={0} pcbY={0.7} width="1mm" height="0.5mm" shape="rect" />
          <smtpad portHints={["pin2"]} pcbX={0} pcbY={-0.7} width="1mm" height="0.5mm" shape="rect" />
        </footprint>
      }
    />
  </group>
)

const SinkBlock = () => (
  <group pcbX={8} pcbY={0}>
    <chip
      name="U_SINK"
      pinLabels={{ pin1: ["DP"], pin2: ["DM"] }}
      footprint={
        <footprint>
          <smtpad portHints={["pin1"]} pcbX={0} pcbY={2} width="1mm" height="0.5mm" shape="rect" />
          <smtpad portHints={["pin2"]} pcbX={0} pcbY={-2} width="1mm" height="0.5mm" shape="rect" />
        </footprint>
      }
    />
  </group>
)

export default () => (
  <board width="30mm" height="16mm" minTraceWidth="0.15mm">
    <SourceBlock />
    <SinkBlock />
    <trace name="TR_BOARD_DP" from=".U_SRC > .DP" to=".U_SINK > .DP" />
    <trace name="TR_BOARD_DM" from=".U_SRC > .DM" to=".U_SINK > .DM" />
    <differentialpair
      name="USB_BOARD_PAIR"
      positiveConnection="TR_BOARD_DP"
      negativeConnection="TR_BOARD_DM"
      pcbTraceGap="1.25mm"
      maxLengthSkew="0.2mm"
      maxUncoupledLength="0.1mm"
    />
  </board>
)
