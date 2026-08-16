#!/usr/bin/env node
// What the board IDE actually binds, read out of the code that binds it.
//
// Run this when `shortcutSheet.test.js` fails. It prints two things:
//
//   1. the sheet as a user will see it — derived by calling the arbiters
//   2. the raw scan of the handlers that keep their keys in a closure, with
//      the effect hash each INLINE_BINDINGS entry has to carry
//
// It writes nothing. The point is to put the real bindings in front of whoever
// has to update the wording, not to regenerate the wording for them: a machine
// can tell you `Q` returns `units.toggle`, it cannot tell you whether "Switch
// between mm and mil" is still an honest sentence.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const boardDir = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../src/client/components/board",
);

const { INLINE_BINDINGS, INLINE_SOURCES, PROBES, SHORTCUT_COPY, buildShortcutSheet, probeSurface } =
  await import(path.join(boardDir, "shortcutSheet.js"));
const { scanKeyBindings } = await import(path.join(boardDir, "shortcutScan.js"));

const pad = (text, width) => String(text).padEnd(width);

console.log("\n=== the sheet ===\n");
for (const group of buildShortcutSheet()) {
  console.log(group.title);
  for (const row of group.rows) {
    console.log(`  ${pad(row.combos.join(" / "), 16)} ${row.label}${row.when ? `  — ${row.when}` : ""}`);
  }
  console.log("");
}

console.log("=== probed (derived by calling the resolver — nothing to maintain) ===\n");
for (const probe of PROBES) {
  console.log(`${probe.surface}  (${probe.file})`);
  for (const row of probeSurface(probe).sort((a, b) => a.combo.localeCompare(b.combo))) {
    const copy = SHORTCUT_COPY[row.id];
    console.log(`  ${pad(row.combo, 16)} ${pad(row.id, 24)} ${copy ? copy.label : "*** NO COPY ***"}`);
  }
  console.log("");
}

console.log("=== scanned (handlers with keys in a closure — INLINE_BINDINGS must match) ===\n");
for (const source of INLINE_SOURCES) {
  const text = fs.readFileSync(path.join(boardDir, source.file), "utf8");
  console.log(`${source.surface}  (${source.file})`);
  for (const binding of scanKeyBindings(source.file, text)) {
    const declared = INLINE_BINDINGS.find((row) => row.surface === source.surface && row.combo === binding.combo);
    const state = !declared
      ? "*** NOT DECLARED ***"
      : declared.effect === binding.effectHash
        ? "ok"
        : `*** effect changed: was ${declared.effect} ***`;
    console.log(`  ${pad(binding.combo, 16)} effect: ${binding.effectHash}  L${pad(binding.line, 5)} ${state}`);
    console.log(`  ${" ".repeat(16)} runs: ${binding.effect.slice(0, 100)}`);
  }
  console.log("");
}
