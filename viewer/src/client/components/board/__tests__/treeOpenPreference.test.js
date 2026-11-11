// The PROJECTS sidebar starts collapsed and then remembers the viewer's choice.

import assert from "node:assert/strict";
import test from "node:test";

import { readStoredTreeOpen, TREE_OPEN_STORAGE_KEY, writeStoredTreeOpen } from "../treeOpenPreference.js";

function memoryStorage() {
  const data = new Map();
  return { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)) };
}

test("a first visit starts with the project tree collapsed", () => {
  assert.equal(readStoredTreeOpen(memoryStorage()), false);
  assert.equal(readStoredTreeOpen(undefined), false);
});

test("the viewer's open/closed choice is remembered", () => {
  const storage = memoryStorage();
  writeStoredTreeOpen(true, storage);
  assert.equal(storage.getItem(TREE_OPEN_STORAGE_KEY), "true");
  assert.equal(readStoredTreeOpen(storage), true);
  writeStoredTreeOpen(false, storage);
  assert.equal(readStoredTreeOpen(storage), false);
});

test("blocked storage neither throws nor opens the tree", () => {
  const blocked = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } };
  assert.equal(readStoredTreeOpen(blocked), false);
  assert.equal(writeStoredTreeOpen(true, blocked), true);
});
