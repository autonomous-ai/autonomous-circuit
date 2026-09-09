// Model catalog for the composer's switcher.
//
// `id` is the selection key: it's what gets persisted in AppSettings.model
// (via app_set_model) and mirrored in desktop/src-tauri/src/commands/app.rs.
// `value` is the underlying model the row runs — informational here, since the
// Rust driver resolves the persisted `id` to the real `--model` string (local
// ids map to themselves; both proxy tiers map to PROXY_MODEL_MINIMAX_M3).
//
// Free and Pro deliberately share one `value` (same model; quota is enforced by
// the backend per subscription). They stay distinct rows only because their
// `id`s differ — that's what lets the switcher remember which tier you picked.
// The switcher only renders the "Pro" row for accounts already on an active Pro
// plan; Free accounts see an "Upgrade to Pro" CTA in its place (see
// ModelControl.jsx).
export const MODEL_CHOICES = [
  { id: "fable", value: "fable", label: "Fable", requiresPandaSignIn: false },
  { id: "opus", value: "opus", label: "Opus", requiresPandaSignIn: false },
  { id: "sonnet", value: "sonnet", label: "Sonnet", requiresPandaSignIn: false },
  { id: "vibe-free", value: "minimax,minimax/minimax-m3", label: "Free", requiresPandaSignIn: true },
  { id: "vibe-pro", value: "minimax,minimax/minimax-m3", label: "Pro", requiresPandaSignIn: true },
];

// Codex rows are keyed by the model string handed to `codex exec --model`.
// The empty value is deliberate: it omits the flag entirely so the CLI uses
// whatever `~/.codex/config.toml` selects, which is the only row guaranteed to
// work on an account whose entitlements we do not know. Named rows are the
// ones we have actually run a turn against — verified 2026-09-07 that
// `gpt-6-astra` answers on a ChatGPT team login and that `gpt-5.3-codex` and
// `gpt-5.6-astra` are not real ids (HTTP 400 on every account tried).
export const CODEX_CHOICES = [
  { id: "codex-default", value: "", label: "Codex · Default", provider: "codex", requiresPandaSignIn: false },
  { id: "gpt-6-astra", value: "gpt-6-astra", label: "Codex · GPT-6 Astra", provider: "codex", requiresPandaSignIn: false },
];

// Default selection when AppSettings.model is unset; matches the driver's
// `None → "fable"`. This is an `id`, like everything persisted.
export const DEFAULT_MODEL = "fable";

export function availableModelChoices({ signedInToPanda = false } = {}) {
  return MODEL_CHOICES.filter(
    (choice) => !choice.requiresPandaSignIn || signedInToPanda,
  );
}

// Friendly label for a stored selection id. Falls back to the default's label
// for an unset or unrecognized id so a legacy/garbage setting still renders.
// Both catalogs are searched: a Codex id that only matched MODEL_CHOICES would
// render the pill as "Fable" while the dropdown checkmark sat on Codex.
export function labelForModel(modelId) {
  const found = [...CODEX_CHOICES, ...MODEL_CHOICES].find((choice) => choice.id === modelId);
  if (found) return found.label;
  const fallback = MODEL_CHOICES.find((choice) => choice.id === DEFAULT_MODEL);
  return (fallback ?? MODEL_CHOICES[0]).label;
}

// Persisted AppSettings -> the switcher's selection id. The two providers key
// their rows differently: a Claude row IS its id, while a Codex row is found by
// the `--model` string it runs (absent/empty = the CLI's own config default).
export function choiceIdForSettings({ provider, model } = {}) {
  if (provider === "codex") {
    const found = CODEX_CHOICES.find((choice) => choice.value === (model ?? ""));
    return (found ?? CODEX_CHOICES[0]).id;
  }
  return model ?? DEFAULT_MODEL;
}
