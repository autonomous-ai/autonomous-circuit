#!/usr/bin/env node
import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { register } from "node:module"
import { dirname, join, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath, pathToFileURL } from "node:url"

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
register(
  new URL("./fixtures/export-fanout-autorouter-loader.mjs", import.meta.url),
  import.meta.url,
)

const corePath =
  process.env.TSCIRCUIT_CORE_PATH ??
  join(
    repoRoot,
    "toolchain",
    "node_modules",
    "@tscircuit",
    "core",
    "dist",
    "index.js",
  )
const capacityPath =
  process.env.TSCIRCUIT_CAPACITY_PATH ??
  join(
    repoRoot,
    "toolchain",
    "node_modules",
    "@tscircuit",
    "capacity-autorouter",
    "dist",
    "index.js",
  )
const fixturePath = join(
  repoRoot,
  "scripts",
  "tests",
  "fixtures",
  "same-layer-plane-fanout.simple-route.json",
)

const traceLength = (trace) => {
  const wires = trace.route.filter((point) => point.route_type === "wire")
  let length = 0
  for (let index = 1; index < wires.length; index++) {
    length += Math.hypot(
      wires[index].x - wires[index - 1].x,
      wires[index].y - wires[index - 1].y,
    )
  }
  return length
}

test("same-layer plane contact emits no fabricated via while cross-layer fanout stays local", async (t) => {
  let FanoutAutorouter
  let AutoroutingPipelineSolver
  try {
    ;({ FanoutAutorouter } = await import(pathToFileURL(corePath).href))
    ;({ AutoroutingPipelineSolver } = await import(
      `${pathToFileURL(capacityPath).href}?same-layer-plane=${Date.now()}`
    ))
  } catch (error) {
    if (String(error).includes("ERR_MODULE_NOT_FOUND")) {
      t.skip("pinned toolchain is not installed")
      return
    }
    throw error
  }

  const srj = JSON.parse(await readFile(fixturePath, "utf8"))
  const options = {
    mode: "fanout",
    fanoutRoutingLayers: ["top"],
    componentNamesById: new Map(),
  }
  const fanoutBounds = FanoutAutorouter.resolveFanoutBounds(srj, options)
  const router = new FanoutAutorouter(srj, { ...options, fanoutBounds })

  assert.equal(
    router.getFanoutSolverOptions().busDirections.TR_CROSS_LAYER,
    "left",
    "plane direction is inferred from the pad's component-local edge, not its board-global position",
  )

  const traces = router.solveSync()
  assert.equal(traces.length, 2)
  const direct = traces.find(
    (trace) => trace.connection_name === "source_trace_direct",
  )
  const crossLayer = traces.find(
    (trace) => trace.connection_name === "source_trace_cross_layer",
  )
  assert.ok(direct)
  assert.ok(crossLayer)
  assert.equal(
    direct.source_trace_id,
    "source_trace_direct",
    "the plane-contact marker retains its authored source trace identity",
  )

  assert.deepEqual(direct.route, [
    {
      route_type: "wire",
      x: -20,
      y: 0,
      width: 0.2,
      layer: "top",
      start_pcb_port_id: "port_direct",
      is_inside_copper_pour: true,
    },
  ])
  assert.equal(
    direct.route.some((point) => point.route_type === "via"),
    false,
    "a pad already on its target plane must not receive a needless via",
  )
  assert.equal(traceLength(direct), 0)

  assert.equal(
    crossLayer.route.filter((point) => point.route_type === "via").length,
    1,
    "a top pad targeting a bottom plane still needs exactly one via",
  )
  assert.ok(
    traceLength(crossLayer) <= 2,
    `cross-layer dogbone is ${traceLength(crossLayer).toFixed(3)}mm`,
  )

  const drcInput = {
    ...srj,
    connections: [],
    buses: [],
    traces,
  }
  const drcSolver = new AutoroutingPipelineSolver(drcInput, {
    cacheProvider: null,
  })
  let steps = 0
  while (!drcSolver.solved && !drcSolver.failed && steps < 1_000_000) {
    drcSolver.step()
    steps += 1
  }
  assert.ok(steps < 1_000_000, "exact DRC must terminate within its hard step cap")
  assert.equal(drcSolver.failed, false, drcSolver.error)
  assert.equal(
    drcSolver.exactGeometryDrcForceImproveSolver?.stats
      ?.drcBranchPortfolioFinalDrcIssueCount,
    0,
  )
})
