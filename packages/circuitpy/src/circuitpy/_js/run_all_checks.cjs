// Stage-2 independent re-check: @tscircuit/checks runAllChecks over a
// circuit.json. Invoked by circuitpy.checks via `node` with NODE_PATH at the
// repo toolchain's node_modules. Prints exactly one JSON array (the findings)
// on stdout; exits 0 whether or not findings exist — the Python side gates on
// the parsed array, never the exit code.
//
// `--skip=<name,name>` drops named checks from the run. It exists for exactly
// one caller — the IDE's sub-second edit gate — and for exactly one check:
// `checkTracesAreContiguous` costs 1,491ms of the 2,319ms this file spends on
// terminal-keyboard (measured 2026-08-16), which is affordable once per build
// and not affordable after a drag. `circuitpy.checks.trace_anchor_warnings` is
// the 3.8ms Python stand-in the gate runs instead. The build never passes
// --skip; a fast answer is not a build verdict.
const fs = require("node:fs")

const args = process.argv.slice(2)
const skip = new Set()
const positional = []
for (const arg of args) {
  if (arg.startsWith("--skip=")) {
    for (const name of arg.slice("--skip=".length).split(",")) {
      if (name.trim()) skip.add(name.trim())
    }
  } else {
    positional.push(arg)
  }
}

const inputPath = positional[0]
if (!inputPath) {
  process.stderr.write("usage: node run_all_checks.cjs [--skip=a,b] <circuit.json>\n")
  process.exit(2)
}

let circuitJson
try {
  circuitJson = JSON.parse(fs.readFileSync(inputPath, "utf8"))
} catch (err) {
  process.stderr.write(`cannot read circuit json: ${err}\n`)
  process.exit(2)
}

const checks = require("@tscircuit/checks")

// The routing group is the only one reassembled by hand, because it is the only
// one that has to be: `checkTracesAreContiguous` lives in it, and every one of
// its ten members is exported. The other three groups are called as the library
// composes them — `runAllPlacementChecks` reaches an internal
// `checkCourtyardOverlap` that is NOT exported, and that check is precisely the
// one that catches a dragged part landing on its neighbour, so hand-rolling the
// placement group would have silently dropped the finding this gate is for.
const ROUTING_CHECKS = [
  "checkEachPcbPortConnectedToPcbTraces",
  "checkSourceTracesHavePcbTraces",
  "checkPcbTraceLengths",
  "checkEachPcbTraceNonOverlapping",
  "checkPadTraceClearance",
  "checkViaTraceClearance",
  "checkSameNetViaSpacing",
  "checkDifferentNetViaSpacing",
  "checkTracesAreContiguous",
  "checkPcbTracesOutOfBoard",
]

async function run() {
  if (!skip.size) {
    return await checks.runAllChecks(circuitJson)
  }
  const findings = []
  for (const group of [
    checks.runAllPlacementChecks,
    checks.runAllNetlistChecks,
    checks.runAllPinSpecificationChecks,
  ]) {
    findings.push(...(await group(circuitJson)))
  }
  for (const name of ROUTING_CHECKS) {
    if (skip.has(name)) continue
    const check = checks[name]
    if (typeof check !== "function") continue
    findings.push(...(check(circuitJson) || []))
  }
  return findings
}

// A name in --skip that no group owns would silently do nothing, and a gate
// that quietly runs a check it promised to drop is a gate that lies about its
// own latency. Refuse instead.
const skippable = new Set(ROUTING_CHECKS)
for (const name of skip) {
  if (!skippable.has(name)) {
    process.stderr.write(`--skip does not know ${name}\n`)
    process.exit(2)
  }
}

run()
  .then((findings) => {
    process.stdout.write(JSON.stringify(findings || []))
  })
  .catch((err) => {
    process.stderr.write(String((err && err.stack) || err))
    process.exit(1)
  })
