// Does the exported placement-family list reproduce `runAllPlacementChecks`?
// Checked on the shipped boards AND on deliberately broken copies of them, so
// "both returned nothing" cannot be the whole evidence.
const fs = require("node:fs");
const checks = require(require("node:path").join(__dirname, "../../toolchain/node_modules/@tscircuit/checks"));
const PLACEMENT = [
  "checkPcbComponentOverlap",
  "checkPadPadClearance",
  "checkPcbComponentsOutOfBoard",
  "checkViasInPads",
  "checkViasOffBoard",
  "checkConnectorAccessibleOrientation",
  "checkTestPointAccessibility",
  "checkSourceTracesMatchPcbTraceThickness",
];
const key = (f) => `${f.type || f.error_type}|${f.message || ""}`;
async function compare(label, list) {
  const group = (await checks.runAllPlacementChecks(list)) || [];
  const composed = [];
  for (const name of PLACEMENT) composed.push(...((await checks[name](list)) || []));
  const a = new Set(group.map(key));
  const b = new Set(composed.map(key));
  const missing = [...a].filter((k) => !b.has(k));
  const extra = [...b].filter((k) => !a.has(k));
  console.log(`${label}: group ${group.length}, composed ${composed.length}, missing ${missing.length}, extra ${extra.length}`);
  for (const m of missing.slice(0, 5)) console.log(`   MISSING ${m.slice(0, 120)}`);
  for (const m of extra.slice(0, 3)) console.log(`   EXTRA   ${m.slice(0, 120)}`);
}
(async () => {
  for (const path of process.argv.slice(2)) {
    const list = JSON.parse(fs.readFileSync(path, "utf8"));
    await compare(`${path} as built`, list);
    // Broken copy: shove every third component's pads 3mm east — overlaps,
    // off-board copper, vias in pads, the lot.
    const broken = JSON.parse(JSON.stringify(list));
    let n = 0;
    for (const e of broken) {
      if (e.type === "pcb_smtpad" || e.type === "pcb_component" || e.type === "pcb_plated_hole") {
        if (n++ % 3 === 0 && typeof e.x === "number") e.x += 3;
        if (e.center && typeof e.center.x === "number" && n % 3 === 0) e.center.x += 3;
      }
    }
    await compare(`${path} broken`, broken);
  }
})();
