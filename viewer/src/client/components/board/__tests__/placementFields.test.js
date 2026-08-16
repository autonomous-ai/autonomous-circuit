import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { buildBoardIndex } from "../../../lib/boardIndex.js";
import { applyEdits, bindPlacements, formatMm, moveEdits, parseBoardSource } from "../boardSource.js";
import { beginMove, commitMove, refusalForHit, trackMove } from "../placementDrag.js";
import {
  FALLBACK_LIMIT_MM,
  REASONS,
  editBlockedReason,
  fieldLimitMm,
  lockedReason,
  parseMmField,
  placementFields,
  placementMoveTo,
  placementSummary,
} from "../placementFields.js";

// Same corpus the parser tests use: the real hydrate-coaster board, because it
// is the one example where every component binds (44/44) and so the typed-field
// path can be run end to end against source a compiler actually accepted.
const REPO = path.resolve(fileURLToPath(new URL("../../../../../..", import.meta.url)));
const HYDRATE_TSX = path.join(REPO, "examples/hydrate-coaster/boards/main.tsx");
const HYDRATE_JSON = path.join(REPO, "examples/hydrate-coaster/boards/main.circuit.json");
const hasExamples = fs.existsSync(HYDRATE_TSX) && fs.existsSync(HYDRATE_JSON);
// The other end of the corpus: the board where most parts are NOT editable.
const KEYBOARD_TSX = path.join(REPO, "examples/terminal-keyboard/boards/main.tsx");
const KEYBOARD_JSON = path.join(REPO, "examples/terminal-keyboard/boards/main.circuit.json");
const hasKeyboard = fs.existsSync(KEYBOARD_TSX) && fs.existsSync(KEYBOARD_JSON);

const readBoard = () => fs.readFileSync(HYDRATE_TSX, "utf8");
const readIndex = () => buildBoardIndex(JSON.parse(fs.readFileSync(HYDRATE_JSON, "utf8")));

const PLACEMENT = { id: "StatusLed[1]", tag: "StatusLed", x: -45.8, y: -32, locked: false, refdes: ["LED1", "R20"], label: "LED1 +1", offset: { dx: 0, dy: 0 }, anchor: { x: -45.8, y: -32 } };
const READY = { viewing: false, editing: true, ready: true, reason: "", busy: false };

// --- reading a typed number ------------------------------------------------

test("an empty field asks for a number rather than writing zero", () => {
  const result = parseMmField("");
  assert.equal(result.ok, false);
  assert.equal(result.reason, "type a number in millimetres");
  assert.equal(result.written, "");
});

test("a value the board file could not hold is refused, never coerced", () => {
  // "3mm" is the exact thing readNumericProp already refuses to read back
  // (boardSource.js `pcbX="3mm"` is a string the compiler parses); parseFloat
  // would happily return 3 and write a silently different board.
  for (const raw of ["3mm", "abc", "--3", "1e2", "12,7", "0x10", " "]) {
    const result = parseMmField(raw);
    assert.equal(result.ok, false, `${raw} should be refused`);
    assert.match(result.reason, /is not a number|type a number/);
  }
});

test("a plain decimal is accepted, sign and leading dot included", () => {
  assert.deepEqual(
    ["-45.8", "+3", ".5", "0", "12."].map((raw) => parseMmField(raw).value),
    [-45.8, 3, 0.5, 0, 12],
  );
});

test("rounding to a micron is reported, not silent", () => {
  const result = parseMmField("-12.3456", { limitMm: 100 });
  assert.equal(result.ok, true);
  assert.equal(result.rounded, true);
  assert.equal(result.written, "-12.346");
  const exact = parseMmField("-12.346", { limitMm: 100 });
  assert.equal(exact.rounded, false);
});

test("a coordinate further out than the board itself is refused, with the number in the reason", () => {
  const result = parseMmField("-458", { limitMm: 100 });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "-458 mm is off the board — ±100 mm is as far as this one goes");
  assert.equal(parseMmField("-45.8", { limitMm: 100 }).ok, true);
});

test("the limit comes from the board, and falls back when there is no board", () => {
  assert.equal(fieldLimitMm({ width: 100, height: 60 }), 100);
  assert.equal(fieldLimitMm({ width: 8, height: 4 }), 10); // never tighter than 10mm
  assert.equal(fieldLimitMm(null), FALLBACK_LIMIT_MM);
});

// --- one edit shape, whatever produced it ----------------------------------

test("a typed number produces the edit a drag to the same place produces", () => {
  // The one that matters. Not "the same keys" — the same object, because both
  // sides end in placementDrag.commitMove. If someone changes the edit shape
  // for the canvas, this fails rather than the panel quietly emitting the old
  // one into a file a fab reads.
  const dragged = commitMove(trackMove(beginMove(PLACEMENT, { x: 0, y: 0 }, { x: 0, y: 0 }), { x: 2.8, y: 0 }, { clientX: 40, clientY: 0, step: 0.1 }));
  const typed = placementMoveTo(PLACEMENT, { x: -43 });
  assert.deepEqual(Object.keys(typed), ["placementId", "label", "refdes", "x", "y", "dx", "dy"]);
  assert.equal(typed.placementId, dragged.placementId);
  assert.equal(typed.label, dragged.label);
  assert.deepEqual(typed.refdes, dragged.refdes);
  // Both arrive at the same literal. The floats differ in the last bit -
  // -45.8 + 2.8 is not exactly -43 either way - and formatMm is the gate both
  // paths already go through before anything is written.
  assert.equal(formatMm(typed.x), formatMm(dragged.x));
  assert.equal(formatMm(typed.y), formatMm(dragged.y));
  assert.equal(formatMm(typed.x), "-43");
});

test("typing one axis leaves the other exactly where the file has it", () => {
  const x = placementMoveTo(PLACEMENT, { x: -43 });
  assert.equal(x.y, -32);
  assert.equal(x.dy, 0);
  const y = placementMoveTo(PLACEMENT, { y: -30 });
  assert.equal(y.x, -45.8);
  assert.equal(y.dx, 0);
  assert.equal(formatMm(y.y), "-30");
});

test("typing back the number already in the file writes nothing", () => {
  // commitMove's null, inherited: a drag that snaps back to its own start is a
  // click, and a field that is committed unchanged is not an edit either.
  assert.equal(placementMoveTo(PLACEMENT, { x: -45.8, y: -32 }), null);
  assert.equal(placementMoveTo(PLACEMENT, {}), null);
  assert.equal(placementMoveTo(null, { x: 1 }), null);
});

// --- why a field refuses ---------------------------------------------------

test("the blocking reason is the most immediate one, not the deepest", () => {
  // Someone looking at last week's build does not need to be told their part
  // is unreachable; they need to be told to come back to the current build.
  assert.equal(editBlockedReason({ viewing: true, editing: false, ready: false }), REASONS.viewing);
  assert.equal(editBlockedReason({ editing: false, ready: false }), REASONS.off);
  assert.equal(editBlockedReason({ editing: true, ready: false }), REASONS.loading);
  assert.equal(editBlockedReason({ editing: true, ready: false, reason: "no <board> element in this file" }), "no <board> element in this file");
  assert.equal(editBlockedReason(READY), "");
});

test("a part with no line of source behind it says so, rather than showing an empty box", () => {
  const fields = placementFields({ placement: null, editor: READY, board: { width: 100, height: 100 } });
  assert.equal(fields.x.editable, false);
  assert.equal(fields.x.reason, REASONS.unbound);
  assert.equal(fields.y.reason, REASONS.unbound);
  assert.equal(fields.lock.editable, false);
  assert.equal(fields.x.text, "");
});

test("the reason for an unreachable part is the canvas's own sentence, not a second one", () => {
  // placementDrag.refusalForHit already distinguishes copper from a block from
  // a loop. Typing and dragging must not grow two vocabularies for the same
  // refusal, so the panel passes that answer straight through.
  const refusal = { kind: "block", reason: "C7 moves with its block, and no line in this board file places that block" };
  const fields = placementFields({ placement: null, editor: READY, refusal });
  assert.equal(fields.x.reason, refusal.reason);
  assert.equal(fields.y.reason, refusal.reason);
  assert.equal(fields.lock.reason, refusal.reason);
  assert.equal(fields.rotation.reason, refusal.reason);
});

test("a bound placement is editable and shows the file's own numbers", () => {
  const fields = placementFields({ placement: PLACEMENT, editor: READY, board: { width: 100, height: 100 } });
  assert.equal(fields.x.editable, true);
  assert.equal(fields.x.text, "-45.8");
  assert.equal(fields.y.text, "-32");
  assert.equal(fields.x.reason, "");
  assert.equal(fields.limitMm, 100);
});

test("a locked placement refuses the numbers but keeps the way back out", () => {
  const fields = placementFields({ placement: { ...PLACEMENT, locked: true }, editor: READY });
  assert.equal(fields.x.editable, false);
  assert.equal(fields.x.reason, "LED1 +1 is locked by hand — unlock it to change these numbers");
  assert.equal(fields.x.reason, lockedReason(PLACEMENT.label));
  assert.equal(fields.lock.editable, true, "the unlock button is the only exit from a lock");
  assert.equal(fields.lock.value, true);
});

test("rotation reports the built angle and the file angle separately", () => {
  // The two are allowed to differ, and after a turn they do: `value` is what
  // the last build drew (which is where the pads on screen are), `fileValue`
  // is what boards/<stem>.tsx says now. Collapsing them would hide exactly the
  // state a rebuild resolves.
  const fields = placementFields({ placement: PLACEMENT, editor: READY, builtRotation: 90 });
  assert.equal(fields.x.editable, true);
  assert.equal(fields.rotation.value, 90);
  assert.equal(fields.rotation.fileValue, 0);
  // A turnable placement is turnable here too, through the same command the
  // canvas and the edit bar use — the panel does not have a second opinion.
  assert.equal(fields.rotation.editable, true);
  assert.equal(fields.rotation.reason, "");

  // A locked placement refuses in `placementRotate`'s words, not ours.
  const locked = placementFields({ placement: { ...PLACEMENT, locked: true }, editor: READY });
  assert.equal(locked.rotation.editable, false);
  assert.ok(locked.rotation.reason.length > 0);

  // …and when the whole panel is blocked, the blocking reason wins — telling
  // someone about a wrapper while they look at an old build is noise.
  const stale = placementFields({ placement: PLACEMENT, editor: { ...READY, viewing: true } });
  assert.equal(stale.rotation.reason, REASONS.viewing);
  assert.equal(stale.rotation.editable, false);
});

test("move mode off is a reason, not a hidden field", () => {
  const fields = placementFields({ placement: PLACEMENT, editor: { ...READY, editing: false } });
  assert.equal(fields.x.editable, false);
  assert.equal(fields.x.reason, REASONS.off);
  assert.equal(fields.x.text, "-45.8", "the number is still readable while it is not typeable");
});

// --- what the field says before it commits ---------------------------------

test("the summary names every part a typed number will move", () => {
  const summary = placementSummary(PLACEMENT);
  assert.equal(summary.parts, 2);
  assert.equal(summary.moves, "moves 2 parts — LED1, R20");
  assert.equal(summary.pending, "");
});

test("a hole or a label owns no parts, and says that instead of '0 parts'", () => {
  const summary = placementSummary({ ...PLACEMENT, refdes: [], label: "MountingHole", tag: "MountingHole" });
  assert.equal(summary.parts, 0);
  assert.equal(summary.moves, "moves the drawing on this line, no parts");
});

test("an edit the board has not been rebuilt for prints both numbers", () => {
  const summary = placementSummary({ ...PLACEMENT, x: -43, offset: { dx: 2.8, dy: 0 } });
  assert.equal(
    summary.pending,
    "the file says -43, -32 — the board below is still the last build at -45.8, -32",
  );
});

// --- end to end on a real board --------------------------------------------

test(
  "typing X then Y produces byte-identical source to dragging the same distance once",
  { skip: hasExamples ? false : "examples/hydrate-coaster is not in this checkout" },
  () => {
    const original = readBoard();
    const index = readIndex();
    const parsed = parseBoardSource(original);
    const binding = bindPlacements(parsed.placements, index);
    const start = [...binding.byId.values()].find((placement) => placement.refdes.length > 0);
    assert.ok(start, "hydrate-coaster has at least one bound placement with parts");
    const limitMm = fieldLimitMm(index.board);

    // The canvas: one drop, both axes at once.
    const drop = placementMoveTo(start, { x: start.x + 1.5, y: start.y - 0.25 });
    const dragged = applyEdits(original, moveEdits(original, start, drop.x, drop.y));

    // The panel: two fields, two commits, with the re-parse in between that
    // usePlacementEditor does after every write.
    const typedX = parseMmField(String(start.x + 1.5), { limitMm });
    assert.equal(typedX.ok, true);
    const afterX = placementMoveTo(start, { x: typedX.value });
    let typed = applyEdits(original, moveEdits(original, start, afterX.x, afterX.y));

    const reparsed = parseBoardSource(typed).placements.find((placement) => placement.id === start.id);
    assert.ok(reparsed, "the placement id survives its own edit");
    const typedY = parseMmField(String(start.y - 0.25), { limitMm });
    assert.equal(typedY.ok, true);
    const afterY = placementMoveTo(reparsed, { y: typedY.value });
    typed = applyEdits(typed, moveEdits(typed, reparsed, afterY.x, afterY.y));

    assert.equal(typed, dragged);
    assert.notEqual(typed, original);
    // And only the two literals moved — nothing reformatted around them.
    assert.equal(typed.split("\n").length, original.split("\n").length);
  },
);

test(
  "every bound placement on a real board offers two typeable numbers, and every refusal carries a reason",
  { skip: hasExamples ? false : "examples/hydrate-coaster is not in this checkout" },
  () => {
    const index = readIndex();
    const parsed = parseBoardSource(readBoard());
    const binding = bindPlacements(parsed.placements, index);
    let editable = 0;
    for (const placement of binding.byId.values()) {
      const fields = placementFields({ placement, editor: READY, board: index.board });
      assert.equal(fields.x.editable, !placement.locked);
      assert.equal(fields.y.editable, !placement.locked);
      if (fields.x.editable) editable += 1;
      else assert.ok(fields.x.reason.length > 0, `${placement.id} refuses without a reason`);
      // Rotation either turns or says why. The one thing it may not do is
      // refuse in silence.
      assert.ok(
        fields.rotation.editable || fields.rotation.reason.length > 0,
        `${placement.id} rotation refuses without a reason`,
      );
      assert.equal(parseMmField(fields.x.text, { limitMm: fieldLimitMm(index.board) }).ok, true);
      assert.equal(parseMmField(fields.y.text, { limitMm: fieldLimitMm(index.board) }).ok, true);
    }
    assert.equal(editable, 28, "hydrate-coaster's 28 bound placements are all typeable");
    for (const entry of binding.unmatched) assert.ok(entry.reason.length > 0);
  },
);

test(
  "on terminal-keyboard every one of the 100 unreachable parts gets a sentence, not a blank",
  { skip: hasKeyboard ? false : "examples/terminal-keyboard is not in this checkout" },
  () => {
    // The board that makes this requirement real: 37 of 137 components bind,
    // and the other 100 are keys emitted by keyCells(). A panel that just
    // greyed those out would leave a user with no way to tell "no literal
    // exists" from "this app is broken".
    const index = buildBoardIndex(JSON.parse(fs.readFileSync(KEYBOARD_JSON, "utf8")));
    const parsed = parseBoardSource(fs.readFileSync(KEYBOARD_TSX, "utf8"));
    const placements = bindPlacements(parsed.placements, index);

    let bound = 0;
    let refused = 0;
    for (const component of index.components) {
      const id = placements.byComponentKey.get(component.key);
      if (id) {
        bound += 1;
        continue;
      }
      const refusal = refusalForHit(index, placements, {
        componentKey: component.key,
        elementId: component.pcbId || "",
        element: null,
      });
      const fields = placementFields({ placement: null, editor: READY, board: index.board, refusal });
      assert.equal(fields.x.editable, false);
      assert.ok(fields.x.reason.length > 0, `${component.refdes} refuses without a reason`);
      assert.ok(
        fields.x.reason.includes(component.refdes),
        `${component.refdes}'s reason should name it, got: ${fields.x.reason}`,
      );
      refused += 1;
    }
    assert.equal(bound, 37, "the contract's measured coverage: 37 of 137");
    assert.equal(refused, 100);
  },
);
