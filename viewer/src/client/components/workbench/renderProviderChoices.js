// Render-provider catalog for the Settings screen's picker.
//
// `id` is the selection key: it's what gets persisted as
// AppSettings.renderProvider (via app_settings_write) and what the server
// driver exports to the render chain as VIDEO_PROVIDER. The id set mirrors
// the server's allowlist in src/server/video/settings.mjs — absent/unknown
// means mock, the zero-network default the whole loop must work on.
//
// API keys for the hosted providers live in ~/.autonomous-video/keys.env;
// docs/providers.md explains which key each provider needs.
export const RENDER_PROVIDER_CHOICES = [
  {
    id: "mock",
    label: "Animatic",
    description: "Instant, free, placeholder look with spoken scratch dialogue",
  },
  {
    id: "fal",
    label: "fal",
    description: "Hosted Wan 2.2 — real footage, needs FAL_KEY",
  },
  {
    id: "minimax",
    label: "MiniMax",
    description: "Hailuo — real footage with native dialogue audio, needs MINIMAX_API_KEY",
  },
  {
    id: "dashscope",
    label: "DashScope",
    description: "Alibaba Wan line, needs DASHSCOPE_API_KEY",
  },
];

// Default selection when AppSettings.renderProvider is unset; matches the
// server's default (no VIDEO_PROVIDER exported → dramapy renders mock).
export const DEFAULT_RENDER_PROVIDER = "mock";

// Coerce a stored value to a known provider id. Absent, empty, or
// unrecognized (legacy/garbage settings) → the mock default, so the picker
// always shows a real row.
export function normalizeRenderProvider(value) {
  return RENDER_PROVIDER_CHOICES.some((choice) => choice.id === value)
    ? value
    : DEFAULT_RENDER_PROVIDER;
}
