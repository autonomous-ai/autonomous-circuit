const Source = () => (
  <chip
    name="J1"
    pinLabels={{
      pin1: ["DPA"],
      pin2: ["DPB"],
      pin3: ["DMA"],
      pin4: ["DMB"],
    }}
    pcbX={-7}
    pcbY={0}
    footprint={
      <footprint>
        <smtpad portHints={["pin1"]} pcbX={-1.2} pcbY={0.7} width="0.8mm" height="0.5mm" shape="rect" />
        <smtpad portHints={["pin2"]} pcbX={1.2} pcbY={0.7} width="0.8mm" height="0.5mm" shape="rect" />
        <smtpad portHints={["pin3"]} pcbX={-1.2} pcbY={-0.7} width="0.8mm" height="0.5mm" shape="rect" />
        <smtpad portHints={["pin4"]} pcbX={1.2} pcbY={-0.7} width="0.8mm" height="0.5mm" shape="rect" />
      </footprint>
    }
  />
)

const Esd = () => (
  <chip
    name="U1"
    pinLabels={{
      pin1: ["IO1"],
      pin2: ["IO2"],
      pin3: ["IO2B"],
      pin4: ["IO1B"],
    }}
    internallyConnectedPins={[["IO1", "IO1B"], ["IO2", "IO2B"]]}
    pcbX={7}
    pcbY={0}
    footprint={
      <footprint>
        <smtpad portHints={["pin1"]} pcbX={-1} pcbY={0.7} width="0.8mm" height="0.45mm" shape="rect" />
        <smtpad portHints={["pin2"]} pcbX={-1} pcbY={-0.7} width="0.8mm" height="0.45mm" shape="rect" />
        <smtpad portHints={["pin3"]} pcbX={1} pcbY={-0.7} width="0.8mm" height="0.45mm" shape="rect" />
        <smtpad portHints={["pin4"]} pcbX={1} pcbY={0.7} width="0.8mm" height="0.45mm" shape="rect" />
      </footprint>
    }
  />
)

export default () => (
  <board width="24mm" height="10mm" minTraceWidth="0.15mm">
    <autoroutingphase name="connector-dp-orientation" phaseIndex={0} />
    <autoroutingphase name="connector-dm-orientation" phaseIndex={1} />
    <autoroutingphase name="connector-esd-pair" phaseIndex={2} />
    <Source />
    <Esd />
    <trace name="TR_J1_dp_pair" from=".J1 > .DPA" to=".J1 > .DPB" routingPhaseIndex={0} />
    <trace name="TR_J1_dm_pair" from=".J1 > .DMA" to=".J1 > .DMB" routingPhaseIndex={1} />
    <trace name="TR_J1_dp_esd" from=".J1 > .DPB" to=".U1 > .IO1" routingPhaseIndex={2} />
    <trace name="TR_J1_dm_esd" from=".J1 > .DMB" to=".U1 > .IO2" routingPhaseIndex={2} />
    <differentialpair
      name="USB_CONNECTOR_ESD"
      positiveConnection="TR_J1_dp_esd"
      negativeConnection="TR_J1_dm_esd"
      pcbTraceGap="1.25mm"
      maxLengthSkew="0.2mm"
      maxUncoupledLength="2mm"
    />
  </board>
)
