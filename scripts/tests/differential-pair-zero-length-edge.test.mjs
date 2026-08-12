#!/usr/bin/env node
import assert from "node:assert/strict"
import { access } from "node:fs/promises"
import { dirname, join, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath, pathToFileURL } from "node:url"

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
const toolchainDir =
  process.env.TSCIRCUIT_TOOLCHAIN_DIR ?? join(repoRoot, "toolchain")
const capacityPath =
  process.env.CAPACITY_AUTOROUTER_PATH ??
  join(
    toolchainDir,
    "node_modules",
    "@tscircuit",
    "capacity-autorouter",
    "dist",
    "index.js",
  )

const makeSrj = (maxUncoupledLength) => ({
  bounds: { minX: -10, maxX: 10, minY: -14, maxY: 13 },
  layerCount: 2,
  minTraceWidth: 0.15,
  nominalTraceWidth: 0.15,
  minViaDiameter: 0.6,
  minViaHoleDiameter: 0.3,
  minViaPadDiameter: 0.6,
  min_via_hole_diameter: 0.3,
  min_via_pad_diameter: 0.6,
  minTraceToPadEdgeClearance: 0.15,
  minViaEdgeToPadEdgeClearance: 0.15,
  minViaHoleEdgeToViaHoleEdgeClearance: 0.1,
  minPlatedHoleDrillEdgeToDrillEdgeClearance: 0.15,
  minPadEdgeToPadEdgeClearance: 0.1,
  minBoardEdgeClearance: 0.2,
  connections: [
    {
      name: "source_trace_positive",
      source_trace_id: "source_trace_positive",
      nominalTraceWidth: 0.15,
      width: 0.15,
      pointsToConnect: [
        {
          x: -0.249936,
          y: -4.8259568,
          layer: "top",
          pointId: "positive_bottom",
          pcb_port_id: "positive_bottom",
        },
        {
          x: -0.9500000000000001,
          y: 6.2,
          layer: "top",
          pointId: "positive_top",
          pcb_port_id: "positive_top",
        },
      ],
    },
    {
      name: "source_trace_negative",
      source_trace_id: "source_trace_negative",
      nominalTraceWidth: 0.15,
      width: 0.15,
      pointsToConnect: [
        {
          x: 0.249936,
          y: -4.8259568,
          layer: "top",
          pointId: "negative_bottom",
          pcb_port_id: "negative_bottom",
        },
        {
          x: 0.9499999999999998,
          y: 6.2,
          layer: "top",
          pointId: "negative_top",
          pcb_port_id: "negative_top",
        },
      ],
    },
  ],
  obstacles: [
    {
      type: "rect",
      obstacleId: "blocking_component",
      layers: ["top", "bottom"],
      center: { x: 0, y: 0 },
      width: 18,
      height: 6,
      connectedTo: [],
    },
  ],
  traces: [],
  differentialPairs: [
    {
      connectionNames: ["source_trace_positive", "source_trace_negative"],
      lengthTolerance: 3.8,
      traceGap: 0.15,
      maxUncoupledLength,
    },
  ],
})

const runBounded = (Solver, srj) => {
  const solver = new Solver(srj, { effort: 1 })
  let steps = 0
  let thrown = null
  while (!solver.solved && !solver.failed && steps < 120_000) {
    try {
      solver.step()
    } catch (error) {
      thrown = error
      break
    }
    steps += 1
  }
  assert.ok(steps < 120_000, "differential-pair regression exceeded its step bound")
  return { solver, thrown, steps }
}

test(
  "composite differential-pair grids reject numerical zero edges and retain fail-closed contracts",
  { timeout: 15_000 },
  async (t) => {
    try {
      await access(capacityPath)
    } catch {
      t.skip("pinned capacity autorouter is not installed")
      return
    }
    const { AutoroutingPipelineSolver7_MultiGraph } = await import(
      `${pathToFileURL(capacityPath).href}?zero-edge=${Date.now()}`
    )

    // A permissive physical contract proves the graph remains connected after
    // the directionless boundary adjacency is removed.
    const permissive = runBounded(
      AutoroutingPipelineSolver7_MultiGraph,
      makeSrj(100),
    )
    assert.equal(permissive.thrown, null)
    assert.equal(permissive.solver.failed, false, permissive.solver.error)
    assert.equal(permissive.solver.solved, true, permissive.solver.error)
    assert.equal(
      permissive.solver.getOutputSimpleRouteJson().traces.length,
      2,
    )

    // The same blocked geometry cannot meet the production coupling budget.
    // It must report that real contract, not abort inside graph construction.
    const strict = runBounded(
      AutoroutingPipelineSolver7_MultiGraph,
      makeSrj(3),
    )
    assert.ok(strict.thrown)
    assert.doesNotMatch(strict.thrown.message, /zero-length planar edge/)
    assert.match(strict.thrown.message, /exceeds maxUncoupledLength 3mm/)
    assert.equal(strict.solver.failed, true)
  },
)
