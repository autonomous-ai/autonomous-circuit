import assert from "node:assert/strict";
import test from "node:test";
import {
  activityTouchesBoard,
  boardLabel,
  boardStatus,
  boardStem,
  isBoardEntry,
  selectBoardEntries,
  selectPartsEntry,
} from "./boardModel.js";

test("isBoardEntry accepts boards/<stem>.tsx and boards/<stem>.circuit.json only", () => {
  assert.equal(isBoardEntry({ file: "boards/main.tsx", kind: "tsx" }), true);
  assert.equal(isBoardEntry({ file: "boards/main.circuit.json", kind: "json" }), true);
  // Not boards: nested files, hidden helpers, other roots/kinds.
  assert.equal(isBoardEntry({ file: "boards/_helper.tsx" }), false);
  assert.equal(isBoardEntry({ file: "boards/main_review/_schematic.png" }), false);
  assert.equal(isBoardEntry({ file: "blocks/ldo.tsx" }), false);
  assert.equal(isBoardEntry({ file: "boards/main.board.json" }), false);
  assert.equal(isBoardEntry({ file: "product.json" }), false);
  assert.equal(isBoardEntry(null), false);
});

test("boardStem strips the compound .circuit.json suffix and plain extensions", () => {
  assert.equal(boardStem("boards/main.tsx"), "main");
  assert.equal(boardStem("boards/main.circuit.json"), "main");
  assert.equal(boardStem("boards/desk-air.tsx"), "desk-air");
  assert.equal(boardStem(""), "");
});

test("boardLabel shortens long stems for the rail chip", () => {
  assert.equal(boardLabel("main"), "main");
  assert.equal(boardLabel("desk-air-monitor"), "desk-air-…");
});

test("selectBoardEntries dedupes by stem, preferring the artifact-bearing entry", () => {
  const tsx = { file: "boards/main.tsx", kind: "tsx" };
  const ir = {
    file: "boards/main.circuit.json",
    kind: "json",
    artifact: { metadataUrl: "/m?v=1-2" },
  };
  const other = { file: "boards/aux.tsx", kind: "tsx" };
  const entries = selectBoardEntries({ entries: [tsx, ir, other, { file: "notes.md", kind: "md" }] });
  assert.deepEqual(
    entries.map((e) => e.file),
    ["boards/aux.tsx", "boards/main.circuit.json"],
  );
});

test("activityTouchesBoard matches the board's file family by stem prefix", () => {
  assert.equal(activityTouchesBoard("boards/main.tsx", "main"), true);
  assert.equal(activityTouchesBoard("boards/main.circuit.json", "main"), true);
  assert.equal(activityTouchesBoard("boards/main.board.json", "main"), true);
  assert.equal(activityTouchesBoard("boards/main_review/_pcb.png", "main"), true);
  assert.equal(activityTouchesBoard("boards/main_fab/gerbers.zip", "main"), true);
  assert.equal(activityTouchesBoard("boards/aux.tsx", "main"), false);
  assert.equal(activityTouchesBoard("parts.json", "main"), false);
  assert.equal(activityTouchesBoard("anything", ""), false);
});

test("boardStatus: building within the activity window, ready with artifacts, else pending", () => {
  const now = 1_000_000;
  const entry = {
    file: "boards/main.tsx",
    artifact: { metadataUrl: "/m?v=1-2", schematicUrl: "/s?v=1-2" },
  };
  // Recent activity on the board's family → building.
  assert.equal(
    boardStatus(entry, { activity: { "boards/main_review/_pcb.png": now - 1000 }, now }),
    "building",
  );
  // Stale activity is ignored; artifacts present → ready.
  assert.equal(
    boardStatus(entry, { activity: { "boards/main.tsx": now - 60_000 }, now }),
    "ready",
  );
  // Activity on a different board never marks this one.
  assert.equal(
    boardStatus(entry, { activity: { "boards/aux.tsx": now - 1000 }, now }),
    "ready",
  );
  // No artifacts yet → pending.
  assert.equal(boardStatus({ file: "boards/main.tsx" }, { activity: {}, now }), "pending");
  // Sidecar without a schematic render is still pending (not "done" shape).
  assert.equal(
    boardStatus(
      { file: "boards/main.tsx", artifact: { metadataUrl: "/m?v=1-2" } },
      { activity: {}, now },
    ),
    "pending",
  );
});

test("selectPartsEntry finds the root parts.json regardless of kind", () => {
  const parts = { file: "parts.json", kind: "json", url: "/p?v=3-4" };
  assert.equal(selectPartsEntry({ entries: [{ file: "boards/main.tsx" }, parts] }), parts);
  assert.equal(selectPartsEntry({ entries: [{ file: "boards/parts.json" }] }), null);
  assert.equal(selectPartsEntry(null), null);
});
