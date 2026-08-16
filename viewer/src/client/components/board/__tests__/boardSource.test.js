import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { buildBoardIndex } from "../../../lib/boardIndex.js";
import {
  LOCK_COMMENT,
  applyEdits,
  bindPlacements,
  describeMove,
  formatMm,
  geometrySnapshot,
  lockEdits,
  maskCommentsAndStrings,
  moveEdits,
  parseBoardSource,
  pendingMoves,
  readNumericProp,
  rebindPlacements,
  snapDelta,
} from "../boardSource.js";

// The real hydrate-coaster board. A synthetic fixture would agree with the
// parser by construction; this file is 200 lines of the exact thing the parser
// has to survive — JSX comments holding apostrophes and `<`, a nested `<group>`
// wrapper, props written as template literals, and forty measurements in prose
// that an edit must not touch.
const REPO = path.resolve(fileURLToPath(new URL("../../../../../..", import.meta.url)));
const HYDRATE_TSX = path.join(REPO, "examples/hydrate-coaster/boards/main.tsx");
const HYDRATE_JSON = path.join(REPO, "examples/hydrate-coaster/boards/main.circuit.json");
const hasExamples = fs.existsSync(HYDRATE_TSX) && fs.existsSync(HYDRATE_JSON);

const readBoard = () => fs.readFileSync(HYDRATE_TSX, "utf8");
const readIndex = () => buildBoardIndex(JSON.parse(fs.readFileSync(HYDRATE_JSON, "utf8")));

// --- masking ---------------------------------------------------------------

test("masking hides comment and string content from the structural scan", () => {
  const text = `const a = "x<y"; /* <fake /> */ <real a={1} /> // <also fake>\n`;
  const mask = maskCommentsAndStrings(text);
  const visible = [...text].map((c, i) => (mask[i] ? " " : c)).join("");
  assert.equal(visible.includes("<fake"), false);
  assert.equal(visible.includes("<also"), false);
  assert.equal(visible.includes("x<y"), false);
  assert.equal(visible.includes("<real a={1} />"), true);
});

test("an apostrophe inside a block comment does not open a string", () => {
  const text = `{/* the router's own clearance */}\n<r pcbX={1} pcbY={2} />`;
  const mask = maskCommentsAndStrings(text);
  const at = text.indexOf("<r ");
  assert.equal(mask[at], 0);
});

// --- prop reading ----------------------------------------------------------

test("readNumericProp takes the whole literal and nothing around it", () => {
  const text = `<resistor pcbX={-2.54} pcbY={ 12 } pcbXOffset={9} />`;
  const mask = maskCommentsAndStrings(text);
  const x = readNumericProp(text, mask, 0, text.length, "pcbX");
  assert.equal(x.value, -2.54);
  assert.equal(text.slice(x.start, x.end), "-2.54");
  const y = readNumericProp(text, mask, 0, text.length, "pcbY");
  assert.equal(y.value, 12);
  assert.equal(text.slice(y.start, y.end), "12");
});

test("a position written as text is reported, never rewritten as a number", () => {
  const text = `<resistor pcbX="3mm" pcbY={0} />`;
  const mask = maskCommentsAndStrings(text);
  assert.equal(readNumericProp(text, mask, 0, text.length, "pcbX"), "non-numeric");
  const parsed = parseBoardSource(`<board><resistor pcbX="3mm" pcbY={0} /></board>`);
  assert.deepEqual(parsed.placements, []);
  assert.equal(parsed.skipped.length, 1);
});

// --- parsing ---------------------------------------------------------------

test("only direct children of <board> are placements", () => {
  const source = [
    "<board width='10mm'>",
    "  <Block pcbX={1} pcbY={2}>",
    "    <Inner pcbX={0} pcbY={0} />",
    "  </Block>",
    "  <resistor name='R1' pcbX={3} pcbY={4} />",
    "</board>",
  ].join("\n");
  const { placements } = parseBoardSource(source);
  assert.deepEqual(
    placements.map((p) => [p.id, p.x, p.y, p.name]),
    [
      ["Block[1]", 1, 2, ""],
      ["resistor[1]", 3, 4, "R1"],
    ],
  );
});

test("a comment at the end of the <board> tag is not a self-closing slash", () => {
  // terminal-keyboard closes its <board> attribute list with a block comment.
  // Reading that comment's `*/` as `/>` made the whole board parse as empty
  // and silently produced zero placements — a wrong answer wearing a right
  // answer's clothes, which is the only kind this parser can give.
  const source = "<board\n  width={1}\n  /* a note ending in a slash / */\n>\n  <r pcbX={1} pcbY={2} />\n</board>";
  assert.equal(parseBoardSource(source).placements.length, 1);
});

test("a file with no <board> is refused with a reason, not a crash", () => {
  const parsed = parseBoardSource("export const x = 1\n");
  assert.equal(parsed.ok, false);
  assert.match(parsed.reason, /no <board>/);
  assert.deepEqual(parsed.placements, []);
});

test("the real hydrate-coaster board parses into the placements it declares", { skip: !hasExamples }, () => {
  const { ok, placements } = parseBoardSource(readBoard());
  assert.equal(ok, true);
  const byTag = new Map();
  for (const p of placements) byTag.set(p.tag, (byTag.get(p.tag) || 0) + 1);
  assert.equal(byTag.get("UsbCData"), 1);
  assert.equal(byTag.get("Ldo3v3"), 1);
  assert.equal(byTag.get("group"), 1);
  assert.equal(byTag.get("StatusLed"), 2);
  assert.equal(byTag.get("MountingHole"), 4);
  assert.equal(byTag.get("resistor"), 4);
  assert.equal(byTag.get("testpoint"), 3);
  // The RP2040 lives inside the <group> wrapper, so the board file cannot move
  // it on its own and it must not appear as a placement.
  assert.equal(placements.some((p) => p.tag === "Rp2040Core"), false);
  const usb = placements.find((p) => p.tag === "UsbCData");
  assert.deepEqual([usb.x, usb.y], [0, -34]);
});

// --- editing ---------------------------------------------------------------

test("a move rewrites two numbers and leaves every comment byte alone", { skip: !hasExamples }, () => {
  const source = readBoard();
  const { placements } = parseBoardSource(source);
  const ldo = placements.find((p) => p.tag === "Ldo3v3");
  const next = applyEdits(source, moveEdits(source, ldo, 16.5, -18));

  assert.equal(next.includes("<Ldo3v3 pcbX={16.5} pcbY={-18}"), true);
  assert.equal(source.length - next.length, "14".length - "16.5".length);
  // Everything outside the two literals is byte-identical.
  const stripped = (text) => text.replace(/pcbX=\{[^}]*\}\s*pcbY=\{[^}]*\}/g, "@@");
  assert.equal(stripped(next), stripped(source));
  // And the file still parses to the same placement set, at the new spot.
  const reparsed = parseBoardSource(next);
  assert.equal(reparsed.placements.length, placements.length);
  assert.deepEqual(
    [reparsed.placements.find((p) => p.tag === "Ldo3v3").x, reparsed.placements.find((p) => p.tag === "Ldo3v3").y],
    [16.5, -18],
  );
});

test("moving to where it already is produces no edit at all", { skip: !hasExamples }, () => {
  const source = readBoard();
  const { placements } = parseBoardSource(source);
  const usb = placements.find((p) => p.tag === "UsbCData");
  assert.deepEqual(moveEdits(source, usb, 0, -34), []);
});

test("every edit carries the text it expects to replace", () => {
  const source = `<board>\n  <r pcbX={1} pcbY={2} />\n</board>`;
  const { placements } = parseBoardSource(source);
  const edits = moveEdits(source, placements[0], 5, 6);
  assert.deepEqual(
    edits.map((e) => [source.slice(e.start, e.end), e.expected, e.text]),
    [
      ["1", "1", "5"],
      ["2", "2", "6"],
    ],
  );
});

test("overlapping edits throw rather than writing a plausible wrong file", () => {
  assert.throws(() => applyEdits("abcdef", [{ start: 0, end: 3, text: "x" }, { start: 2, end: 4, text: "y" }]));
});

test("formatMm writes a number a board file would write", () => {
  assert.equal(formatMm(-0), "0");
  assert.equal(formatMm(2.5), "2.5");
  assert.equal(formatMm(2.0004), "2");
  assert.equal(formatMm(-2.5400001), "-2.54");
  assert.equal(formatMm(1 / 3), "0.333");
});

test("snapping the delta keeps an odd pitch odd", () => {
  // A 2.54mm testpoint nudged 0.62mm on a 0.5 grid moves 0.5 and stays on pitch.
  assert.equal(formatMm(-2.54 + snapDelta(0.62, 0.5)), "-2.04");
  assert.equal(snapDelta(0.2, 0.5), 0);
  assert.equal(snapDelta(-0.9, 0.5), -1);
  assert.equal(snapDelta(0.37, 0.01), 0.37);
});

// --- locking ---------------------------------------------------------------

test("locking inserts a comment above the element at its own indent", () => {
  const source = `<board>\n    <r name="R1" pcbX={1} pcbY={2} />\n</board>`;
  const first = parseBoardSource(source).placements[0];
  assert.equal(first.locked, false);
  const locked = applyEdits(source, lockEdits(source, first, true));
  assert.equal(locked.includes(`    ${LOCK_COMMENT}\n    <r name="R1"`), true);

  const reparsed = parseBoardSource(locked).placements[0];
  assert.equal(reparsed.locked, true);
  // A locked placement is still movable text — the lock is advice to the next
  // agent, not a write barrier, so its spans must still be right.
  assert.equal(locked.slice(reparsed.xSpan.start, reparsed.xSpan.end), "1");

  const unlocked = applyEdits(locked, lockEdits(locked, reparsed, false));
  assert.equal(unlocked, source);
});

test("locking twice is a no-op", () => {
  const source = `<board>\n  <r pcbX={1} pcbY={2} />\n</board>`;
  const locked = applyEdits(source, lockEdits(source, parseBoardSource(source).placements[0], true));
  assert.deepEqual(lockEdits(locked, parseBoardSource(locked).placements[0], true), []);
});

// --- binding to geometry ---------------------------------------------------

test("placements bind to the real board's groups and parts", { skip: !hasExamples }, () => {
  const source = readBoard();
  const index = readIndex();
  const { placements } = parseBoardSource(source);
  const { byId, byComponentKey, unmatched } = bindPlacements(placements, index);

  const usb = byId.get("UsbCData[1]");
  assert.equal(usb.kind, "group");
  assert.deepEqual(usb.refdes, ["C1", "J1", "R1", "R2", "R3", "R4", "U1"]);

  // The <group pcbRotation={180}> wrapper owns the whole RP2040 block through
  // its child group — 22 parts the board file moves with one number.
  const brain = byId.get("group[1]");
  assert.equal(brain.kind, "group");
  assert.equal(brain.refdes.includes("U3"), true);
  assert.equal(brain.refdes.includes("Y1"), true);
  assert.equal(brain.componentKeys.length, 22);

  // A part the board wrote itself binds to exactly that part.
  const r30 = [...byId.values()].find((p) => p.name === "R30");
  assert.equal(r30.kind, "component");
  assert.deepEqual(r30.refdes, ["R30"]);

  // Every bound placement is reachable from any part it owns — that is what
  // makes "click a pad, drag the block" work.
  assert.equal(byComponentKey.get(brain.componentKeys[0]), "group[1]");

  // A mounting hole is a group that owns no parts and a silkscreen label has
  // no component at all; both still draw something exactly where the board
  // file says, and both bind through that.
  const hole = byId.get("MountingHole[1]");
  assert.deepEqual(hole.componentKeys, []);
  assert.equal(hole.pcbIds.size >= 1, true);
  assert.equal(byId.get("silkscreentext[1]").kind, "loose");

  // Every line of this board that names a position is draggable, and every
  // drawn element belongs to at most one of them.
  assert.equal(unmatched.length, 0);
  assert.equal(byId.size, placements.length);
});

test("two placements on one point bind to neither", () => {
  const index = buildBoardIndex([
    { type: "source_group", source_group_id: "g_root" },
    { type: "source_component", source_component_id: "sc_a", name: "R1", source_group_id: "g_root" },
    { type: "pcb_component", pcb_component_id: "pc_a", source_component_id: "sc_a", center: { x: 1, y: 2 }, width: 1, height: 1 },
    { type: "pcb_smtpad", pcb_smtpad_id: "pad_a", pcb_component_id: "pc_a", x: 1, y: 2, width: 1, height: 1, layer: "top" },
  ]);
  const source = `<board>\n  <r name="R1" pcbX={1} pcbY={2} />\n  <r name="R2" pcbX={1} pcbY={2} />\n</board>`;
  const { byId, unmatched } = bindPlacements(parseBoardSource(source).placements, index);
  assert.equal(byId.size, 0);
  assert.equal(unmatched.length, 2);
  for (const entry of unmatched) assert.match(entry.reason, /same exact spot|exact spot/);
});

test("no built board means every placement is unbound, with a reason", () => {
  const source = `<board>\n  <r pcbX={1} pcbY={2} />\n</board>`;
  const { byId, unmatched } = bindPlacements(parseBoardSource(source).placements, null);
  assert.equal(byId.size, 0);
  assert.match(unmatched[0].reason, /not been built/);
});

test("a block is named after the part that leads it, not the first refdes", { skip: !hasExamples }, () => {
  const { byId } = bindPlacements(parseBoardSource(readBoard()).placements, readIndex());
  // The RP2040 group holds C4..C17 as well as U3. Sorted alphabetically the
  // first refdes is C4, and "C4 +21" reads as a capacitor that brought a
  // microcontroller along with it.
  assert.equal(byId.get("group[1]").label, "U3 +21");
  assert.equal(byId.get("UsbCData[1]").label, "J1 +6");
});

test("geometry survives an edit the board has not been rebuilt for", { skip: !hasExamples }, () => {
  const source = readBoard();
  const index = readIndex();
  const first = bindPlacements(parseBoardSource(source).placements, index);
  const snapshot = geometrySnapshot(first);

  // Move the brain 7mm. The built board still has it at the old anchor, so a
  // fresh coordinate match would unbind exactly the thing that was just
  // dragged — no second drag, and no undo.
  const moved = applyEdits(source, moveEdits(source, first.byId.get("group[1]"), -13, -18.5));
  assert.equal(bindPlacements(parseBoardSource(moved).placements, index).byId.has("group[1]"), false);

  const rebound = rebindPlacements(parseBoardSource(moved).placements, snapshot);
  const brain = rebound.byId.get("group[1]");
  assert.deepEqual([brain.x, brain.y], [-13, -18.5]);
  assert.equal(brain.label, "U3 +21");
  assert.deepEqual([...brain.pcbIds], [...first.byId.get("group[1]").pcbIds]);
  assert.equal(rebound.byId.size, first.byId.size);
  // And it can be moved again, from where the file now says it is.
  assert.equal(moveEdits(moved, brain, -13, -18.5).length, 0);
  assert.equal(moveEdits(moved, brain, -20, -22).length, 2);
});

test("all three example boards bind every placement they declare", { skip: !hasExamples }, () => {
  for (const board of ["harness-puck", "terminal-keyboard", "hydrate-coaster"]) {
    const dir = path.join(REPO, "examples", board, "boards");
    const source = fs.readFileSync(path.join(dir, "main.tsx"), "utf8");
    const index = buildBoardIndex(JSON.parse(fs.readFileSync(path.join(dir, "main.circuit.json"), "utf8")));
    const parsed = parseBoardSource(source);
    const bound = bindPlacements(parsed.placements, index);
    assert.equal(parsed.ok, true, board);
    assert.ok(parsed.placements.length > 0, `${board} has placements`);
    assert.equal(bound.unmatched.length, 0, `${board} binds all ${parsed.placements.length}`);
  }
});

test(
  "every example board still parses as TSX after moving and locking everything",
  { skip: !hasExamples },
  async () => {
    // The one way a placement edit can break a board outright is by producing
    // a file the compiler will not read — a span that landed inside a comment,
    // or a lock comment inserted somewhere JSX does not allow one. Moving and
    // locking *every* placement on all three boards is 57 chances to do that,
    // and esbuild answers in milliseconds.
    const { transform } = await import("esbuild");
    for (const board of ["harness-puck", "terminal-keyboard", "hydrate-coaster"]) {
      const file = path.join(REPO, "examples", board, "boards", "main.tsx");
      const source = fs.readFileSync(file, "utf8");
      const { placements } = parseBoardSource(source);
      const edits = [];
      placements.forEach((placement, i) => {
        edits.push(...moveEdits(source, placement, placement.x + 1.25, placement.y - 0.75));
        if (i % 3 === 0) edits.push(...lockEdits(source, placement, true));
      });
      const next = applyEdits(source, edits);
      await transform(next, { loader: "tsx", jsx: "preserve" });

      // And the edited file still describes the same placements, moved.
      const reparsed = parseBoardSource(next);
      assert.equal(reparsed.placements.length, placements.length, board);
      assert.deepEqual(
        reparsed.placements.map((p) => [p.id, p.x, p.y]),
        placements.map((p) => [p.id, p.x + 1.25, p.y - 0.75]),
        board,
      );
      assert.equal(reparsed.placements.filter((p) => p.locked).length, Math.ceil(placements.length / 3), board);
    }
  },
);

test("describeMove says what changed in millimetres", () => {
  assert.equal(describeMove("U3", { x: 1, y: 2 }, { x: 3.5, y: 2 }), "U3 moved 2.5, 0 mm");
});

test("pendingMoves is every part the file has moved since the board was built", () => {
  // The gate grades the built artifact plus these translations, so an anchor
  // that never moved must not be in the list at all: sending `dx: 0` for 137
  // parts would make every check on terminal-keyboard carry 137 no-ops.
  const binding = {
    byId: new Map([
      ["a", { anchor: { x: 1, y: 2 }, offset: { dx: 0, dy: 0 } }],
      ["b", { anchor: { x: -4.5, y: 6 }, offset: { dx: 2.5, dy: -0.75 } }],
      ["c", { anchor: { x: 0, y: 0 }, offset: { dx: 0, dy: 3 } }],
      // Unbound: nothing on the built board sits where this line says, so
      // there is no anchor to translate from and it is dropped rather than
      // guessed at.
      ["d", { offset: { dx: 9, dy: 9 } }],
    ]),
  };

  assert.deepEqual(pendingMoves(binding), [
    { anchor: { x: -4.5, y: 6 }, dx: 2.5, dy: -0.75 },
    { anchor: { x: 0, y: 0 }, dx: 0, dy: 3 },
  ]);
  assert.deepEqual(pendingMoves(null), []);
  assert.deepEqual(pendingMoves({ byId: new Map() }), []);
});
