import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_EFFORT,
  EFFORT_HINTS,
  EFFORT_LABELS,
  EFFORT_LEVELS,
  EFFORT_STORAGE_KEY,
  effortDirective,
  normalizeEffort,
  readStoredEffort,
  writeStoredEffort,
} from "../effortChoices.js";

test("the levels are Vibe's five, low to max, and the default is high", () => {
  assert.deepEqual(EFFORT_LEVELS, ["low", "medium", "high", "xhigh", "max"]);
  assert.equal(DEFAULT_EFFORT, "high");
  for (const level of EFFORT_LEVELS) {
    assert.ok(EFFORT_LABELS[level], `${level} needs a label`);
    assert.ok(EFFORT_HINTS[level], `${level} needs a hint`);
  }
});

test("normalizeEffort accepts our levels and falls back rather than passing junk through", () => {
  assert.equal(normalizeEffort("max"), "max");
  assert.equal(normalizeEffort("  XHigh "), "xhigh");
  assert.equal(normalizeEffort("ludicrous"), DEFAULT_EFFORT);
  assert.equal(normalizeEffort(""), DEFAULT_EFFORT);
  assert.equal(normalizeEffort(null), DEFAULT_EFFORT);
});

test("medium says nothing — it is the CLI's own default, and a directive would be a lie", () => {
  assert.equal(effortDirective("medium"), "");
});

test("every other level carries a directive that names the board work, not just the thinking", () => {
  for (const level of EFFORT_LEVELS.filter((l) => l !== "medium")) {
    const directive = effortDirective(level);
    assert.ok(directive.startsWith(`[Effort: ${level} —`), `${level} names its level`);
    assert.ok(directive.endsWith("]"), `${level} is a bracketed aside`);
    assert.ok(directive.length > 60, `${level} says what the budget buys`);
  }
});

test("the directives escalate through Claude Code's own thinking triggers", () => {
  assert.match(effortDirective("high"), /think hard/);
  assert.match(effortDirective("xhigh"), /think harder/);
  assert.match(effortDirective("max"), /ultrathink/);
});

test("an unknown level directs at the default rather than silently at nothing", () => {
  assert.equal(effortDirective("bananas"), effortDirective(DEFAULT_EFFORT));
});

function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
    map,
  };
}

test("the pick round-trips through storage under one key", () => {
  const storage = fakeStorage();
  assert.equal(writeStoredEffort("max", storage), "max");
  assert.equal(storage.map.get(EFFORT_STORAGE_KEY), "max");
  assert.equal(readStoredEffort(storage), "max");
});

test("a corrupt or absent stored value reads as the default", () => {
  assert.equal(readStoredEffort(fakeStorage()), DEFAULT_EFFORT);
  assert.equal(readStoredEffort(fakeStorage({ [EFFORT_STORAGE_KEY]: "turbo" })), DEFAULT_EFFORT);
  assert.equal(readStoredEffort(undefined), DEFAULT_EFFORT);
});

test("blocked storage never throws — private mode must not eat the click", () => {
  const blocked = {
    getItem() {
      throw new Error("SecurityError");
    },
    setItem() {
      throw new Error("QuotaExceededError");
    },
  };
  assert.equal(readStoredEffort(blocked), DEFAULT_EFFORT);
  assert.equal(writeStoredEffort("low", blocked), "low");
});
