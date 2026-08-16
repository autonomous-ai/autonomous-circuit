import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { buildBoardIndex } from "../../../lib/boardIndex.js";
import {
  ROTATION_STEPS,
  applyEdits,
  bindPlacements,
  describeRotate,
  formatDeg,
  geometrySnapshot,
  invertEdits,
  lockEdits,
  moveEdits,
  normalizeDeg,
  parseBoardSource,
  rebindPlacements,
  remapSnapshot,
  rotateEdits,
  rotateReasonFor,
  wrapPreview,
} from "../boardSource.js";

// The three committed boards, not fixtures. A synthetic board would agree with
// the parser by construction; these are the exact files the rotate path has to
// survive — a hand-written `<group pcbRotation={180}>`, a nine-line multi-line
// placement, forty lines of measurement prose an edit must not touch, and a
// `<board>` attribute list that ends in a block comment.
const REPO = path.resolve(fileURLToPath(new URL("../../../../../..", import.meta.url)));
const BOARDS = ["harness-puck", "hydrate-coaster", "terminal-keyboard"];
const tsxPath = (name) => path.join(REPO, `examples/${name}/boards/main.tsx`);
const jsonPath = (name) => path.join(REPO, `examples/${name}/boards/main.circuit.json`);
const hasExamples = BOARDS.every((name) => fs.existsSync(tsxPath(name)) && fs.existsSync(jsonPath(name)));

const readBoard = (name) => fs.readFileSync(tsxPath(name), "utf8");
const readIndex = (name) => buildBoardIndex(JSON.parse(fs.readFileSync(jsonPath(name), "utf8")));
const bindBoard = (name, source = readBoard(name)) => {
  const parsed = parseBoardSource(source);
  return { parsed, binding: bindPlacements(parsed.placements, readIndex(name)) };
};
const boundById = (name, id, source = readBoard(name)) => bindBoard(name, source).binding.byId.get(id);
const placementById = (source, id) => parseBoardSource(source).placements.find((p) => p.id === id);

// --- degrees ----------------------------------------------------------------

test("normalizeDeg wraps into [0, 360) and never returns -0", () => {
  assert.equal(normalizeDeg(337.5 + 90), 67.5);
  assert.equal(normalizeDeg(-90), 270);
  assert.equal(normalizeDeg(360), 0);
  assert.equal(normalizeDeg(720), 0);
  assert.equal(Object.is(normalizeDeg(-0), 0), true);
  assert.equal(normalizeDeg("nonsense"), 0);
});

test("formatDeg writes what a board file would write", () => {
  assert.equal(formatDeg(-0), "0");
  assert.equal(formatDeg(360), "0");
  assert.equal(formatDeg(90), "90");
  assert.equal(formatDeg(337.5), "337.5");
  // Rounds to a millidegree, and rounding that lands on a full turn is zero.
  assert.equal(formatDeg(359.99999), "0");
  assert.equal(formatDeg(45.00004), "45");
});

test("describeRotate reports the shorter way round, with the direction", () => {
  assert.equal(describeRotate("U4", 180, 270), "U4 turned 90° CCW (now 270°)");
  assert.equal(describeRotate("U4", 90, 0), "U4 turned 90° CW (now 0°)");
  assert.equal(describeRotate("U4", 0, 180), "U4 turned 180° CCW (now 180°)");
});

test("the rotation steps start at Altium's published default", () => {
  assert.equal(ROTATION_STEPS[0], 90);
  assert.deepEqual([...ROTATION_STEPS], [90, 45, 30, 15]);
});

// --- what the parser now reads ----------------------------------------------

test("parseBoardSource reads a rotation that is there, and 0 for one that is not", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const group = placementById(source, "group[1]");
  assert.equal(group.rotation, 180);
  assert.equal(source.slice(group.rotationSpan.start, group.rotationSpan.end), "180");
  assert.equal(source.slice(group.rotationPropSpan.start, group.rotationPropSpan.end), " pcbRotation={180}");

  const ldo = placementById(source, "Ldo3v3[1]");
  assert.equal(ldo.rotation, 0);
  assert.equal(ldo.rotationSpan, null);
  assert.equal(ldo.rotationPropSpan, null);
  // The insertion anchor is one past `pcbY={-18}`'s closing brace.
  assert.equal(source.slice(ldo.rotationInsertAt - 10, ldo.rotationInsertAt), "pcbY={-18}");
});

test(
  "across the three boards: 26 turn by prop, 18 by wrap, 13 refuse, 0 silently dropped",
  { skip: !hasExamples },
  () => {
    const tally = { prop: 0, wrap: 0, no: 0 };
    const refused = [];
    let bound = 0;
    let placements = 0;
    for (const name of BOARDS) {
      const { parsed, binding } = bindBoard(name);
      placements += parsed.placements.length;
      bound += binding.byId.size;
      assert.deepEqual(binding.unmatched, [], `${name} should bind every placement`);
      for (const placement of binding.byId.values()) {
        tally[placement.rotateVia] += 1;
        if (placement.rotateVia === "no") refused.push(placement.tag);
        // Nothing may be refused without a sentence saying why.
        assert.equal(placement.rotateVia === "no", Boolean(placement.rotateReason), placement.id);
      }
    }
    assert.equal(placements, 57);
    assert.equal(bound, 57);
    assert.deepEqual(tally, { prop: 26, wrap: 18, no: 13 });
    // Every refusal on our own boards is a round drill and nothing else.
    assert.deepEqual([...new Set(refused)], ["MountingHole"]);
  },
);

test("every wrappable placement is self-closing — the invariant the wrap rests on", { skip: !hasExamples }, () => {
  for (const name of BOARDS) {
    for (const placement of bindBoard(name).binding.byId.values()) {
      if (placement.rotateVia !== "wrap") continue;
      assert.equal(placement.selfClosing, true, `${name} ${placement.id}`);
      assert.equal(placement.elementEnd, placement.tagStart + (placement.elementEnd - placement.tagStart));
      assert.equal(readBoard(name)[placement.elementEnd - 1], ">");
    }
  }
});

// --- replacing a literal that is already there -------------------------------

test("turning a placement that already has an angle rewrites three bytes", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const group = placementById(source, "group[1]");
  const edits = rotateEdits(source, group, 270);
  assert.equal(edits.length, 1);
  assert.equal(edits[0].expected, "180");
  assert.equal(edits[0].text, "270");
  assert.equal(edits[0].end - edits[0].start, 3);

  const after = applyEdits(source, edits);
  assert.equal(after.length, source.length);
  // Every other byte of a 200-line file, including the measurement prose the
  // module header exists to protect.
  assert.equal(after.slice(0, edits[0].start), source.slice(0, edits[0].start));
  assert.equal(after.slice(edits[0].end), source.slice(edits[0].end));
  assert.equal(placementById(after, "group[1]").rotation, 270);
});

test("turning onto the angle already written is not an edit", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const group = placementById(source, "group[1]");
  assert.deepEqual(rotateEdits(source, group, 180), []);
  assert.deepEqual(rotateEdits(source, group, 540), []);
});

test("a placement with no angle and no turn asked for writes nothing", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  assert.deepEqual(rotateEdits(source, placementById(source, "testpoint[1]"), 0), []);
  assert.deepEqual(rotateEdits(source, placementById(source, "testpoint[1]"), 360), []);
});

// --- inserting a prop that is absent -----------------------------------------

test("inserting an angle disturbs no other byte, and re-parses to the same place", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const testpoint = placementById(source, "testpoint[1]");
  const edits = rotateEdits(source, testpoint, 45);
  assert.equal(edits.length, 1);
  assert.equal(edits[0].start, edits[0].end, "an insertion is a zero-width edit");
  assert.equal(edits[0].expected, "");
  assert.equal(edits[0].text, " pcbRotation={45}");

  const after = applyEdits(source, edits);
  assert.equal(after.length, source.length + edits[0].text.length);
  assert.equal(after.slice(0, edits[0].start), source.slice(0, edits[0].start));
  assert.equal(after.slice(edits[0].start + edits[0].text.length), source.slice(edits[0].start));

  const reparsed = placementById(after, "testpoint[1]");
  assert.equal(reparsed.rotation, 45);
  assert.equal(reparsed.x, testpoint.x);
  assert.equal(reparsed.y, testpoint.y);
  assert.equal(reparsed.rotateVia, "prop");
});

test("undo of an insertion removes the prop and its separator, byte for byte", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const after = applyEdits(source, rotateEdits(source, placementById(source, "testpoint[1]"), 45));
  const back = applyEdits(after, rotateEdits(after, placementById(after, "testpoint[1]"), null));
  assert.equal(back, source);
});

test("props written one per line get a new line at the same indent", () => {
  const fixture = [
    "const board = (",
    "  <board>",
    "    <resistor",
    '      name="R1"',
    "      pcbX={1}",
    "      pcbY={2}",
    '      resistance="1k"',
    "    />",
    "  </board>",
    ")",
    "",
  ].join("\n");
  const placement = parseBoardSource(fixture).placements[0];
  assert.equal(placement.rotationInsertPrefix, "\n      ");
  const after = applyEdits(fixture, rotateEdits(fixture, placement, 90));
  assert.equal(after.includes("      pcbY={2}\n      pcbRotation={90}\n"), true);
  // No line was joined and none was re-indented.
  assert.equal(after.split("\n").length, fixture.split("\n").length + 1);
  assert.equal(placementById(after, "resistor[1]").rotation, 90);
});

// --- wrapping one of our own components --------------------------------------

test("wrapping writes exactly the four-line block, and copies the coordinates", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const ldo = placementById(source, "Ldo3v3[1]");
  assert.equal(ldo.rotateVia, "wrap");
  const edits = rotateEdits(source, ldo, 90);
  assert.equal(edits.length, 4);

  assert.equal(
    wrapPreview(source, ldo, 90),
    ["    <group pcbX={14} pcbY={-18} pcbRotation={90}>", "      <Ldo3v3 pcbX={0} pcbY={0} schX={-46} schY={22} />", "    </group>"].join(
      "\n",
    ),
  );
});

test("a wrap obeys the server's own write contract", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const edits = rotateEdits(source, placementById(source, "Ldo3v3[1]"), 90);
  const sorted = [...edits].sort((a, b) => a.start - b.start || a.end - b.end);
  assert.ok(edits.length <= 8, "MAX_SOURCE_EDITS");
  for (const edit of edits) {
    assert.ok(edit.text.length <= 200, "MAX_EDIT_TEXT");
    assert.equal(edit.expected, source.slice(edit.start, edit.end), "compare-and-swap needs the exact bytes");
    assert.equal(/[^\t\n\r\x20-￿]/.test(edit.text), false, "no control characters");
  }
  for (let i = 1; i < sorted.length; i += 1) assert.ok(sorted[i].start >= sorted[i - 1].end, "no overlap");
  // Two insertions at one point would have an undefined order; there is one.
  const insertions = edits.filter((edit) => edit.start === edit.end).map((edit) => edit.start);
  assert.equal(new Set(insertions).size, insertions.length);
});

test("a wrap touches nothing outside its four spans", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const ldo = placementById(source, "Ldo3v3[1]");
  const edits = rotateEdits(source, ldo, 90);
  const after = applyEdits(source, edits);
  const first = Math.min(...edits.map((edit) => edit.start));
  assert.equal(after.slice(0, first), source.slice(0, first));
  // The six lines of measurement prose directly above the LDO are inside that
  // prefix, and this is the assertion that says so by name.
  assert.equal(after.includes("that put the LDO's own input cap inside C1's"), true);
  assert.equal(after.includes("This position is the best of the three measured."), true);
  // The tail past the closing tag is untouched too.
  const tail = source.slice(ldo.elementEnd);
  assert.equal(after.endsWith(tail), true);
});

test("the wrapper carries pcb props only, and the child keeps its schematic ones", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const preview = wrapPreview(source, placementById(source, "Ldo3v3[1]"), 90);
  const open = preview.split("\n")[0];
  assert.equal(open.includes("schX"), false);
  assert.equal(open.includes("schY"), false);
  assert.equal(preview.includes("schX={-46} schY={22}"), true);
});

test("a multi-line element keeps every interior byte and its own indent", { skip: !hasExamples }, () => {
  const source = readBoard("harness-puck");
  const ldo = placementById(source, "Ldo3v3[2]");
  assert.equal(source.slice(ldo.tagStart, ldo.elementEnd).includes("\n"), true);
  const preview = wrapPreview(source, ldo, 90);
  const lines = preview.split("\n");
  assert.equal(lines[0], "    <group pcbX={-12} pcbY={-11} pcbRotation={90}>");
  // Child indent equals the group indent — no `+2`, because re-indenting the
  // nine interior lines would rewrite bytes we promised not to touch.
  assert.equal(lines[1], "    <Ldo3v3");
  assert.equal(lines[lines.length - 1], "    </group>");
  const after = applyEdits(source, rotateEdits(source, ldo, 90));
  for (const line of ['      u="U5"', '      cin="C20"', '      cout="C21"', "      voutNet={LED_RAIL}", "      schX={-28}"]) {
    assert.equal(after.includes(`${line}\n`), true, line);
  }
});

test("coordinates are copied byte for byte into the wrapper, never reformatted", () => {
  const fixture = "const b = (\n  <board>\n    <Widget pcbX={014} pcbY={-18.0} />\n  </board>\n)\n";
  const placement = parseBoardSource(fixture).placements[0];
  assert.equal(placement.rotateVia, "wrap");
  assert.equal(wrapPreview(fixture, placement, 90).split("\n")[0], "    <group pcbX={014} pcbY={-18.0} pcbRotation={90}>");
});

test("the second turn of a wrapped placement is one edit, and wraps never nest", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const after = applyEdits(source, rotateEdits(source, placementById(source, "Ldo3v3[1]"), 90));
  // The new wrapper sits above the hand-written one, so it takes `group[1]`.
  const wrapper = parseBoardSource(after).placements.find((p) => p.tag === "group" && p.x === 14 && p.y === -18);
  assert.equal(wrapper.rotateVia, "prop");
  assert.equal(wrapper.rotation, 90);
  const second = rotateEdits(after, wrapper, 180);
  assert.equal(second.length, 1);
  assert.equal(applyEdits(after, second).includes("<group pcbX={14} pcbY={-18} pcbRotation={180}>"), true);
});

test("a lock above a wrapped element still reads as a lock afterwards", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const locked = applyEdits(source, lockEdits(source, placementById(source, "Ldo3v3[1]"), true));
  const ldo = placementById(locked, "Ldo3v3[1]");
  assert.equal(ldo.locked, true);
  const after = applyEdits(locked, rotateEdits(locked, ldo, 90));
  const wrapper = parseBoardSource(after).placements.find((p) => p.tag === "group" && p.x === 14 && p.y === -18);
  assert.equal(wrapper.locked, true);
});

// --- refusals ----------------------------------------------------------------

test("an angle written as an expression is refused, and the part still moves", () => {
  const fixture = "const b = (\n  <board>\n    <resistor pcbX={1} pcbY={2} pcbRotation={rot} />\n  </board>\n)\n";
  const placement = parseBoardSource(fixture).placements[0];
  assert.equal(placement.rotateVia, "no");
  assert.equal(placement.rotateBlock, "expression");
  assert.match(placement.rotateReason, /written as an expression/);
  assert.deepEqual(rotateEdits(fixture, placement, 90), []);
  // Still a placement, still draggable: an unturnable part must not lose its move.
  assert.equal(placement.x, 1);
  assert.equal(moveEdits(fixture, placement, 5, 2).length, 1);
});

test("an angle written as text is refused with its own reason", () => {
  const fixture = 'const b = (\n  <board>\n    <resistor pcbX={1} pcbY={2} pcbRotation="90deg" />\n  </board>\n)\n';
  const placement = parseBoardSource(fixture).placements[0];
  assert.equal(placement.rotateVia, "no");
  assert.equal(placement.rotateBlock, "text");
  assert.match(placement.rotateReason, /written as text, not a number/);
  assert.deepEqual(rotateEdits(fixture, placement, 90), []);
});

test("one of our components written with a closing tag is refused, not half-wrapped", () => {
  const fixture = "const b = (\n  <board>\n    <Widget pcbX={1} pcbY={2}>\n      <resistor pcbX={0} pcbY={0} />\n    </Widget>\n  </board>\n)\n";
  const placement = parseBoardSource(fixture).placements[0];
  assert.equal(placement.selfClosing, false);
  assert.equal(placement.elementEnd, null);
  assert.equal(placement.rotateVia, "no");
  assert.equal(placement.rotateBlock, "closing-tag");
  assert.deepEqual(rotateEdits(fixture, placement, 90), []);
});

test("an element sharing its line with other code is refused rather than overwriting it", () => {
  const fixture = "const b = (\n  <board>\n    {label}<Widget pcbX={1} pcbY={2} />\n  </board>\n)\n";
  const placement = parseBoardSource(fixture).placements[0];
  assert.equal(placement.rotateVia, "no");
  assert.equal(placement.rotateBlock, "shared-line");
  assert.deepEqual(rotateEdits(fixture, placement, 90), []);
});

test("a wrap that would exceed the server's per-edit cap is refused before the key", () => {
  const indent = " ".repeat(150);
  const fixture = `const b = (\n  <board>\n${indent}<Widget pcbX={1} pcbY={2} />\n  </board>\n)\n`;
  const placement = parseBoardSource(fixture).placements[0];
  assert.equal(placement.rotateVia, "no");
  assert.equal(placement.rotateBlock, "too-deep");
  assert.deepEqual(rotateEdits(fixture, placement, 90), []);
});

test("a round drill refuses, and it is the built board that decides that", { skip: !hasExamples }, () => {
  const source = readBoard("terminal-keyboard");
  // The parser alone would happily wrap a `<MountingHole>` — it is one of ours
  // and self-closing. Only the binding knows it came out as a `pcb_hole`.
  assert.equal(placementById(source, "MountingHole[1]").rotateVia, "wrap");
  const bound = boundById("terminal-keyboard", "MountingHole[1]", source);
  assert.equal(bound.roundDrill, true);
  assert.equal(bound.rotateVia, "no");
  assert.equal(bound.rotateBlock, "drill");
  assert.match(bound.rotateReason, /round drill/);
  assert.deepEqual(rotateEdits(source, bound, 90), []);
  // Still draggable — the safest edit on the board keeps working.
  assert.equal(moveEdits(source, bound, bound.x + 1, bound.y).length, 1);
});

test("a refusal reason always names the placement", () => {
  for (const block of ["expression", "text", "closing-tag", "shared-line", "too-deep", "drill"]) {
    assert.match(rotateReasonFor(block, "U7"), /^U7[ ']/);
  }
  assert.equal(rotateReasonFor("", "U7"), "");
});

// --- binding, undo and composition -------------------------------------------

test("a turn cannot break the binding — the anchor key does not move", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const before = bindBoard("hydrate-coaster", source);
  const after = applyEdits(source, rotateEdits(source, placementById(source, "group[1]"), 270));
  const rebound = bindBoard("hydrate-coaster", after);
  assert.equal(rebound.binding.byId.size, before.binding.byId.size);
  assert.deepEqual(rebound.binding.unmatched, []);
  assert.deepEqual(
    [...rebound.binding.byId.get("group[1]").pcbIds].sort(),
    [...before.binding.byId.get("group[1]").pcbIds].sort(),
  );
});

test("a wrap re-binds against the unchanged build, all 28, with the wrapper at the same anchor", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const after = applyEdits(source, rotateEdits(source, placementById(source, "Ldo3v3[1]"), 90));
  const { parsed, binding } = bindBoard("hydrate-coaster", after);
  assert.equal(parsed.placements.length, 28);
  assert.equal(binding.byId.size, 28);
  assert.deepEqual(binding.unmatched, []);
  const wrapper = [...binding.byId.values()].find((p) => p.tag === "group" && p.x === 14 && p.y === -18);
  assert.ok(wrapper, "the new wrapper binds");
  assert.deepEqual(wrapper.refdes, ["C2", "C3", "U2"]);
});

test("remapSnapshot carries geometry across the renumbering a wrap causes", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const before = bindBoard("hydrate-coaster", source);
  const snapshot = geometrySnapshot(before.binding);
  const ldoGeometry = snapshot.byId.get("Ldo3v3[1]");
  const rp2040Geometry = snapshot.byId.get("group[1]");
  assert.notDeepEqual(ldoGeometry.refdes, rp2040Geometry.refdes);

  const after = applyEdits(source, rotateEdits(source, placementById(source, "Ldo3v3[1]"), 90));
  const afterPlacements = parseBoardSource(after).placements;
  // Without the remap the new wrapper takes the name `group[1]` and would
  // inherit the RP2040 block's 22 components. This is that bug, asserted.
  const naive = rebindPlacements(afterPlacements, snapshot);
  assert.deepEqual(naive.byId.get("group[1]").refdes, rp2040Geometry.refdes);

  const remapped = remapSnapshot(snapshot, before.parsed.placements, afterPlacements);
  const rebound = rebindPlacements(afterPlacements, remapped);
  assert.deepEqual(rebound.unmatched, []);
  assert.deepEqual(rebound.byId.get("group[1]").refdes, ldoGeometry.refdes);
  assert.deepEqual(rebound.byId.get("group[2]").refdes, rp2040Geometry.refdes);
});

test("remapSnapshot drops the snapshot rather than guessing when the list changed size", () => {
  const snapshot = { byId: new Map([["a[1]", { refdes: ["U1"] }]]), reasonById: new Map() };
  const out = remapSnapshot(snapshot, [{ id: "a[1]" }], [{ id: "a[1]" }, { id: "b[1]" }]);
  assert.equal(out.byId.size, 0);
});

test("invertEdits puts every edit shape back, byte for byte", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const ldo = placementById(source, "Ldo3v3[1]");
  const cases = [
    rotateEdits(source, placementById(source, "group[1]"), 270),
    rotateEdits(source, placementById(source, "testpoint[1]"), 45),
    rotateEdits(source, ldo, 90),
    moveEdits(source, ldo, 20, -20),
    lockEdits(source, ldo, true),
  ];
  for (const edits of cases) {
    assert.ok(edits.length, "the case should produce edits");
    const after = applyEdits(source, edits);
    const inverse = invertEdits(edits);
    assert.equal(applyEdits(after, inverse), source);
    // And the inverse is itself a legal write: sorted, non-overlapping, and
    // every entry carrying the bytes then present.
    for (let i = 0; i < inverse.length; i += 1) {
      assert.equal(inverse[i].expected, after.slice(inverse[i].start, inverse[i].end));
      if (i) assert.ok(inverse[i].start >= inverse[i - 1].end);
    }
  }
});

test("a move and a turn on one placement compose into one legal write", { skip: !hasExamples }, () => {
  const source = readBoard("hydrate-coaster");
  const group = placementById(source, "group[1]");
  const edits = [...moveEdits(source, group, group.x + 3, group.y - 2), ...rotateEdits(source, group, 90)];
  assert.equal(edits.length, 3);
  const sorted = [...edits].sort((a, b) => a.start - b.start);
  for (let i = 1; i < sorted.length; i += 1) assert.ok(sorted[i].start >= sorted[i - 1].end);
  const after = applyEdits(source, edits);
  const moved = placementById(after, "group[1]");
  assert.equal(moved.x, group.x + 3);
  assert.equal(moved.y, group.y - 2);
  assert.equal(moved.rotation, 90);
});

test("every board round-trips: turn, re-parse, undo, byte-identical", { skip: !hasExamples }, () => {
  for (const name of BOARDS) {
    const source = readBoard(name);
    for (const placement of bindBoard(name, source).binding.byId.values()) {
      if (placement.rotateVia === "no") {
        assert.deepEqual(rotateEdits(source, placement, 90), [], `${name} ${placement.id} must write nothing`);
        continue;
      }
      const edits = rotateEdits(source, placement, 90);
      assert.ok(edits.length, `${name} ${placement.id} should turn`);
      const after = applyEdits(source, edits);
      assert.notEqual(after, source);

      // Undo by the same route the editor takes: a wrap replays its inverse,
      // a prop edit goes back to the angle the file had — `null` when it had
      // no prop at all, which is what makes the file identical again.
      const back =
        placement.rotateVia === "wrap"
          ? applyEdits(after, invertEdits(edits))
          : applyEdits(after, rotateEdits(after, placementById(after, placement.id), placement.rotationSpan ? placement.rotation : null));
      assert.equal(back, source, `${name} ${placement.id} did not round-trip`);
    }
  }
});
