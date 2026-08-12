const leftYs = [-8.1, -6.3, -4.5, -2.7, -0.9, 0.9, 2.7, 4.5, 6.3, 8.1]

export const RouterCacheFixture = ({
  version,
  effort = "1x",
}: {
  version: "beta_pipeline7" | "beta_pipeline9"
  effort?: "1x" | "5x"
}) => (
  <board
    width="30mm"
    height="22mm"
    minTraceWidth="0.2mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
    minTraceToPadEdgeClearance="0.1mm"
    minViaEdgeToPadEdgeClearance="0.1mm"
    autorouterVersion={version}
    autorouterEffortLevel={effort}
    placementDrcChecksDisabled
  >
    {leftYs.flatMap((y, index) => {
      const number = index + 1
      const rightY = leftYs[leftYs.length - 1 - index]
      return [
        <resistor
          key={`left-${number}`}
          name={`L${number}`}
          resistance="1k"
          footprint="0402"
          pcbX={-13}
          pcbY={y}
          pcbRotation={90}
        />,
        <resistor
          key={`right-${number}`}
          name={`R${number}`}
          resistance="1k"
          footprint="0402"
          pcbX={13}
          pcbY={rightY}
          pcbRotation={90}
        />,
        <trace
          key={`trace-${number}`}
          name={`T${number}`}
          from={`.L${number} > .pin1`}
          to={`.R${number} > .pin1`}
        />,
      ]
    })}
  </board>
)
