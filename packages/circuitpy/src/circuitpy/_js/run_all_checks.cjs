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
let maxOutlinePoints = 0
const positional = []
for (const arg of args) {
  if (arg.startsWith("--skip=")) {
    for (const name of arg.slice("--skip=".length).split(",")) {
      if (name.trim()) skip.add(name.trim())
    }
  } else if (arg.startsWith("--max-outline-points=")) {
    maxOutlinePoints = Number(arg.slice("--max-outline-points=".length)) || 0
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

// Thin the board outline before the checks read it. The IDE's edit gate is the
// only caller; a build never passes this.
//
// `checkPcbComponentsOutOfBoard` walks every pad against every outline segment,
// and tscircuit emits an outline of a couple of thousand points for any board
// with a radius on it — so the cost is the board's *shape*, not its part count
// (measured 2026-08-16, `work/fastcheck-timing/timecheck4.cjs`):
//
//     board              outline points   checkPcbComponentsOutOfBoard
//     harness-puck                2,205                       5,368 ms
//     hydrate-coaster             1,013                       1,523 ms
//     terminal-keyboard    none (a rect)                          49 ms
//
// One check was 84% of a "sub-second" answer, on the board most likely to be
// somebody's first. Keeping every Nth vertex is an approximation and the error
// is bounded and small: on harness-puck's 35mm radius, 64 segments put the
// chord 0.042mm inside the true arc, against a 0.2mm edge-clearance rule that
// `dfm_edge_clearance` measures exactly. So a part can be up to ~0.04mm outside
// the true edge and read as inside here — and the build, which never thins
// anything, is what says whether a board may ship.
function thinOutline(circuitJson, maxPoints) {
  if (!maxPoints || maxPoints < 8) return circuitJson
  return circuitJson.map((element) => {
    if (!element || element.type !== "pcb_board") return element
    const outline = element.outline
    if (!Array.isArray(outline) || outline.length <= maxPoints) return element
    const stride = Math.ceil(outline.length / maxPoints)
    const kept = outline.filter((_, index) => index % stride === 0)
    // The last point matters: dropping it can leave a chord across the widest
    // part of the shape.
    if (kept[kept.length - 1] !== outline[outline.length - 1]) kept.push(outline[outline.length - 1])
    return { ...element, outline: kept }
  })
}

// The routing group is the only one reassembled by hand, because it is the only
// one that can be: `checkTracesAreContiguous` lives in it and every one of its
// ten members is exported.
//
// The placement group is NOT composable, and this was measured the expensive
// way. `runAllPlacementChecks` reaches an internal `checkCourtyardOverlap` that
// is not exported; composing the group from its exported members reproduced
// 31 of 31 and 125 of 125 findings on deliberately broken boards
// (`work/fastcheck-timing/verifygroup.cjs`) and still lost
// `pcb_courtyard_overlap_error`, which a server test caught within the hour.
// Two agreeing measurements on boards that happened not to have a courtyard
// overlap are not the same as coverage. The group is called as the library
// composes it.


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
  circuitJson = thinOutline(circuitJson, maxOutlinePoints)
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
