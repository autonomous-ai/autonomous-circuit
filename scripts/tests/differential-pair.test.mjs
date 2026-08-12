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

const serializedErrors = (circuitJson) =>
  circuitJson.filter((element) => String(element.type ?? "").endsWith("_error"))

const compileFixture = async (t, fixtureName) => {
  const root = await mkdtemp(join(tmpdir(), `${fixtureName}-`))
  t.after(() => rm(root, { recursive: true, force: true }))
  const sourceName = `${fixtureName}.tsx`
  const debugDir = join(root, "autorouter-debug")
  await copyFile(join(fixtureDir, sourceName), join(root, sourceName))
  await writeFile(
    join(root, "package.json"),
    JSON.stringify({ name: fixtureName, private: true }),
  )

  const result = spawnSync(
    process.execPath,
    [
      "--import",
      tsxLoaderPath,
      cliMainPath,
      "build",
      sourceName,
      "--disable-parts-engine",
      "--autorouter-debug",
      "--autorouter-debug-dir",
      debugDir,
      "--autorouter-dump-srj",
      "all",
    ],
    {
      cwd: root,
      encoding: "utf8",
      timeout: 45_000,
      env: { ...process.env, CIRCUIT_PARTS_ENGINE: "off" },
    },
  )
  assert.equal(
    result.signal,
    null,
    `cold compile timed out:\n${result.stdout}\n${result.stderr}`,
  )
  const circuitJson = JSON.parse(
    await readFile(join(root, "dist", fixtureName, "circuit.json"), "utf8"),
  )
  return { circuitJson, debugDir, result }
}

const solveWithPipeline9 = async (srj) => {
  const { AutoroutingPipelineSolver9_PreloadedTraceGraph } = await import(
    pathToFileURL(capacityPath).href
  )
  const solver = new AutoroutingPipelineSolver9_PreloadedTraceGraph(srj, {
    effort: 1,
  })
  let steps = 0
  while (!solver.solved && !solver.failed && steps < 250_000) {
    solver.step()
    steps += 1
  }
  assert.ok(steps < 250_000, "Pipeline9 exceeded the bounded regression budget")
  assert.equal(solver.failed, false, solver.error)
  assert.equal(solver.solved, true, solver.error)
  return solver
}

test("direct two-port differential pair survives the reversing-spine candidate and preserves constraints", async (t) => {
  try {
    await access(cliMainPath)
  } catch {
    t.skip("pinned tscircuit CLI is not installed")
    return
  }

  const { circuitJson, debugDir, result } = await compileFixture(
    t,
    "differential-pair-direct",
  )
  assert.deepEqual(serializedErrors(circuitJson), [], result.stdout + result.stderr)

  const srj = JSON.parse(
    await readFile(join(debugDir, "phase-0.input.simple-route.json"), "utf8"),
  )
  assert.deepEqual(srj.differentialPairs, [
    {
      connectionNames: ["source_trace_0", "source_trace_1"],
      lengthTolerance: 0.2,
      traceGap: 1.25,
      maxUncoupledLength: 1,
    },
  ])

  const routed = circuitJson
    .filter((element) => element.type === "pcb_trace")
    .sort((left, right) => left.source_trace_id.localeCompare(right.source_trace_id))
  assert.equal(routed.length, 2)
  for (const trace of routed) {
    assert.equal(trace.route.length, 2)
    assert.ok(trace.route.every((point) => point.route_type === "wire"))
    assert.ok(trace.route.every((point) => point.layer === "top"))
  }
  const lengths = routed.map((trace) =>
    Math.hypot(
      trace.route[1].x - trace.route[0].x,
      trace.route[1].y - trace.route[0].y,
    ),
  )
  assert.ok(Math.abs(lengths[0] - lengths[1]) <= 1e-8)
  assert.ok(
    Math.abs(Math.abs(routed[0].route[0].y - routed[1].route[0].y) - 1.4) <=
      1e-8,
  )

  const pipeline9 = await solveWithPipeline9(srj)
  assert.equal(pipeline9.getOutputSimpleRouteJson().traces.length, 2)
})

test("trace-name differential-pair selectors use the selected trace's own endpoints", async (t) => {
  try {
    await access(cliMainPath)
  } catch {
    t.skip("pinned tscircuit CLI is not installed")
    return
  }

  const { circuitJson, debugDir, result } = await compileFixture(
    t,
    "differential-pair-adjacent-connectivity",
  )
  assert.deepEqual(serializedErrors(circuitJson), [], result.stdout + result.stderr)
  const pairWarnings = circuitJson.filter(
    (element) =>
      element.type === "source_property_ignored_warning" &&
      /Differential pair/.test(element.message ?? ""),
  )
  assert.deepEqual(
    pairWarnings,
    [],
    "an adjacent package-internal connection must not make an explicitly named two-port trace ambiguous",
  )

  const sourceTraces = new Map(
    circuitJson
      .filter((element) => element.type === "source_trace")
      .map((trace) => [trace.name, trace]),
  )
  for (const name of ["TR_J1_dp_esd", "TR_J1_dm_esd"]) {
    const trace = sourceTraces.get(name)
    assert.ok(trace)
    assert.equal(trace.connected_source_port_ids.length, 2)
    assert.deepEqual(trace.connected_source_net_ids, [])
  }

  const pairSrj = JSON.parse(
    await readFile(join(debugDir, "phase-2.input.simple-route.json"), "utf8"),
  )
  assert.deepEqual(pairSrj.differentialPairs, [
    {
      connectionNames: ["source_trace_2", "source_trace_3"],
      lengthTolerance: 0.2,
      traceGap: 1.25,
      maxUncoupledLength: 2,
    },
  ])
})

test("maxUncoupledLength rejects a geometrically routed but insufficiently coupled pair", async (t) => {
  try {
    await access(cliMainPath)
  } catch {
    t.skip("pinned tscircuit CLI is not installed")
    return
  }

  const { circuitJson, debugDir, result } = await compileFixture(
    t,
    "differential-pair-uncoupled",
  )
  const error = serializedErrors(circuitJson).find(
    (element) => element.type === "pcb_autorouting_error",
  )
  assert.ok(error, result.stdout + result.stderr)
  assert.match(error.message, /exceeds maxUncoupledLength 0\.1mm/)
  assert.match(error.message, /mm of uncoupled copper/)
  assert.equal(
    circuitJson.some((element) => element.type === "pcb_trace"),
    false,
  )

  const srj = JSON.parse(
    await readFile(join(debugDir, "phase-0.input.simple-route.json"), "utf8"),
  )
  const pipeline9 = await solveWithPipeline9(srj)
  assert.throws(
    () => pipeline9.getOutputSimpleRouteJson(),
    /exceeds maxUncoupledLength 0\.1mm/,
  )
})

test("named-net differential-pair selectors fail closed instead of losing trace identity", async (t) => {
  try {
    await access(cliMainPath)
  } catch {
    t.skip("pinned tscircuit CLI is not installed")
    return
  }

  const { circuitJson, result } = await compileFixture(
    t,
    "differential-pair-named-net",
  )
  const error = serializedErrors(circuitJson).find(
    (element) => element.type === "pcb_autorouting_error",
  )
  assert.ok(error, result.stdout + result.stderr)
  assert.match(error.message, /must select a direct two-port source trace/)
  assert.match(error.message, /named-net aggregate and composed source traces/)
  assert.equal(
    circuitJson.some((element) => element.type === "pcb_trace"),
    false,
  )
})

test("maxUncoupledLength without pcbTraceGap fails closed because coupling is undefined", async (t) => {
  try {
    await access(cliMainPath)
  } catch {
    t.skip("pinned tscircuit CLI is not installed")
    return
  }

  const { circuitJson, result } = await compileFixture(
    t,
    "differential-pair-missing-gap",
  )
  const error = serializedErrors(circuitJson).find(
    (element) => element.type === "pcb_autorouting_error",
  )
  assert.ok(error, result.stdout + result.stderr)
  assert.match(error.message, /declares maxUncoupledLength without pcbTraceGap/)
  assert.match(error.message, /coupling threshold is undefined/)
  assert.equal(
    circuitJson.some((element) => element.type === "pcb_trace"),
    false,
  )
})
