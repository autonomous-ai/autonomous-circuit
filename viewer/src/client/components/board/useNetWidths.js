// useNetWidths — what a net is routed at, and what it could be, on demand.
//
// The measurement fans 180 bearings out of every pad on the net and costs
// seconds, not milliseconds, so this is deliberately **not** something the
// workspace computes for every net when a board opens. It is asked for the net
// a person has selected, once per build, and cached until the board is rebuilt
// — a ceiling is a property of the placement, so a new build is the only thing
// that can change it.

import { useCallback, useEffect, useRef, useState } from "react";

import { getApiBase } from "@/lib/transport.ts";

const EMPTY = { byNet: new Map(), busy: false, reason: "", powerFloorMm: null, minTraceMm: null };

export default function useNetWidths({ projectId, stem, buildKey, enabled = true }) {
  const [state, setState] = useState(EMPTY);
  // Which nets have been asked for on THIS build. Cleared when the build
  // changes, because that is exactly when the answer can have moved.
  const askedRef = useRef(new Set());

  useEffect(() => {
    askedRef.current = new Set();
    setState(EMPTY);
  }, [projectId, stem, buildKey]);

  const measure = useCallback(
    async (net) => {
      const name = String(net || "").trim();
      if (!enabled || !projectId || !stem || !name) return;
      if (askedRef.current.has(name)) return;
      askedRef.current.add(name);
      setState((prev) => ({ ...prev, busy: true, reason: "" }));
      try {
        const response = await fetch(`${getApiBase()}/api/board_net_widths`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ id: projectId, file: `boards/${stem}.tsx`, nets: [name] }),
        });
        const payload = await response.json().catch(() => null);
        if (!response.ok || !payload || payload.ok === false) {
          // A net that could not be measured is asked again next time: the
          // usual cause is a board that has not finished building, which fixes
          // itself.
          askedRef.current.delete(name);
          setState((prev) => ({
            ...prev,
            busy: false,
            reason: String(payload?.reason || payload?.message || "the width could not be measured"),
          }));
          return;
        }
        setState((prev) => {
          const byNet = new Map(prev.byNet);
          for (const row of payload.nets || []) byNet.set(String(row.net), row);
          return {
            byNet,
            busy: false,
            reason: "",
            powerFloorMm: payload.powerFloorMm ?? prev.powerFloorMm,
            minTraceMm: payload.minTraceMm ?? prev.minTraceMm,
          };
        });
      } catch (error) {
        askedRef.current.delete(name);
        setState((prev) => ({
          ...prev,
          busy: false,
          reason: error instanceof Error ? error.message : String(error),
        }));
      }
    },
    [enabled, projectId, stem],
  );

  return { ...state, measure };
}
