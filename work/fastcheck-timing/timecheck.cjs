// Where does the sub-second gate's time actually go?
//
// The judge measured `board_fast_check` at 6.7s on harness-puck and 1.1s warm
// on terminal-keyboard, against a module that promises an answer between a
// mouse-up and the next frame. This times each group of `@tscircuit/checks`
// separately, on each shipped board, so the cost has a name.
const fs = require("node:fs");
const checks = require(require("node:path").join(__dirname, "../../toolchain/node_modules/@tscircuit/checks"));

const BOARDS = process.argv.slice(2);

const ROUTING = [
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
];

async function timeOne(label, fn) {
  const started = process.hrtime.bigint();
  const out = (await fn()) || [];
  const ms = Number(process.hrtime.bigint() - started) / 1e6;
  return { label, ms: Math.round(ms), findings: out.length };
}

(async () => {
  for (const path of BOARDS) {
    const circuitJson = JSON.parse(fs.readFileSync(path, "utf8"));
    const counts = {};
    for (const element of circuitJson) counts[element.type] = (counts[element.type] || 0) + 1;
    const rows = [];
    rows.push(await timeOne("placement group", () => checks.runAllPlacementChecks(circuitJson)));
    rows.push(await timeOne("netlist group", () => checks.runAllNetlistChecks(circuitJson)));
    rows.push(await timeOne("pin-spec group", () => checks.runAllPinSpecificationChecks(circuitJson)));
    for (const name of ROUTING) {
      if (typeof checks[name] !== "function") continue;
      rows.push(await timeOne(name, () => checks[name](circuitJson)));
    }
    rows.sort((a, b) => b.ms - a.ms);
    console.log(`\n=== ${path}`);
    console.log(
      `elements ${circuitJson.length} · components ${counts.pcb_component || 0} · pads ${
        counts.pcb_smtpad || 0
      } · traces ${counts.pcb_trace || 0} · vias ${counts.pcb_via || 0}`,
    );
    let total = 0;
    for (const row of rows) {
      total += row.ms;
      console.log(`  ${String(row.ms).padStart(6)}ms  ${row.label} (${row.findings})`);
    }
    console.log(`  ${String(total).padStart(6)}ms  TOTAL`);
  }
})();
