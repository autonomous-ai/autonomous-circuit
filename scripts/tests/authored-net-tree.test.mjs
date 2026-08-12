#!/usr/bin/env node
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import {
  access,
  copyFile,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises"
import { register } from "node:module"
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
const corePath = join(
  nodeModules,
  "@tscircuit",
  "core",
  "dist",
  "index.js",
)
const propsPath = join(
  nodeModules,
  "@tscircuit",
  "props",
  "dist",
  "index.js",
)
const checksPath = join(
  nodeModules,
  "@tscircuit",
  "checks",
  "dist",
  "index.js",
)

const serializedErrors = (circuitJson) =>
  circuitJson.filter((element) => String(element.type ?? "").endsWith("_error"))

const compileFixture = async (t, fixtureName, { debug = true } = {}) => {
  const root = await mkdtemp(join(tmpdir(), `authored-net-tree-${fixtureName}-`))
  t.after(() => rm(root, { recursive: true, force: true }))
  const sourceName = `${fixtureName}.tsx`
  await copyFile(
    join(fixtureDir, `authored-net-tree-${sourceName}`),
    join(root, sourceName),
  )
  await writeFile(
    join(root, "package.json"),
    JSON.stringify({ name: `authored-net-tree-${fixtureName}`, private: true }),
  )

  const debugDir = join(root, "autorouter-debug")
  const args = [
    "--import",
    tsxLoaderPath,
    cliMainPath,
    "build",
    sourceName,
    "--disable-parts-engine",
  ]
  if (debug) {
    args.push(
      "--autorouter-debug",
      "--autorouter-debug-dir",
      debugDir,
      "--autorouter-dump-srj",
      "all",
    )
  }
  const result = spawnSync(process.execPath, args, {
    cwd: root,
    encoding: "utf8",
    timeout: 60_000,
    env: {
      ...process.env,
      CIRCUIT_PARTS_ENGINE: "off",
    },
  })
  assert.equal(
    result.signal,
    null,
    `cold compile timed out:\n${result.stdout}\n${result.stderr}`,
  )

  const artifactPath = join(root, "dist", fixtureName, "circuit.json")
  const circuitJson = JSON.parse(await readFile(artifactPath, "utf8"))
  let phaseInput
  if (debug) {
    phaseInput = JSON.parse(
      await readFile(join(debugDir, "phase-0.input.simple-route.json"), "utf8"),
    )
  }
  return { circuitJson, phaseInput, result }
}

const sourceTraceByName = (circuitJson) =>
  new Map(
    circuitJson
      .filter((element) => element.type === "source_trace")
      .map((element) => [element.name, element]),
  )

const sourcePortIdForPoint = (circuitJson, point) => {
  const pcbPortId = point.pcb_port_id ?? point.pointId
  return circuitJson.find(
    (element) => element.type === "pcb_port" && element.pcb_port_id === pcbPortId,
  )?.source_port_id
}

const assertConnectionMatchesSourceEdge = (
  circuitJson,
  phaseConnection,
  sourceTrace,
) => {
  assert.equal(phaseConnection.source_trace_id, sourceTrace.source_trace_id)
  assert.deepEqual(
    new Set(
      phaseConnection.pointsToConnect.map((point) =>
        sourcePortIdForPoint(circuitJson, point),
      ),
    ),
    new Set(sourceTrace.connected_source_port_ids),
    `${sourceTrace.name} must keep its exact authored endpoint pair`,
  )
  assert.equal(phaseConnection.__preserveConnectionTopology, true)
}

test("authored net trees preserve every non-collinear edge and omit a redundant aggregate", async (t) => {
  try {
    await access(cliMainPath)
  } catch {
    t.skip("pinned tscircuit CLI is not installed")
    return
  }

  const { circuitJson, phaseInput, result } = await compileFixture(
    t,
    "noncollinear",
  )
  assert.deepEqual(serializedErrors(circuitJson), [], result.stdout + result.stderr)

  const sources = sourceTraceByName(circuitJson)
  const expectedEdges = [
    ["TR_LOCAL_1", 0.21],
    ["TR_LOCAL_2", 0.22],
    ["TR_LOCAL_3", 0.23],
  ]
  assert.equal(phaseInput.connections.length, expectedEdges.length)
  assert.ok(
    phaseInput.connections.every((connection) =>
      connection.source_trace_id?.startsWith("source_trace_"),
    ),
    "the fully authored rail must not emit a redundant source_net connection",
  )

  for (const [name, width] of expectedEdges) {
    const sourceTrace = sources.get(name)
    assert.ok(sourceTrace, name)
    const connection = phaseInput.connections.find(
      (candidate) => candidate.source_trace_id === sourceTrace.source_trace_id,
    )
    assert.ok(connection, name)
    assertConnectionMatchesSourceEdge(circuitJson, connection, sourceTrace)
    assert.equal(connection.nominalTraceWidth, width)
    assert.equal(connection.width, width)
  }

  const outputTraces = circuitJson.filter(
    (element) => element.type === "pcb_trace",
  )
  assert.equal(outputTraces.length, expectedEdges.length)
  for (const [name, width] of expectedEdges) {
    const sourceTrace = sources.get(name)
    const output = outputTraces.find(
      (trace) => trace.source_trace_id === sourceTrace.source_trace_id,
    )
    assert.ok(output, name)
    assert.ok(
      output.route
        .filter((point) => point.route_type === "wire")
        .every((point) => point.width === width),
      `${name} must retain ${width}mm copper`,
    )
  }

  const { runAllChecks } = await import(pathToFileURL(checksPath).href)
  assert.deepEqual(await runAllChecks(circuitJson), [])
})

test("two authored subtrees contract to boundary ports while their rail backbone remains routable", async (t) => {
  try {
    await access(cliMainPath)
  } catch {
    t.skip("pinned tscircuit CLI is not installed")
    return
  }

  const { circuitJson, phaseInput, result } = await compileFixture(t, "composed")
  assert.deepEqual(serializedErrors(circuitJson), [], result.stdout + result.stderr)
  const sources = sourceTraceByName(circuitJson)
  const localNames = [
    "TR_A1_BOUNDARY",
    "TR_A2_BOUNDARY",
    "TR_B1_BOUNDARY",
    "TR_B2_BOUNDARY",
  ]
  for (const name of localNames) {
    const sourceTrace = sources.get(name)
    const connection = phaseInput.connections.find(
      (candidate) => candidate.source_trace_id === sourceTrace.source_trace_id,
    )
    assert.ok(connection, name)
    assertConnectionMatchesSourceEdge(circuitJson, connection, sourceTrace)
  }

  const sourceNet = circuitJson.find(
    (element) => element.type === "source_net" && element.name === "PWR",
  )
  assert.ok(sourceNet)
  const backbone = phaseInput.connections.find(
    (connection) => connection.name === sourceNet.source_net_id,
  )
  assert.ok(backbone, "the two subtree boundaries and unmarked load need a backbone")
  const expectedBackbonePortIds = new Set(
    ["TR_A_BOUNDARY", "TR_B_BOUNDARY", "TR_UNMARKED_LOAD"].flatMap(
      (name) => sources.get(name).connected_source_port_ids,
    ),
  )
  assert.deepEqual(
    new Set(
      backbone.pointsToConnect.map((point) =>
        sourcePortIdForPoint(circuitJson, point),
      ),
    ),
    expectedBackbonePortIds,
  )
  assert.equal(backbone.nominalTraceWidth, 0.8)
  assert.equal(backbone.width, 0.8)

  const outputTraces = circuitJson.filter(
    (element) => element.type === "pcb_trace",
  )
  assert.equal(
    outputTraces.filter((trace) => localNames.some(
      (name) => sources.get(name).source_trace_id === trace.connection_name,
    )).length,
    4,
  )
  const backboneOutputTraces = outputTraces.filter(
    (trace) => trace.connection_name === sourceNet.source_net_id,
  )
  assert.equal(
    backboneOutputTraces.length,
    2,
    "three retained backbone points must route as two segments",
  )
  assert.ok(
    backboneOutputTraces.every(
      (trace) => trace.source_trace_id === undefined,
    ),
    "an aggregate rail segment must not impersonate a constrained local source edge",
  )
  assert.equal(
    circuitJson.some(
      (element) =>
        element.type === "pcb_trace_too_long_warning" &&
        backboneOutputTraces.some(
          (trace) => trace.pcb_trace_id === element.pcb_trace_id,
        ),
    ),
    false,
    "local maxLength constraints must not leak onto the aggregate backbone",
  )

  const { runAllChecks } = await import(pathToFileURL(checksPath).href)
  assert.deepEqual(await runAllChecks(circuitJson), [])
})

test("cycles and multiple boundaries fail closed into parsed PCB errors", async (t) => {
  try {
    await access(cliMainPath)
  } catch {
    t.skip("pinned tscircuit CLI is not installed")
    return
  }

  for (const [fixtureName, expectedMessage] of [
    ["invalid-cycle", /expected 2 for an acyclic tree/],
    ["invalid-multiple-boundaries", /exactly one port-to-net boundary, found 2/],
  ]) {
    const { circuitJson } = await compileFixture(t, fixtureName, { debug: false })
    const errors = circuitJson.filter(
      (element) => element.type === "pcb_autorouting_error",
    )
    assert.equal(errors.length, 1, fixtureName)
    assert.match(errors[0].message, expectedMessage)
  }
})

test("the typed marker survives parsing, changes cache identity, and leaves unmarked inputs untouched", async (t) => {
  try {
    await access(corePath)
  } catch {
    t.skip("pinned tscircuit core is not installed")
    return
  }

  const { traceProps } = await import(pathToFileURL(propsPath).href)
  assert.equal(
    traceProps.parse({
      from: ".TP1 > .pin1",
      to: "net.V3_3",
      authoredNetTreeBoundary: true,
    }).authoredNetTreeBoundary,
    true,
  )

  register(
    new URL("./fixtures/export-fanout-autorouter-loader.mjs", import.meta.url),
    import.meta.url,
  )
  const {
    Group_applyAuthoredNetTreeContracts,
    getLocalAutoroutingCacheKey,
  } = await import(
    `${pathToFileURL(corePath).href}?authored-tree-identity=${Date.now()}`
  )
  const unmarkedSrj = { connections: [{ name: "legacy", pointsToConnect: [] }] }
  const unmarkedGroup = {
    selectAll: (kind) => (kind === "trace" ? [{ _parsedProps: {} }] : []),
  }
  assert.equal(
    Group_applyAuthoredNetTreeContracts(unmarkedGroup, unmarkedSrj),
    unmarkedSrj,
    "legacy unmarked routing inputs must be returned byte-for-byte",
  )

  const markedSrj = structuredClone(unmarkedSrj)
  markedSrj.connections[0].__preserveConnectionTopology = true
  assert.notEqual(
    getLocalAutoroutingCacheKey(markedSrj, { preset: "capacity" }),
    getLocalAutoroutingCacheKey(unmarkedSrj, { preset: "capacity" }),
    "the route cache must distinguish an explicit topology-preservation contract",
  )
})
