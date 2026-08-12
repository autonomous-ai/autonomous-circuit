const SourceBlock = () => (
  <group pcbX={-8} pcbY={0}>
    <chip
      name="U_SRC"
      pinLabels={{ pin1: ["DP"], pin2: ["DM"] }}
      pcbX={0}
      pcbY={0}
      footprint={
        <footprint>
          <smtpad portHints={["pin1"]} pcbX={0} pcbY={0.7} width="1mm" height="0.5mm" shape="rect" />
          <smtpad portHints={["pin2"]} pcbX={0} pcbY={-0.7} width="1mm" height="0.5mm" shape="rect" />
        </footprint>
      }
    />
  </group>
)

const SeriesBlock = () => (
  <group pcbX={8} pcbY={0}>
    <resistor name="R_DP" resistance="27" footprint="0402" pcbX={0} pcbY={0.7} pcbRotation={180} />
    <resistor name="R_DM" resistance="27" footprint="0402" pcbX={0} pcbY={-0.7} pcbRotation={180} />
  </group>
)

export default () => (
  <board width="30mm" height="16mm" minTraceWidth="0.15mm">
    <SourceBlock />
    <SeriesBlock />
    <trace name="TR_BOARD_DP" from=".U_SRC > .DP" to=".R_DP > .pin2" />
    <trace name="TR_BOARD_DM" from=".U_SRC > .DM" to=".R_DM > .pin2" />
    <differentialpair
      name="USB_BOARD_PAIR"
      positiveConnection="TR_BOARD_DP"
      negativeConnection="TR_BOARD_DM"
      pcbTraceGap="1.25mm"
      maxLengthSkew="0.2mm"
      maxUncoupledLength="1mm"
    />
  </board>
)
