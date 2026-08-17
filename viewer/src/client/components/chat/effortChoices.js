// Effort catalog for the composer's second pill.
//
// The levels are Vibe's, verbatim from
// `panda-website/src/constants/generationModels.ts`, whose own comment reads:
// "Mirrors the Claude Code CLI's --effort choices and the backend's
// AllowedEffortLevels — keep all three in sync." Ours is the third copy; do not
// invent a sixth level here.
//
// Where the pick goes. Two places, and it needs both.
//
// `app_set_effort` persists the level and the driver spends it as the CLI's
// `--effort` flag — the real reasoning budget. That is the lever; everything
// else here is about what the budget buys.
//
// The directive below still rides the turn, appended the same way the
// view-context note is and never shown in the echoed bubble. An earlier note
// here said to delete it once the flag landed, and that was wrong: `--effort`
// buys thinking, while "check every block's pin assignment against its declared
// pinout and state the power budget arithmetic" is what we want the thinking
// spent on. A budget with no direction gets spent on whatever the model already
// wanted to do.
//
// (The flag landed on the server and the client did not call it for a day —
// the pill said Max while every turn ran at the CLI's default. The guard
// against a repeat is `commandsAreWired.test.mjs`.)
//
// Why this matters more for a board than for a mesh: Vibe's desktop driver
// hard-pins `--effort low` for every phase
// (desktop/src-tauri/src/commands/claude_driver.rs:486). Defensible for print
// geometry, wrong for us — our review loop is three phases of electrical
// reasoning, and cheap is how a wrong pinout ships.

export const EFFORT_LEVELS = Object.freeze(["low", "medium", "high", "xhigh", "max"]);

export const EFFORT_LABELS = Object.freeze({
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra High",
  max: "Max",
});

/** Vibe's default too — a board is not the place to start cheap. */
export const DEFAULT_EFFORT = "high";

export const EFFORT_STORAGE_KEY = "circuit:effort";

/** A stored or user-supplied level, or the default when it is not one of ours. */
export function normalizeEffort(value) {
  const level = String(value || "").trim().toLowerCase();
  return EFFORT_LEVELS.includes(level) ? level : DEFAULT_EFFORT;
}

/** One-line description for the dropdown row — says what the level buys. */
export const EFFORT_HINTS = Object.freeze({
  low: "Fastest. Take the obvious route.",
  medium: "The CLI's own default.",
  high: "Think before writing the board.",
  xhigh: "Re-derive the numbers and re-check the pinouts.",
  max: "Everything, and argue with itself first.",
});

// The directives escalate through Claude Code's own thinking triggers ("think
// hard" < "think harder" < "ultrathink"), each paired with the *board* work the
// extra budget should buy. A directive that only says "think hard" spends the
// budget on whatever the model already wanted to do.
const DIRECTIVES = Object.freeze({
  low: "[Effort: low — take the direct route. Skip exploratory checks; do not re-derive what the blocks already fix.]",
  // medium is the CLI's default: saying nothing is the accurate instruction.
  medium: "",
  high:
    "[Effort: high — think hard before writing the board. Check every block's pin assignment against its declared pinout and state the power budget arithmetic.]",
  xhigh:
    "[Effort: xhigh — think harder. Re-derive every component value from first principles, verify each net's fanout and current, and name any assumption you could not confirm.]",
  max:
    "[Effort: max — ultrathink. Before writing anything, argue the design against itself: what would a reviewer reject, what fails at temperature, what fails at 5% tolerance. Then build the version that survives that.]",
});

/**
 * The model-facing line for a level, or "" when the level says nothing. The
 * caller appends it to the sent message and keeps the echoed bubble clean.
 */
export function effortDirective(level) {
  return DIRECTIVES[normalizeEffort(level)] || "";
}

/** Read the persisted pick. Storage-less environments get the default. */
export function readStoredEffort(storage = globalThis.localStorage) {
  try {
    return normalizeEffort(storage?.getItem?.(EFFORT_STORAGE_KEY));
  } catch {
    return DEFAULT_EFFORT;
  }
}

/** Persist the pick. Never throws — a blocked storage must not eat the click. */
export function writeStoredEffort(level, storage = globalThis.localStorage) {
  const normalized = normalizeEffort(level);
  try {
    storage?.setItem?.(EFFORT_STORAGE_KEY, normalized);
  } catch {
    /* private mode, disabled storage — the in-memory pick still applies */
  }
  return normalized;
}
