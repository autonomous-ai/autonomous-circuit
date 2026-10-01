// The second exit from "ready". "Order at JLCPCB" walks the person through one
// fab's upload form; the gerbers themselves are the thing a *different* fab
// needs — the owner has a shop near him (2026-10-01). So the strip offers the
// file beside the walkthrough, under the same gate, and nowhere else: a board
// that is not ready shows neither button, and a ready board whose packet has
// no gerbers.zip shows only the walkthrough.
//
// And the file is written by the server, not the browser: the Harness pane is
// a webview without a download manager, and the first version of this button
// (an <a download>) painted the zip's bytes on screen as text.

import assert from "node:assert/strict";
import test from "node:test";

import { click, flush, mount } from "../../../test/render.js";

const READY = {
  source: { engine: "kicad-native" },
  fab: { ready: true },
  native: { manufacturing: { prototypeReady: true } },
  validation: { warnings: [] },
};

async function load() {
  return (await import("../BoardVerdict.jsx")).default;
}

test("a ready board with gerbers offers Export Gerber beside Order; clicking asks the server and prints the path it answers", async () => {
  const BoardVerdict = await load();
  const calls = [];
  const ui = mount(BoardVerdict, {
    sidecar: READY,
    boardName: "pet-rover",
    gerbersUrl: "/projects/p1/boards/main_review/abc/manufacturing/gerbers.zip?v=1-1",
    onExportGerbers: async () => {
      calls.push(1);
      return { path: "/Users/me/Desktop/pet-rover-gerbers.zip", filename: "pet-rover-gerbers.zip" };
    },
  });
  try {
    assert.ok(ui.container.querySelector('[data-slot="verdict-order"]'), "the JLCPCB walkthrough button is missing");
    const button = ui.container.querySelector('[data-slot="verdict-gerbers"]');
    assert.ok(button, "no Export Gerber button on a ready board that has gerbers");
    assert.match(button.textContent, /Export Gerber/);
    click(button);
    await flush();
    await flush();
    assert.equal(calls.length, 1, "clicking Export Gerber did not ask the server");
    const note = ui.container.querySelector('[data-slot="verdict-export-note"]');
    assert.ok(note, "the strip did not say where the file went");
    assert.equal(note.textContent, "Saved to /Users/me/Desktop/pet-rover-gerbers.zip");
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount?.();
  }
});

test("a server refusal is printed, not swallowed", async () => {
  const BoardVerdict = await load();
  const ui = mount(BoardVerdict, {
    sidecar: READY,
    boardName: "pet-rover",
    gerbersUrl: "/projects/p1/gerbers.zip",
    onExportGerbers: async () => { throw new Error("no gerbers.zip in the packet yet"); },
  });
  try {
    click(ui.container.querySelector('[data-slot="verdict-gerbers"]'));
    await flush();
    await flush();
    assert.match(ui.text?.('[data-slot="verdict-export-note"]') ?? ui.container.querySelector('[data-slot="verdict-export-note"]').textContent, /Could not export: no gerbers\.zip/);
  } finally {
    ui.unmount?.();
  }
});

test("no gerbers.zip in the packet: only the walkthrough; not ready: neither", async () => {
  const BoardVerdict = await load();
  const ui = mount(BoardVerdict, { sidecar: READY, boardName: "pet-rover", gerbersUrl: "", onExportGerbers: async () => ({}) });
  try {
    assert.ok(ui.container.querySelector('[data-slot="verdict-order"]'));
    assert.equal(ui.container.querySelector('[data-slot="verdict-gerbers"]'), null, "Export Gerber offered with no file behind it");
    ui.set({
      sidecar: { ...READY, fab: { ready: false }, native: { manufacturing: { prototypeReady: false } } },
      gerbersUrl: "/projects/p1/gerbers.zip",
    });
    assert.equal(ui.container.querySelector('[data-slot="verdict-order"]'), null, "Order offered on an unready board");
    assert.equal(ui.container.querySelector('[data-slot="verdict-gerbers"]'), null, "Export Gerber offered on an unready board");
  } finally {
    ui.unmount?.();
  }
});
