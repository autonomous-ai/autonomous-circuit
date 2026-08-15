// Independent routing DRC for the router tournament.
//
// `runAllChecks` is the pipeline's stage-2 call, but it runs every check in one
// try-less pass: `checkPadPadClearance` throws on a pill-shaped plated hole
// (`distanceBetweenPolygonAndPolygon` reads `.points` off undefined), and one
// throw loses the whole report. Twelve of the sixteen benchmark instances carry
// a USB-C receptacle, so twelve of sixteen produced no findings at all — which
// reads exactly like "clean".
//
// So this runs each check by name, isolates its failures, and reports per-check
// status alongside the findings. A check that threw is reported as having
// thrown, never as having passed.
const fs = require("node:fs")

const inputPath = process.argv[2]
if (!inputPath) {
  process.stderr.write("usage: node routing_checks.cjs <circuit.json>\n")
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

// Checks about the copper a router placed. `checkPadPadClearance` and
// `checkPcbComponentOverlap` are about the placement the router may not move —
// run anyway, reported separately, because a finding there belongs to the
// instance and not to any route.
const ROUTING = [
  "checkEachPcbTraceNonOverlapping",
  "checkPadTraceClearance",
  "checkViaTraceClearance",
  "checkViasInPads",
  "checkViasOffBoard",
  "checkPcbTracesOutOfBoard",
  "checkTracesAreContiguous",
  "checkSameNetViaSpacing",
  "checkDifferentNetViaSpacing",
  "checkSourceTracesMatchPcbTraceThickness",
]
const CONNECTIVITY = ["checkEachPcbPortConnectedToPcbTraces"]
const PLACEMENT = ["checkPadPadClearance", "checkPcbComponentOverlap"]

async function run(name) {
  const fn = checks[name]
  if (typeof fn !== "function") return { name, status: "missing", findings: [] }
  try {
    const out = await Promise.resolve(fn(circuitJson))
    return { name, status: "ok", findings: Array.isArray(out) ? out : [] }
  } catch (err) {
    return {
      name,
      status: "threw",
      error: String((err && err.message) || err),
      findings: [],
    }
  }
}

;(async () => {
  const out = { routing: [], connectivity: [], placement: [] }
  for (const name of ROUTING) out.routing.push(await run(name))
  for (const name of CONNECTIVITY) out.connectivity.push(await run(name))
  for (const name of PLACEMENT) out.placement.push(await run(name))
  process.stdout.write(JSON.stringify(out))
})().catch((err) => {
  process.stderr.write(String((err && err.stack) || err))
  process.exit(1)
})
