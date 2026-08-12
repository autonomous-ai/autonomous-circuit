const Endpoint = ({ name, x }: { name: string; x: number }) => (
  <chip
    name={name}
    pinLabels={{ pin1: ["DP"], pin2: ["DM"] }}
    pcbX={x}
    footprint={
      <footprint>
        <smtpad portHints={["pin1"]} pcbX={0} pcbY={0.7} width="1mm" height="0.5mm" shape="rect" />
        <smtpad portHints={["pin2"]} pcbX={0} pcbY={-0.7} width="1mm" height="0.5mm" shape="rect" />
      </footprint>
    }
  />
)

export default () => (
  <board width="30mm" height="16mm" minTraceWidth="0.15mm">
    <Endpoint name="U_SRC" x={-8} />
    <Endpoint name="U_SINK" x={8} />
    <trace name="TR_BOARD_DP" from=".U_SRC > .DP" to=".U_SINK > .DP" />
    <trace name="TR_BOARD_DM" from=".U_SRC > .DM" to=".U_SINK > .DM" />
    <differentialpair
      name="USB_BOARD_PAIR"
      positiveConnection="TR_BOARD_DP"
      negativeConnection="TR_BOARD_DM"
      maxLengthSkew="0.2mm"
      maxUncoupledLength="1mm"
    />
  </board>
)
