#!/usr/bin/env node
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import {
  copyFile,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises"
import { tmpdir } from "node:os"
import { dirname, join, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath, pathToFileURL } from "node:url"

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
const fixtureDir = join(repoRoot, "scripts", "tests", "fixtures")
const cliPath =
  process.env.TSCIRCUIT_CLI_PATH ??
  join(repoRoot, "toolchain", "node_modules", ".bin", "tscircuit-cli")
const nodeModules = resolve(dirname(cliPath), "..")
const cliMainPath = join(
  nodeModules,
  "@tscircuit",
  "cli",
  "dist",
  "cli",
  "main.js",
)
const tsxLoaderPath = join(nodeModules, "tsx", "dist", "loader.mjs")
const capacityPath = join(
  nodeModules,
  "@tscircuit",
  "capacity-autorouter",
  "dist",
  "index.js",
)

const getTransitionPoint = (route) =>
  route.route.find(
    (point, index, points) =>
      index > 0 &&
      points[index - 1].z !== point.z &&
      points[index - 1].x === point.x &&
      points[index - 1].y === point.y,
  )

const makeScenario = ({
  secondVia = false,
  terminalVia = false,
  allowViaInPad = false,
  differentNetPad = false,
  platedHole = false,
} = {}) => {
  const obstacle = {
    type: "rect",
    center: { x: 0, y: 0 },
    width: 0.8,
    height: 0.8,
    layers: platedHole ? ["top", "bottom"] : ["top"],
    connectedTo: platedHole
      ? ["pcb_plated_hole_0", "pcb_port_0", "A"]
      : ["pcb_smtpad_0", "pcb_port_0", differentNetPad ? "B" : "A"],
    circuitJsonMetadata: platedHole
      ? {
          pcb_plated_hole_id: "pcb_plated_hole_0",
          pcb_port_id: "pcb_port_0",
        }
      : { pcb_smtpad_id: "pcb_smtpad_0", pcb_port_id: "pcb_port_0" },
  }
  const start = terminalVia
    ? { x: 0, y: 0, z: 0, pcb_port_id: "p1" }
    : { x: -2, y: 0, z: 0, pcb_port_id: "p1" }
  const primaryRoute = {
    connectionName: "A",
    rootConnectionName: "A",
    traceThickness: 0.15,
    viaDiameter: 0.6,
    vias: [{ x: 0, y: 0 }],
    route: terminalVia
      ? [
          start,
          { x: 0, y: 0, z: 1 },
          { x: 2, y: 0, z: 1, pcb_port_id: "p2" },
        ]
      : [
          start,
          { x: 0, y: 0, z: 0 },
          { x: 0, y: 0, z: 1 },
          { x: 2, y: 0, z: 1, pcb_port_id: "p2" },
        ],
  }
  const routes = [primaryRoute]
  const connections = [
    {
      name: "A",
      pointsToConnect: [
        {
          x: start.x,
          y: start.y,
          layer: "top",
          pointId: "p1",
          pcb_port_id: "p1",
        },
        {
          x: 2,
          y: 0,
          layer: "bottom",
          pointId: "p2",
          pcb_port_id: "p2",
        },
      ],
      mergedConnectionNames: [],
    },
  ]
  if (secondVia) {
    connections.push({
      name: "B",
      pointsToConnect: [
        { x: -2, y: 3, layer: "top", pointId: "p3", pcb_port_id: "p3" },
        {
          x: 3,
          y: 3,
          layer: "bottom",
          pointId: "p4",
          pcb_port_id: "p4",
        },
      ],
      mergedConnectionNames: [],
    })
    routes.push({
      connectionName: "B",
      rootConnectionName: "B",
      traceThickness: 0.15,
      viaDiameter: 0.6,
      vias: [{ x: 2.5, y: 3 }],
      route: [
        { x: -2, y: 3, z: 0, pcb_port_id: "p3" },
        { x: 2.5, y: 3, z: 0 },
        { x: 2.5, y: 3, z: 1 },
        { x: 3, y: 3, z: 1, pcb_port_id: "p4" },
      ],
    })
  }
  return {
    srj: {
      layerCount: 2,
      bounds: { minX: -5, minY: -5, maxX: 5, maxY: 5 },
      minTraceWidth: 0.15,
      minViaDiameter: 0.6,
      minViaHoleDiameter: 0.3,
      minTraceToPadEdgeClearance: 0.15,
      minViaEdgeToPadEdgeClearance: 0.15,
      allowViaInPad,
      connections,
      obstacles: [obstacle],
      traces: [],
    },
    routes,
  }
}

const makeExactRepair = (Pipeline, { srj, routes }) => {
  const pipeline = new Pipeline(srj, { effort: 1 })
  pipeline.srj = srj
  pipeline.originalSrj = srj
  pipeline.srjWithPointPairs = srj
  pipeline.netToPointPairsSolver = { newConnections: srj.connections }
  pipeline.globalDrcForceImproveSolver = { getOutput: () => routes }
  const step = pipeline.pipelineDef.find(
    (candidate) =>
      candidate.solverName === "exactGeometryDrcForceImproveSolver",
  )
  assert.ok(step)
  const [params] = step.getConstructorParams(pipeline)
  params.maxIterations = 64
  params.broadMaxIterations = 1
  params.failOnUnresolvedDrc = true
  const initialDrc = params.drcEvaluator({ hdRoutes: routes })
  const solver = new step.solverClass(params)
  solver.solve()
  const output = solver.getOutput()
  const finalDrc = params.drcEvaluator({ hdRoutes: output })
  return { initialDrc, finalDrc, output, solver }
}

test("Pipeline7 prevents via-in-SMD-pad while retaining legal opt-ins and PTH", async (t) => {
  let Pipeline
  try {
    ;({ AutoroutingPipelineSolver7_MultiGraph: Pipeline } = await import(
      `${pathToFileURL(capacityPath).href}?via-in-smd=${Date.now()}`
    ))
  } catch (error) {
    if (String(error).includes("ERR_MODULE_NOT_FOUND")) {
      t.skip("pinned toolchain is not installed")
      return
    }
    throw error
  }

  const single = makeExactRepair(Pipeline, makeScenario())
  assert.ok(single.initialDrc.errors.some((error) => error.pcb_smtpad_id))
  assert.equal(single.finalDrc.errors.length, 0)
  assert.equal(single.solver.failed, false, single.solver.error)
  const singleVia = getTransitionPoint(single.output[0])
  assert.ok(singleVia)
  assert.ok(
    Math.hypot(singleVia.x, singleVia.y) - 0.4 - 0.3 >= 0.15 - 5e-3,
  )

  const twoVia = makeExactRepair(Pipeline, makeScenario({ secondVia: true }))
  assert.equal(twoVia.finalDrc.errors.length, 0)
  assert.deepEqual(getTransitionPoint(twoVia.output[1]), {
    x: 2.5,
    y: 3,
    z: 1,
  })

  const explicitSameNet = makeExactRepair(
    Pipeline,
    makeScenario({ allowViaInPad: true }),
  )
  assert.equal(explicitSameNet.initialDrc.errors.length, 0)
  assert.equal(getTransitionPoint(explicitSameNet.output[0]).x, 0)

  const platedHole = makeExactRepair(
    Pipeline,
    makeScenario({ platedHole: true }),
  )
  assert.equal(platedHole.initialDrc.errors.length, 0)
  assert.equal(getTransitionPoint(platedHole.output[0]).x, 0)

  const differentNet = makeExactRepair(
    Pipeline,
    makeScenario({ allowViaInPad: true, differentNetPad: true }),
  )
  assert.ok(differentNet.initialDrc.errors.some((error) => error.pcb_smtpad_id))
  assert.equal(differentNet.finalDrc.errors.length, 0)
  assert.notEqual(getTransitionPoint(differentNet.output[0]).x, 0)

  const immovable = makeExactRepair(
    Pipeline,
    makeScenario({ terminalVia: true }),
  )
  assert.equal(immovable.finalDrc.errors.length, 1)
  assert.equal(immovable.solver.solved, false)
  assert.equal(immovable.solver.failed, true)
  assert.match(immovable.solver.error, /Unresolved DRC issues.*1/)
})

const compileOutputGateFixture = async (t, extraEnv = {}) => {
  const root = await mkdtemp(join(tmpdir(), "via-in-smd-output-gate-"))
  t.after(() => rm(root, { recursive: true, force: true }))
  await copyFile(
    join(fixtureDir, "via-in-smd-output-gate.tsx"),
    join(root, "main.tsx"),
  )
  await writeFile(
    join(root, "package.json"),
    JSON.stringify({ name: "via-in-smd-output-gate", private: true }),
  )
  const result = spawnSync(
    process.execPath,
    [
      "--import",
      tsxLoaderPath,
      cliMainPath,
      "build",
      "main.tsx",
      "--disable-parts-engine",
    ],
    {
      cwd: root,
      encoding: "utf8",
      timeout: 30_000,
      env: {
        ...process.env,
        CIRCUIT_PARTS_ENGINE: "off",
        ...extraEnv,
      },
    },
  )
  assert.equal(
    result.signal,
    null,
    `cold output-gate compile timed out:\n${result.stdout}\n${result.stderr}`,
  )
  const circuitJson = JSON.parse(
    await readFile(join(root, "dist", "main", "circuit.json"), "utf8"),
  )
  return { circuitJson, result }
}

test("core rejects illegal cached or non-P7 output before copper export", async (t) => {
  const rejected = await compileOutputGateFixture(t)
  const rejectedErrors = rejected.circuitJson.filter(
    (element) => element.type === "pcb_autorouting_error",
  )
  assert.equal(rejectedErrors.length, 1)
  assert.match(rejectedErrors[0].message, /illegal via-to-SMD-pad clearance/)
  assert.match(rejectedErrors[0].message, /required 0\.150mm/)
  assert.equal(
    rejected.circuitJson.filter((element) => element.type === "pcb_via").length,
    0,
  )

  const explicitSameNet = await compileOutputGateFixture(t, {
    TSCIRCUIT_TEST_ALLOW_VIA_IN_PAD: "1",
  })
  assert.equal(
    explicitSameNet.circuitJson.filter((element) =>
      String(element.type ?? "").endsWith("_error"),
    ).length,
    0,
  )
  assert.equal(
    explicitSameNet.circuitJson.filter((element) => element.type === "pcb_via")
      .length,
    2,
  )

  const differentNet = await compileOutputGateFixture(t, {
    TSCIRCUIT_TEST_ALLOW_VIA_IN_PAD: "1",
    TSCIRCUIT_TEST_DIFFERENT_NET_PAD: "1",
  })
  const differentNetErrors = differentNet.circuitJson.filter(
    (element) => element.type === "pcb_autorouting_error",
  )
  assert.equal(differentNetErrors.length, 1)
  assert.match(differentNetErrors[0].message, /via and pad are not connected/i)
  assert.equal(
    differentNet.circuitJson.filter((element) => element.type === "pcb_via")
      .length,
    0,
  )
})
