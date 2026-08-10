#!/usr/bin/env node
// Fake `claude` CLI for driver tests. NEVER the real thing — tests point
// CIRCUIT_CLAUDE_BIN here and the driver spawns it through the current node.
//
// Behavior is scripted by the JSON file at CIRCUIT_FAKE_SCENARIO:
//
//   {
//     "plan":      { "lines": [...], "writeFiles": [...], "sleepAfterMs": 0, "exitCode": 0 },
//     "implement": { ... },
//     "review":    { ... }
//   }
//
// The phase is derived from argv exactly the way the driver builds it:
// `--permission-mode plan` → "plan"; otherwise the `--append-system-prompt`
// text containing "post-build" (the REVIEW_SYSTEM_PROMPT marker) → "review",
// else "implement". Keep these strings in sync with driver.mjs prompts.
//
// `lines` entries are emitted to stdout one per line; objects are
// JSON.stringify'd, strings pass through raw. `writeFiles` are written
// relative to cwd (the workspace) BEFORE the lines are emitted — circuit
// scenarios write boards/main.tsx, boards/main.board.json, and friends. When
// CIRCUIT_FAKE_LOG is set, one JSON line {phase, argv, stdin} is appended per
// invocation so tests can assert spawn sequences and prompt contents.

import fs from "node:fs";
import path from "node:path";

function detectPhase(argv) {
  const modeIdx = argv.indexOf("--permission-mode");
  const mode = modeIdx !== -1 ? argv[modeIdx + 1] : "";
  if (mode === "plan") {
    return "plan";
  }
  const promptIdx = argv.indexOf("--append-system-prompt");
  const systemPrompt = promptIdx !== -1 ? String(argv[promptIdx + 1] || "") : "";
  return systemPrompt.includes("post-build") ? "review" : "implement";
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const argv = process.argv.slice(2);
const phase = detectPhase(argv);

let scenario = {};
try {
  scenario = JSON.parse(fs.readFileSync(process.env.CIRCUIT_FAKE_SCENARIO, "utf8"));
} catch {
  scenario = {};
}
const script = scenario[phase] || {};

const stdin = await readStdin();

if (process.env.CIRCUIT_FAKE_LOG) {
  fs.appendFileSync(
    process.env.CIRCUIT_FAKE_LOG,
    `${JSON.stringify({ phase, argv, stdin })}\n`,
  );
}

for (const file of script.writeFiles || []) {
  const target = path.resolve(process.cwd(), file.path);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, String(file.content ?? ""));
}

for (const line of script.lines || []) {
  process.stdout.write(`${typeof line === "string" ? line : JSON.stringify(line)}\n`);
}

if (script.stderr) {
  process.stderr.write(String(script.stderr));
}

if (script.sleepAfterMs) {
  await sleep(Number(script.sleepAfterMs));
}

process.exit(Number(script.exitCode ?? 0));
