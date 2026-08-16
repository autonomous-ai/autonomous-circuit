// editEngine — the round trip is the deliverable.
//
// Two properties this file exists to hold, both checked against the real board
// files rather than fixtures, because a fixture agrees with the parser by
// construction and these boards do not:
//
//   1. **Nothing is silently dropped.** Every `pcbX=` in every example board is
//      either an editable placement or a refusal with a sentence. The partition
//      is exact and the counts are asserted, so a parser change that starts
//      quietly ignoring a tag fails here rather than in front of a user who
//      cannot work out why a part will not drag.
//   2. **parse → edit → write → re-parse is stable, and undo is byte-exact.**
//      Every editable placement on all three boards is moved, re-read at its
//      new position, and undone back to a file that is byte-identical to the
//      one we started with. A no-op edit produces no edits at all.
//
// terminal-keyboard is the board that matters most here: 100 of its 137 parts
// come out of a `for` loop in `keyCells()`, and there is no literal in the file
// for any one of them. They must be refused with a reason, never mangled by an
// edit aimed at the nearest number.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { buildBoardIndex } from "../../../lib/boardIndex.js";
import { LOCK_COMMENT, applyEdits, parseBoardSource } from "../boardSource.js";
import {
  PLAN_ERROR,
  REFUSAL,
  applyPlan,
  createHistory,
  invertEdits,
  planLock,
  planMove,
  planNudge,
  readBoard,
  scanRefusals,
} from "../editEngine.js";

const REPO = path.resolve(fileURLToPath(new URL("../../../../../..", import.meta.url)));
const BOARDS = ["terminal-keyboard", "harness-puck", "hydrate-coaster"];
const boardDir = (name) => path.join(REPO, "examples", name, "boards");
const hasExamples = BOARDS.every(
  (name) => fs.existsSync(path.join(boardDir(name), "main.tsx")) && fs.existsSync(path.join(boardDir(name), "main.circuit.json")),
);
const readSource = (name) => fs.readFileSync(path.join(boardDir(name), "main.tsx"), "utf8");
const readIndex = (name) => buildBoardIndex(JSON.parse(fs.readFileSync(path.join(boardDir(name), "main.circuit.json"), "utf8")));

// --- the partition: editable, or refused with a reason ----------------------

test("every position in every real board is either editable or refused", { skip: !hasExamples }, () => {
  // Measured 2026-08-16 against the checked-in examples. If a board file gains
  // a placement these numbers move, and the count of `pcbX=` in the file is the
  // arithmetic that says the two lists still cover it.
  const expected = {
    "terminal-keyboard": { editable: 12, refused: 6 },
    "harness-puck": { editable: 17, refused: 7 },
    "hydrate-coaster": { editable: 28, refused: 1 },
  };
  for (const board of BOARDS) {
    const source = readSource(board);
    const scan = scanRefusals(source);
    const written = (source.match(/\bpcbX\s*=/g) || []).length;
    assert.equal(scan.editable.length, expected[board].editable, `${board} editable`);
    assert.equal(scan.refused.length, expected[board].refused, `${board} refused`);
    assert.equal(
      scan.editable.length + scan.refused.length,
      written,
      `${board}: ${written} tags name a pcbX; every one must land in exactly one list`,
    );
    // Nothing appears twice, and every refusal says something.
    const seen = new Set();
    for (const entry of [...scan.editable, ...scan.refused]) {
      assert.equal(seen.has(entry.at), false, `${board}: ${entry.tag} classified twice`);
      seen.add(entry.at);
    }
    for (const refusal of scan.refused) {
      assert.ok(refusal.reason.length > 20, `${board} L${refusal.line}: a refusal needs a sentence`);
      assert.ok(Object.values(REFUSAL).includes(refusal.code), `${board}: unknown code ${refusal.code}`);
      assert.ok(refusal.line > 0);
    }
  }
});

test("the editable list is exactly what the parser accepts", { skip: !hasExamples }, () => {
  for (const board of BOARDS) {
    const source = readSource(board);
    const scan = scanRefusals(source);
    const parsed = parseBoardSource(source);
    assert.deepEqual(
      scan.editable.map((entry) => entry.at).sort((a, b) => a - b),
      parsed.placements.map((placement) => placement.tagStart).sort((a, b) => a - b),
      `${board}: the scanner and the parser must agree on which tags are editable`,
    );
    // A tag the parser skipped is never just dropped — it is refused with a
    // reason, which is the whole difference between this and a silent no-op.
    for (const skip of parsed.skipped) {
      assert.ok(
        scan.refused.some((refusal) => refusal.tag === skip.tag),
        `${board}: parser skipped <${skip.tag}> with no refusal to show for it`,
      );
    }
  }
});

test("terminal-keyboard's 50 loop-placed keys are refused, not mangled", { skip: !hasExamples }, () => {
  const source = readSource("terminal-keyboard");
  const { editable, refused } = scanRefusals(source);

  // The three tags keyCells() emits — 50 diodes, 50 switches, 50 legends —
  // every one placed at `pcbX={x - 1.4}` / `pcbX={x}` where x = colX(c).
  const loops = refused.filter((entry) => entry.code === REFUSAL.LOOP_PLACED);
  assert.deepEqual(
    loops.filter((entry) => entry.line < 200).map((entry) => entry.tag),
    ["diode", "SwTact", "silkscreentext"],
  );
  for (const entry of loops) assert.match(entry.reason, /emits these in a loop/);

  // Not one of them leaks into the editable set, by tag or by byte range.
  const keyCellsStart = source.indexOf("const keyCells");
  const keyCellsEnd = source.indexOf("const matrixToMcu");
  assert.ok(keyCellsStart > 0 && keyCellsEnd > keyCellsStart);
  for (const entry of editable) {
    assert.equal(
      entry.at > keyCellsStart && entry.at < keyCellsEnd,
      false,
      `<${entry.tag}> inside keyCells() must not be editable`,
    );
  }
  const { byId } = readBoard(source, { index: readIndex("terminal-keyboard") });
  for (const id of byId.keys()) assert.equal(/^(diode|SwTact)\[/.test(id), false, `${id} is a generated key`);

  // The honest coverage number from the contract: 37 of 137 components move,
  // and the 100 that do not are exactly the key field.
  const index = readIndex("terminal-keyboard");
  const reachable = new Set();
  for (const placement of byId.values()) for (const key of placement.componentKeys) reachable.add(key);
  assert.equal(index.components.length, 137);
  assert.equal(reachable.size, 37);
});

test("a part inside another element is refused by name", { skip: !hasExamples }, () => {
  // hydrate-coaster wraps the RP2040 block in <group pcbRotation={180}>. The
  // group is draggable; the block inside it is not, because the board file's
  // only handle is the wrapper's own coordinates.
  const { refused } = scanRefusals(readSource("hydrate-coaster"));
  assert.equal(refused.length, 1);
  assert.equal(refused[0].code, REFUSAL.INSIDE_ELEMENT);
  assert.equal(refused[0].tag, "Rp2040Core");
  assert.match(refused[0].reason, /inside <group>/);
});

test("a part placed by a helper component names the helper", { skip: !hasExamples }, () => {
  const { refused } = scanRefusals(readSource("harness-puck"));
  const helpers = refused.filter((entry) => entry.code === REFUSAL.HELPER_COMPONENT);
  assert.deepEqual(helpers.map((entry) => entry.tag), ["group", "resistor", "group"]);
  assert.match(helpers[0].reason, /<PuckRing> places this/);
  assert.match(helpers[2].reason, /<DebugPort> places this/);
});

test("a computed position and a text position are refused differently", () => {
  const computed = scanRefusals(`<board>\n  <r pcbX={x + 1} pcbY={0} />\n</board>`).refused;
  assert.equal(computed[0].code, REFUSAL.COMPUTED_POSITION);
  assert.match(computed[0].reason, /computed \(pcbX=\{x \+ 1\}\)/);

  const text = scanRefusals(`<board>\n  <r pcbX="3mm" pcbY={0} />\n</board>`).refused;
  assert.equal(text[0].code, REFUSAL.TEXT_POSITION);
  assert.match(text[0].reason, /written as text/);

  // A `{cond && <x/>}` in child position is code, not a tag the file can move.
  const inExpression = scanRefusals(`<board>\n  {ok && <r pcbX={1} pcbY={2} />}\n</board>`).refused;
  assert.equal(inExpression[0].code, REFUSAL.INSIDE_EXPRESSION);
});

test("a pad inside an inline footprint is not mistaken for the part's position", () => {
  // Found by sweeping every .tsx in the repo: `blocks/*/ldo-3v3.tsx` writes the
  // land pattern inline, `footprint={<footprint><smtpad pcbX="2.92995985mm" …/>}`,
  // so a flat scan of the tag finds the PAD's coordinate before the part's own.
  // That is a wrong number wearing a right number's clothes — exactly what
  // "match, never guess" is for — so both the parser and this scanner skip
  // whole `{…}` attribute values.
  const source = [
    "<board>",
    '  <chip name="U1" footprint={<footprint><smtpad pcbX="2.93mm" pcbY="-2.3mm" /></footprint>}',
    "    pcbX={7} pcbY={-4} />",
    "</board>",
  ].join("\n");
  const { placements } = parseBoardSource(source);
  assert.equal(placements.length, 1);
  assert.deepEqual([placements[0].x, placements[0].y], [7, -4]);
  assert.equal(placements[0].name, "U1");
  // The nested pad is its own tag, and it is refused rather than dropped.
  const { editable, refused } = scanRefusals(source);
  assert.deepEqual(editable.map((entry) => entry.tag), ["chip"]);
  assert.deepEqual(refused.map((entry) => entry.tag), ["smtpad"]);
  // And a move writes the part's number, never the pad's.
  const plan = planMove(source, placements[0], 1, 2);
  assert.deepEqual(plan.edits.map((edit) => [edit.expected, edit.text]), [["7", "1"], ["-4", "2"]]);
  assert.equal(applyEdits(source, plan.edits).includes('pcbX="2.93mm"'), true);
});

// --- reading ----------------------------------------------------------------

test("readBoard reads positions with no built board at all", { skip: !hasExamples }, () => {
  const read = readBoard(readSource("hydrate-coaster"));
  assert.equal(read.ok, true);
  assert.equal(read.placements.length, 28);
  // Writable, but nothing to draw a handle on until a build exists.
  assert.equal(read.unbound.length, 28);
  assert.match(read.unbound[0].bindReason, /not been built/);
  const usb = read.byId.get("UsbCData[1]");
  assert.deepEqual([usb.x, usb.y], [0, -34]);
});

test("readBoard with a built board binds every placement and labels it", { skip: !hasExamples }, () => {
  const read = readBoard(readSource("hydrate-coaster"), { index: readIndex("hydrate-coaster") });
  assert.equal(read.unbound.length, 0);
  assert.equal(read.byId.get("group[1]").label, "U3 +21");
  assert.equal(read.byId.get("UsbCData[1]").label, "J1 +6");
  assert.ok(read.snapshot?.byId?.size === 28);
});

test("a file with no board is refused with a reason, not a crash", () => {
  const read = readBoard("export const x = 1\n");
  assert.equal(read.ok, false);
  assert.match(read.reason, /no <board>/);
  assert.deepEqual(read.placements, []);
});

// --- the round trip ---------------------------------------------------------

test("moving to where it already is produces no edit and no new bytes", { skip: !hasExamples }, () => {
  for (const board of BOARDS) {
    const source = readSource(board);
    for (const placement of readBoard(source).placements) {
      const plan = planMove(source, placement, placement.x, placement.y);
      assert.equal(plan.ok, true);
      assert.deepEqual(plan.edits, [], `${board} ${placement.id}: a no-op move must produce no edits`);
      const applied = applyPlan(source, plan);
      assert.equal(applied.changed, false);
      assert.equal(applied.text, source, `${board} ${placement.id}: a no-op must be byte-identical`);
    }
  }
});

test("every placement on every board survives move → re-parse → undo", { skip: !hasExamples }, () => {
  let moved = 0;
  for (const board of BOARDS) {
    const source = readSource(board);
    for (const placement of readBoard(source).placements) {
      // A delta with three decimals and a sign change on one axis, so the
      // literal's length changes in both directions across the corpus.
      const to = { x: placement.x + 1.234, y: placement.y - 7.5 };
      const plan = planMove(source, placement, to.x, to.y);
      assert.equal(plan.ok, true, `${board} ${placement.id}: ${plan.reason}`);
      assert.equal(plan.edits.length, 2);
      const { text, inverse } = applyPlan(source, plan);

      // Re-parse: same placements, this one at the new spot.
      const after = readBoard(text);
      assert.equal(after.placements.length, readBoard(source).placements.length, `${board} ${placement.id}: placement lost`);
      const again = after.byId.get(placement.id);
      // Against `plan.to`, not the float we asked for: the file is written to a
      // micron, so -45.8 + 1.234 lands as -44.566 and the plan says so. A test
      // that compared to the raw sum would be asserting the board file carries
      // -44.565999999999995, which is not a number anyone would write.
      assert.deepEqual([again.x, again.y], [plan.to.x, plan.to.y], `${board} ${placement.id}: did not land where it was sent`);
      assert.ok(Math.abs(plan.to.x - to.x) <= 0.0005 && Math.abs(plan.to.y - to.y) <= 0.0005);

      // Only the two numbers changed: strip every pcbX/pcbY value and the rest
      // of the file — comments, measurements, whitespace — is byte-identical.
      const stripped = (value) => value.replace(/pcb[XY]=\{[^}]*\}/g, "@");
      assert.equal(stripped(text), stripped(source), `${board} ${placement.id}: something other than the numbers moved`);

      // And the inverse restores the file exactly, byte for byte.
      assert.equal(applyEdits(text, inverse.edits), source, `${board} ${placement.id}: undo was not byte-exact`);
      moved += 1;
    }
  }
  assert.equal(moved, 57); // 12 + 17 + 28, the contract's table
});

test("a whole session of edits undoes back to the original file", { skip: !hasExamples }, () => {
  // The real shape of use: many edits in a row, each re-reading the file the
  // previous write produced, then undo all the way back. Offsets from an
  // earlier parse are dead the moment a write lands, so each step re-reads.
  const original = readSource("harness-puck");
  const history = createHistory();
  let source = original;
  const ids = readBoard(source).placements.map((placement) => placement.id);
  for (const [n, id] of ids.entries()) {
    const placement = readBoard(source).byId.get(id);
    const plan = planMove(source, placement, placement.x + (n % 2 ? 0.25 : -1.5), placement.y + 0.1);
    const applied = applyPlan(source, plan);
    history.push(applied.inverse);
    source = applied.text;
  }
  assert.equal(history.depth, ids.length);
  assert.notEqual(source, original);

  while (history.canUndo) {
    const plan = history.undo(source);
    assert.equal(plan.ok, true, plan.reason);
    source = applyPlan(source, plan).text;
  }
  assert.equal(source, original, "undoing every edit must give back the exact file we started from");
});

test("a lock is written above the tag and undone byte-exactly", { skip: !hasExamples }, () => {
  const source = readSource("hydrate-coaster");
  const placement = readBoard(source).byId.get("Ldo3v3[1]");
  assert.equal(placement.locked, false);

  const plan = planLock(source, placement, true);
  const { text, inverse } = applyPlan(source, plan);
  assert.equal(text.includes(`${placement.indent}${LOCK_COMMENT}\n`), true);
  assert.equal(readBoard(text).byId.get("Ldo3v3[1]").locked, true);
  // Inert to the compiler and to us: the placement is still at its coordinates
  // and still movable with the lock in place.
  const relocked = readBoard(text).byId.get("Ldo3v3[1]");
  assert.deepEqual([relocked.x, relocked.y], [placement.x, placement.y]);
  assert.equal(planMove(text, relocked, 1, 2).edits.length, 2);

  assert.equal(applyEdits(text, inverse.edits), source);
  // Locking an already-locked placement changes nothing.
  assert.deepEqual(planLock(text, relocked, true).edits, []);
});

// --- the inverse ------------------------------------------------------------

test("invertEdits accounts for the length change of every earlier edit", () => {
  const source = "abc DEF ghi";
  const edits = [
    { start: 0, end: 3, text: "wxyz", expected: "abc" }, // grows by 1
    { start: 8, end: 11, text: "g", expected: "ghi" }, // shrinks by 2
  ];
  const next = applyEdits(source, edits);
  assert.equal(next, "wxyz DEF g");
  const back = invertEdits(edits);
  assert.deepEqual(back, [
    { start: 0, end: 4, text: "abc", expected: "wxyz" },
    { start: 9, end: 10, text: "ghi", expected: "g" },
  ]);
  assert.equal(applyEdits(next, back), source);
});

test("an edit with no expected text cannot be inverted", () => {
  // `expected` is what makes both the write and the undo a compare-and-swap;
  // an edit without it could only be undone by trusting our own bookkeeping.
  assert.throws(() => invertEdits([{ start: 0, end: 1, text: "x" }]), /expected/);
});

// --- refusals on the request ------------------------------------------------

test("a plan against a file that moved under us is refused", { skip: !hasExamples }, () => {
  const source = readSource("hydrate-coaster");
  const placement = readBoard(source).byId.get("Ldo3v3[1]");
  // The agent edited the same line between our read and our drag.
  const agentEdited = source.replace("<Ldo3v3 pcbX={14} pcbY={-18}", "<Ldo3v3 pcbX={99} pcbY={-18}");
  assert.notEqual(agentEdited, source);
  const plan = planMove(agentEdited, placement, 1, 2);
  assert.equal(plan.ok, false);
  assert.equal(plan.code, PLAN_ERROR.STALE_SOURCE);
  assert.deepEqual(plan.edits, []);
  assert.throws(() => applyPlan(agentEdited, plan), /refused plan/);
});

test("a missing placement and a nonsense coordinate are refused", () => {
  const source = `<board>\n  <r pcbX={1} pcbY={2} />\n</board>`;
  const placement = readBoard(source).byId.get("r[1]");
  assert.equal(planMove(source, null, 1, 2).code, PLAN_ERROR.UNKNOWN_PLACEMENT);
  assert.equal(planMove(source, placement, Number.NaN, 2).code, PLAN_ERROR.INVALID_POSITION);
  assert.equal(planMove(source, placement, 1, Infinity).code, PLAN_ERROR.INVALID_POSITION);
});

// --- nudging ----------------------------------------------------------------

test("a nudge snaps the movement, not the destination", { skip: !hasExamples }, () => {
  // hydrate-coaster's TP1 sits at -2.54: a header on 2.54mm pitch. Snapping the
  // destination to a 0.5mm grid would land it at -2.5 and lose the pitch;
  // snapping the delta moves it exactly 0.5 and keeps it.
  const source = readSource("hydrate-coaster");
  const tp1 = readBoard(source).byId.get("testpoint[1]");
  assert.equal(tp1.x, -2.54);
  const plan = planNudge(source, tp1, 0.62, 0, { step: 0.5 });
  assert.deepEqual([plan.to.x, plan.to.y], [-2.04, -12]);
  assert.equal(plan.summary, "TP1 moved 0.5, 0 mm");
  // Under half a step is no movement at all, so no write.
  assert.deepEqual(planNudge(source, tp1, 0.2, 0, { step: 0.5 }).edits, []);
});

// --- history ----------------------------------------------------------------

test("history is 50 deep and refuses an undo onto somebody else's work", () => {
  const history = createHistory({ limit: 3 });
  const source = `<board>\n  <r name="R1" pcbX={1} pcbY={2} />\n</board>`;
  let text = source;
  for (let n = 0; n < 5; n += 1) {
    const placement = readBoard(text).byId.get("r[1]");
    const applied = applyPlan(text, planMove(text, placement, n + 10, 2));
    history.push(applied.inverse);
    text = applied.text;
  }
  assert.equal(history.depth, 3);
  assert.match(history.top.summary, /^undid: R1 moved/);

  // The agent rewrote the line. The undo would have to overwrite that, so it
  // refuses and keeps the entry rather than silently clobbering.
  const clobbered = text.replace("pcbX={14}", "pcbX={77}");
  const refused = history.undo(clobbered);
  assert.equal(refused.ok, false);
  assert.equal(refused.code, PLAN_ERROR.STALE_UNDO);
  assert.equal(history.depth, 3);

  assert.equal(applyPlan(text, history.undo(text)).text.includes("pcbX={13}"), true);
  assert.equal(history.depth, 2);
  history.clear();
  assert.equal(history.canUndo, false);
  assert.equal(history.undo(text).code, PLAN_ERROR.STALE_UNDO);
});

// --- the seam to the one command that writes a file --------------------------

test("the server splices an engine plan to exactly the text the engine predicts", { skip: !hasExamples }, async () => {
  // The engine computes the edit and the server applies it; if the two splicers
  // ever disagree the file on disk is not what the canvas thinks it is. Driving
  // the real `planSourceWrite` here is what makes "predicted === written" a
  // property rather than a hope.
  const { planSourceWrite } = await import("../../../../server/circuit/http.mjs");
  const source = readSource("harness-puck");
  const placement = readBoard(source).byId.get("Rp2040Core[1]");

  const plan = planMove(source, placement, placement.x - 3, placement.y + 0.5);
  const { text, inverse } = applyPlan(source, plan);
  const written = planSourceWrite(source, plan.edits, source.length);
  assert.equal(written.ok, true);
  assert.equal(written.text, text);

  // The inverse is the same kind of thing: it goes back through the same
  // compare-and-swap, so an undo is refused rather than clobbering an agent.
  const undone = planSourceWrite(text, inverse.edits, text.length);
  assert.equal(undone.ok, true);
  assert.equal(undone.text, source);

  // A drag is two edits and a lock is one, both well under the server's cap.
  assert.equal(plan.edits.length <= 8, true);
  assert.equal(planLock(source, placement, true).edits.length, 1);

  // And a plan built against a file that has since changed is refused by the
  // server too, on the same evidence the engine used: the expected text.
  const moved = source.replace("<Rp2040Core pcbX={-2} pcbY={11}", "<Rp2040Core pcbX={-9} pcbY={11}");
  assert.equal(planSourceWrite(moved, plan.edits, moved.length).code, "SOURCE_CHANGED");
});

test("the default history depth is the contract's fifty", () => {
  const history = createHistory();
  for (let n = 0; n < 60; n += 1) history.push({ ok: true, edits: [], summary: `#${n}` });
  assert.equal(history.depth, 50);
  assert.equal(history.top.summary, "#59");
});
