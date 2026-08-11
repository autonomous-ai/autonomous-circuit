import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  CANT_BUILD_YET,
  boundariesHit,
  boundaryNote,
  screenOptions,
} from "./catalogBoundary.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BLOCKS_DIR = path.resolve(HERE, "../../../../packages/golden-blocks/blocks");

function releasedBlockIds() {
  return new Set(
    fs
      .readdirSync(BLOCKS_DIR, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name),
  );
}

test("every row names the boundary, the reason, and a way forward", () => {
  for (const row of CANT_BUILD_YET) {
    assert.ok(row.id, "row has an id");
    assert.ok(row.ask, `${row.id} names the ask`);
    assert.ok(row.why.length > 20, `${row.id} explains why not`);
    assert.ok(row.instead.length > 10, `${row.id} offers the nearest thing we can do`);
    assert.ok(row.match instanceof RegExp, `${row.id} knows what it looks like in prose`);
  }
});

// The row is a claim about the repo. The day someone lands the block, this
// fails and the row has to go — which is the only thing that stops the app
// refusing something it can build.
test("no row claims a block is missing that is sitting in the catalog", () => {
  const released = releasedBlockIds();
  assert.ok(released.size >= 8, "found the golden-block catalog on disk");
  for (const row of CANT_BUILD_YET) {
    for (const id of row.blocks || []) {
      assert.ok(
        !released.has(id),
        `"${row.ask}" says we cannot build it, but ${id} is in ${BLOCKS_DIR}`,
      );
    }
  }
});

// The two runs this was written from, option by option.
test("the ceiling-dimmer card's traps are caught", () => {
  const caught = (label) => boundariesHit({ label }).map((r) => r.id);
  assert.deepEqual(caught("Mains fixture (120/230V)"), ["mains"]);
  assert.deepEqual(caught("Wi-Fi app / Home Assistant"), ["radio"]);
  assert.deepEqual(caught("Rotary knob"), ["knob"]);
  // …and the options that are real stay real.
  assert.deepEqual(caught("24V LED strip / panel"), []);
  assert.deepEqual(caught("Buttons only"), []);
  assert.deepEqual(caught("Up to 5A (≈120W at 24V)"), []);
  assert.deepEqual(caught("Fully assembled (PCBA)"), []);
  assert.deepEqual(caught("Bare PCB"), []);
});

test("the nightlight card's traps are caught", () => {
  const caught = (label) => boundariesHit({ label }).map((r) => r.id);
  assert.deepEqual(caught("USB-C + rechargeable battery"), ["battery"]);
  assert.deepEqual(caught("AA/AAA batteries"), ["battery"]);
  assert.deepEqual(caught("Add motion sensing"), ["sensing"]);
  assert.deepEqual(caught("Wi-Fi / app control"), ["radio"]);
  assert.deepEqual(caught("USB-C wall adapter"), []);
  assert.deepEqual(caught("Soft warm white glow"), []);
  assert.deepEqual(caught("Color changing (RGB)"), []);
  assert.deepEqual(caught("Dumb and reliable"), []);
  assert.deepEqual(caught("Tiny plug-in (~40x40mm)"), []);
  assert.deepEqual(caught("Bare PCB, I'll solder it"), []);
});

// A false positive is the same dead end from the other side: a good answer
// that cannot be clicked. These are the near-misses worth pinning.
test("words that only look like the boundary are left alone", () => {
  const clean = [
    "A nightlight that glows",
    "Light up the whole desk",
    "12V LED strip",
    "USB-C wall adapter",
    "Nine keys, no lights",
    "Lightweight, under 30 grams",
    "Temperature and humidity",
    "One indicator LED",
    "Up to 10A (≈240W at 24V)",
  ];
  for (const label of clean) {
    assert.deepEqual(boundariesHit({ label }), [], `"${label}" is buildable`);
  }
});

test("the small print is read too", () => {
  const hit = boundariesHit({
    label: "Smart mode",
    description: "Recommended — control it from your phone app",
  });
  assert.deepEqual(hit.map((r) => r.id), ["radio"]);
});

test("the delegate option is never screened out", () => {
  const { options } = screenOptions([
    { label: "Let Circuit choose", description: "Recommended — we pick the best for you" },
    { label: "Wi-Fi / app control" },
  ]);
  assert.equal(options[0].blockedBy, null);
  assert.equal(options[1].blockedBy.id, "radio");
});

test("screening keeps the model's order and writes one note per blocked option", () => {
  const { options, notes } = screenOptions([
    { label: "Let Circuit choose" },
    { label: "Dumb and reliable" },
    { label: "Add motion sensing" },
    { label: "Wi-Fi / app control" },
  ]);
  assert.deepEqual(
    options.map((o) => o.label),
    ["Let Circuit choose", "Dumb and reliable", "Add motion sensing", "Wi-Fi / app control"],
  );
  assert.equal(notes.length, 2);
  for (const note of notes) {
    assert.match(note.text, /not yet\./);
    assert.match(note.text, /Nearest thing we can build:/);
  }
});

test("a note names the option, the reason and the way out", () => {
  const row = CANT_BUILD_YET.find((r) => r.id === "radio");
  const note = boundaryNote({ label: "Wi-Fi / app control" }, row);
  assert.ok(note.startsWith("Wi-Fi / app control — not yet."));
  assert.ok(note.includes(row.why));
  assert.ok(note.includes(row.instead));
});

test("screening an empty or malformed option list is a no-op, not a throw", () => {
  assert.deepEqual(screenOptions(undefined), { options: [], notes: [] });
  assert.deepEqual(screenOptions([]), { options: [], notes: [] });
  const { options } = screenOptions([{}]);
  assert.equal(options[0].blockedBy, null);
});
