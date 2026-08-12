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
  "fixtures/same-net-fanout-local-tie.simple-route.json",
)
const { AutoroutingPipelineSolver } = await import(
  `${pathToFileURL(capacityPath)}?dynamic-trace-connectivity=${Date.now()}`
)
const srj = JSON.parse(await readFile(fixturePath, "utf8"))
const solver = new AutoroutingPipelineSolver(srj, { cacheProvider: null })
let steps = 0
while (!solver.solved && !solver.failed && steps < 1_000_000) {
  solver.step()
  steps += 1
}

assert.ok(steps < 1_000_000, "fixture must terminate within its hard step cap")
assert.equal(solver.failed, false, solver.error)
assert.equal(solver.solved, true)
assert.equal(
  solver.connMap.areIdsConnected(
    "source_trace_fanout",
    "source_trace_local",
  ),
  true,
)
assert.deepEqual(
  solver.exactGeometryDrcForceImproveSolver?.inputSnapshot?.errors,
  [],
  "a fanout and local tie on the same source net may meet at their shared pad",
)
assert.equal(
  solver.exactGeometryDrcForceImproveSolver?.stats
    ?.drcBranchPortfolioFinalDrcIssueCount,
  0,
)

process.stdout.write("dynamic trace connectivity regression passed\n")
