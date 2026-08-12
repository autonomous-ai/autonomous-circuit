import assert from "node:assert/strict"
import React from "../../../toolchain/node_modules/react"

;(globalThis as typeof globalThis & { React: typeof React }).React = React
const { PowerTrunk } = require("../blocks/glue") as typeof import("../blocks/glue")

const legacy = {
  name: "V5_MAIN",
  source: ".U1 > .OUT",
  net: "V5",
  start: { x: 0, y: 0 },
  end: { x: 8, y: 0 },
  startTestpoint: "TP1",
  endTestpoint: "TP2",
} as const

const crossLayer = {
  ...legacy,
  sourcePoint: { x: -1.5, y: 0 },
  trunkVia: { x: 1.5, y: 0 },
  sourceLayer: "top",
  trunkLayer: "bottom",
  maxNeckdownLengthMm: 2,
  viaOuterDiameterMm: 0.8,
  viaHoleDiameterMm: 0.5,
  minViaEdgeToPadEdgeClearanceMm: 0.15,
} as const

assert.doesNotThrow(() => PowerTrunk(legacy))
assert.doesNotThrow(() => PowerTrunk(crossLayer))

for (const field of ["sourceLayer", "trunkLayer", "sourcePoint", "trunkVia"] as const) {
  const incomplete: Record<string, unknown> = { ...crossLayer }
  delete incomplete[field]
  assert.throws(
    () => PowerTrunk(incomplete as any),
    /requires sourceLayer, trunkLayer, sourcePoint, and trunkVia together/,
  )
}

assert.throws(
  () => PowerTrunk({ ...crossLayer, trunkLayer: "top" }),
  /must be different/,
)
assert.throws(
  () => PowerTrunk({ ...crossLayer, layer: "top" }),
  /layer must equal trunkLayer/,
)
assert.throws(
  () => PowerTrunk({
    ...crossLayer,
    sourcePoint: { x: Number.NaN, y: 0 },
  }),
  /coordinates must be finite/,
)
assert.throws(
  () => PowerTrunk({ ...crossLayer, maxNeckdownLengthMm: 1 }),
  /neck exceeds/,
)
assert.throws(
  () => PowerTrunk({ ...crossLayer, maxNeckdownLengthMm: 0 }),
  /dimensions require/,
)
assert.throws(
  () => PowerTrunk({ ...crossLayer, start: crossLayer.end }),
  /start\/end coordinates must be different/,
)
assert.throws(
  () => PowerTrunk({ ...crossLayer, trunkVia: { x: 0.5, y: 0 } }),
  /clear both boundary-pad edges/,
)
assert.throws(
  () => PowerTrunk({ ...crossLayer, trunkVia: { x: 7.5, y: 0 } }),
  /clear both boundary-pad edges/,
)
assert.throws(
  () => PowerTrunk({
    ...crossLayer,
    viaOuterDiameterMm: 0.5,
    viaHoleDiameterMm: 0.5,
  }),
  /dimensions require/,
)
assert.throws(
  () => PowerTrunk({ ...crossLayer, minViaEdgeToPadEdgeClearanceMm: -0.01 }),
  /dimensions require/,
)

console.log("PowerTrunk legacy and cross-layer validation regressions passed")
