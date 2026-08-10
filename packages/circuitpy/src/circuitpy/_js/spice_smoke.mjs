// DC smoke simulation: the only check in the gauntlet that knows Ohm's law.
//
// Every other stage checks *structure* — is it connected, does it fit, can it
// be made. None of them notice a 10-ohm resistor where 10k belongs, a reversed
// LED, or a divider that lands at the wrong voltage. This one runs the netlist
// and looks at the numbers.
//
// Deliberately a smoke test, not verification. Most real boards contain parts
// with no SPICE model (MCUs, modules, connectors), so the simulated netlist is
// always partial. It therefore reports what it *could* evaluate alongside what
// it found, and the caller never treats silence as a pass.
//
// STATUS 2026-08-10: **scaffold, not yet wired into the gauntlet.** Measured on
// a real block-composed board, `circuit-json-to-spice` 0.0.45 emits 7 of ~10
// components (passives and the LED, nothing with an IC model) and — the
// blocker — anonymises nodes to N1..N5, so a rail cannot be identified by
// name. There is also no voltage source, because power arrives through a
// connector that has no model, which makes a DC operating point all zeros.
//
// Wiring this in today would add a check that always finds nothing, which is
// worse than no check: it implies coverage of exactly the failure class we are
// blind to. It stays here, correct and runnable, for when the converter names
// its nodes — at which point injecting a source on the declared power rail
// makes the rail-collapse test real. Until then the Ohm's-law gap is addressed
// by the direct checks in circuitlib, which work on circuit.json connectivity.
//
// Run: node spice_smoke.mjs <circuit.json> <toolchain/node_modules>
// Prints exactly one JSON object on stdout; exits 0 regardless of findings.
import { readFileSync } from "node:fs"

const inputPath = process.argv[2]
if (!inputPath) {
  process.stderr.write("usage: node spice_smoke.mjs <circuit.json>\n")
  process.exit(2)
}

const out = (payload) => {
  process.stdout.write(JSON.stringify(payload))
  process.exit(0)
}

let circuitJson
try {
  circuitJson = JSON.parse(readFileSync(inputPath, "utf8"))
} catch (err) {
  out({ ran: false, reason: `cannot read circuit json: ${err}`, findings: [] })
}

// ESM ignores NODE_PATH, so resolve out of the pinned toolchain explicitly.
// argv[3] is the toolchain's node_modules; CIRCUIT_TOOLCHAIN is the fallback.
const modulesDir =
  process.argv[3] ||
  (process.env.CIRCUIT_TOOLCHAIN
    ? `${process.env.CIRCUIT_TOOLCHAIN}/node_modules`
    : null)
if (!modulesDir) {
  out({ ran: false, reason: "no toolchain node_modules given", findings: [] })
}

const load = async (pkg) => {
  const { createRequire } = await import("node:module")
  const require = createRequire(`${modulesDir}/`)
  return import(new URL(`file://${require.resolve(pkg)}`).href)
}

let convert
let stringify
let simulate
try {
  const spiceMod = await load("circuit-json-to-spice")
  convert = spiceMod.circuitJsonToSpice || spiceMod.default
  stringify = spiceMod.convertSpiceNetlistToString
  const spicey = await load("spicey")
  simulate = spicey.simulate
} catch (err) {
  out({ ran: false, reason: `spice toolchain unavailable: ${err}`, findings: [] })
}

if (typeof convert !== "function") {
  out({ ran: false, reason: "no circuit-json-to-spice entry point", findings: [] })
}

// The converter returns a SpiceNetlist object; a separate call renders it.
let netlist
let componentCount = 0
try {
  const result = convert(circuitJson)
  componentCount = Array.isArray(result?.components) ? result.components.length : 0
  netlist =
    typeof result === "string"
      ? result
      : stringify
        ? stringify(result)
        : result?.toString?.()
} catch (err) {
  out({ ran: false, reason: `netlist conversion failed: ${err}`, findings: [] })
}

if (!netlist || !netlist.trim()) {
  out({ ran: false, reason: "empty netlist", findings: [] })
}

// Components the converter could not model are the measure of how much of the
// board this check actually saw. Report it rather than implying full coverage.
const lines = netlist.split("\n").map((l) => l.trim()).filter(Boolean)
const modelled = componentCount || lines.filter((l) => /^[RCLDVIQMX]/i.test(l)).length

let analysis
try {
  analysis = simulate ? simulate(netlist) : null
} catch (err) {
  out({
    ran: false,
    reason: `simulation failed: ${err}`,
    modelledComponents: modelled,
    findings: [],
  })
}

const findings = []
const nodeVoltages =
  analysis?.nodeVoltages || analysis?.voltages || analysis?.operatingPoint || null

if (nodeVoltages && typeof nodeVoltages === "object") {
  for (const [node, raw] of Object.entries(nodeVoltages)) {
    const v = typeof raw === "number" ? raw : Number(raw?.re ?? raw)
    if (!Number.isFinite(v)) continue
    const name = String(node).toUpperCase()

    // A named rail that has collapsed is the loudest thing this check can say.
    const expected = /V3_?3/.test(name) ? 3.3 : /V5|VBUS/.test(name) ? 5.0 : null
    if (expected !== null) {
      const drift = Math.abs(v - expected) / expected
      if (drift > 0.1) {
        findings.push({
          part: String(node),
          detail:
            `rail ${node} simulates at ${v.toFixed(3)}V, expected about ` +
            `${expected}V — check the regulator feedback and the load`,
          severity: v < expected * 0.5 ? "error" : "warning",
        })
      }
    }
    if (Math.abs(v) > 60) {
      findings.push({
        part: String(node),
        detail: `node ${node} simulates at ${v.toFixed(1)}V, outside the low-voltage envelope`,
        severity: "error",
      })
    }
  }
}

out({
  ran: true,
  modelledComponents: modelled,
  totalLines: lines.length,
  nodeCount: nodeVoltages ? Object.keys(nodeVoltages).length : 0,
  findings,
})
