/**
 * What a turn cost, read off the wire.
 *
 * `claude -p --output-format stream-json` ends every turn with one `result`
 * line carrying the whole bill. The driver parsed that line for its `result`
 * text and dropped everything else, so the server knew what a turn *said* and
 * nothing about what it spent, how long it took, or whether it failed.
 *
 * **Why four counters and not a total.** Measured on weather-badge-13,
 * 2026-08-18, one board end to end:
 *
 *     input_tokens                      420
 *     cache_creation_input_tokens 2,248,535
 *     cache_read_input_tokens    30,555,177
 *     output_tokens                 241,443
 *
 * Raw input is a rounding error and cache reads are 99% of the volume. The
 * four are priced roughly 1x / 1.25x / 0.1x / 5x relative to base input, a 50x
 * spread inside a single turn, so a `total_tokens` column cannot be multiplied
 * back into money by anyone. Logging the obvious two — input and output —
 * would have recorded 242k of 33M tokens: 0.7% of the volume.
 *
 * `total_cost_usd` is kept as well, and not as a duplicate. It is a **price
 * snapshot**: recomputing a turn from its counters at next quarter's rates
 * gives a number that was never charged to anyone.
 *
 * **Why the review children have to be metered too.** Split by phase across
 * three real boards (weather-badge-10, -11, -13), review rounds are **23-39%**
 * of the weighted spend of a board. `pre-deploy.md` guessed "plausibly the
 * majority"; the measurement says less than that and far too much to omit. A
 * meter that reads only the main turn undercounts every board by a quarter to
 * two fifths.
 */

/** The counters, in the order a reader wants them. */
export const USAGE_KEYS = [
  "input_tokens",
  "output_tokens",
  "cache_creation_input_tokens",
  "cache_read_input_tokens",
];

/** Relative price of each counter against base input, for weighting only.
 *
 * Deliberately NOT money. Rates change and differ per model; these exist so a
 * log line can say which phase dominated a board without pretending to bill
 * it. `total_cost_usd` off the wire is the only figure here that is money.
 */
export const USAGE_WEIGHTS = {
  input_tokens: 1,
  output_tokens: 5,
  cache_creation_input_tokens: 1.25,
  cache_read_input_tokens: 0.1,
};

function toCount(value) {
  return Number.isFinite(value) && value > 0 ? Math.trunc(value) : 0;
}

/** A zero accumulator. */
export function newUsage() {
  const usage = { costUsd: 0, turns: 0 };
  for (const key of USAGE_KEYS) {
    usage[key] = 0;
  }
  return usage;
}

/**
 * The billable facts on a stream-json `result` line, or `null` when the object
 * is not one.
 *
 * Never throws and never invents: a missing counter reads 0, a missing cost
 * reads `null` rather than 0, because "not reported" and "free" are different
 * claims and only one of them is safe to sum.
 */
export function readResultLine(obj) {
  if (!obj || typeof obj !== "object" || obj.type !== "result") {
    return null;
  }
  const raw = obj.usage && typeof obj.usage === "object" ? obj.usage : {};
  const usage = {};
  for (const key of USAGE_KEYS) {
    usage[key] = toCount(raw[key]);
  }
  const cost = Number(obj.total_cost_usd);
  return {
    usage,
    costUsd: Number.isFinite(cost) ? cost : null,
    durationMs: toCount(obj.duration_ms) || null,
    isError: obj.is_error === true,
    stopReason: typeof obj.stop_reason === "string" ? obj.stop_reason : null,
    apiErrorStatus:
      obj.api_error_status === undefined || obj.api_error_status === null
        ? null
        : String(obj.api_error_status),
    model: typeof obj.model === "string" ? obj.model : null,
  };
}

/** Fold one `readResultLine` result into an accumulator. Returns it. */
export function addUsage(acc, record) {
  if (!acc || !record) {
    return acc;
  }
  for (const key of USAGE_KEYS) {
    acc[key] += toCount(record.usage?.[key]);
  }
  if (Number.isFinite(record.costUsd)) {
    acc.costUsd += record.costUsd;
  }
  acc.turns += 1;
  return acc;
}

/** Input-equivalent weight of a counter set — for "which phase dominated". */
export function weigh(usage) {
  if (!usage) {
    return 0;
  }
  let total = 0;
  for (const key of USAGE_KEYS) {
    total += toCount(usage[key]) * USAGE_WEIGHTS[key];
  }
  return total;
}

function compact(n) {
  const value = toCount(n);
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${Math.round(value / 1000)}k`;
  return String(value);
}

/**
 * One line, one turn, in the field order `pre-deploy.md` asks for.
 *
 * Space-separated `key=value` rather than JSON: this goes to stdout beside
 * every other server line, and a grep-able line that a person can read at 3am
 * beats a payload they have to pipe through a tool first. Absent values are
 * omitted rather than printed as `null`, so a short line means little
 * happened, not that something broke.
 */
export function formatTurnLog(fields = {}) {
  const {
    turnId,
    projectId,
    userId,
    phase,
    model,
    effort,
    elapsedMs,
    usage,
    costUsd,
    exit,
    error,
  } = fields;
  const parts = [];
  const push = (key, value) => {
    if (value === undefined || value === null || value === "") {
      return;
    }
    parts.push(`${key}=${value}`);
  };
  push("turn", turnId);
  push("project", projectId);
  push("user", userId);
  push("phase", phase);
  push("model", model);
  push("effort", effort);
  if (Number.isFinite(elapsedMs)) {
    push("elapsed_ms", Math.round(elapsedMs));
  }
  if (usage) {
    push("in", compact(usage.input_tokens));
    push("out", compact(usage.output_tokens));
    push("cache_w", compact(usage.cache_creation_input_tokens));
    push("cache_r", compact(usage.cache_read_input_tokens));
    if (toCount(usage.turns) > 1) {
      push("claude_turns", usage.turns);
    }
  }
  if (Number.isFinite(costUsd)) {
    push("cost_usd", costUsd.toFixed(4));
  }
  push("exit", exit);
  if (error) {
    // One line means one line: a stack trace here would break every log
    // consumer that splits on newlines.
    push("error", JSON.stringify(String(error).replace(/\s+/g, " ").slice(0, 300)));
  }
  return parts.join(" ");
}
