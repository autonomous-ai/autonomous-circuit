import { useEffect, useState } from "react";
import { transport } from "@/lib/transport.ts";

/** How often history is re-read while a turn is running. Rounds are minutes
 *  apart, so this is deliberately slower than the build-stage poll. */
export const HISTORY_POLL_MS = 6000;

/**
 * The board's build history — what it looked like on previous review rounds.
 *
 * Polled, not subscribed. History is written to `<project>/.circuit/`, which
 * the artifact snapshotter skips on purpose, so it never fires an
 * `artifact_changed` event; waiting for one would mean waiting forever.
 *
 * Read once when the project opens (a finished board still has a history worth
 * a sentence) and then on a slow timer while a turn runs.
 *
 * @param {string} projectId
 * @param {boolean} active true while a turn is running
 * @returns {import("@/lib/transport.ts").BuildHistory|null}
 */
export default function useBuildHistory(projectId, active) {
  const [history, setHistory] = useState(null);

  useEffect(() => {
    if (!projectId) {
      setHistory(null);
      return undefined;
    }
    let cancelled = false;
    let timer = null;

    const tick = async () => {
      try {
        const next = await transport.build_revisions(projectId);
        if (!cancelled) setHistory(next && typeof next === "object" ? next : null);
      } catch {
        // A project whose server predates the command, or a read that lost a
        // race with an append. Either way the last answer stands.
      }
      if (!cancelled && active) timer = setTimeout(tick, HISTORY_POLL_MS);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [projectId, active]);

  return history;
}
