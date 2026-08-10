import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_RENDER_PROVIDER,
  normalizeRenderProvider,
  RENDER_PROVIDER_CHOICES,
} from "../renderProviderChoices.js";

test("RENDER_PROVIDER_CHOICES mirrors the server allowlist, led by mock", () => {
  assert.deepEqual(
    RENDER_PROVIDER_CHOICES.map((c) => c.id),
    ["mock", "fal", "minimax", "dashscope"],
  );
  assert.equal(DEFAULT_RENDER_PROVIDER, "mock");
  assert.ok(RENDER_PROVIDER_CHOICES.some((c) => c.id === DEFAULT_RENDER_PROVIDER));
});

test("every choice carries a label and a description for the two-line rows", () => {
  for (const choice of RENDER_PROVIDER_CHOICES) {
    assert.ok(choice.label.length > 0, `${choice.id} has a label`);
    assert.ok(choice.description.length > 0, `${choice.id} has a description`);
  }
  const ids = RENDER_PROVIDER_CHOICES.map((c) => c.id);
  assert.equal(new Set(ids).size, ids.length, "ids are unique");
});

test("normalizeRenderProvider passes known ids through untouched", () => {
  assert.equal(normalizeRenderProvider("mock"), "mock");
  assert.equal(normalizeRenderProvider("fal"), "fal");
  assert.equal(normalizeRenderProvider("minimax"), "minimax");
  assert.equal(normalizeRenderProvider("dashscope"), "dashscope");
});

test("normalizeRenderProvider falls back to mock for absent or unknown values", () => {
  assert.equal(normalizeRenderProvider(undefined), "mock");
  assert.equal(normalizeRenderProvider(null), "mock");
  assert.equal(normalizeRenderProvider(""), "mock");
  assert.equal(normalizeRenderProvider("comfyui"), "mock");
  // Provider labels are not selection ids.
  assert.equal(normalizeRenderProvider("Animatic"), "mock");
});
