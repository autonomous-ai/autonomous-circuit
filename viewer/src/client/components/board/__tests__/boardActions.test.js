import assert from "node:assert/strict";
import test from "node:test";

import { boardActions, JLCPCB_QUOTE_URL, packetDownloads, visibleBoardActions } from "../boardActions.js";

const FULL_ARTIFACT = {
  gerbersUrl: "/p/main_fab/gerbers.zip?v=1-2",
  bomUrl: "/p/main_fab/bom.csv?v=1-2",
  cplUrl: "/p/main_fab/cpl.csv?v=1-2",
  kicadProjectUrl: "/p/main_fab/kicad-project.zip?v=1-2",
  glbUrl: "/p/main_fab/board.glb?v=1-2",
  orderUrl: "/p/main_fab/ORDER.md?v=1-2",
};

const byId = (actions, id) => actions.find((action) => action.id === id);

test("packetDownloads lists only the members that exist, in fab order", () => {
  assert.deepEqual(
    packetDownloads("main", FULL_ARTIFACT).map((entry) => entry.id),
    ["gerbers", "bom", "cpl", "kicad", "glb"],
  );
  assert.deepEqual(
    packetDownloads("main", { bomUrl: "/b.csv" }).map((entry) => entry.id),
    ["bom"],
  );
  assert.deepEqual(packetDownloads("main", null), []);
});

test("packet filenames carry the board stem so three boards do not collide in ~/Downloads", () => {
  const [gerbers] = packetDownloads("harness-puck", FULL_ARTIFACT);
  assert.equal(gerbers.filename, "harness-puck-gerbers.zip");
  assert.equal(packetDownloads("", FULL_ARTIFACT)[0].filename, "board-gerbers.zip");
});

test("Open in KiCad is NOT gated on fab-readiness — a broken board is the one you want in a real tool", () => {
  const actions = boardActions({
    stem: "main",
    artifact: FULL_ARTIFACT,
    sidecar: { fab: { ready: false }, validation: { warnings: [{ severity: "error" }] } },
  });
  const kicad = byId(actions, "open-kicad");
  assert.equal(kicad.enabled, true);
  assert.equal(kicad.kind, "download");
  assert.equal(kicad.note, "Unzip and open main.kicad_pro");
});

test("Open in KiCad is disabled with a reason before the project exists", () => {
  const kicad = byId(boardActions({ stem: "main", artifact: {} }), "open-kicad");
  assert.equal(kicad.enabled, false);
  assert.ok(kicad.reason.length > 0, "a disabled action must say why");
});

test("Order at JLCPCB needs both fab-readiness and an ORDER.md", () => {
  const ready = byId(
    boardActions({ artifact: FULL_ARTIFACT, sidecar: { fab: { ready: true } } }),
    "order",
  );
  assert.equal(ready.enabled, true);
  assert.equal(ready.kind, "tab");
  assert.equal(ready.target, "fab");
  assert.equal(ready.href, JLCPCB_QUOTE_URL);

  const notReady = byId(
    boardActions({ artifact: FULL_ARTIFACT, sidecar: { fab: { ready: false } } }),
    "order",
  );
  assert.equal(notReady.enabled, false);
  assert.match(notReady.reason, /some checks still fail/);

  // Fab says ready but the walkthrough never landed — a different problem, a
  // different sentence.
  const noOrderMd = byId(
    boardActions({ artifact: { ...FULL_ARTIFACT, orderUrl: "" }, sidecar: { fab: { ready: true } } }),
    "order",
  );
  assert.equal(noOrderMd.enabled, false);
  assert.match(noOrderMd.reason, /ordering guide/);
});

// The reasons are read by someone who just found a grey button, so they may
// not lean on a filename or a term of art to explain themselves.
test("no disabled reason needs electronics or filesystem knowledge to read", () => {
  const jargon = /gerber|fab-ready|fab packet|ORDER\.md|kicad-project\.zip|artifact|netlist|DRC|blocking finding/i;
  for (const sidecar of [null, { fab: { ready: false } }, { fab: { ready: true } }]) {
    for (const artifact of [null, {}, FULL_ARTIFACT]) {
      for (const action of boardActions({ artifact, sidecar })) {
        if (action.enabled) continue;
        assert.doesNotMatch(action.reason, jargon, `${action.id}: "${action.reason}"`);
        assert.match(action.reason, /\.$/, `${action.id} reason is a sentence`);
      }
    }
  }
});

test("every disabled action carries a reason and every enabled one does not", () => {
  for (const sidecar of [null, { fab: { ready: false } }, { fab: { ready: true } }]) {
    for (const artifact of [null, {}, FULL_ARTIFACT]) {
      for (const action of boardActions({ artifact, sidecar })) {
        if (action.enabled) assert.equal(action.reason, "", `${action.id} enabled but has a reason`);
        else assert.ok(action.reason, `${action.id} disabled with no reason`);
      }
    }
  }
});

test("the packet menu disappears when there is nothing in it; KiCad and Order always show", () => {
  const empty = visibleBoardActions(boardActions({ artifact: {} }));
  assert.deepEqual(empty.map((a) => a.id), ["open-kicad", "order"]);

  const full = visibleBoardActions(boardActions({ artifact: FULL_ARTIFACT, sidecar: { fab: { ready: true } } }));
  assert.deepEqual(full.map((a) => a.id), ["open-kicad", "packet", "order"]);
  assert.deepEqual(visibleBoardActions(null), []);
});
