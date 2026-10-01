// The second exit from "ready". "Order at JLCPCB" walks the person through one
// fab's upload form; the gerbers themselves are the thing a *different* fab
// needs — the owner has a shop near him (2026-10-01). So the strip offers the
// file beside the walkthrough, under the same gate, and nowhere else: a board
// that is not ready shows neither button, and a ready board whose packet has
// no gerbers.zip shows only the walkthrough.

import assert from "node:assert/strict";
import test from "node:test";

import { click, mount } from "../../../test/render.js";

const READY = {
  source: { engine: "kicad-native" },
  fab: { ready: true },
  native: { manufacturing: { prototypeReady: true } },
  validation: { warnings: [] },
};

async function load() {
  return (await import("../BoardVerdict.jsx")).default;
}

test("a ready board with gerbers offers Export Gerber beside Order, and clicking it asks the browser for the zip", async () => {
  const BoardVerdict = await load();
  const ui = mount(BoardVerdict, {
    sidecar: READY,
    boardName: "pet-rover",
    gerbersUrl: "/projects/p1/boards/main_review/abc/manufacturing/gerbers.zip?v=1-1",
  });
  try {
    assert.ok(ui.container.querySelector('[data-slot="verdict-order"]'), "the JLCPCB walkthrough button is missing");
    const button = ui.container.querySelector('[data-slot="verdict-gerbers"]');
    assert.ok(button, "no Export Gerber button on a ready board that has gerbers");
    assert.match(button.textContent, /Export Gerber/);

    // The download helper appends an <a download> and clicks it; catch that click.
    const clicked = [];
    const original = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () { clicked.push({ href: this.getAttribute("href"), download: this.download }); };
    try {
      click(button);
    } finally {
      HTMLAnchorElement.prototype.click = original;
    }
    assert.equal(clicked.length, 1, "clicking Export Gerber did not request a download");
    assert.match(clicked[0].href, /gerbers\.zip/);
    assert.equal(clicked[0].download, "pet-rover-gerbers.zip");
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount?.();
  }
});

test("no gerbers.zip in the packet: only the walkthrough; not ready: neither", async () => {
  const BoardVerdict = await load();
  const ui = mount(BoardVerdict, { sidecar: READY, boardName: "pet-rover", gerbersUrl: "" });
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
