import assert from "node:assert/strict";
import test from "node:test";

import {
  describeRevision,
  dotWindow,
  formatRevisionAge,
  MAX_DOTS,
  mergeRevision,
  REVISION_LIMIT,
  revisionToken,
  stepIndex,
  summarizeRevision,
  urlBelongsToProject,
  worstSeverity,
} from "../boardRevisions.js";

test("revisionToken reads the artifact URL's ?v= cache-bust", () => {
  assert.equal(revisionToken("/projects/p/boards/main.circuit.json?v=1754870000-2669644"), "1754870000-2669644");
  assert.equal(revisionToken("/x.json?a=1&v=abc&b=2"), "abc");
  assert.equal(revisionToken("/x.json"), "");
  assert.equal(revisionToken(""), "");
  assert.equal(revisionToken(null), "");
});

function sidecarWith(warnings, extra = {}) {
  return { validation: { warnings }, board: { widthMm: 70, heightMm: 70 }, ...extra };
}

test("summarizeRevision counts by severity and defaults an unknown severity to warning", () => {
  const summary = summarizeRevision({
    sidecar: sidecarWith(
      [
        { severity: "error" },
        { severity: "error" },
        { severity: "warning" },
        { severity: "info" },
        { severity: "bananas" },
        {},
      ],
      { fab: { ready: false } },
    ),
    index: { stats: { components: 58, nets: 37, elements: 4200 } },
  });
  assert.deepEqual(summary, {
    components: 58,
    nets: 37,
    elements: 4200,
    errors: 2,
    warnings: 3,
    infos: 1,
    fabReady: false,
    widthMm: 70,
    heightMm: 70,
  });
});

test("summarizeRevision survives a missing sidecar and a missing index", () => {
  const summary = summarizeRevision({});
  assert.equal(summary.errors, 0);
  assert.equal(summary.components, 0);
  assert.equal(summary.fabReady, false);
});

test("worstSeverity ranks errors over warnings over clean", () => {
  assert.equal(worstSeverity({ errors: 1, warnings: 9 }), "error");
  assert.equal(worstSeverity({ errors: 0, warnings: 2 }), "warning");
  assert.equal(worstSeverity({ errors: 0, warnings: 0 }), "clean");
  assert.equal(worstSeverity(null), "clean");
});

const rev = (token, errors, capturedAt) => ({
  token,
  capturedAt,
  summary: { errors, warnings: 0 },
});

test("mergeRevision appends newest last and dedupes by token", () => {
  let list = [];
  ({ list } = mergeRevision(list, rev("a", 6, 100)));
  ({ list } = mergeRevision(list, rev("b", 3, 200)));
  const again = mergeRevision(list, rev("b", 3, 999));
  assert.equal(again.added, false);
  assert.equal(again.list, list, "an unchanged re-read must not make a new array");
  assert.deepEqual(list.map((r) => r.token), ["a", "b"]);
});

test("a re-read refreshes the summary but keeps the original capturedAt", () => {
  let list = [];
  ({ list } = mergeRevision(list, rev("a", 6, 100)));
  // The sidecar can land before the IR, so a second look may see fewer errors.
  const merged = mergeRevision(list, rev("a", 2, 500));
  assert.equal(merged.added, false);
  assert.equal(merged.list[0].summary.errors, 2);
  assert.equal(merged.list[0].capturedAt, 100);
});

test("mergeRevision caps the ring, dropping the oldest", () => {
  let list = [];
  for (let i = 0; i < REVISION_LIMIT + 3; i += 1) {
    ({ list } = mergeRevision(list, rev(`t${i}`, 0, i)));
  }
  assert.equal(list.length, REVISION_LIMIT);
  assert.equal(list[0].token, "t3");
  assert.equal(list[list.length - 1].token, `t${REVISION_LIMIT + 2}`);
});

test("mergeRevision ignores an entry with no token", () => {
  const list = [rev("a", 0, 1)];
  const out = mergeRevision(list, { capturedAt: 2 });
  assert.equal(out.added, false);
  assert.equal(out.list, list);
});

test("stepIndex wraps in both directions and is safe on an empty ring", () => {
  assert.equal(stepIndex(0, -1, 5), 4);
  assert.equal(stepIndex(4, 1, 5), 0);
  assert.equal(stepIndex(2, 1, 5), 3);
  assert.equal(stepIndex(0, -3, 5), 2);
  assert.equal(stepIndex(0, 1, 0), 0);
});

test("dotWindow shows every dot below the cap", () => {
  const win = dotWindow(5, 2);
  assert.deepEqual(win.indices, [0, 1, 2, 3, 4]);
  assert.equal(win.hasBefore, false);
  assert.equal(win.hasAfter, false);
  assert.equal(win.isEdge(0), false);
});

test("dotWindow slides a fixed-width window past the cap and marks the edges", () => {
  const total = 12;
  const middle = dotWindow(total, 6);
  assert.equal(middle.indices.length, MAX_DOTS);
  assert.deepEqual(middle.indices, [3, 4, 5, 6, 7, 8, 9]);
  assert.equal(middle.hasBefore, true);
  assert.equal(middle.hasAfter, true);
  assert.equal(middle.isEdge(3), true);
  assert.equal(middle.isEdge(9), true);
  assert.equal(middle.isEdge(6), false);

  // Clamped at both ends — the window never runs off the list.
  assert.deepEqual(dotWindow(total, 0).indices, [0, 1, 2, 3, 4, 5, 6]);
  assert.equal(dotWindow(total, 0).hasBefore, false);
  assert.deepEqual(dotWindow(total, 11).indices, [5, 6, 7, 8, 9, 10, 11]);
  assert.equal(dotWindow(total, 11).hasAfter, false);

  // Out-of-range active indices are clamped rather than producing junk.
  assert.deepEqual(dotWindow(3, 99).indices, [0, 1, 2]);
  assert.deepEqual(dotWindow(0, 0).indices, []);
});

test("formatRevisionAge is short and coarse", () => {
  const now = 1_000_000_000;
  assert.equal(formatRevisionAge(now - 5_000, now), "now");
  assert.equal(formatRevisionAge(now - 240_000, now), "4m");
  assert.equal(formatRevisionAge(now - 7_200_000, now), "2h");
  assert.equal(formatRevisionAge(now - 3 * 86_400_000, now), "3d");
  assert.equal(formatRevisionAge(0, now), "");
});

test("describeRevision reads as a story against the previous build", () => {
  const now = 1_000_000_000;
  const older = { token: "a", capturedAt: now - 600_000, summary: { errors: 6, warnings: 2 } };
  const newer = { token: "b", capturedAt: now - 60_000, summary: { errors: 2, warnings: 1 } };
  const clean = { token: "c", capturedAt: now, summary: { errors: 0, warnings: 0, fabReady: true } };

  assert.equal(describeRevision(older, null, now), "10m — 6 errors · 2 warnings");
  assert.equal(describeRevision(newer, older, now), "1m — 2 errors · 1 warning · −4 vs previous");
  assert.equal(describeRevision(clean, newer, now), "now — fab-ready · −2 vs previous");
  // A regression reads as a regression.
  assert.equal(describeRevision(older, clean, now).endsWith("+6 vs previous"), true);
});

test("urlBelongsToProject stops a project switch filing one board's build under another", () => {
  const A = "1f3ecd83-1a1b-4b0b-9add-5e65bf3bcfbc";
  const B = "b6a59eab-5089-4af5-9221-567d3f41819d";
  const urlA = `/projects/${A}/boards/main.circuit.json?v=1-2`;
  assert.equal(urlBelongsToProject(urlA, A), true);
  // The frame where projectId has flipped but the artifacts have not.
  assert.equal(urlBelongsToProject(urlA, B), false);
  assert.equal(urlBelongsToProject(urlA, ""), false);
  assert.equal(urlBelongsToProject("", A), false);
  assert.equal(urlBelongsToProject(null, null), false);
});
