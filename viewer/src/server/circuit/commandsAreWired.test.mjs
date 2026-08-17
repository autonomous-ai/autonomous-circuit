// Every server command has a caller. Nothing else in this suite checks that.
//
// The most expensive defect this project has shipped is not a wrong number —
// it is a *join*. Code that compiles, passes its own tests, gets documented as
// landed, and is never called by anything:
//
//   - `board_fast_check` — the whole legality gate, wired to no client at all.
//   - `onPlacementRotate` — passed as a prop the component never declared.
//   - `PadHeader` — a real block that existed only under a name no engineer
//     would search for, so two of them hand-copied its internals.
//   - `editEngine.js` — 687 lines, zero importers.
//   - `app_set_effort` — settings key, driver `--effort`, three phases of the
//     review loop, and a pill in the composer that said "Max" while every turn
//     ran at the CLI's default, because no client code named the command.
//
// Four of those landed in one day. Every one had green tests on both sides of
// the gap, because a unit test proves a piece works and nothing proves the app
// reaches it. This is the check that does: the server's command table, against
// every non-test file under `src/client/`.
//
// It is deliberately a string search rather than a call-graph. Half the client
// reaches the API through `transport.ts` and half (the board editor) through a
// bare `fetch("/api/<cmd>")`, and a check that only understood one of those
// would have a blind spot exactly where the last bug was. The command name is
// a string on the wire either way — so if the name appears nowhere in the
// client, nothing can be calling it, and that is the claim being made.
//
// A command that genuinely has no UI belongs in AGENT_ONLY *with a reason*.
// The allowlist is the point: it turns "nobody wired it" into a sentence
// somebody had to write.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const VIEWER = path.resolve(HERE, "../../..");
const CLIENT = path.join(VIEWER, "src/client");
const HTTP = path.join(HERE, "http.mjs");

/** Commands with no UI, and why. Adding a line here is a decision, not a
 *  formality — it says a human looked and meant it. */
const AGENT_ONLY = new Map([
  [
    "board_edit_apply",
    "the agent-facing edit primitive: whole-placement moves by name, used by " +
      "the circuitcode skill and by scripts. The GUI's own edits go through " +
      "board_source_write, which is byte-ranged and compare-and-swapped.",
  ],
]);

/** The command table's keys, read off the source rather than guessed. */
function serverCommands() {
  const source = fs.readFileSync(HTTP, "utf8");
  const at = source.indexOf("const commands = {");
  assert.notEqual(at, -1, "http.mjs no longer declares `const commands = {` — teach this test the new shape");
  const names = [...source.slice(at).matchAll(/^ {4}([a-z][a-z0-9_]*):/gm)].map((m) => m[1]);
  assert.ok(names.length > 15, `only found ${names.length} commands — the parse is wrong, not the app`);
  return names;
}

/** Every line of client code that ships, as one blob. Tests excluded: a
 *  command called only by its own test is exactly the bug being hunted. */
function clientSource() {
  const files = [];
  (function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "__tests__") walk(full);
      } else if (/\.(js|jsx|ts|tsx)$/.test(entry.name) && !/\.test\.|test-helper/.test(entry.name)) {
        files.push(full);
      }
    }
  })(CLIENT);
  assert.ok(files.length > 50, `only ${files.length} client files — the walk is wrong`);
  return files.map((file) => fs.readFileSync(file, "utf8")).join("\n");
}

test("every server command is named by shipping client code, or listed as agent-only with a reason", () => {
  const blob = clientSource();
  const orphans = serverCommands().filter((name) => !AGENT_ONLY.has(name) && !blob.includes(name));
  assert.deepEqual(
    orphans,
    [],
    `these commands exist on the server and nothing in the client calls them:\n` +
      orphans.map((name) => `  - ${name}`).join("\n") +
      `\n\nEither wire one up, or add it to AGENT_ONLY with the reason it has no UI.`,
  );
});

test("the allowlist cannot outlive the commands it excuses", () => {
  const names = new Set(serverCommands());
  for (const [name, why] of AGENT_ONLY) {
    assert.ok(names.has(name), `AGENT_ONLY lists ${name}, which is no longer a server command`);
    assert.ok(why.length > 40, `AGENT_ONLY's reason for ${name} is too short to be a real one`);
  }
});

// The suite that guards against dead code must not be the thing that is dead.
test("the check fails when a command loses its caller", () => {
  const blob = clientSource();
  assert.ok(blob.includes("board_fast_check"), "board_fast_check lost its client caller again");
  assert.ok(blob.includes("app_set_effort"), "the effort pill stopped reaching the CLI's --effort flag");
  assert.ok(!blob.includes("board_no_such_command"), "the search matches strings that are not there");
});
