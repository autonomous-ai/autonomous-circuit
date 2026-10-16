// The Case tab's arithmetic: what a status means, which parts show, where an
// exploded part goes, and what a print rotation asks of the slicer.

import assert from "node:assert/strict";
import test from "node:test";

import { defaultVisibility, describeStatus, downloadName, explodeOffset, orderParts, printNote, roleLabel } from "../caseView.js";

test("only a passing check says the case fits; stale and failed say not to print yet", () => {
  assert.equal(describeStatus("passed").title, "Fits the board");
  assert.equal(describeStatus("failed", "2 case errors").tone, "bad");
  assert.match(describeStatus("failed", "2 case errors").detail, /^2 case errors/);
  assert.match(describeStatus("stale", "the board changed since the case was checked").detail, /Do not print/);
  assert.equal(describeStatus(undefined).title, "No case yet");
});

test("the coupon is listed first and left out of the assembly", () => {
  const parts = [
    { name: "plunger_SW1", role: "plunger" },
    { name: "front", role: "shell" },
    { name: "coupon", role: "coupon" },
    { name: "back", role: "shell" },
  ];
  assert.deepEqual(orderParts(parts).map((p) => p.name), ["coupon", "back", "front", "plunger_SW1"]);
  assert.deepEqual(defaultVisibility(parts), { plunger_SW1: true, front: true, coupon: false, back: true });
  assert.equal(roleLabel("coupon"), "Test-fit coupon");
  assert.equal(roleLabel("whatever"), "Part");
});

test("exploding pulls what is above the board up and what is below down; the coupon goes beside", () => {
  const above = { min: [0, 0, -1.6], max: [0, 0, 12] };
  const below = { min: [0, 0, -9], max: [0, 0, -1.6] };
  assert.deepEqual(explodeOffset({ role: "shell" }, above, 32, 20), [0, 0, 20]);
  assert.deepEqual(explodeOffset({ role: "shell" }, below, 32, 20), [0, 0, -20]);
  // the coupon's left edge lands half a gap right of the case's right edge
  assert.deepEqual(explodeOffset({ role: "coupon" }, { min: [-20, 0, 0], max: [10, 0, 5] }, 32, 20), [62, 0, 0]);
});

test("a print rotation is said in words", () => {
  assert.equal(printNote({ printRotateDeg: [180, 0, 0], supports: false }), "flip it face down on the bed, no supports");
  assert.equal(printNote({ printRotateDeg: [0, 0, 0] }), "print it as modelled");
  assert.equal(printNote({}), "print it as modelled");
  assert.match(printNote({ printRotateDeg: [90, 0, 0] }), /rotate it 90° \/ 0° \/ 0°/);
});

test("download names are safe and say which board", () => {
  assert.equal(downloadName("main", "plunger_SW1", "stl"), "main-plunger_SW1.stl");
  assert.equal(downloadName("my board", "../front", "step"), "my-board-front.step");
});
