#!/usr/bin/env node
import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { readFile } from "node:fs/promises"
import { dirname, resolve } from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

const testDir = dirname(fileURLToPath(import.meta.url))
const capacityPath = resolve(
  process.env.TSCIRCUIT_CAPACITY_PATH ??
    resolve(
      testDir,
      "../../toolchain/node_modules/@tscircuit/capacity-autorouter/dist/index.js",
    ),
)
const corePath = resolve(
  process.env.TSCIRCUIT_CORE_PATH ??
    resolve(testDir, "../../toolchain/node_modules/@tscircuit/core/dist/index.js"),
)
const fixturePath = resolve(
  testDir,
  "fixtures/layer-reversal-retry.simple-route.json",
)
const widthFixturePath = resolve(
  testDir,
  "fixtures/explicit-trace-width.simple-route.json",
)

const { AutoroutingPipelineSolver } = await import(
  `${pathToFileURL(capacityPath)}?layer-reversal-retry=${Date.now()}`
)
const fixture = JSON.parse(await readFile(fixturePath, "utf8"))
const widthFixture = JSON.parse(await readFile(widthFixturePath, "utf8"))

const PRISTINE_MIRRORED_OUTPUT = [
  {
    type: "pcb_trace",
    pcb_trace_id: "source_trace_19_0",
    connection_name: "source_trace_19",
    connectsTo: ["pcb_port_32", "pcb_port_50"],
    route: [
      {
        route_type: "wire",
        x: 0.9500000000000002,
        y: -2.1499999999999995,
        width: 0.15,
        layer: "top",
      },
      {
        route_type: "wire",
        x: 0.9500000000000002,
        y: -4.125956801155812,
        width: 0.15,
        layer: "top",
      },
      {
        route_type: "wire",
        x: 0.249936,
        y: -4.8259568,
        width: 0.15,
        layer: "top",
      },
    ],
  },
  {
    type: "pcb_trace",
    pcb_trace_id: "source_trace_16_0",
    connection_name: "source_trace_16",
    connectsTo: ["pcb_port_31", "pcb_port_48"],
    route: [
      {
        route_type: "wire",
        x: -0.9499999999999997,
        y: -2.15,
        width: 0.15,
        layer: "top",
      },
      {
        route_type: "wire",
        x: -0.9177325177920879,
        y: -3.4976634238027726,
        width: 0.15,
        layer: "top",
      },
      {
        route_type: "wire",
        x: -0.3262899209946573,
        y: -4.0891060206002035,
        width: 0.15,
        layer: "top",
      },
      {
        route_type: "wire",
        x: -0.3585574032025691,
        y: -4.71744259679743,
        width: 0.15,
        layer: "top",
      },
      {
        route_type: "wire",
        x: -0.249936,
        y: -4.8259568,
        width: 0.15,
        layer: "top",
      },
    ],
  },
]

const reverseLayerName = (layer, layerCount) => {
  const z =
    layer === "top"
      ? 0
      : layer === "bottom"
        ? layerCount - 1
        : /^inner\d+$/.test(layer)
          ? Number(layer.slice(5))
          : Number.NaN
  if (!Number.isInteger(z) || z < 0 || z >= layerCount) return layer
  const reversedZ = layerCount - 1 - z
  if (reversedZ === 0) return "top"
  if (reversedZ === layerCount - 1) return "bottom"
  return `inner${reversedZ}`
}

const transformLayers = (
  value,
  { key, layerCount, mirrorX = false } = {},
) => {
  const effectiveLayerCount = layerCount ?? value?.layerCount
  if (Array.isArray(value)) {
    if (key === "layers") {
      return value.map((layer) =>
        reverseLayerName(layer, effectiveLayerCount),
      )
    }
    if (["zLayers", "__zLayers", "availableZ"].includes(key)) {
      return value.map((z) => effectiveLayerCount - 1 - z)
    }
    return value.map((item) =>
      transformLayers(item, {
        layerCount: effectiveLayerCount,
        mirrorX,
      }),
    )
  }
  if (value === null || typeof value !== "object") {
    if (["layer", "from_layer", "to_layer"].includes(key)) {
      return reverseLayerName(value, effectiveLayerCount)
    }
    if (["z", "from_z", "to_z"].includes(key) && typeof value === "number") {
      return effectiveLayerCount - 1 - value
    }
    if (key === "x" && mirrorX && typeof value === "number") return -value
    if (
      key === "ccwRotationDegrees" &&
      mirrorX &&
      typeof value === "number"
    ) {
      return (180 - value + 360) % 360
    }
    return value
  }
  return Object.fromEntries(
    Object.entries(value).map(([entryKey, entryValue]) => [
      entryKey,
      transformLayers(entryValue, {
        key: entryKey,
        layerCount: effectiveLayerCount,
        mirrorX,
      }),
    ]),
  )
}

const solve = (srj, options = {}) => {
  const solver = new AutoroutingPipelineSolver(structuredClone(srj), {
    cacheProvider: null,
    ...options,
  })
  let steps = 0
  while (!solver.solved && !solver.failed && steps < 100_000) {
    solver.step()
    steps += 1
  }
  assert.ok(steps < 100_000, "layer-reversal fixture must terminate")
  return { solver, steps }
}

const assertApproximateTraceMirror = (topTraces, bottomTraces) => {
  assert.deepEqual(
    bottomTraces.map(({ connection_name }) => connection_name),
    topTraces.map(({ connection_name }) => connection_name),
  )
  for (let traceIndex = 0; traceIndex < topTraces.length; traceIndex += 1) {
    const top = topTraces[traceIndex]
    const bottom = bottomTraces[traceIndex]
    assert.equal(bottom.pcb_trace_id, top.pcb_trace_id)
    assert.deepEqual(bottom.connectsTo, top.connectsTo)
    assert.equal(bottom.route.length, top.route.length)
    for (let pointIndex = 0; pointIndex < top.route.length; pointIndex += 1) {
      const topPoint = top.route[pointIndex]
      const bottomPoint = bottom.route[pointIndex]
      assert.equal(bottomPoint.route_type, topPoint.route_type)
      assert.equal(bottomPoint.width, topPoint.width)
      assert.ok(Math.abs(bottomPoint.x + topPoint.x) <= 1e-12)
      assert.ok(Math.abs(bottomPoint.y - topPoint.y) <= 1e-12)
      if (topPoint.layer) {
        assert.equal(bottomPoint.layer, reverseLayerName(topPoint.layer, 2))
      }
      if (topPoint.from_layer) {
        assert.equal(
          bottomPoint.from_layer,
          reverseLayerName(topPoint.from_layer, 2),
        )
      }
      if (topPoint.to_layer) {
        assert.equal(
          bottomPoint.to_layer,
          reverseLayerName(topPoint.to_layer, 2),
        )
      }
    }
  }
}

const mirroredTopFixture = transformLayers(fixture, {
  layerCount: fixture.layerCount,
  mirrorX: true,
})
const top = solve(mirroredTopFixture)
assert.equal(top.solver.failed, false, top.solver.error)
assert.equal(top.solver.solved, true)
assert.equal(
  top.solver.layerReversalRetrySolver,
  null,
  "a clean original route must never enter the retry portfolio",
)
const topOutput = top.solver.getOutputSimpleRouteJson().traces
assert.deepEqual(
  topOutput,
  PRISTINE_MIRRORED_OUTPUT,
  "successful original-orientation copper must stay byte-identical",
)

const optionProbe = {
  layer: "bottom",
  layers: ["bottom"],
  z: 1,
  availableZ: [1],
}
const bottom = solve(fixture, { retryTransformProbe: optionProbe })
assert.equal(bottom.solver.failed, false, bottom.solver.error)
assert.equal(bottom.solver.solved, true)
assert.ok(bottom.solver.layerReversalRetrySolver)
assert.match(
  bottom.solver.originalLayerReversalFailure ?? "",
  /source_trace_16.*minimum trace width 0\.15mm/,
)
assert.equal(
  bottom.solver.layerReversalRetrySolver.exactGeometryDrcForceImproveSolver
    ?.stats?.drcBranchPortfolioFinalDrcIssueCount,
  0,
  "retry copper must pass the authoritative exact DRC gate",
)
assert.equal(
  bottom.solver.layerReversalRetrySolver.lengthMatchingPostProcessingSolver
    ?.solved,
  true,
  "retry copper must pass native differential-pair postprocessing",
)
assert.deepEqual(
  bottom.solver.layerReversalRetrySolver.opts.retryTransformProbe,
  {
    layer: "top",
    layers: ["top"],
    z: 0,
    availableZ: [0],
  },
  "layer-dependent P7 options must reverse with the SRJ",
)

const expectedRetrySrj = transformLayers(fixture, {
  layerCount: fixture.layerCount,
})
const normalizeObstacleLayers = (obstacle) => {
  const zLayers = [
    ...new Set(
      obstacle.layers.map((layer) =>
        layer === "top"
          ? 0
          : layer === "bottom"
            ? fixture.layerCount - 1
            : Number(layer.slice(5)),
      ),
    ),
  ].sort((a, b) => a - b)
  return {
    ...obstacle,
    layers: zLayers.map((z) =>
      z === 0
        ? "top"
        : z === fixture.layerCount - 1
          ? "bottom"
          : `inner${z}`,
    ),
    zLayers,
    __zLayers: zLayers,
  }
}
assert.deepEqual(
  bottom.solver.layerReversalRetrySolver.originalSrj.obstacles,
  expectedRetrySrj.obstacles.map(normalizeObstacleLayers),
  "physical obstacles and their derived z layers must reverse together",
)
assert.deepEqual(
  bottom.solver.layerReversalRetrySolver.originalSrj.traces,
  expectedRetrySrj.traces,
  "preloaded copper must reverse with connection endpoints",
)
assert.deepEqual(
  bottom.solver.layerReversalRetrySolver.originalSrj.connections,
  expectedRetrySrj.connections,
)

const bottomOutput = bottom.solver.getOutputSimpleRouteJson().traces
for (const trace of bottomOutput) {
  const inputConnection = fixture.connections.find(
    ({ name }) => name === trace.connection_name,
  )
  assert.ok(inputConnection)
  assert.equal(trace.pcb_trace_id, `${trace.connection_name}_0`)
  assert.deepEqual(
    trace.connectsTo,
    inputConnection.pointsToConnect.map(({ pointId }) => pointId),
  )
  assert.ok(
    trace.route.every(
      (point) =>
        point.layer === undefined || point.layer === "bottom",
    ),
  )
}
assertApproximateTraceMirror(topOutput, bottomOutput)

const impossible = solve(widthFixture)
assert.equal(impossible.solver.solved, false)
assert.equal(impossible.solver.failed, true)
assert.ok(impossible.solver.layerReversalRetrySolver)
assert.match(
  impossible.solver.error ?? "",
  /failed in the original orientation .* and layer-reversal retry /,
)
assert.match(
  impossible.solver.error ?? "",
  /minimum trace width 0\.25mm/g,
)
assert.throws(() => impossible.solver.getOutputSimpleRouteJson())

const legacyWidthFixture = structuredClone(widthFixture)
delete legacyWidthFixture.connections[0].nominalTraceWidth
const legacy = solve(legacyWidthFixture)
assert.equal(legacy.solver.failed, false, legacy.solver.error)
assert.equal(legacy.solver.solved, true)
assert.equal(
  legacy.solver.layerReversalRetrySolver,
  null,
  "a no-nominal legacy route keeps board-floor width behavior without retry",
)
assert.ok(
  legacy.solver
    .getOutputSimpleRouteJson()
    .traces[0].route.filter(({ route_type }) => route_type === "wire")
    .every(({ width }) => width >= legacyWidthFixture.minTraceWidth),
)

const capacitySource = await readFile(capacityPath, "utf8")
assert.ok(capacitySource.includes('"p7-layer-reversal-v1:"'))
const coreSource = await readFile(corePath, "utf8")
assert.ok(
  coreSource.includes(
    'capacityLayerReversalRetry: "p7-layer-reversal-v1"',
  ),
  "the whole-phase route-cache descriptor must version retry semantics",
)
const cacheKeyFunctionSource = coreSource.match(
  /var getLocalAutoroutingCacheKey = ([^\n]+);/,
)?.[1]
assert.ok(cacheKeyFunctionSource)
const getSrjHash = (value) =>
  createHash("sha256").update(JSON.stringify(value)).digest("hex")
const getLocalAutoroutingCacheKey = Function(
  "getSrjHash",
  "package_default",
  "autorouterVersion",
  `return (${cacheKeyFunctionSource})`,
)(getSrjHash, { version: "0.0.1642" }, "0.0.782")
const cacheSrj = { connections: [], layerCount: 2 }
const oldCacheDescriptor = { phaseStageCount: 1 }
const retryCacheDescriptor = {
  phaseStageCount: 1,
  capacityLayerReversalRetry: "p7-layer-reversal-v1",
}
assert.notEqual(
  getLocalAutoroutingCacheKey(cacheSrj, oldCacheDescriptor),
  getLocalAutoroutingCacheKey(cacheSrj, retryCacheDescriptor),
  "pre-retry and retry-aware whole-phase cache identities must differ",
)

process.stdout.write("bounded Pipeline7 layer-reversal retry regression passed\n")
