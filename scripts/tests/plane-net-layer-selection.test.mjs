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

const sourceNets = [{ source_net_id: "source_net_gnd", name: "GND" }]
const sourceTraces = [
  {
    source_trace_id: "source_trace_top",
    name: "TR_TOP_GND",
    connected_source_port_ids: ["source_port_top"],
    connected_source_net_ids: ["source_net_gnd"],
  },
  {
    source_trace_id: "source_trace_bottom",
    name: "TR_BOTTOM_GND",
    connected_source_port_ids: ["source_port_bottom"],
    connected_source_net_ids: ["source_net_gnd"],
  },
]
const pcbPorts = [
  { source_port_id: "source_port_top", layers: ["top"] },
  { source_port_id: "source_port_bottom", layers: ["bottom"] },
]

test("a net poured on both faces terminates each SMD pad on its own face", async (t) => {
  let getPlaneTerminatedSourceTraceLayers
  try {
    ;({ getPlaneTerminatedSourceTraceLayers } = await import(
      pathToFileURL(corePath).href
    ))
  } catch (error) {
    if (String(error).includes("ERR_MODULE_NOT_FOUND")) {
      t.skip("pinned toolchain is not installed")
      return
    }
    throw error
  }

  const layers = getPlaneTerminatedSourceTraceLayers({
    fanoutPourNetMap: { top: "net.GND", bottom: "net.GND" },
    sourceNets,
    sourceTraces,
    pcbPorts,
  })
  assert.equal(layers.get("source_trace_top"), "top")
  assert.equal(layers.get("source_trace_bottom"), "bottom")

  const bottomOnly = getPlaneTerminatedSourceTraceLayers({
    fanoutPourNetMap: { bottom: "net.GND" },
    sourceNets,
    sourceTraces,
    pcbPorts,
  })
  assert.equal(
    bottomOnly.get("source_trace_top"),
    "bottom",
    "a single authored plane layer retains the existing cross-layer behavior",
  )
})
