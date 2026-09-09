import test from "node:test";
import assert from "node:assert/strict";

import {
  availableModelChoices,
  choiceIdForSettings,
  CODEX_CHOICES,
  DEFAULT_MODEL,
  labelForModel,
  MODEL_CHOICES,
} from "../modelChoices.js";

test("MODEL_CHOICES lists the selectable models led by Fable, the default", () => {
  assert.deepEqual(
    MODEL_CHOICES.map((c) => c.id),
    ["fable", "opus", "sonnet", "vibe-free", "vibe-pro"],
  );
  assert.equal(DEFAULT_MODEL, "fable");
  assert.ok(MODEL_CHOICES.some((c) => c.id === DEFAULT_MODEL));
});

test("Free and Pro are distinct rows that share one underlying model value", () => {
  const ids = MODEL_CHOICES.map((c) => c.id);
  assert.equal(new Set(ids).size, ids.length, "ids are unique");

  const free = MODEL_CHOICES.find((c) => c.id === "vibe-free");
  const pro = MODEL_CHOICES.find((c) => c.id === "vibe-pro");
  assert.equal(free.value, pro.value, "same model");
  assert.equal(free.value, "minimax,minimax/minimax-m3");
});

test("availableModelChoices hides proxy models unless signed in", () => {
  assert.deepEqual(
    availableModelChoices({ signedInToPanda: false }).map((c) => c.value),
    ["fable", "opus", "sonnet"],
  );
  assert.deepEqual(
    availableModelChoices({ signedInToPanda: true }).map((c) => c.id),
    ["fable", "opus", "sonnet", "vibe-free", "vibe-pro"],
  );
});

test("labelForModel returns the friendly label for a known selection id", () => {
  assert.equal(labelForModel("opus"), "Opus");
  assert.equal(labelForModel("sonnet"), "Sonnet");
  assert.equal(labelForModel("fable"), "Fable");
  assert.equal(labelForModel("vibe-free"), "Free");
  assert.equal(labelForModel("vibe-pro"), "Pro");
});

test("labelForModel falls back to the default label for unset or unknown ids", () => {
  assert.equal(labelForModel(undefined), "Fable");
  assert.equal(labelForModel(""), "Fable");
  assert.equal(labelForModel("gpt-4"), "Fable");
  // The raw model string is no longer a selection id.
  assert.equal(labelForModel("minimax,minimax/minimax-m3"), "Fable");
});

// ---------------------------------------------------------------------------
// Codex rows — a second provider in the same switcher
// ---------------------------------------------------------------------------

test("CODEX_CHOICES leads with the config default and offers the one verified model id", () => {
  assert.deepEqual(
    CODEX_CHOICES.map((c) => c.id),
    ["codex-default", "gpt-6-astra"],
  );
  // Empty value = omit --model entirely, so the CLI uses ~/.codex/config.toml.
  assert.equal(CODEX_CHOICES[0].value, "");
  assert.equal(CODEX_CHOICES[1].value, "gpt-6-astra");
  assert.ok(CODEX_CHOICES.every((c) => c.provider === "codex"));
  assert.ok(CODEX_CHOICES.every((c) => !c.requiresPandaSignIn));
});

test("labelForModel finds Codex ids too, so the pill never shows a Claude label for a Codex turn", () => {
  assert.equal(labelForModel("codex-default"), "Codex · Default");
  assert.equal(labelForModel("gpt-6-astra"), "Codex · GPT-6 Astra");
});

test("choiceIdForSettings maps a Codex turn by its --model string, not by id", () => {
  assert.equal(choiceIdForSettings({ provider: "codex", model: "gpt-6-astra" }), "gpt-6-astra");
  // No stored model = no --model flag = the CLI's own config default.
  assert.equal(choiceIdForSettings({ provider: "codex" }), "codex-default");
  assert.equal(choiceIdForSettings({ provider: "codex", model: "" }), "codex-default");
  // An id we no longer offer (a stale settings.json) falls back, not crashes.
  assert.equal(choiceIdForSettings({ provider: "codex", model: "gpt-5.3-codex" }), "codex-default");
});

test("choiceIdForSettings passes a Claude selection straight through", () => {
  assert.equal(choiceIdForSettings({ provider: "claude", model: "opus" }), "opus");
  assert.equal(choiceIdForSettings({ provider: "claude" }), DEFAULT_MODEL);
  assert.equal(choiceIdForSettings({}), DEFAULT_MODEL);
  assert.equal(choiceIdForSettings(), DEFAULT_MODEL);
});
