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
  "fixtures/preloaded-diagonal-trace-clearance.simple-route.json",
)

const { AutoroutingPipelineSolver } = await import(
  `${pathToFileURL(capacityPath)}?preloaded-trace-exact-drc=${Date.now()}`
)
const fixture = JSON.parse(await readFile(fixturePath, "utf8"))

const solve = (srj) => {
  const solver = new AutoroutingPipelineSolver(srj, { cacheProvider: null })
  let steps = 0
  while (!solver.solved && !solver.failed && steps < 1_000_000) {
    solver.step()
    steps += 1
  }
  assert.ok(steps < 1_000_000, "fixture must terminate within its hard step cap")
  return solver
}

const withParallelEdgeGap = (edgeGap) => {
  const srj = structuredClone(fixture)
  const traceCenterDistance = edgeGap + srj.minTraceWidth
  const perpendicularOffset = traceCenterDistance / Math.SQRT2
  const route = srj.traces[1].route
  route[0].x = -2 - perpendicularOffset
  route[0].y = -2 + perpendicularOffset
  route[1].x = 2 - perpendicularOffset
  route[1].y = 2 + perpendicularOffset
  return srj
}

const cleanSolver = solve(withParallelEdgeGap(0.16))
assert.equal(cleanSolver.failed, false, cleanSolver.error)
assert.equal(cleanSolver.solved, true)
assert.equal(
  cleanSolver.exactGeometryDrcForceImproveSolver?.stats
    ?.drcBranchPortfolioFinalDrcIssueCount,
  0,
  "routing-only AABB approximations must not reject exact preloaded copper",
)

const violatingSolver = solve(withParallelEdgeGap(0.14))
assert.equal(violatingSolver.solved, false)
assert.equal(violatingSolver.failed, true)
assert.match(violatingSolver.error ?? "", /Unresolved DRC issues after exact repair: 1/)
assert.deepEqual(
  violatingSolver.exactGeometryDrcForceImproveSolver?.inputSnapshot?.errors.map(
    (error) => error.message,
  ),
  ["PCB trace fixed_a is too close to PCB trace fixed_b (gap: 0.140mm)"],
  "the exact dynamic line check must still reject real sub-clearance copper",
)

const throughObstacleSrj = structuredClone(fixture)
throughObstacleSrj.traces = [
  {
    type: "pcb_trace",
    pcb_trace_id: "fixed_through",
    connection_name: "net_through",
    connectsTo: ["pcb_port_through_start", "pcb_port_through_end"],
    route: [
      {
        route_type: "through_obstacle",
        start: { x: -1, y: 0 },
        end: { x: 1, y: 0 },
        width: 0.2,
        from_layer: "top",
        to_layer: "bottom",
      },
    ],
  },
  {
    type: "pcb_trace",
    pcb_trace_id: "fixed_crossing",
    connection_name: "net_crossing",
    connectsTo: ["pcb_port_crossing_start", "pcb_port_crossing_end"],
    route: [
      { route_type: "wire", x: 0, y: -1, width: 0.2, layer: "top" },
      { route_type: "wire", x: 0, y: 1, width: 0.2, layer: "top" },
    ],
  },
]
const throughObstacleSolver = solve(throughObstacleSrj)
assert.equal(throughObstacleSolver.solved, false)
assert.equal(throughObstacleSolver.failed, true)
assert.ok(
  throughObstacleSolver.exactGeometryDrcForceImproveSolver?.inputSnapshot?.errors.some(
    (error) =>
      error.pcb_trace_id === "fixed_crossing" &&
      error.message.includes("accidental contact"),
  ),
  "through-obstacle copper lacks an exact dynamic representation, so its conservative obstacle must remain in DRC",
)

process.stdout.write("preloaded trace exact DRC regression passed\n")
