import { useEffect, useState } from "react";

import { normalizeEpochMs } from "./buildStatus.js";

/**
 * Seconds since a build started, ticking once a second.
 *
 * The pipeline's own `elapsedS` only exists once the build is over, and the
 * slow part is everything before that: `compile` routes the board, and on a
 * microcontroller board at 5× router effort it holds that one stage for
 * fifteen minutes or more. Its quiet limit is 45 minutes for exactly that
 * reason, so nothing marks the record stale and nothing on screen changes.
 *
 * Watched on a real run: nine minutes into a build the board pane read
 * "Compiling the board · 1/7" — the same words it showed at second one, with
 * no number anywhere. The chat sidebar had a live counter the whole time. A
 * wait with a clock on it is a wait; a wait without one is a hang.
 *
 * @param {{startedAt?: number}|null} status a build-status record
 * @param {boolean} running tick only while there is something to tick for
 * @returns {number} seconds, or 0 when there is no start time
 */
export function useLiveElapsed(status, running) {
  const startedAt = normalizeEpochMs(status?.startedAt);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running || !startedAt) return undefined;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running, startedAt]);
  if (!startedAt) return 0;
  return Math.max(0, (now - startedAt) / 1000);
}
