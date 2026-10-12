// The Case tab, mounted for real with the 3D view stubbed (no WebGL in node):
// the empty state says when a case appears; a built case lists its parts with
// the coupon first, offers each as STL and STEP, and shows the checker's word.

import assert from "node:assert/strict";
import test from "node:test";

import { click, flush, mount } from "../../../test/render.js";
import CaseTab from "../CaseTab.jsx";

const sceneProps = [];
function StubScene(props) {
  sceneProps.push(props);
  return null;
}

function caseArtifact(state = "passed", extra = {}) {
  return {
    case: {
      state,
      reason: state === "failed" ? "1 case error" : "",
      contractUrl: "/projects/p/boards/main.enclosure.json?v=1-1",
      reportUrl: "/projects/p/case/reports/case-check.json?v=1-1",
      parts: [
        { name: "front", role: "shell", stlUrl: "/projects/p/case/out/front.stl?v=1-1", stepUrl: "/projects/p/case/out/front.step?v=1-1", bytes: 28000, printRotateDeg: [180, 0, 0], supports: false },
        { name: "back", role: "shell", stlUrl: "/projects/p/case/out/back.stl?v=1-1", bytes: 200000 },
        { name: "coupon", role: "coupon", stlUrl: "/projects/p/case/out/coupon.stl?v=1-1", bytes: 4400, printRotateDeg: [180, 0, 0] },
      ],
      ...extra,
    },
  };
}

test("no case yet: the tab says what comes first", () => {
  const ui = mount(CaseTab, { artifact: {}, Scene: StubScene });
  try {
    const empty = ui.container.querySelector('[data-slot="case-empty"]');
    assert.ok(empty);
    assert.match(empty.textContent, /No case yet/);
    assert.match(empty.textContent, /waits for finished copper/);
    ui.set({ artifact: { case: { state: "none", parts: [], contractUrl: "/c.json" } } });
    assert.match(ui.container.textContent, /The board knows where its screen, buttons and ports are/);
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
  }
});

test("a checked case: coupon first and hidden from the assembly, every part downloadable", () => {
  sceneProps.length = 0;
  const ui = mount(CaseTab, { artifact: caseArtifact(), boardName: "main", Scene: StubScene });
  try {
    const status = ui.container.querySelector('[data-slot="case-status"]');
    assert.equal(status.dataset.state, "passed");
    assert.match(status.textContent, /Fits the board/);
    assert.ok(ui.container.querySelector('[data-slot="case-coupon"]'), "the coupon is called out");

    const rows = [...ui.container.querySelectorAll('[data-slot="case-part"]')];
    assert.deepEqual(rows.map((r) => r.dataset.part), ["coupon", "back", "front"]);
    const front = rows[2];
    const stl = front.querySelector('[data-slot="case-download-stl"]');
    assert.equal(stl.getAttribute("download"), "main-front.stl");
    assert.equal(stl.getAttribute("href"), "/projects/p/case/out/front.stl?v=1-1");
    assert.ok(front.querySelector('[data-slot="case-download-step"]'));
    assert.equal(rows[1].querySelector('[data-slot="case-download-step"]'), null, "no STEP on disk, no link");
    assert.match(front.textContent, /flip it face down on the bed, no supports/);

    const last = sceneProps.at(-1);
    assert.equal(last.contractUrl, "/projects/p/boards/main.enclosure.json?v=1-1");
    assert.deepEqual(last.visible, { front: true, back: true, coupon: false });

    // Show the coupon, explode the case: the scene hears both.
    click(rows[0].querySelector('[data-slot="case-part-toggle"]'));
    click(ui.container.querySelector('[data-slot="case-explode"]'));
    assert.equal(sceneProps.at(-1).visible.coupon, true);
    assert.equal(sceneProps.at(-1).exploded, true);
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
  }
});

test("a failed check lists the checker's errors; a stale one says not to print", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ errors: [{ kind: "case_interface_blocked", message: "SW1 (button) is covered by front" }] }) });
  const ui = mount(CaseTab, { artifact: caseArtifact("failed"), Scene: StubScene });
  try {
    for (let i = 0; i < 4; i += 1) await flush();
    assert.match(ui.container.querySelector('[data-slot="case-status"]').textContent, /Does not fit yet/);
    assert.match(ui.container.querySelector('[data-slot="case-errors"]').textContent, /SW1 \(button\) is covered by front/);
    ui.set({ artifact: caseArtifact("stale", { reason: "the board changed since the case was checked" }) });
    assert.match(ui.container.textContent, /Needs a recheck/);
    assert.match(ui.container.textContent, /Do not print/);
  } finally {
    globalThis.fetch = original;
    ui.unmount();
  }
});
