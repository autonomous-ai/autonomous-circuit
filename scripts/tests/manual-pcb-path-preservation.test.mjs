import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { dirname, join, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath, pathToFileURL } from "node:url"

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
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
const capacityPath = join(
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
  "manual-pcb-path-preservation.circuit.json",
)

test("fixed pcbPath copper is preloaded before a crossing route is solved", async (t) => {
  let getSimpleRouteJsonFromCircuitJson
  let AutoroutingPipelineSolver
  try {
    ;({ getSimpleRouteJsonFromCircuitJson } = await import(
      pathToFileURL(corePath).href
    ))
    ;({ AutoroutingPipelineSolver } = await import(
      pathToFileURL(capacityPath).href
    ))
  } catch (error) {
    if (String(error).includes("ERR_MODULE_NOT_FOUND")) {
      t.skip("pinned toolchain is not installed")
      return
    }
    throw error
  }

  const circuitJson = JSON.parse(await readFile(fixturePath, "utf8"))
  const fixedTraceComponent = {
    source_trace_id: "source_trace_fixed",
    _parsedProps: { pcbPath: [{ x: 0, y: 0 }] },
  }
  const subcircuitComponent = {
    selectAll: (kind) => (kind === "trace" ? [fixedTraceComponent] : []),
  }
  const { simpleRouteJson } = getSimpleRouteJsonFromCircuitJson({
    circuitJson,
    subcircuit_id: "subcircuit_source_group_board",
    subcircuitComponent,
    minTraceWidth: 0.15,
  })

  const fixedTrace = simpleRouteJson.traces?.find(
    (trace) => trace.source_trace_id === "source_trace_fixed",
  )
  assert.ok(fixedTrace, "pcbPath copper must not disappear from the base SRJ")
  assert.deepEqual(fixedTrace.route, circuitJson.at(-1).route)
  assert.equal(
    simpleRouteJson.connections.some(
      (connection) => connection.source_trace_id === "source_trace_fixed",
    ),
    false,
    "fixed copper is preloaded, not routed a second time",
  )

  const { simpleRouteJson: unmarkedRouteState } =
    getSimpleRouteJsonFromCircuitJson({
      circuitJson,
      subcircuit_id: "subcircuit_source_group_board",
      subcircuitComponent: {
        selectAll: (kind) =>
          kind === "trace"
            ? [{ ...fixedTraceComponent, _parsedProps: {} }]
            : [],
      },
      minTraceWidth: 0.15,
    })
  assert.equal(
    unmarkedRouteState.traces,
    undefined,
    "existing autorouter state must not become fixed merely because it is in the DB",
  )

  simpleRouteJson.obstacles.push(
    {
      type: "rect",
      center: { x: 0, y: -5 },
      width: 1,
      height: 1,
      layers: ["top"],
      connectedTo: ["AUTO_VERTICAL"],
    },
    {
      type: "rect",
      center: { x: 0, y: 5 },
      width: 1,
      height: 1,
      layers: ["top"],
      connectedTo: ["AUTO_VERTICAL"],
    },
  )
  simpleRouteJson.connections.push({
    name: "AUTO_VERTICAL",
    pointsToConnect: [
      { x: 0, y: -5, layer: "top" },
      { x: 0, y: 5, layer: "top" },
    ],
    width: 0.15,
    nominalTraceWidth: 0.15,
  })

  const solver = new AutoroutingPipelineSolver(simpleRouteJson)
  let steps = 0
  while (!solver.solved && !solver.failed && steps < 100_000) {
    solver.step()
    steps += 1
  }
  assert.equal(solver.failed, false, solver.error)
  assert.equal(solver.solved, true)
  const output = solver.getOutputSimpleRouteJson()
  const automaticOutput = output.traces.find(
    (trace) => trace.connection_name === "AUTO_VERTICAL",
  )
  assert.ok(automaticOutput)
  assert.ok(
    automaticOutput.route.some(
      (point) => point.route_type === "via" || point.layer === "bottom",
    ),
    "automatic copper must change layer instead of crossing the fixed top trace",
  )
})
