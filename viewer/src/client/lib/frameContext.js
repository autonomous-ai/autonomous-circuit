// Build the short, model-facing note that rides alongside a grabbed video
// frame ("Send to AI" on the episode player). The frame shows *what* the user
// is looking at; this note tells the model *where* it came from — episode and
// timecode — so it can map the image back to the shot in the generating
// episodes/epNNN.py. Modeled on the donor's highlightContext.js: pure,
// bracketed, unit-tested without a DOM.
//
// The note is stored in the chat store's `pendingViewContext` (repurposed from
// the donor's highlight context per docs/video-interfaces.md §2) and appended
// to the next turn's userMessage — never shown in the echoed bubble.

/**
 * "mm:ss" for under an hour, "h:mm:ss" above. Fractions floor: a frame at
 * 14.9s reads 00:14 (matches what the player's scrubber shows).
 * @param {number} seconds
 * @returns {string}
 */
export function formatTimecode(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/**
 * Compose the bracketed context note, e.g.
 *   "[Viewer context: frame at 00:14 of ep001]"
 * Optional shot id sharpens it:
 *   "[Viewer context: frame at 00:14 of ep001, during shot s1_02]"
 * Returns "" when there's no episode to name, so callers can guard cheaply.
 *
 * @param {{episode?: string, timeSeconds?: number, shotId?: string}} input
 * @returns {string}
 */
export function buildFrameContextNote({ episode, timeSeconds, shotId } = {}) {
  const stem = String(episode || "").trim();
  if (!stem) return "";
  const at = formatTimecode(timeSeconds);
  const during = String(shotId || "").trim();
  const suffix = during ? `, during shot ${during}` : "";
  return `[Viewer context: frame at ${at} of ${stem}${suffix}]`;
}
