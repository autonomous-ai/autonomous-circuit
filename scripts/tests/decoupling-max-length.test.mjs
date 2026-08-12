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
const checksPath = join(
  nodeModules,
  "@tscircuit",
  "checks",
  "dist",
  "index.js",
)

const serializedErrors = (circuitJson) =>
  circuitJson.filter((element) => String(element.type ?? "").endsWith("_error"))

const compileFixture = async (t, fixtureName) => {
  const root = await mkdtemp(join(tmpdir(), `decoupling-max-${fixtureName}-`))
  t.after(() => rm(root, { recursive: true, force: true }))
  const sourceName = `${fixtureName}.tsx`
  await copyFile(join(fixtureDir, sourceName), join(root, sourceName))
  await writeFile(
    join(root, "package.json"),
    JSON.stringify({ name: `decoupling-max-${fixtureName}`, private: true }),
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
    ],
    {
      cwd: root,
      encoding: "utf8",
      timeout: 60_000,
      env: {
        ...process.env,
        CIRCUIT_PARTS_ENGINE: "off",
      },
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
  return { circuitJson, result }
}

const sourceTracesByName = (circuitJson) =>
  new Map(
    circuitJson
      .filter((element) => element.type === "source_trace")
      .map((element) => [element.name, element]),
  )

const pcbPortForSourcePort = (circuitJson, sourcePortId) =>
  circuitJson.find(
    (element) =>
      element.type === "pcb_port" && element.source_port_id === sourcePortId,
  )

test("capacitor tree and plane branches do not inherit an unrelated decoupling limit", async (t) => {
  try {
    await access(cliMainPath)
  } catch {
    t.skip("pinned tscircuit CLI is not installed")
    return
  }

  const { circuitJson, result } = await compileFixture(
    t,
    "decoupling-max-length-tree",
  )
  assert.deepEqual(serializedErrors(circuitJson), [], result.stdout + result.stderr)

  const traces = sourceTracesByName(circuitJson)
  assert.equal(traces.get("TR_CAP_RAIL")?.max_length, undefined)
  assert.equal(traces.get("TR_RAIL_BOUNDARY")?.max_length, undefined)
  assert.equal(traces.get("TR_C1_GND")?.max_length, 1)
  assert.equal(traces.get("TR_C2_GND")?.max_length, 1)

  const remotePort = pcbPortForSourcePort(
    circuitJson,
    traces.get("TR_REMOTE_GND").connected_source_port_ids[0],
  )
  for (const traceName of ["TR_C1_GND", "TR_C2_GND"]) {
    const sourceTrace = traces.get(traceName)
    const localPort = pcbPortForSourcePort(
      circuitJson,
      sourceTrace.connected_source_port_ids[0],
    )
    assert.ok(Math.hypot(localPort.x - remotePort.x, localPort.y - remotePort.y) > 1)

    const outputTrace = circuitJson.find(
      (element) =>
        element.type === "pcb_trace" &&
        element.source_trace_id === sourceTrace.source_trace_id,
    )
    assert.ok(outputTrace, traceName)
    assert.equal(outputTrace.route.length, 1)
    assert.equal(outputTrace.route[0].route_type, "wire")
    assert.equal(outputTrace.route[0].is_inside_copper_pour, true)
    assert.equal(outputTrace.route[0].start_pcb_port_id, localPort.pcb_port_id)
    assert.equal(outputTrace.route[0].end_pcb_port_id, localPort.pcb_port_id)
  }

  const { runAllChecks } = await import(pathToFileURL(checksPath).href)
  assert.deepEqual(await runAllChecks(circuitJson), [])
})

test("direct capacitor-to-device limits remain inferred and explicit", async (t) => {
  try {
    await access(cliMainPath)
  } catch {
    t.skip("pinned tscircuit CLI is not installed")
    return
  }

  const { circuitJson, result } = await compileFixture(
    t,
    "decoupling-max-length-direct",
  )
  const traces = sourceTracesByName(circuitJson)
  assert.equal(traces.get("TR_EXPLICIT_LOCAL")?.max_length, 2)
  assert.equal(traces.get("TR_INFERRED_LOCAL")?.max_length, 1)

  const preflightError = serializedErrors(circuitJson).find(
    (error) =>
      error.type === "pcb_autorouting_error" &&
      error.message.includes("maximum length"),
  )
  assert.ok(preflightError, result.stdout + result.stderr)
  assert.match(
    preflightError.message,
    /2mm maximum length for \.C1 > \.pin1 to \.U1 > \.VDD/,
  )
  assert.match(preflightError.message, /\(1 additional violation\)/)

  for (const traceName of ["TR_EXPLICIT_LOCAL", "TR_INFERRED_LOCAL"]) {
    const sourceTrace = traces.get(traceName)
    assert.equal(
      circuitJson.some(
        (element) =>
          element.type === "pcb_trace" &&
          element.source_trace_id === sourceTrace.source_trace_id,
      ),
      false,
      `${traceName} must not route after the fail-closed straight-line proof`,
    )
  }
})
