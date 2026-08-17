import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { BOARD_COMMANDS } from "../boardKeymap.js";
import {
  INLINE_BINDINGS,
  INLINE_SOURCES,
  KEY_SPACE,
  PROBES,
  SHORTCUT_COPY,
  SHORTCUT_GROUPS,
  allBindings,
  bindingsMissingCopy,
  buildShortcutSheet,
  copyWithUnknownGroup,
  copyWithoutBindings,
  probeSurface,
  shortcutSheetKeyAction,
} from "../shortcutSheet.js";
import { comboParts, maskSource, scanAll, scanKeyBindings } from "../shortcutScan.js";

const BOARD_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (file) => fs.readFileSync(path.join(BOARD_DIR, file), "utf8");

const FIX = "run `node scripts/shortcut-report.mjs` from viewer/ to see what the code actually binds";

// --- the sheet cannot omit a binding, and cannot invent one ------------------

test("every binding the app resolves has a line of copy", () => {
  const missing = bindingsMissingCopy();
  assert.deepEqual(
    missing,
    [],
    `these bindings would not appear on the shortcut sheet: ${missing.join(", ")}. ` +
      `Add each to SHORTCUT_COPY in shortcutSheet.js. ${FIX}`,
  );
});

test("no line of copy outlives the binding it describes", () => {
  const orphans = copyWithoutBindings();
  assert.deepEqual(
    orphans,
    [],
    `SHORTCUT_COPY describes keys nothing resolves to any more: ${orphans.join(", ")}. ` +
      `Delete them, or restore the binding. ${FIX}`,
  );
});

test("every command boardKeymap can return reaches the sheet", () => {
  // BOARD_COMMANDS is the arbiter's own declared surface. A command listed
  // there but unreachable by any key is a dead command; one reachable but
  // undocumented is a key the user meets with no way to look it up.
  const probed = new Set(probeSurface(PROBES.find((p) => p.surface === "workspace")).map((r) => r.id));
  for (const command of BOARD_COMMANDS) {
    assert.ok(probed.has(command), `no key resolves to ${command} — dead command, or the sweep missed it`);
    assert.ok(SHORTCUT_COPY[command], `${command} has no line of copy in shortcutSheet.js`);
  }
});

test("copy only uses groups the sheet renders", () => {
  assert.deepEqual(copyWithUnknownGroup(), []);
  const ids = SHORTCUT_GROUPS.map((group) => group.id);
  assert.equal(new Set(ids).size, ids.length, "group ids must be unique");
});

// --- the probe is a real sweep, not a lucky sample ---------------------------

test("the key space covers the letters, digits, punctuation and named keys", () => {
  for (const key of ["a", "z", "0", "9", "[", "]", " ", "Escape", "Insert", "PageDown", "F1", "?"]) {
    assert.ok(KEY_SPACE.includes(key), `key space is missing ${JSON.stringify(key)}`);
  }
});

test("a modifier is only printed when it changes the answer", () => {
  const workspace = probeSurface(PROBES.find((p) => p.surface === "workspace"));
  const combos = workspace.map((row) => row.combos ?? row.combo);
  // Ctrl+M measures and so does Ctrl+Shift+M, because the handler never asks
  // about Shift. Only the meaningful one belongs on a sheet someone is reading
  // to learn the tool.
  assert.ok(combos.includes("Mod+M"));
  assert.ok(!combos.includes("Mod+Shift+M"));
  assert.ok(combos.includes("Esc"));
  assert.ok(!combos.includes("Shift+Esc"));
  // Shift genuinely changes these two, so both are printed.
  assert.ok(combos.includes("M"));
  assert.ok(combos.includes("Shift+N"));
  // …and Shift+M is Altium's Board Insight Lens (ALTIUM-NOTES §3), so it is
  // spent on nothing of ours and must never appear on a sheet teaching Altium
  // users our keys.
  assert.ok(!combos.includes("Shift+M"));
});

test("the legacy Spacebar spelling is accepted but never taught", () => {
  const pcb = probeSurface(PROBES.find((p) => p.surface === "pcb"));
  const combos = pcb.map((row) => row.combo);
  assert.ok(combos.includes("Space"));
  assert.ok(combos.includes("Shift+Space"));
  assert.ok(!combos.includes("Spacebar"));
});

test("a key that only works in one situation says which", () => {
  const rows = allBindings();
  const undo = rows.find((row) => row.id === "edit.undo");
  assert.equal(undo.when, "after you move a part");
  const rotate = rows.find((row) => row.id === "canvas.rotate.ccw");
  assert.equal(rotate.when, "while dragging a part");
});

// --- the sheet's own key ------------------------------------------------------

test("the sheet lists the key that opens it", () => {
  const help = buildShortcutSheet().find((group) => group.id === "help");
  const toggle = help.rows.find((row) => row.id === "sheet.toggle");
  // Altium's own: Shift+F1 lists the shortcuts valid right now.
  // https://www.altium.com/documentation/altium-designer/shortcut-keys
  assert.deepEqual(toggle.combos, ["?", "Shift+F1"]);
});

test("the sheet's key stays out of the way of typing and of other handlers", () => {
  const press = (over) => ({ key: "?", defaultPrevented: false, target: null, ...over });
  assert.equal(shortcutSheetKeyAction(press()), "sheet.toggle");
  assert.equal(shortcutSheetKeyAction(press({ target: { tagName: "INPUT" } })), null);
  assert.equal(shortcutSheetKeyAction(press({ target: { isContentEditable: true } })), null);
  assert.equal(shortcutSheetKeyAction(press({ defaultPrevented: true })), null);
  assert.equal(shortcutSheetKeyAction(press({ metaKey: true })), null);
  assert.equal(shortcutSheetKeyAction(press({ altKey: true })), null);
  assert.equal(shortcutSheetKeyAction({ key: "Escape" }, { open: true }), "sheet.close");
  assert.equal(shortcutSheetKeyAction({ key: "Escape" }, { open: false }), null);
});

// --- the two handlers we can only read, not call -----------------------------

test("the declared inline bindings match what those files actually bind", () => {
  const scanned = scanAll(INLINE_SOURCES.map((source) => ({ ...source, text: read(source.file) })));
  const seen = scanned.map((row) => `${row.surface} ${row.combo}`).sort();
  const declared = INLINE_BINDINGS.map((row) => `${row.surface} ${row.combo}`).sort();
  assert.deepEqual(
    declared,
    seen,
    "INLINE_BINDINGS in shortcutSheet.js no longer matches the keys in " +
      `${INLINE_SOURCES.map((s) => s.file).join(" / ")}. ${FIX}`,
  );
});

test("changing what an inline binding DOES puts the wording back in front of a human", () => {
  const scanned = scanAll(INLINE_SOURCES.map((source) => ({ ...source, text: read(source.file) })));
  for (const declared of INLINE_BINDINGS) {
    const found = scanned.find((row) => row.surface === declared.surface && row.combo === declared.combo);
    assert.ok(found, `${declared.combo} is declared for ${declared.file} but that file no longer binds it`);
    assert.equal(
      found.effectHash,
      declared.effect,
      `the code behind ${declared.combo} in ${declared.file} changed (${found.effectHash}, ` +
        `was ${declared.effect}). Re-read it, confirm "${SHORTCUT_COPY[declared.id].label}" is still ` +
        `true, then update the effect hash. It now runs: ${found.effect}`,
    );
  }
});

// --- nothing may resolve a key behind the sheet's back -----------------------

test("every keydown handler in the board IDE is either probed or declared", () => {
  // Discovered, not listed: a new file that starts answering keys is found by
  // walking the directory, so the sheet cannot quietly fall behind the app.
  //
  // Two kinds of handler, held to two bars:
  //
  //   window listener  — a real shortcut, works with nothing focused. It must
  //                      hand the key to a pure arbiter the sheet probes, or
  //                      be declared in INLINE_SOURCES.
  //   onKeyDown prop   — scoped to one control. It only has to be declared if
  //                      it decides a key meaning of its own; Enter and Space
  //                      activating a focused button is what every button
  //                      does and is not a shortcut to teach.
  //
  // The bounded gap: a scoped handler written as a negated guard
  // (`if (event.key !== "x") return`) is invisible to the reader, because that
  // is exactly the shape ARIA activation is written in. A window listener
  // cannot hide that way — it has to delegate.
  const probedFiles = new Set(PROBES.map((probe) => probe.file));
  const declaredFiles = new Set(INLINE_SOURCES.map((source) => source.file));
  const files = fs.readdirSync(BOARD_DIR).filter((name) => /\.jsx?$/u.test(name));
  let globalHandlers = 0;

  for (const file of files) {
    const text = read(file);
    const isGlobal = text.includes('addEventListener("keydown"');
    if (!isGlobal && !text.includes("onKeyDown")) continue;
    // An arbiter the sheet already calls, or a handler already written down.
    if (probedFiles.has(file) || declaredFiles.has(file)) continue;

    assert.deepEqual(
      scanKeyBindings(file, text).map((binding) => binding.combo),
      [],
      `${file} decides key meanings inline, so the shortcut sheet cannot see them. ` +
        `Either move the decision into a pure resolver and add it to PROBES, or add ${file} ` +
        `to INLINE_SOURCES with its bindings. ${FIX}`,
    );

    if (!isGlobal) continue;
    globalHandlers += 1;
    const delegates = [...probedFiles].some((probed) => text.includes(probed.replace(/\.jsx?$/u, "")));
    assert.ok(
      delegates,
      `${file} listens on window for keydown but calls no arbiter the sheet knows about, ` +
        `so its keys would be undiscoverable. ${FIX}`,
    );
  }

  // If the walk stops finding the handlers we know exist, the guard has gone
  // quiet rather than clean.
  assert.ok(globalHandlers >= 2, `expected the workspace and canvas listeners, found ${globalHandlers}`);
});

// --- the reader itself --------------------------------------------------------

test("masking keeps offsets and survives JSX, templates and comments", () => {
  const source = [
    'const a = "brace { in a string";',
    "// comment with a } brace",
    "const b = `text ${ { nested: 1 } } more`;",
    "const el = <div className=\"x\">closing</div>;",
    "/* block } comment */",
    "if (event.key === 'x') { run(); }",
  ].join("\n");
  const { code, mask } = maskSource(source);
  assert.equal(mask.length, source.length, "masking must preserve offsets");
  assert.equal(code.length, source.length);
  let depth = 0;
  for (const c of mask) {
    if (c === "{") depth += 1;
    else if (c === "}") depth -= 1;
  }
  assert.equal(depth, 0, "every brace the masker keeps must pair");
  // The literal survives in `code` so a key can still be read out of it.
  assert.ok(code.includes("'x'") || code.includes('"x"'));
});

test("the reader refuses a file it could not parse rather than reporting nothing", () => {
  assert.throws(() => scanKeyBindings("broken.js", 'if (event.key === "a") { run();'), /unclosed brace/u);
  assert.throws(() => scanAll([{ surface: "x", file: "quiet.js", text: "export const x = 1;\n" }]), /no bindings/u);
});

test("the reader finds a switch, a braceless if and an enclosing modifier guard", () => {
  const source = [
    "function onKey(event) {",
    "  const key = event.key;",
    '  if (key === "Escape") return "clear";',
    "  if (event.shiftKey) {",
    '    if (key.toLowerCase() === "c") setFilter(null);',
    "    return null;",
    "  }",
    "  switch (key) {",
    '    case "e":',
    '    case "E":',
    "      toggleEdit();",
    "      break;",
    "    default:",
    "      break;",
    "  }",
    "}",
  ].join("\n");
  const found = scanKeyBindings("sample.js", source);
  const combos = found.map((binding) => binding.combo);
  assert.deepEqual(combos, ["E", "Esc", "Shift+C"]);
  assert.deepEqual(found.find((b) => b.combo === "E").keys, ["e", "E"]);
  assert.equal(found.find((b) => b.combo === "Shift+C").effect, "setFilter(null)");
});

test("a combo splits back into the keycaps it was joined from", () => {
  assert.deepEqual(comboParts("Mod+Shift+PgDn"), ["Mod", "Shift", "PgDn"]);
  assert.deepEqual(comboParts("?"), ["?"]);
  // `+` is a separator and also a key. Joining and splitting live together so
  // the renderer can never draw an empty keycap.
  assert.deepEqual(comboParts("+"), ["+"]);
  assert.deepEqual(comboParts("Mod++"), ["Mod", "+"]);
  for (const row of buildShortcutSheet().flatMap((group) => group.rows)) {
    for (const combo of row.combos) {
      for (const part of comboParts(combo)) assert.ok(part, `${row.id}: empty keycap in ${combo}`);
    }
  }
});

test("a negated modifier is not read as a binding on that modifier", () => {
  const source = [
    "function onKey(event) {",
    "  if (!event.shiftKey) {",
    '    if (event.key === "q") toggleUnits();',
    "  }",
    "}",
  ].join("\n");
  assert.deepEqual(scanKeyBindings("sample.js", source).map((b) => b.combo), ["Q"]);
});

// --- what the user finally sees ----------------------------------------------

test("the sheet renders as groups of rows, one row per thing to learn", () => {
  const sheet = buildShortcutSheet();
  assert.ok(sheet.length >= 4, "expected at least the four groups a user thinks in");
  const rows = sheet.flatMap((group) => group.rows);
  assert.ok(rows.length >= 25, `expected the whole binding set, got ${rows.length}`);
  for (const row of rows) {
    assert.ok(row.label, `${row.id} has no label`);
    assert.ok(row.combos.length > 0, `${row.id} has no key`);
    for (const combo of row.combos) assert.ok(!/undefined|NaN/u.test(combo), `${row.id}: bad combo ${combo}`);
  }
  // One command reachable two ways is one row, not two.
  const fit = rows.find((row) => row.id === "view.fit");
  assert.deepEqual(fit.combos, ["F", "Mod+PgDn"]);
  // Row ids are unique inside a group.
  for (const group of sheet) {
    const ids = group.rows.map((row) => row.id);
    assert.equal(new Set(ids).size, ids.length, `${group.id} repeats a row`);
  }
});


// An apostrophe in JSX prose is not a string quote.
//
// `NetWidthRow.jsx` gained the sentence "That is what this net's own pads
// allow" and the scanner reported the whole file as ending inside two unclosed
// braces — it had taken the apostrophe as a string start and swallowed every
// brace after it. It failed loudly, which is the right failure and is why this
// was caught immediately, but it made ordinary English a landmine in any file
// the sheet scans, and the sheet scans the ones full of user-facing copy.
test("prose with an apostrophe does not unbalance the scanner", () => {
  const source = [
    "export default function Row() {",
    "  return (",
    "    <div>",
    "      <p>That is what this net's own pads allow, and it isn't a promise.</p>",
    "      <button onKeyDown={(e) => { if (e.key === 'Enter') act(); }}>Set</button>",
    "    </div>",
    "  );",
    "}",
  ].join("\n");
  // Balanced: the apostrophes did not open anything.
  assert.doesNotThrow(() => scanKeyBindings(source, "Prose.jsx"));

  // And on the real file that prompted this. `scanAll` is the entry point the
  // sheet's honesty test uses and the one that threw; it must both survive the
  // prose and still find NetWidthRow's Enter binding, because a scanner that
  // goes quiet is as bad as one that throws.
  assert.match(
    read("NetWidthRow.jsx"),
    /net's own pads/,
    "the prose that broke the scanner is gone; keep a case that still has it",
  );
  const scanned = scanAll(INLINE_SOURCES.map((one) => ({ ...one, text: read(one.file) })));
  const fromRow = scanned.filter((one) => String(one.file || "").includes("NetWidthRow"));
  assert.ok(
    fromRow.some((one) => String(one.combo || "").includes("Enter")),
    `NetWidthRow's Enter binding was lost: ${JSON.stringify(scanned)}`,
  );
});
