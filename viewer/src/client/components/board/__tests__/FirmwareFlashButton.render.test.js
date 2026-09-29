// The Flash button, mounted for real with the transport stubbed at its seam:
// nothing is written on the first click, the confirm names the exact device,
// the second click flashes that address and only that address, and a refusal
// shows its reason instead of a port picker.

import assert from "node:assert/strict";
import test from "node:test";

import { click, flush, mount } from "../../../test/render.js";
import { setTransport, resetTransport } from "../../../lib/transport.ts";
import FirmwareFlashButton from "../FirmwareFlashButton.jsx";

const C3 = { address: "/dev/cu.usbmodem1201", vid: "0x303a", pid: "0x1001", serial: "E0:72:A1:6C:D3:F8", boards: ["ESP32C3 Dev Module"] };

function stub({ decision = "one", candidates = [C3], hint = "", flashOk = true } = {}) {
  const calls = [];
  setTransport({
    firmware_detect: async (id) => {
      calls.push(["detect", id]);
      return { family: "esp32", recipe: "firmware/deck (esp32:esp32:esp32c3) via arduino-cli", decision, candidates, others: [], ignored: 0, hint };
    },
    firmware_flash: async (id, port) => {
      calls.push(["flash", id, port]);
      return {
        ok: flashOk,
        port,
        target: candidates[0],
        steps: flashOk ? [{ name: "compile", code: 0, output: "" }, { name: "upload", code: 0, output: "" }] : [{ name: "compile", code: 2, output: "error: pins.h not found" }],
        serial: flashOk ? { text: "deck ready", note: "read 8s" } : null,
      };
    },
  });
  return calls;
}

async function settle() {
  for (let i = 0; i < 4; i += 1) await flush();
}

const q = (ui, slot) => ui.container.querySelector(`[data-slot="${slot}"]`);

test("disabled until the board is ready and a recipe exists — with the reason in the tooltip", () => {
  const ui = mount(FirmwareFlashButton, { projectId: "p", ready: false, hasRecipe: true });
  try {
    const button = q(ui, "firmware-flash-button");
    assert.equal(button.disabled, true);
    assert.match(button.title, /check out first/);
    ui.set({ ready: true, hasRecipe: false });
    assert.equal(button.disabled, true);
    assert.match(button.title, /flash\.json/);
    ui.set({ ready: true, hasRecipe: true });
    assert.equal(button.disabled, false);
  } finally {
    ui.unmount();
  }
});

test("first click only looks; the confirm names the device; the second click flashes that address", async () => {
  const calls = stub();
  const ui = mount(FirmwareFlashButton, { projectId: "p", ready: true, hasRecipe: true });
  try {
    click(q(ui, "firmware-flash-button"));
    await settle();
    assert.deepEqual(calls, [["detect", "p"]], "nothing written on the first click");
    const confirm = q(ui, "firmware-flash-confirm");
    assert.ok(confirm, "the confirm card renders");
    assert.match(confirm.textContent, /Found an ESP32-family device/);
    assert.match(confirm.textContent, /arduino-cli calls it "ESP32C3 Dev Module"/);
    assert.match(confirm.textContent, /\/dev\/cu\.usbmodem1201/);
    assert.match(confirm.textContent, /vid 0x303a · pid 0x1001 · E0:72:A1:6C:D3:F8/);
    assert.match(confirm.textContent, /firmware\/deck/);

    click(q(ui, "firmware-flash-go"));
    await settle();
    assert.deepEqual(calls.at(-1), ["flash", "p", "/dev/cu.usbmodem1201"]);
    const result = q(ui, "firmware-flash-result");
    assert.match(result.textContent, /Flashed \/dev\/cu\.usbmodem1201/);
    assert.match(result.textContent, /compile/);
    assert.match(result.textContent, /upload/);
    assert.match(q(ui, "firmware-flash-serial").textContent, /deck ready/);
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
    resetTransport();
  }
});

test("cancel on the confirm writes nothing", async () => {
  const calls = stub();
  const ui = mount(FirmwareFlashButton, { projectId: "p", ready: true, hasRecipe: true });
  try {
    click(q(ui, "firmware-flash-button"));
    await settle();
    assert.ok(q(ui, "firmware-flash-confirm"));
    click(q(ui, "firmware-flash-button")); // reads "Cancel" now
    await settle();
    assert.equal(q(ui, "firmware-flash-confirm"), null);
    assert.deepEqual(calls, [["detect", "p"]]);
  } finally {
    ui.unmount();
    resetTransport();
  }
});

test("a foreign or missing board is a refusal with the reason, never a port picker", async () => {
  const calls = stub({ decision: "none", candidates: [], hint: "The USB serial device on this machine (/dev/cu.usbmodem1201 vid 0x2e8a) is not this board's family (0x303a). Not flashing it." });
  const ui = mount(FirmwareFlashButton, { projectId: "p", ready: true, hasRecipe: true });
  try {
    click(q(ui, "firmware-flash-button"));
    await settle();
    assert.equal(q(ui, "firmware-flash-confirm"), null);
    assert.equal(q(ui, "firmware-flash-go"), null);
    assert.match(q(ui, "firmware-flash-refused").textContent, /not this board's family/);
    assert.deepEqual(calls, [["detect", "p"]]);
  } finally {
    ui.unmount();
    resetTransport();
  }
});

test("a failed step shows its output", async () => {
  stub({ flashOk: false });
  const ui = mount(FirmwareFlashButton, { projectId: "p", ready: true, hasRecipe: true });
  try {
    click(q(ui, "firmware-flash-button"));
    await settle();
    click(q(ui, "firmware-flash-go"));
    await settle();
    const result = q(ui, "firmware-flash-result");
    assert.match(result.textContent, /Flash failed at compile/);
    assert.match(result.textContent, /pins\.h not found/);
  } finally {
    ui.unmount();
    resetTransport();
  }
});
