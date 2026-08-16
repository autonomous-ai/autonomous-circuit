import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  AUTHOR,
  DEFAULT_REVISION_LIMIT,
  REVISION_KIND,
  countWarnings,
  readRevisions,
  recordEdit,
  recordRevision,
  revisionTrend,
  revisionsPath,
} from "./revisions.mjs";

function workspace() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "circuit-revisions-"));
}

const isBlocking = (w) => w.severity === "error";
const isElectrical = (w) => w.kind === "power_budget";

// --------------------------------------------------------------- counting

test("countWarnings splits into blocking, electrical and the rest", () => {
  const counts = countWarnings(
    [
      { severity: "error", kind: "dfm_edge_clearance" },
      { severity: "error", kind: "drc_violation" },
      { severity: "warning", kind: "power_budget" },
      { severity: "info", kind: "silkscreen_overlap" },
    ],
    { isBlocking, isElectrical },
  );
  assert.deepEqual(counts, {
    total: 4,
    blocking: 2,
    electrical: 1,
    other: 1,
  });
});

test("a blocking warning is never double-counted as electrical", () => {
  // An error-severity power_budget matches both predicates. The buckets have
  // to sum to the total or every percentage drawn from them is wrong.
  const counts = countWarnings([{ severity: "error", kind: "power_budget" }], {
    isBlocking,
    isElectrical,
  });
  assert.equal(counts.total, 1);
  assert.equal(counts.blocking, 1);
  assert.equal(counts.electrical, 0);
  assert.equal(counts.blocking + counts.electrical + counts.other, counts.total);
});

test("countWarnings tolerates junk instead of a list", () => {
  assert.equal(countWarnings(null).total, 0);
  assert.equal(countWarnings(undefined).total, 0);
});

// -------------------------------------------------------------- recording

test("recordRevision appends one JSON line per call and creates .circuit/", () => {
  const dir = workspace();
  assert.equal(recordRevision(dir, { turnId: "t1", phase: "structure", round: 1 }), true);
  assert.equal(recordRevision(dir, { turnId: "t1", phase: "structure", round: 2 }), true);

  const raw = fs.readFileSync(revisionsPath(dir), "utf8");
  const lines = raw.trim().split("\n");
  assert.equal(lines.length, 2);
  assert.equal(JSON.parse(lines[1]).round, 2);
});

test("fabReady is tri-state — unknown stays null rather than false", () => {
  // A round recorded mid-loop has not re-run the fab gate. Writing false
  // there would draw a history of failures that never happened.
  const dir = workspace();
  recordRevision(dir, { phase: "craft" });
  recordRevision(dir, { phase: "final", fabReady: true });
  recordRevision(dir, { phase: "final", fabReady: false });

  const got = readRevisions(dir).map((r) => r.fabReady);
  assert.deepEqual(got, [null, true, false]);
});

test("recording never throws, even at an unwritable path", () => {
  const dir = path.join(os.tmpdir(), "circuit-revisions-nope", "\0bad");
  assert.equal(recordRevision(dir, { phase: "final" }), false);
});

test("counts default to zero rather than undefined on the wire", () => {
  const dir = workspace();
  recordRevision(dir, { phase: "final" });
  assert.deepEqual(readRevisions(dir)[0].counts, {
    total: 0,
    blocking: 0,
    electrical: 0,
    other: 0,
  });
});

// ---------------------------------------------------------------- reading

test("a project that has never been built has an empty history, not an error", () => {
  assert.deepEqual(readRevisions(workspace()), []);
});

test("a truncated final line is skipped and the rest survives", () => {
  // The normal state of a file being appended to while it is read.
  const dir = workspace();
  recordRevision(dir, { phase: "structure", round: 1 });
  fs.appendFileSync(revisionsPath(dir), '{"phase":"craft","rou');

  const revisions = readRevisions(dir);
  assert.equal(revisions.length, 1);
  assert.equal(revisions[0].phase, "structure");
});

test("readRevisions returns oldest first and honours a limit from the end", () => {
  const dir = workspace();
  for (let i = 1; i <= 5; i += 1) {
    recordRevision(dir, { phase: "craft", round: i });
  }
  assert.deepEqual(
    readRevisions(dir, { limit: 2 }).map((r) => r.round),
    [4, 5],
  );
  assert.equal(readRevisions(dir).length, 5);
});

test("the default limit is applied when none is given", () => {
  const dir = workspace();
  for (let i = 0; i < DEFAULT_REVISION_LIMIT + 10; i += 1) {
    recordRevision(dir, { phase: "craft", round: i });
  }
  assert.equal(readRevisions(dir).length, DEFAULT_REVISION_LIMIT);
});

// ------------------------------------------------------------------ trend

test("one revision is not a trend", () => {
  // Claiming progress from a single data point is exactly the confident-but-
  // empty statement this app is trying not to make.
  assert.equal(revisionTrend([]), null);
  assert.equal(revisionTrend([{ counts: { blocking: 3 } }]), null);
  assert.equal(revisionTrend(null), null);
});

test("trend reports the fall in blocking findings across history", () => {
  const trend = revisionTrend([
    { counts: { blocking: 6 } },
    { counts: { blocking: 3 } },
    { counts: { blocking: 1 }, fabReady: false },
  ]);
  assert.deepEqual(trend, {
    builds: 3,
    from: 6,
    to: 1,
    fixed: 5,
    worse: false,
    fabReady: false,
  });
});

test("a board that got worse is reported as worse", () => {
  // A fix that moved a part can break clearance elsewhere. Saying so is the
  // point of keeping history at all.
  const trend = revisionTrend([
    { counts: { blocking: 1 } },
    { counts: { blocking: 4 } },
  ]);
  assert.equal(trend.worse, true);
  assert.equal(trend.fixed, 0, "fixed never goes negative");
});

test("trend surfaces fab-ready from the newest revision only", () => {
  const trend = revisionTrend([
    { counts: { blocking: 2 }, fabReady: false },
    { counts: { blocking: 0 }, fabReady: true },
  ]);
  assert.equal(trend.fabReady, true);
  assert.equal(trend.to, 0);
});

test("a full round trip through disk preserves the trend", () => {
  const dir = workspace();
  const mk = (blocking) => ({
    counts: countWarnings(
      Array.from({ length: blocking }, () => ({ severity: "error" })),
      { isBlocking, isElectrical },
    ),
  });
  recordRevision(dir, { phase: "structure", round: 1, ...mk(6) });
  recordRevision(dir, { phase: "structure", round: 2, ...mk(2) });
  recordRevision(dir, { phase: "final", ...mk(0), fabReady: true });

  const trend = revisionTrend(readRevisions(dir));
  assert.equal(trend.from, 6);
  assert.equal(trend.to, 0);
  assert.equal(trend.fixed, 6);
  assert.equal(trend.fabReady, true);
});

// ------------------------------------------------------- who changed what

test("a hand edit is recorded as a human placement round", () => {
  const dir = workspace();
  recordEdit(dir, {
    summary: "moved StatusLed from (-45.8, -32) to (-43, -32)",
    file: "boards/main.tsx",
    counts: { total: 14, blocking: 14 },
  });
  const [row] = readRevisions(dir);
  assert.equal(row.author, AUTHOR.HUMAN);
  assert.equal(row.kind, REVISION_KIND.EDIT);
  assert.equal(row.phase, "placement");
  assert.equal(row.file, "boards/main.tsx");
  // No compile ran, so orderability is unknown — never false.
  assert.equal(row.fabReady, null);
});

test("history written before author/kind existed still reads as an agent build", () => {
  const dir = workspace();
  fs.mkdirSync(path.dirname(revisionsPath(dir)), { recursive: true });
  fs.writeFileSync(
    revisionsPath(dir),
    `${JSON.stringify({ at: "2026-08-01T00:00:00Z", phase: "structure", counts: { blocking: 3 } })}\n`,
  );
  const [row] = readRevisions(dir);
  assert.equal(row.author, AUTHOR.AGENT);
  assert.equal(row.kind, REVISION_KIND.BUILD);
});

test("an author the UI cannot render is normalised, not stored", () => {
  const dir = workspace();
  recordRevision(dir, { phase: "final", author: "a passing stranger", kind: "vandalism" });
  const [row] = readRevisions(dir);
  assert.equal(row.author, AUTHOR.AGENT);
  assert.equal(row.kind, REVISION_KIND.BUILD);
});

test("edits are counted in the trend but never averaged into it", () => {
  // An edit's blocking count comes from the fast gate on predicted geometry and
  // a build's from the full gauntlet. Putting the two on one line would draw a
  // convergence no single measurement supports.
  const dir = workspace();
  recordRevision(dir, { phase: "structure", counts: { blocking: 6 } });
  recordEdit(dir, { summary: "moved R20", counts: { blocking: 14 } });
  recordRevision(dir, { phase: "final", counts: { blocking: 0 }, fabReady: true });

  const trend = revisionTrend(readRevisions(dir));
  assert.equal(trend.builds, 2);
  assert.equal(trend.edits, 1);
  assert.equal(trend.from, 6);
  assert.equal(trend.to, 0);
  assert.equal(trend.worse, false);
});

test("one build plus one edit is still not a trend, but the edit is reported", () => {
  const dir = workspace();
  recordRevision(dir, { phase: "structure", counts: { blocking: 2 } });
  recordEdit(dir, { summary: "moved R20" });
  assert.deepEqual(revisionTrend(readRevisions(dir)), { builds: 1, edits: 1 });
});
