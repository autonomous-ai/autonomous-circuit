import assert from "node:assert/strict"
import { register } from "node:module"
import { dirname, join, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath, pathToFileURL } from "node:url"

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
register(
  new URL("./fixtures/export-fanout-autorouter-loader.mjs", import.meta.url),
  import.meta.url,
)

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

const fakeGroup = ({ region, reroute = false }) => {
  const trace = {
    name: "TR_CRITICAL",
    props: { routingPhaseIndex: 1 },
    _findConnectedNets: () => ({ nets: [] }),
    getTracePortPathSelectors: () => [],
  }
  const phase = {
    _parsedProps: {
      name: reroute ? "repair" : "critical",
      phaseIndex: 1,
      region,
      reroute,
    },
  }
  const group = {
    _parsedProps: {},
    selectAll: (kind) => {
      if (kind === "trace") return [trace]
      if (kind === "autoroutingphase") return [phase]
      return []
    },
  }
  trace.parent = group
  return group
}

test("ordinary phase region constrains SRJ bounds without changing reroute semantics", async (t) => {
  let Group_getRoutingPhasePlans
  let Group_filterSimpleRouteJsonForPhase
  try {
    ;({ Group_getRoutingPhasePlans, Group_filterSimpleRouteJsonForPhase } =
      await import(pathToFileURL(corePath).href))
  } catch (error) {
    if (String(error).includes("ERR_MODULE_NOT_FOUND")) {
      t.skip("pinned toolchain is not installed")
      return
    }
    throw error
  }

  const boardBounds = { minX: -50, maxX: 50, minY: -40, maxY: 40 }
  const region = { minX: -9.658, maxX: 9.21, minY: -10.7, maxY: 24.5301 }
  const srj = {
    bounds: boardBounds,
    connections: [{ name: "source_trace_critical" }],
    obstacles: [],
    traces: [],
  }

  const [ordinaryPlan] = Group_getRoutingPhasePlans(
    fakeGroup({ region }),
  )
  assert.deepEqual(ordinaryPlan.region, region)
  assert.deepEqual(ordinaryPlan.routingBounds, region)
  const ordinaryInput = Group_filterSimpleRouteJsonForPhase(srj, {
    ...ordinaryPlan,
    traces: [{ source_trace_id: "source_trace_critical" }],
  })
  assert.deepEqual(ordinaryInput.bounds, region)

  const [reroutePlan] = Group_getRoutingPhasePlans(
    fakeGroup({ region, reroute: true }),
  )
  assert.deepEqual(reroutePlan.region, region)
  assert.equal(reroutePlan.routingBounds, undefined)
})
