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
const fixturePath = join(
  repoRoot,
  "scripts",
  "tests",
  "fixtures",
  "automatic-via-style.tsx",
)

test("trace-local automatic via sizes override defaults and remain floored at board minima", async (t) => {
  try {
    await access(cliMainPath)
  } catch {
    t.skip("pinned tscircuit CLI is not installed")
    return
  }

  const root = await mkdtemp(join(tmpdir(), "automatic-via-style-"))
  t.after(() => rm(root, { recursive: true, force: true }))
  await copyFile(fixturePath, join(root, "automatic-via-style.tsx"))
  await writeFile(
    join(root, "package.json"),
    JSON.stringify({ name: "automatic-via-style-fixture", private: true }),
  )

  const result = spawnSync(
    process.execPath,
    [
      "--import",
      tsxLoaderPath,
      cliMainPath,
      "build",
      "automatic-via-style.tsx",
      "--disable-parts-engine",
    ],
    {
      cwd: root,
      encoding: "utf8",
      timeout: 30_000,
      env: { ...process.env, CIRCUIT_PARTS_ENGINE: "off" },
    },
  )
  assert.equal(
    result.signal,
    null,
    `cold compile timed out:\n${result.stdout}\n${result.stderr}`,
  )

  const circuitJson = JSON.parse(
    await readFile(
      join(root, "dist", "automatic-via-style", "circuit.json"),
      "utf8",
    ),
  )
  const serializedErrors = circuitJson.filter((element) =>
    String(element.type ?? "").endsWith("_error"),
  )
  assert.deepEqual(serializedErrors, [], result.stdout + result.stderr)

  const sourceById = new Map(
    circuitJson
      .filter((element) => element.type === "source_trace")
      .map((element) => [element.source_trace_id, element]),
  )
  const traceById = new Map(
    circuitJson
      .filter((element) => element.type === "pcb_trace")
      .map((element) => [element.pcb_trace_id, element]),
  )
  const expectedByName = new Map([
    ["TR_POWER_FANOUT", [0.8, 0.5]],
    ["TR_SIGNAL_FANOUT", [0.6, 0.3]],
  ])

  for (const [sourceName, expected] of expectedByName) {
    const source = [...sourceById.values()].find(
      (element) => element.name === sourceName,
    )
    assert.ok(source, sourceName)
    const traces = [...traceById.values()].filter(
      (element) => element.source_trace_id === source.source_trace_id,
    )
    assert.equal(traces.length, 1, sourceName)
    const routeVias = traces[0].route.filter(
      (point) => point.route_type === "via",
    )
    assert.equal(routeVias.length, 1, sourceName)
    assert.deepEqual(
      [routeVias[0].via_diameter, routeVias[0].via_hole_diameter],
      expected,
      `${sourceName} route point`,
    )

    const vias = circuitJson.filter(
      (element) =>
        element.type === "pcb_via" &&
        element.pcb_trace_id === traces[0].pcb_trace_id,
    )
    assert.equal(vias.length, 1, sourceName)
    assert.deepEqual(
      [vias[0].outer_diameter, vias[0].hole_diameter],
      expected,
      `${sourceName} standalone pcb_via`,
    )
  }

  const checksPath = join(
    nodeModules,
    "@tscircuit",
    "checks",
    "dist",
    "index.js",
  )
  const { runAllChecks } = await import(pathToFileURL(checksPath).href)
  assert.deepEqual(await runAllChecks(circuitJson), [])
})
