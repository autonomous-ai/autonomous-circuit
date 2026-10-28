// The Case tab, mounted for real with the 3D view stubbed (no WebGL in node):
// the empty state says when a case appears; a built case lists its parts with
// the coupon first, offers each as STL and STEP, and shows the checker's word.

import assert from "node:assert/strict";
import test from "node:test";

import { click, flush, mount } from "../../../test/render.js";
import CaseTab from "../CaseTab.jsx";
import { __setTransportForTesting } from "@/lib/transport.ts";

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
  const calls = [];
  const restore = __setTransportForTesting({ export_case: async (...args) => { calls.push(args); return { path: "/Desktop/main-front.stl" }; } });
  const ui = mount(CaseTab, { artifact: caseArtifact(), projectId: "p", boardName: "main", Scene: StubScene });
  try {
    const status = ui.container.querySelector('[data-slot="case-status"]');
    assert.equal(status.dataset.state, "passed");
    assert.match(status.textContent, /Fits the board/);
    assert.ok(ui.container.querySelector('[data-slot="case-coupon"]'), "the coupon is called out");

    const rows = [...ui.container.querySelectorAll('[data-slot="case-part"]')];
    assert.deepEqual(rows.map((r) => r.dataset.part), ["coupon", "back", "front"]);
    const front = rows[2];
    const stl = front.querySelector('[data-slot="case-download-stl"]');
    assert.equal(stl.tagName, "BUTTON", "the webview must not navigate to STL bytes");
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
    click(stl);
    assert.deepEqual(calls, [["p", "main", "stl", "front"]]);
    assert.deepEqual(ui.errors, []);
  } finally {
    restore();
    ui.unmount();
  }
});

test("print plates group their exact parts while coupon and reference models stay separate", () => {
  sceneProps.length = 0;
  const parts = [
    ...caseArtifact().case.parts,
    { name: "board_reference", role: "reference", printable: false, stlUrl: "/projects/p/case/out/board_reference.stl?v=1-1", bytes: 12000 },
  ];
  const ui = mount(CaseTab, {
    artifact: caseArtifact("passed", {
      parts,
      plates: [
        { index: 1, name: "Base and front links", parts: ["front"] },
        { index: 2, name: "Lid and rear links", parts: ["back"] },
      ],
    }),
    Scene: StubScene,
  });
  const names = (element) => [...element.querySelectorAll('[data-slot="case-part"]')].map((row) => row.dataset.part);
  try {
    const plate1 = ui.container.querySelector('[data-slot="case-plate"][data-plate="1"]');
    const plate2 = ui.container.querySelector('[data-slot="case-plate"][data-plate="2"]');
    const other = ui.container.querySelector('[data-slot="case-other-parts"]');
    assert.ok(plate1);
    assert.ok(plate2);
    assert.ok(other);
    assert.match(plate1.textContent, /Plate 1.*Base and front links/s);
    assert.match(plate2.textContent, /Plate 2.*Lid and rear links/s);
    assert.deepEqual(names(plate1), ["front"]);
    assert.deepEqual(names(plate2), ["back"]);
    assert.deepEqual(names(other), ["coupon", "board_reference"]);
    assert.deepEqual(names(ui.container), ["front", "back", "coupon", "board_reference"], "each model appears in exactly one group");

    click(plate1.querySelector('[data-slot="case-part-toggle"]'));
    assert.equal(sceneProps.at(-1).visible.front, false);
    assert.equal(sceneProps.at(-1).visible.back, true);
    assert.equal(sceneProps.at(-1).visible.coupon, false);
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
  }
});

test("show only isolates one print plate and show all restores the assembly", () => {
  sceneProps.length = 0;
  const parts = [
    ...caseArtifact().case.parts,
    { name: "board_reference", role: "reference", printable: false, stlUrl: "/projects/p/case/out/board_reference.stl?v=1-1", bytes: 12000 },
  ];
  const ui = mount(CaseTab, {
    artifact: caseArtifact("passed", {
      parts,
      plates: [
        { index: 1, name: "Base and front links", parts: ["front"] },
        { index: 2, name: "Lid and rear links", parts: ["back"] },
      ],
    }),
    Scene: StubScene,
  });
  try {
    const plate2 = ui.container.querySelector('[data-slot="case-plate"][data-plate="2"]');
    assert.ok(plate2);
    click(plate2.querySelector('[data-slot="case-plate-focus"]'));
    assert.deepEqual(sceneProps.at(-1).visible, {
      front: false,
      back: true,
      coupon: false,
      board_reference: false,
    });
    click(ui.container.querySelector('[data-slot="case-show-all"]'));
    assert.deepEqual(sceneProps.at(-1).visible, {
      front: true,
      back: true,
      coupon: false,
      board_reference: true,
    });
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
  }
});

test("without verified plates the case keeps its flat parts list", () => {
  const ui = mount(CaseTab, { artifact: caseArtifact("passed", { plates: [] }), Scene: StubScene });
  try {
    assert.equal(ui.container.querySelector('[data-slot="case-plate"]'), null);
    assert.deepEqual(
      [...ui.container.querySelectorAll('[aria-label="Case parts"] [data-slot="case-part"]')].map((row) => row.dataset.part),
      ["coupon", "back", "front"],
    );
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
  }
});

test("one Print all click exports 3MF and opens it in Bambu; stale cases cannot print", async () => {
  const calls = [];
  const restore = __setTransportForTesting({
    export_case: async (...args) => { calls.push(["save", ...args]); return { path: "/Desktop/pet.3mf", canOpenBambu: true }; },
    export_open_bambu: async path => { calls.push(["open", path]); },
  });
  const ui = mount(CaseTab, { artifact: caseArtifact(), projectId: "p", boardName: "main", Scene: StubScene });
  try {
    click(ui.container.querySelector('[data-slot="case-print-all"]'));
    for (let i = 0; i < 4; i++) await flush();
    assert.deepEqual(calls, [["save", "p", "main", "3mf", undefined], ["open", "/Desktop/pet.3mf"]]);
    assert.match(ui.container.querySelector('[data-slot="case-saved"]').textContent, /\/Desktop\/pet.3mf/);
    ui.set({ artifact: caseArtifact("stale") });
    assert.equal(ui.container.querySelector('[data-slot="case-print-all"]').disabled, true);
  } finally { restore(); ui.unmount(); }
});

test("Bambu launch failure keeps the saved 3MF and can retry without exporting again", async () => {
  const calls = [];
  let fail = true;
  const restore = __setTransportForTesting({
    export_case: async () => { calls.push("save"); return { path: "/Desktop/pet.3mf", canOpenBambu: true }; },
    export_open_bambu: async path => { calls.push(path); if (fail) throw new Error("Could not open Bambu Studio"); },
  });
  const ui = mount(CaseTab, { artifact: caseArtifact(), projectId: "p", Scene: StubScene });
  try {
    click(ui.container.querySelector('[data-slot="case-print-all"]'));
    for (let i = 0; i < 4; i++) await flush();
    assert.match(ui.container.querySelector('[role="alert"]').textContent, /Could not open Bambu Studio/);
    assert.match(ui.container.querySelector('[data-slot="case-saved"]').textContent, /\/Desktop\/pet.3mf/);
    fail = false;
    click(ui.container.querySelector('[data-slot="case-open-bambu"]'));
    for (let i = 0; i < 4; i++) await flush();
    assert.deepEqual(calls, ["save", "/Desktop/pet.3mf", "/Desktop/pet.3mf"]);
    assert.equal(ui.container.querySelector('[role="alert"]'), null);
  } finally { restore(); ui.unmount(); }
});

test("Print all stays busy until Bambu opens", async () => {
  let finishOpen;
  const restore = __setTransportForTesting({
    export_case: async () => ({ path: "/Desktop/pet.3mf", canOpenBambu: true }),
    export_open_bambu: () => new Promise(resolve => { finishOpen = resolve; }),
  });
  const ui = mount(CaseTab, { artifact: caseArtifact(), projectId: "p", Scene: StubScene });
  try {
    click(ui.container.querySelector('[data-slot="case-print-all"]'));
    await flush();
    assert.equal(ui.container.querySelector('[data-slot="case-print-all"]').disabled, true);
    assert.equal(ui.container.querySelector('[data-slot="case-open-bambu"]').disabled, true);
    finishOpen(); await flush();
    assert.equal(ui.container.querySelector('[data-slot="case-print-all"]').disabled, false);
  } finally { restore(); ui.unmount(); }
});

test("switching boards during export does not launch the old board's 3MF", async () => {
  let finishSave;
  const opened = [];
  const restore = __setTransportForTesting({
    export_case: () => new Promise(resolve => { finishSave = resolve; }),
    export_open_bambu: async path => opened.push(path),
  });
  const ui = mount(CaseTab, { artifact: caseArtifact(), projectId: "p", Scene: StubScene });
  try {
    click(ui.container.querySelector('[data-slot="case-print-all"]'));
    ui.set({ projectId: "other" });
    finishSave({ path: "/Desktop/pet.3mf", canOpenBambu: true });
    await flush();
    assert.deepEqual(opened, []);
    assert.equal(ui.container.querySelector('[data-slot="case-saved"]'), null);
  } finally { restore(); ui.unmount(); }
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

test('a checked handwired assembly exports despite the irrelevant PCB report failing', async () => {
  const calls=[];
  const restore=__setTransportForTesting({export_case: async (...args) => {calls.push(args); return {path:'/Desktop/handwired.3mf'};}});
  const ui=mount(CaseTab,{artifact:caseArtifact('failed',{nativePCBCheckerApplies:false,printStatus:{state:'passed',scope:'handwired',reason:'Mechanical and printable surfaces checked; fit has not been tested on hardware.'}}),projectId:'p',boardName:'main',Scene:StubScene});
  try {
    await flush();
    assert.equal(ui.container.querySelector('[data-slot="case-print-all"]').disabled,false);
    assert.match(ui.container.querySelector('[data-slot="case-status"]').textContent,/Handwired case checked/);
    assert.doesNotMatch(ui.container.querySelector('[data-slot="case-status"]').textContent,/Fits the board/);
    assert.equal(sceneProps.at(-1).showBoard,false);
    click(ui.container.querySelector('[data-slot="case-print-all"]')); await flush();
    assert.deepEqual(calls,[['p','main','3mf',undefined]]);
    assert.equal(ui.container.querySelector('[role="alert"]'),null,"without Bambu support the 3MF is saved normally");
    ui.set({artifact:caseArtifact('failed',{nativePCBCheckerApplies:false,printStatus:{state:'stale',scope:'handwired',reason:'case changed'}})});
    assert.equal(ui.container.querySelector('[data-slot="case-print-all"]').disabled,true);
  } finally {ui.unmount();restore();}
});
