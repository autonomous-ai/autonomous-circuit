#!/usr/bin/env node
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

const fakeGroup = ({ groupFanoutPourNetMap, phaseFanoutPourNetMap }) => {
  const net = {
    name: "GND",
    _parsedProps: {},
  }
  const localTie = {
    name: "TR_TP3_TP4_LOCAL",
    source_trace_id: "source_trace_local_tie",
    props: {},
    _parsedProps: {},
    _findConnectedNets: () => ({ nets: [] }),
    getTracePortPathSelectors: () => [],
  }
  const phase = {
    _parsedProps: {
      phaseIndex: 10,
      autorouter: "fanout",
      fanoutPourNetMap: phaseFanoutPourNetMap,
    },
  }
  const group = {
    _parsedProps: { fanoutPourNetMap: groupFanoutPourNetMap },
    selectAll: (kind) => {
      if (kind === "net") return [net]
      if (kind === "trace") return [localTie]
      if (kind === "autoroutingphase") return [phase]
      return []
    },
  }
  net.parent = group
  localTie.parent = group
  return group
}

test("fanout-pour nets do not become redundant aggregate route connections", async (t) => {
  let Group_getRoutingPhasePlans
  try {
    ;({ Group_getRoutingPhasePlans } = await import(pathToFileURL(corePath).href))
  } catch (error) {
    if (String(error).includes("ERR_MODULE_NOT_FOUND")) {
      t.skip("pinned toolchain is not installed")
      return
    }
    throw error
  }

  for (const props of [
    { groupFanoutPourNetMap: { bottom: "GND" } },
    { phaseFanoutPourNetMap: { bottom: "net.GND" } },
  ]) {
    const plans = Group_getRoutingPhasePlans(fakeGroup(props))
    assert.equal(
      plans.flatMap((plan) => plan.nets).length,
      0,
      "the plane owns global GND connectivity",
    )
    assert.deepEqual(
      plans.flatMap((plan) => plan.traces).map((trace) => trace.name),
      ["TR_TP3_TP4_LOCAL"],
      "authored local same-net ties remain routable",
    )
  }
})
