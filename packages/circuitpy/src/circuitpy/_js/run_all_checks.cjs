// Stage-2 independent re-check: @tscircuit/checks runAllChecks over a
// circuit.json. Invoked by circuitpy.checks via `node` with NODE_PATH at the
// repo toolchain's node_modules. Prints exactly one JSON array (the findings)
// on stdout; exits 0 whether or not findings exist — the Python side gates on
// the parsed array, never the exit code.
const fs = require("node:fs")

const inputPath = process.argv[2]
if (!inputPath) {
  process.stderr.write("usage: node run_all_checks.cjs <circuit.json>\n")
  process.exit(2)
}

let circuitJson
try {
  circuitJson = JSON.parse(fs.readFileSync(inputPath, "utf8"))
} catch (err) {
  process.stderr.write(`cannot read circuit json: ${err}\n`)
  process.exit(2)
}

const { runAllChecks } = require("@tscircuit/checks")

Promise.resolve(runAllChecks(circuitJson))
  .then((findings) => {
    process.stdout.write(JSON.stringify(findings || []))
  })
  .catch((err) => {
    process.stderr.write(String((err && err.stack) || err))
    process.exit(1)
  })
