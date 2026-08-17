// The EE review's finding 4, on screen.
//
// He asked for the power rails at 0.5–1.0mm instead of 0.2mm. On the board he
// was reviewing, one of those rails can take 1.1mm and the other cannot exceed
// 0.4000mm, because a track leaving the RP2040's 0.400mm-pitch pads is
// `2 x (0.400 - 0.100 - 0.100)` wide and no wider. The app used to be unable to
// tell those two apart, so the only way to find out was to try it — which cost
// harness-puck 33 blocking findings once and terminal-keyboard 2 another time.
//
// So the load-bearing assertion here is that the ceiling, the pin that holds
// it, and the fab's floor are all on screen *before* the box that sets a width.

import assert from "node:assert/strict";
import test from "node:test";

import { click, key, mount, settle } from "../../../test/render.js";
import NetWidthRow from "../NetWidthRow.jsx";

const CAPPED = {
  net: "V3_3",
  ceiling_mm: 0.4,
  ceiling_at: "U3.IOVDD6",
  ceiling_capped: false,
  narrowest_mm: 0.2,
  declared_mm: null,
};

const widths = (row, extra = {}) => ({
  byNet: new Map(row ? [[row.net, row]] : []),
  busy: false,
  reason: "",
  powerFloorMm: 0.5,
  ...extra,
});

test("the ceiling, the pin that holds it, and the floor are all said out loud", async () => {
  const ui = mount(NetWidthRow, {
    netName: "V3_3",
    widths: widths(CAPPED),
    editor: { busy: false },
    onMeasure: () => {},
  });
  try {
    const text = ui.root.textContent.replace(/\s+/gu, " ");
    assert.match(text, /0\.2mm/, "what it is routed at now");
    assert.match(text, /can take 0\.4mm/, "what the placement will allow");
    assert.match(text, /held by U3\.IOVDD6/, "which pin decides it");
    assert.match(text, /under the 0\.5mm power floor/, "that the fab wants more than is possible");
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
  }
});

test("typing over the ceiling is called out before it is written", async () => {
  const ui = mount(NetWidthRow, {
    netName: "V3_3",
    widths: widths(CAPPED),
    editor: { busy: false },
    onMeasure: () => {},
  });
  try {
    const input = ui.root.querySelector('[data-slot="net-width-input"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(input, "0.5");
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    await settle();
    assert.match(
      ui.root.querySelector('[data-slot="net-width-over"]')?.textContent || "",
      /over the 0\.4mm this net can take/,
    );
  } finally {
    ui.unmount();
  }
});

test("Set hands the number to the editor, and a refusal is shown rather than thrown", async () => {
  const calls = [];
  const editor = {
    busy: false,
    setNetWidth: async (net, mm) => {
      calls.push([net, mm]);
      return { ok: false, reason: "V3_3 is wired inside a block" };
    },
  };
  const ui = mount(NetWidthRow, { netName: "V3_3", widths: widths(CAPPED), editor, onMeasure: () => {} });
  try {
    const input = ui.root.querySelector('[data-slot="net-width-input"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(input, "0.4");
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    await settle();
    click(ui.root.querySelector('[data-slot="net-width-apply"]'));
    await settle();

    assert.deepEqual(calls, [["V3_3", 0.4]]);
    assert.match(
      ui.root.querySelector('[data-slot="net-width-error"]')?.textContent || "",
      /wired inside a block/,
    );
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
  }
});

test("a declared width offers Clear, and says the copper has not moved", async () => {
  const ui = mount(NetWidthRow, {
    netName: "V5",
    widths: widths({ ...CAPPED, net: "V5", ceiling_mm: 1.1, ceiling_at: "U1.VBUS", declared_mm: 0.5 }),
    editor: { busy: false },
    onMeasure: () => {},
  });
  try {
    assert.match(ui.root.querySelector('[data-slot="net-width-declared"]').textContent, /declared 0\.5mm/);
    assert.ok(ui.root.querySelector('[data-slot="net-width-clear"]'), "no way back to the default width");
    // A width is a declaration the router reads next build — the honesty the
    // verdict chip owes a pending turn, owed here too.
    assert.match(
      ui.root.querySelector('[data-slot="net-width-note"]').textContent,
      /the copper on screen has not moved/,
    );
  } finally {
    ui.unmount();
  }
});

test("the measurement is asked for once per net, and only for a net", async () => {
  const asked = [];
  const ui = mount(NetWidthRow, {
    netName: "V5",
    widths: widths(null, { busy: true }),
    editor: { busy: false },
    onMeasure: (net) => asked.push(net),
  });
  try {
    await settle();
    assert.deepEqual(asked, ["V5"]);
    // It costs seconds; a re-render must not re-ask.
    ui.set({ widths: widths(null, { busy: true }) });
    await settle();
    assert.deepEqual(asked, ["V5"]);
    assert.match(ui.root.textContent, /measuring what this net can take/);

    ui.set({ netName: "V3_3" });
    await settle();
    assert.deepEqual(asked, ["V5", "V3_3"], "a different net is a different question");
  } finally {
    ui.unmount();
  }
});
