import { useCallback, useEffect, useRef, useState } from "react";
import { svgTwin } from "@/lib/boardRender.js";
import { listRevisions, loadRevision, saveRevision } from "@/lib/revisionStore.js";
import { mergeRevision, REVISION_LIMIT, revisionToken, summarizeRevision } from "./boardRevisions.js";

/**
 * The revision ring for the board currently on screen.
 *
 * Recording is a side effect of looking: whenever the workspace has both the
 * sidecar and the IR for a `?v=` token we have not seen, that build is written
 * to IndexedDB. No extra fetch except the schematic SVG, which the schematic
 * pane is loading anyway — the browser serves it from cache.
 *
 * Stepping back loads that build's stored body and hands it to the caller;
 * stepping to the newest returns null, which means "use the live artifacts".
 * That asymmetry is deliberate: the latest build must never render from a
 * snapshot, or a rebuild would silently show stale copper.
 *
 * @param {{
 *   projectId?: string, file?: string,
 *   circuitJsonUrl?: string, schematicUrl?: string,
 *   circuit?: unknown, sidecar?: object|null, index?: object|null,
 * }} input
 */
export default function useBoardRevisions({
  projectId = "",
  file = "",
  circuitJsonUrl = "",
  schematicUrl = "",
  circuit = null,
  sidecar = null,
  index = null,
} = {}) {
  const [revisions, setRevisions] = useState([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [viewing, setViewing] = useState(null); // null = the live artifacts
  const [loading, setLoading] = useState(false);
  // Tokens written this session, so a re-render never re-writes.
  const writtenRef = useRef(new Set());

  // Switching boards is a different history entirely.
  useEffect(() => {
    setRevisions([]);
    setActiveIndex(0);
    setViewing(null);
    if (!projectId || !file) return undefined;
    let cancelled = false;
    listRevisions(projectId, file).then((stored) => {
      if (cancelled || !stored.length) return;
      setRevisions(stored);
      setActiveIndex(stored.length - 1);
    });
    return () => {
      cancelled = true;
    };
  }, [projectId, file]);

  // Record the build on screen.
  const token = revisionToken(circuitJsonUrl);
  useEffect(() => {
    if (!projectId || !file || !token || !circuit || !sidecar || !index) return undefined;
    let cancelled = false;
    const summary = summarizeRevision({ sidecar, index });

    // Show it in the pager immediately; persistence catches up behind.
    setRevisions((prev) => {
      const { list, added } = mergeRevision(prev, { token, capturedAt: Date.now(), summary }, REVISION_LIMIT);
      if (added) setActiveIndex(list.length - 1);
      return list;
    });

    if (writtenRef.current.has(token)) return undefined;
    writtenRef.current.add(token);

    const svgUrl = svgTwin(schematicUrl);
    const readSvg = svgUrl
      ? fetch(svgUrl)
          .then((response) => (response.ok ? response.text() : ""))
          .catch(() => "")
      : Promise.resolve("");

    readSvg.then((schematicSvg) => {
      if (cancelled) return;
      void saveRevision(
        {
          projectId,
          file,
          token,
          capturedAt: Date.now(),
          summary,
          circuitText: JSON.stringify(circuit),
          sidecarText: JSON.stringify(sidecar),
          schematicSvg,
        },
        REVISION_LIMIT,
      );
    });

    return () => {
      cancelled = true;
    };
  }, [projectId, file, token, circuit, sidecar, index, schematicUrl]);

  const select = useCallback(
    (next) => {
      setActiveIndex(next);
      const target = revisions[next];
      const isLatest = next === revisions.length - 1;
      if (!target || isLatest) {
        setViewing(null);
        return;
      }
      setLoading(true);
      loadRevision(projectId, file, target.token)
        .then((body) => {
          // A body we cannot read is worse than none — fall back to live rather
          // than render an empty board and call it history.
          setViewing(body ? { ...body, token: target.token, summary: target.summary } : null);
        })
        .finally(() => setLoading(false));
    },
    [revisions, projectId, file],
  );

  // A rebuild landing while you are looking at an old build snaps you forward:
  // the new copper is the thing you asked for.
  const countRef = useRef(0);
  useEffect(() => {
    if (revisions.length > countRef.current && viewing) setViewing(null);
    countRef.current = revisions.length;
  }, [revisions.length, viewing]);

  return { revisions, activeIndex, select, viewing, loading };
}
