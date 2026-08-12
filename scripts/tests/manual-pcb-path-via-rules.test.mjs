#!/usr/bin/env node
import assert from "node:assert/strict"
import { access, copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { spawnSync } from "node:child_process"
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
  "manual-pcb-path-via.tsx",
)

test("manual pcbPath vias survive autorouter reinsertion with scoped sizes and board floors", async (t) => {
  try {
    await access(cliPath)
  } catch {
    t.skip("pinned tscircuit CLI is not installed")
    return
  }

  const root = await mkdtemp(join(tmpdir(), "manual-pcb-path-via-"))
  t.after(() => rm(root, { recursive: true, force: true }))
  await mkdir(root, { recursive: true })
  await copyFile(fixturePath, join(root, "manual-pcb-path-via.tsx"))
  await writeFile(
    join(root, "package.json"),
    JSON.stringify({ name: "manual-pcb-path-via-fixture", private: true }),
  )

  const result = spawnSync(
    process.execPath,
    [
      "--import",
      tsxLoaderPath,
      cliMainPath,
      "build",
      "manual-pcb-path-via.tsx",
      "--disable-parts-engine",
    ],
    {
      cwd: root,
      encoding: "utf8",
      timeout: 30_000,
      env: {
        ...process.env,
        CIRCUIT_PARTS_ENGINE: "off",
        TMPDIR: root,
      },
    },
  )
  assert.equal(result.signal, null, `cold compile timed out:\n${result.stderr}`)

  const artifactPath = join(
    root,
    "dist",
    "manual-pcb-path-via",
    "circuit.json",
  )
  const circuitJson = JSON.parse(await readFile(artifactPath, "utf8"))
  const serializedErrors = circuitJson.filter((element) =>
    String(element.type ?? "").endsWith("_error"),
  )
  assert.deepEqual(serializedErrors, [], result.stdout + result.stderr)

  const sources = new Map(
    circuitJson
      .filter((element) => element.type === "source_trace")
      .map((element) => [element.source_trace_id, element]),
  )
  const traces = new Map(
    circuitJson
      .filter((element) => element.type === "pcb_trace")
      .map((element) => [element.pcb_trace_id, element]),
  )
  const viasBySourceName = new Map()
  for (const via of circuitJson.filter((element) => element.type === "pcb_via")) {
    const trace = traces.get(via.pcb_trace_id)
    const sourceName = sources.get(trace?.source_trace_id)?.name
    const vias = viasBySourceName.get(sourceName) ?? []
    vias.push(via)
    viasBySourceName.set(sourceName, vias)
  }

  const signalVias = viasBySourceName.get("TR_SIGNAL_FIXED") ?? []
  assert.equal(signalVias.length, 2)
  assert.ok(
    signalVias.every(
      (via) => via.outer_diameter === 0.6 && via.hole_diameter === 0.3,
    ),
    "a smaller local style must be floored at the board's 0.6/0.3 manufacturing minima",
  )

  const powerVias = viasBySourceName.get("TR_POWER_FIXED") ?? []
  assert.equal(powerVias.length, 2)
  assert.ok(
    powerVias.every(
      (via) => via.outer_diameter === 0.8 && via.hole_diameter === 0.5,
    ),
    "a local pcbStyle must retain the power path's legal 0.8/0.5 via contract",
  )

  for (const trace of traces.values()) {
    for (const point of trace.route.filter((routePoint) => routePoint.route_type === "via")) {
      const sourceName = sources.get(trace.source_trace_id)?.name
      const expected = sourceName === "TR_POWER_FIXED" ? [0.8, 0.5] : [0.6, 0.3]
      assert.deepEqual(
        [point.via_diameter, point.via_hole_diameter],
        expected,
        "serialized fixed copper must carry the same legal dimensions as pcb_via",
      )
    }
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
