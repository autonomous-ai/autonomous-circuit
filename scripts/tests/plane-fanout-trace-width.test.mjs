#!/usr/bin/env node
import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import {
  cp,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises"
import { register } from "node:module"
import { tmpdir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"
import test from "node:test"

import {
  TOOLCHAIN_PATCHES,
  applyToolchainPatch,
} from "../build/apply-toolchain-patches.mjs"

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
const toolchainDir =
  process.env.TSCIRCUIT_TOOLCHAIN_DIR ?? join(repoRoot, "toolchain")
const sourceNodeModules = join(toolchainDir, "node_modules")
const fixturePath = join(
  repoRoot,
  "scripts",
  "tests",
  "fixtures",
  "plane-fanout-mixed-width.simple-route.json",
)
const digest = (value) => createHash("sha256").update(value).digest("hex")

register(
  new URL(
    "./fixtures/export-plane-fanout-width-loader.mjs",
    import.meta.url,
  ),
  import.meta.url,
)

const reversePatch = (source, patch) => {
  let result = source
  for (const replacement of [...patch.replacements].reverse()) {
    const expectedMatches = replacement.expectedMatches ?? 1
    const actualMatches = result.split(replacement.after).length - 1
    assert.equal(
      actualMatches,
      expectedMatches,
      `reverse ${replacement.label}`,
    )
    result = result.split(replacement.after).join(replacement.before)
  }
  assert.equal(digest(result), patch.pristineSha256)
  return result
}

const createPrivateCoreInstall = async (root, patch) => {
  const nodeModules = join(root, "node_modules")
  const privateScope = join(nodeModules, "@tscircuit")
  await mkdir(privateScope, { recursive: true })

  for (const entry of await readdir(sourceNodeModules)) {
    if (entry === "@tscircuit") continue
    await symlink(join(sourceNodeModules, entry), join(nodeModules, entry), "dir")
  }
  for (const entry of await readdir(join(sourceNodeModules, "@tscircuit"))) {
    if (entry === "core") continue
    await symlink(
      join(sourceNodeModules, "@tscircuit", entry),
      join(privateScope, entry),
      "dir",
    )
  }

  const packageDir = join(privateScope, "core")
  await cp(join(sourceNodeModules, "@tscircuit", "core"), packageDir, {
    recursive: true,
  })
  const distPath = join(packageDir, patch.file)
  let predecessor = await readFile(distPath, "utf8")
  const installedDigest = digest(predecessor)
  if (installedDigest === patch.patchedSha256) {
    predecessor = reversePatch(predecessor, patch)
    await writeFile(distPath, predecessor)
  } else {
    assert.equal(
      installedDigest,
      patch.pristineSha256,
      `installed core has unknown SHA-256 ${installedDigest}`,
    )
  }
  return { nodeModules, distPath }
}

const solveFanout = (FanoutAutorouter, srj) => {
  const options = {
    mode: "fanout",
    fanoutRoutingLayers: ["top"],
    componentNamesById: new Map(),
  }
  const fanoutBounds = FanoutAutorouter.resolveFanoutBounds(srj, options)
  return new FanoutAutorouter(srj, {
    ...options,
    fanoutBounds,
  }).solveSync()
}

const wires = (trace) =>
  trace.route.filter((point) => point.route_type === "wire")

const assertConnectionWidth = (traces, connectionName, expectedWidth) => {
  const trace = traces.find(
    (candidate) => candidate.connection_name === connectionName,
  )
  assert.ok(trace, `missing ${connectionName}`)
  assert.ok(wires(trace).length > 0, `${connectionName} has no wire records`)
  assert.ok(
    wires(trace).every((point) => point.width === expectedWidth),
    `${connectionName} expected ${expectedWidth}mm, got ${wires(trace).map((point) => point.width)}`,
  )
}

const assertExactDrc = (AutoroutingPipelineSolver, srj, traces) => {
  const solver = new AutoroutingPipelineSolver(
    {
      ...structuredClone(srj),
      connections: [],
      buses: [],
      traces,
    },
    { cacheProvider: null },
  )
  let steps = 0
  while (!solver.solved && !solver.failed && steps < 1_000_000) {
    solver.step()
    steps += 1
  }
  assert.ok(steps < 1_000_000, "exact DRC must terminate")
  assert.equal(solver.failed, false, solver.error)
  assert.equal(solver.solved, true)
  assert.equal(
    solver.exactGeometryDrcForceImproveSolver?.stats
      ?.drcBranchPortfolioFinalDrcIssueCount,
    0,
  )
}

const mirrorSrj = (srj) => {
  const reverseLayer = (layer) =>
    layer === "top" ? "bottom" : layer === "bottom" ? "top" : layer
  const mirrored = structuredClone(srj)
  for (const obstacle of mirrored.obstacles) {
    obstacle.center.x *= -1
    obstacle.layers = obstacle.layers.map(reverseLayer)
  }
  for (const connection of mirrored.connections) {
    for (const point of connection.pointsToConnect) {
      point.x *= -1
      point.layer = reverseLayer(point.layer)
    }
  }
  for (const bus of mirrored.buses) {
    if (bus.termination?.type === "plane") {
      bus.termination.layer = reverseLayer(bus.termination.layer)
    }
  }
  return mirrored
}

test("plane fanout preserves exact per-connection widths and fails closed on an undercut", async (t) => {
  const patch = TOOLCHAIN_PATCHES.find(
    (candidate) =>
      candidate.packageName === "@tscircuit/core" &&
      candidate.replacements.some((replacement) =>
        replacement.label.includes("each plane dogbone"),
      ),
  )
  assert.ok(patch, "plane fanout connection-width patch must be in the manifest")

  const root = await mkdtemp(join(tmpdir(), "plane-fanout-width-test-"))
  t.after(() => rm(root, { recursive: true, force: true }))
  const { nodeModules, distPath } = await createPrivateCoreInstall(root, patch)
  const coreUrl = pathToFileURL(distPath).href

  const srj = JSON.parse(await readFile(fixturePath, "utf8"))

  const before = await import(`${coreUrl}?plane-width-before=${Date.now()}`)
  const oldTraces = solveFanout(before.FanoutAutorouter, srj)
  assertConnectionWidth(oldTraces, "source_trace_branch", 0.15)
  assertConnectionWidth(oldTraces, "source_trace_rail", 0.15)
  assertConnectionWidth(oldTraces, "source_trace_control", 0.15)
  assert.equal(before.Group_assertNoRequestedTraceWidthUndercut, undefined)

  assert.deepEqual(await applyToolchainPatch(nodeModules, patch, false), {
    packageName: "@tscircuit/core",
    status: "patched",
  })
  assert.equal(digest(await readFile(distPath, "utf8")), patch.patchedSha256)
  assert.deepEqual(await applyToolchainPatch(nodeModules, patch, false), {
    packageName: "@tscircuit/core",
    status: "already-patched",
  })

  const after = await import(`${coreUrl}?plane-width-after=${Date.now()}`)
  const traces = solveFanout(after.FanoutAutorouter, srj)
  assertConnectionWidth(traces, "source_trace_branch", 0.2)
  assertConnectionWidth(traces, "source_trace_rail", 0.4)
  assertConnectionWidth(traces, "source_trace_control", 0.15)

  const vias = traces
    .flatMap((trace) => trace.route)
    .filter((point) => point.route_type === "via")
  assert.ok(vias.length > 0)
  assert.ok(
    vias.every(
      (point) =>
        point.via_diameter === 0.6 && point.via_hole_diameter === 0.3,
    ),
    "per-connection trace widths must not inflate signal vias",
  )

  const capacityPath = join(
    sourceNodeModules,
    "@tscircuit",
    "capacity-autorouter",
    "dist",
    "index.js",
  )
  const { AutoroutingPipelineSolver } = await import(
    `${pathToFileURL(capacityPath).href}?plane-width-drc=${Date.now()}`
  )
  assertExactDrc(AutoroutingPipelineSolver, srj, traces)

  const mirroredSrj = mirrorSrj(srj)
  const mirroredTraces = solveFanout(after.FanoutAutorouter, mirroredSrj)
  assertConnectionWidth(mirroredTraces, "source_trace_branch", 0.2)
  assertConnectionWidth(mirroredTraces, "source_trace_rail", 0.4)
  assertConnectionWidth(mirroredTraces, "source_trace_control", 0.15)
  assertExactDrc(AutoroutingPipelineSolver, mirroredSrj, mirroredTraces)

  const declaredBusWidthSrj = structuredClone(srj)
  declaredBusWidthSrj.buses.find(
    (bus) => bus.busId === "TR_CONTROL",
  ).traceWidth = 0.25
  const declaredBusWidthTraces = solveFanout(
    after.FanoutAutorouter,
    declaredBusWidthSrj,
  )
  assertConnectionWidth(
    declaredBusWidthTraces,
    "source_trace_control",
    0.25,
  )
  assertConnectionWidth(
    declaredBusWidthTraces,
    "source_trace_rail",
    0.4,
  )

  const tampered = structuredClone(traces)
  for (const point of wires(
    tampered.find(
      (trace) => trace.connection_name === "source_trace_rail",
    ),
  )) {
    point.width = 0.15
  }
  assert.throws(
    () =>
      after.Group_assertNoRequestedTraceWidthUndercut({
        simpleRouteJson: srj,
        traces: tampered,
        phaseName: "mixed-plane-width",
      }),
    /source_trace_rail.*requested minimum trace width 0\.4mm with 0\.15mm copper/,
  )

  const invalid = structuredClone(srj)
  invalid.connections.find(
    (connection) => connection.name === "source_trace_rail",
  ).nominalTraceWidth = -0.25
  assert.throws(
    () => solveFanout(after.FanoutAutorouter, invalid),
    /nominalTraceWidth must be a positive number/,
  )

  assert.notEqual(
    after.getLocalAutoroutingCacheKey(srj, { preset: "fanout" }),
    after.getLocalAutoroutingCacheKey(srj, {
      preset: "fanout",
      planeFanoutConnectionWidths: "per-connection-v1",
    }),
    "the new fanout semantics must invalidate pre-patch route-cache entries",
  )
})
