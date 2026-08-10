// Pure helpers used by ChatInput.jsx. Lives in its own module so node:test
// can exercise them without going through a JSX transform.

// Lazily-created projects are never named by the user. They carry this
// placeholder until Claude Code's AI title lands in the session JSONL, at
// which point the Rust `project_list` command upgrades the stored name in
// place (see commands/project.rs `resolve_ai_title`). Must match the Rust
// `PLACEHOLDER_PROJECT_NAME` constant.
export const PLACEHOLDER_PROJECT_NAME = "New project";

// Window event the top-bar project menu fires after creating a fresh project.
// The chat sidebar owns the composer ref and listens for it to focus the
// textarea — the textarea's mount-time autoFocus doesn't re-fire when the
// active project switches in place (e.g. new project from an already-empty one).
export const FOCUS_CHAT_INPUT_EVENT = "panda:focus-chat-input";

// Window event that pre-fills the composer with a starter line and focuses it.
// Fired by the storyboard strip's per-shot "note" affordance ("Shot s1_02 (at
// 00:14): ") so the creator types taste notes and sends them as a normal turn
// — chat-driven shot regeneration with no new commands. The composer listens
// (see ChatInput) because it owns the textarea state.
export const PREFILL_CHAT_INPUT_EVENT = "video:prefill-chat-input";

/** Fire the composer pre-fill event. No-op outside a window (tests). */
export function prefillChatInput(text) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(PREFILL_CHAT_INPUT_EVENT, { detail: { text: String(text || "") } }),
  );
}
