import { useEffect, useRef, useState } from "react";
import { transport } from "@/lib/transport.ts";
import { isTerminal } from "./buildStatus.js";

/** How often we ask while a turn is running. Stage transitions are ~7 per 45–90s. */
export const BUILD_POLL_MS = 1500;

/**
 * The pipeline's live build stage for one project.
 *
 * Polling is deliberately narrow: only while a chat turn is in progress, and
 * it stops the moment the record reaches a terminal state (`done` / `failed` /
 * `stale`). Nothing is written for a project that has never built, so the
 * first reply is usually `null` and the hook stays silent.
 *
 * The last record is kept after polling stops so a just-finished build can
 * still say "Built · 62s · 0 blocking" for a moment — `buildStatusLine` is what
 * decides when that expires.
 *
 * @param {string} projectId
 * @param {boolean} active  true while a turn is running
 * @returns {import("@/lib/transport.ts").BuildStatus|null}
 */
export default function useBuildStatus(projectId, active) {
  const [status, setStatus] = useState(null);
  // Held in a ref so reaching a terminal state stops the loop without making
  // the effect re-subscribe on every poll.
  const settledRef = useRef(false);

  // A new project, or a new turn, is a new build to follow.
  useEffect(() => {
    settledRef.current = false;
    if (active) setStatus(null);
  }, [projectId, active]);

  useEffect(() => {
    if (!projectId || !active) return undefined;
    let cancelled = false;
    let timer = null;

    const tick = async () => {
      try {
        const next = await transport.build_status(projectId);
        if (cancelled) return;
        setStatus(next || null);
        if (next && isTerminal(next)) {
          settledRef.current = true;
          return; // no reschedule — nothing more is coming
        }
      } catch {
        // A failed poll is not worth surfacing: the build is the story, not
        // our ability to ask about it. Keep the last known record and retry.
      }
      if (!cancelled && !settledRef.current) {
        timer = setTimeout(tick, BUILD_POLL_MS);
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [projectId, active]);

  return status;
}
