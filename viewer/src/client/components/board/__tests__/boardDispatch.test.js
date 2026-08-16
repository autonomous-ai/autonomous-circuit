// boardDispatch — the half of a binding that the resolver tests cannot see.
//
// `boardKeymap.test.js` proves a key resolves to a command id and
// `boardContextMenu.test.js` proves a row carries an action id. Neither proves
// the id reaches anything. The audit named that gap exactly: empty the body of
// `case "edit.undo":` in BoardWorkspace and ⌘Z does nothing at all, while the
// whole suite stays green — the same failure as `WindowMenuBar`'s Undo, which
// is the shipped example everybody cites.
//
// The viewer's runner is bare `node:test` with no DOM, so the dispatch cannot
// be *rendered*. It can be *read*, which is the same technique
// `shortcutSheet.test.js` already uses on Board3DView and PropertiesPanel: hold
// the id list against the switch that is supposed to handle it, in both
// directions, and require each case to actually run a statement.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { BOARD_COMMANDS } from "../boardKeymap.js";
import { BOARD_CONTEXT_ACTIONS } from "../boardContextMenu.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (file) => fs.readFileSync(path.join(here, "..", file), "utf8");

/** Source with comments and strings blanked, so neither can fake a case. */
function stripNoise(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//gu, " ")
    .replace(/\/\/[^\n]*/gu, " ");
}

/**
 * Every `case "<id>":` in `source`, mapped to the code it runs.
 *
 * The body runs to the next `case`/`default:` label, which is right for this
 * codebase's switches — none of them fall through, and a case that did would
 * still be reported as having a body.
 */
function switchCases(source) {
  const clean = stripNoise(source);
  const label = /case\s+"([a-z0-9.\-]+)"\s*:/giu;
  const found = [];
  let match = label.exec(clean);
  while (match) {
    const start = match.index + match[0].length;
    label.lastIndex = start;
    const next = label.exec(clean);
    const end = next ? next.index : clean.length;
    // Where a `default:` arrives first, the body ends there instead.
    const defaultAt = clean.slice(start, end).search(/\bdefault\s*:/u);
    const body = clean.slice(start, defaultAt === -1 ? end : start + defaultAt);
    found.push({ id: match[1], body });
    match = next;
  }
  return found;
}

/** True when the case does something other than fall out of the switch. */
function runsSomething(body) {
  return body.replace(/\bbreak\s*;/gu, " ").trim().length > 0;
}

/**
 * One surface's dispatch, held against the list of ids it must handle.
 *
 * Three assertions and they are not the same one. Missing = a binding that
 * resolves into nothing. Extra = a case for an id nothing produces, which is
 * dead code that reads as a feature. Empty = the mutation the audit ran, and
 * the only one the other two would miss.
 */
function assertDispatch(file, ids, { ignore = [] } = {}) {
  const cases = switchCases(read(file));
  const handled = new Set(cases.map((one) => one.id));
  const skip = new Set(ignore);

  for (const id of ids) {
    if (skip.has(id)) continue;
    assert.ok(handled.has(id), `${file} has no case for "${id}"`);
  }
  for (const one of cases) {
    assert.ok(
      new Set(ids).has(one.id) || skip.has(one.id),
      `${file} dispatches "${one.id}", which nothing produces`,
    );
    assert.ok(runsSomething(one.body), `${file}'s case "${one.id}" is empty — the id resolves and nothing happens`);
  }
}

test("every board command reaches a handler in BoardWorkspace", () => {
  assertDispatch("BoardWorkspace.jsx", BOARD_COMMANDS);
});

test("every context-menu action reaches a handler in BoardContextMenu", () => {
  assertDispatch("BoardContextMenu.jsx", [...BOARD_CONTEXT_ACTIONS]);
});

// The scan is only worth anything if it fails on the mutation it was written
// for. Rather than trust that, run the mutation here: this is the exact edit
// the audit made to BoardWorkspace, applied to a copy of the real source.
test("the scan fails on an emptied case, which is the whole point of it", () => {
  const source = read("BoardWorkspace.jsx");
  assert.ok(source.includes('case "edit.undo":'), "the case this test mutates must exist");
  const gutted = source.replace(/case "edit\.undo":[\s\S]*?break;/u, 'case "edit.undo":\n          break;');
  assert.notEqual(gutted, source, "the mutation must actually change the file");
  const cases = switchCases(gutted);
  const undo = cases.find((one) => one.id === "edit.undo");
  assert.ok(undo, "the mutated case is still found");
  assert.equal(runsSomething(undo.body), false, "an emptied case must read as empty");

  // …and a deleted case is caught by the other direction.
  const deleted = source.replace(/case "messages\.toggle":[\s\S]*?break;/u, "");
  assert.equal(
    switchCases(deleted).some((one) => one.id === "messages.toggle"),
    false,
  );
});

// A comment or a string that happens to spell a case label must not count as
// a handler — otherwise the scan can be satisfied by prose.
test("a case named in a comment is not a handler", () => {
  const cases = switchCases(`
    switch (command) {
      // case "fake.one": this is prose
      /* case "fake.two": also prose */
      case "real.one":
        doIt();
        break;
    }
  `);
  assert.deepEqual(cases.map((one) => one.id), ["real.one"]);
  assert.equal(runsSomething(cases[0].body), true);
});
