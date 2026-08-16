const fs = require("node:fs");
const checks = require(require("node:path").join(__dirname, "../../toolchain/node_modules/@tscircuit/checks"));
const list = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const NAMES = [
  "checkPcbComponentOverlap",
  "checkPadPadClearance",
  "checkPcbComponentsOutOfBoard",
  "checkViasInPads",
  "checkViasOffBoard",
  "checkConnectorAccessibleOrientation",
  "checkTestPointAccessibility",
  "checkSourceTracesMatchPcbTraceThickness",
];
(async () => {
  for (const name of NAMES) {
    const fn = checks[name];
    if (typeof fn !== "function") continue;
    const t = process.hrtime.bigint();
    const out = (await fn(list)) || [];
    console.log(`${String(Math.round(Number(process.hrtime.bigint() - t) / 1e6)).padStart(6)}ms  ${name} (${out.length})`);
  }
})();
