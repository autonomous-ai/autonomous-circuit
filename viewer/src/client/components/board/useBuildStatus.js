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
 * **The router retry is inferred here, not reported.** Stage 0b re-runs the
 * compile at 5× effort and announces itself by setting the stage back to
 * `compile`. That replay is the only signal we get, and it matters: the retry
 * can run for twenty minutes, so a checklist that quietly walks backwards and
 * then stops moving reads as a hang. Watching the high-water mark of
 * `stageIndex` across polls is what turns the replay into a fact the pure
 * presentation layer can render — see buildStatus.ROUTER_RETRY_STAGE.
 *
 * @param {string} projectId
 * @param {boolean} active  true while a turn is running
 * @returns {(import("@/lib/transport.ts").BuildStatus & {routerRetry?: boolean})|null}
 */
export default function useBuildStatus(projectId, active) {
  const [status, setStatus] = useState(null);
  // Held in a ref so reaching a terminal state stops the loop without making
  // the effect re-subscribe on every poll.
  const settledRef = useRef(false);
  const highWaterRef = useRef(-1);
  const retryRef = useRef(false);
  const runIdRef = useRef("");

  // A new project, or a new turn, is a new build to follow.
  useEffect(() => {
    settledRef.current = false;
    highWaterRef.current = -1;
    retryRef.current = false;
    runIdRef.current = "";
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
        if (next) {
          // A whole new build — the agent running the generator a second time
          // in the same turn — also rewinds the stage index, and calling that a
          // router retry would put a 5x-effort row on an ordinary rebuild. The
          // pipeline stamps each invocation with its own `runId`; stage 0b's
          // retry happens inside one, so only a rewind under an unchanged runId
          // is the retry.
          const runId = String(next.runId || "");
          if (runId !== runIdRef.current) {
            runIdRef.current = runId;
            highWaterRef.current = -1;
            retryRef.current = false;
          }
          const index = Number(next.stageIndex);
          if (Number.isFinite(index)) {
            // Backwards within one run is the retry, and it stays set: the
            // stages after it are still the retry's stages.
            if (index < highWaterRef.current) retryRef.current = true;
            else highWaterRef.current = Math.max(highWaterRef.current, index);
          }
          setStatus(retryRef.current ? { ...next, routerRetry: true } : next);
        } else {
          setStatus(null);
        }
        // `stale` is terminal only when nothing is running. While a turn is in
        // flight it means "no news", and a long compile produces it routinely —
        // stopping the poll there freezes the elapsed figure and we never see
        // the build come back, which it does.
        if (next && isTerminal(next) && !(active && next.state === "stale")) {
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
