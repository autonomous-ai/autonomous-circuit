const makeInjectedAutorouter = async (simpleRouteJson: any) => {
  const connection = simpleRouteJson.connections[0]
  const start = connection.pointsToConnect[0]
  const end = connection.pointsToConnect[1]
  const useDifferentNetPad = process.env.TSCIRCUIT_TEST_DIFFERENT_NET_PAD === "1"
  const targetPad = useDifferentNetPad
    ? simpleRouteJson.obstacles.find(
        (obstacle: any) =>
          obstacle.circuitJsonMetadata?.pcb_smtpad_id &&
          !obstacle.connectedTo.some((id: string) =>
            [connection.name, start.pointId, end.pointId].includes(id),
          ),
      )
    : undefined
  const firstVia = targetPad?.center ?? start
  const midpoint = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 }
  const traces = [
    {
      type: "pcb_trace",
      pcb_trace_id: `${connection.name}_0`,
      source_trace_id: connection.source_trace_id,
      connection_name: connection.name,
      connectsTo: [start.pointId, end.pointId],
      route: [
        {
          route_type: "wire",
          x: start.x,
          y: start.y,
          width: 0.15,
          layer: "top",
        },
        {
          route_type: "wire",
          x: firstVia.x,
          y: firstVia.y,
          width: 0.15,
          layer: "top",
        },
        {
          route_type: "via",
          x: firstVia.x,
          y: firstVia.y,
          from_layer: "top",
          to_layer: "bottom",
          via_diameter: 0.6,
          via_hole_diameter: 0.3,
        },
        {
          route_type: "wire",
          x: firstVia.x,
          y: firstVia.y,
          width: 0.15,
          layer: "bottom",
        },
        { route_type: "wire", ...midpoint, width: 0.15, layer: "bottom" },
        {
          route_type: "via",
          ...midpoint,
          from_layer: "bottom",
          to_layer: "top",
          via_diameter: 0.6,
          via_hole_diameter: 0.3,
        },
        { route_type: "wire", ...midpoint, width: 0.15, layer: "top" },
        {
          route_type: "wire",
          x: end.x,
          y: end.y,
          width: 0.15,
          layer: "top",
        },
      ],
    },
  ]
  const listeners = new Map<string, Array<(event: any) => void>>()
  return {
    on(name: string, listener: (event: any) => void) {
      const registered = listeners.get(name) ?? []
      registered.push(listener)
      listeners.set(name, registered)
    },
    start() {
      for (const listener of listeners.get("complete") ?? []) {
        listener({ traces })
      }
    },
    stop() {},
  }
}

const allowViaInPad = process.env.TSCIRCUIT_TEST_ALLOW_VIA_IN_PAD === "1"

export default () => (
  <board
    width="16mm"
    height="10mm"
    minTraceWidth="0.15mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
    isViaInPadAllowed={allowViaInPad}
    autorouter={{
      local: true,
      groupMode: "subcircuit",
      allowViaInPad,
      algorithmFn: makeInjectedAutorouter,
    }}
  >
    <resistor name="R1" resistance="1k" footprint="0402" pcbX={-4} />
    <resistor name="R2" resistance="1k" footprint="0402" pcbX={4} />
    <trace name="TR_SIGNAL" from=".R1 > .pin2" to=".R2 > .pin1" />
  </board>
)
