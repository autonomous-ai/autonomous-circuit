#!/usr/bin/env node
import assert from "node:assert/strict"
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
const fixturePath = resolve(
  testDir,
  "fixtures/explicit-trace-width.simple-route.json",
)
const {
  AutoroutingPipelineSolver,
  AutoroutingPipelineSolver7_MultiGraph: Pipeline7,
} = await import(
  `${pathToFileURL(capacityPath)}?explicit-trace-width=${Date.now()}`
)
const fixture = JSON.parse(await readFile(fixturePath, "utf8"))

const centerlineRoute = {
  connectionName: "explicit_width",
  rootConnectionName: "explicit_width",
  traceThickness: 0.15,
  viaDiameter: 0.6,
  route: [
    { x: -4, y: 0, z: 0, pcb_port_id: "pcb_port_start" },
    { x: 4, y: 0, z: 0, pcb_port_id: "pcb_port_end" },
  ],
  vias: [],
  jumpers: [],
}

const solveTraceWidthStage = (srj, routes = [centerlineRoute]) => {
  const pipeline = new Pipeline7(srj, { effort: 1 })
  pipeline.traceSimplificationSolver = { simplifiedHdRoutes: routes }
  const step = pipeline.pipelineDef.find(
    (candidate) => candidate.solverName === "traceWidthSolver",
  )
  assert.ok(step, "Pipeline7 must expose its trace-width stage")
  const [params] = step.getConstructorParams(pipeline)
  const solver = new step.solverClass(params)
  let steps = 0
  while (!solver.solved && !solver.failed && steps < 100_000) {
    solver.step()
    steps += 1
  }
  assert.ok(steps < 100_000, "trace-width fixture must terminate")
  return solver
}

const withCorridorHalfWidth = (halfWidth) => {
  const srj = structuredClone(fixture)
  for (const obstacle of srj.obstacles) {
    obstacle.center.y = Math.sign(obstacle.center.y) * (1 + halfWidth)
  }
  return srj
}

const solvePipeline = (srj) => {
  const solver = new AutoroutingPipelineSolver(srj, { cacheProvider: null })
  let steps = 0
  while (!solver.solved && !solver.failed && steps < 1_000_000) {
    solver.step()
    steps += 1
  }
  assert.ok(steps < 1_000_000, "pipeline fixture must terminate")
  return solver
}

const blockedPipeline = solvePipeline(withCorridorHalfWidth(0.235))
assert.equal(blockedPipeline.solved, false)
assert.equal(blockedPipeline.failed, true)
assert.match(
  blockedPipeline.error ?? "",
  /explicit_width.*minimum trace width 0\.25mm/,
)
assert.equal(
  blockedPipeline.getCurrentPhase(),
  "traceWidthSolver",
  "the width contract must fail before exporting thinner copper",
)

const clearPipeline = solvePipeline(withCorridorHalfWidth(0.3))
assert.equal(clearPipeline.failed, false, clearPipeline.error)
assert.equal(clearPipeline.solved, true)
const clearPipelineTraces = clearPipeline.getOutputSimpleRouteJson().traces
assert.equal(clearPipelineTraces.length, 1)
assert.ok(
  clearPipelineTraces[0].route
    .filter((point) => point.route_type === "wire")
    .every((point) => point.width >= 0.25),
  "the cold successful pipeline must serialize only 0.25mm-or-wider wire",
)

const blocked = solveTraceWidthStage(withCorridorHalfWidth(0.235))
assert.equal(blocked.solved, false)
assert.equal(blocked.failed, true)
assert.match(
  blocked.error ?? "",
  /explicit_width.*minimum trace width 0\.25mm/,
)
assert.deepEqual(
  blocked.getHdRoutesWithWidths(),
  [],
  "an explicit 0.25mm connection must not fall back to 0.20mm or 0.15mm",
)

const clear = solveTraceWidthStage(withCorridorHalfWidth(0.3))
assert.equal(clear.failed, false, clear.error)
assert.equal(clear.solved, true)
const [clearRoute] = clear.getHdRoutesWithWidths()
assert.equal(clearRoute.traceThickness, 0.25)
assert.ok(
  clearRoute.route.every((point) => point.traceThickness >= 0.25),
  "every serialized segment must retain the explicit 0.25mm minimum",
)

const narrowTerminalSrj = withCorridorHalfWidth(0.3)
narrowTerminalSrj.obstacles = [
  {
    type: "rect",
    center: { x: -4, y: 0 },
    width: 0.5,
    height: 0.18,
    layers: ["top"],
    connectedTo: ["pcb_smtpad_start", "pcb_port_start", "explicit_width"],
    circuitJsonMetadata: {
      pcb_smtpad_id: "pcb_smtpad_start",
      pcb_port_id: "pcb_port_start"
    }
  },
  {
    type: "rect",
    center: { x: 4, y: 0 },
    width: 0.5,
    height: 0.18,
    layers: ["top"],
    connectedTo: ["pcb_smtpad_end", "pcb_port_end", "explicit_width"],
    circuitJsonMetadata: {
      pcb_smtpad_id: "pcb_smtpad_end",
      pcb_port_id: "pcb_port_end"
    }
  }
]
const narrowTerminal = solveTraceWidthStage(narrowTerminalSrj)
assert.equal(narrowTerminal.failed, false, narrowTerminal.error)
assert.ok(
  narrowTerminal
    .getHdRoutesWithWidths()[0]
    .route.every((point) => point.traceThickness >= 0.25),
  "terminal taper must not undercut an explicit minimum",
)

const singlePoint = solveTraceWidthStage(withCorridorHalfWidth(0.3), [
  {
    ...centerlineRoute,
    route: [{ x: 0, y: 0, z: 0, pcb_port_id: "pcb_port_start" }],
  },
])
assert.equal(singlePoint.failed, false, singlePoint.error)
assert.equal(singlePoint.getHdRoutesWithWidths()[0].traceThickness, 0.25)
assert.equal(
  singlePoint.getHdRoutesWithWidths()[0].route[0].traceThickness,
  0.25,
  "a degenerate explicit connection must not silently use the board floor",
)

const legacySrj = withCorridorHalfWidth(0.235)
delete legacySrj.connections[0].nominalTraceWidth
const legacy = solveTraceWidthStage(legacySrj)
assert.equal(legacy.failed, false, legacy.error)
assert.equal(legacy.solved, true)
assert.equal(legacy.getHdRoutesWithWidths()[0].traceThickness, 0.15)

process.stdout.write("explicit trace-width hard-minimum regression passed\n")
