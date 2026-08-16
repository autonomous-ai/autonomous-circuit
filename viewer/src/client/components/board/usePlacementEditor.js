// usePlacementEditor — the board file, live, behind the PCB canvas.
//
// Holds `boards/<stem>.tsx` as text, re-parses it after every change, binds
// its placements to the compiled geometry, and turns a drag into one write.
// The file is the model: nothing here keeps a private copy of where a part is,
// so the canvas and the source cannot drift apart. Every write returns the
// file as the server now has it, and that text is what gets re-parsed — never
// our own prediction of what the write did.
//
// The transport call is a bare `fetch` rather than a `transport.ts` method on
// purpose: this is the only command in the app that writes a user's file, and
// keeping it beside the code that computes the edit means the two are read
// together. It matches `invoke`'s wire contract exactly — POST /api/<cmd>,
// `IpcError {code, message}` on 4xx/5xx.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getApiBase } from "@/lib/transport.ts";
import {
  applyEdits,
  bindPlacements,
  geometrySnapshot,
  lockEdits,
  moveEdits,
  parseBoardSource,
  rebindPlacements,
} from "./boardSource.js";

async function callApi(command, args) {
  const response = await fetch(`${getApiBase()}/api/${command}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(args ?? {}),
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    const error = new Error(payload?.message || text || response.statusText);
    error.code = payload?.code || `HTTP_${response.status}`;
    throw error;
  }
  return payload;
}

const EMPTY_BINDING = { byId: new Map(), byComponentKey: new Map(), byElementId: new Map(), unmatched: [] };

/**
 * @param {{
 *   projectId: string, stem: string, index: object|null,
 *   buildKey: string, enabled: boolean, revision: number,
 * }} options
 *   `buildKey` identifies the BUILD the index came from and must change only
 *   when the board is actually rebuilt — the circuit.json URL, which carries
 *   `?v=<mtime>-<size>`, is exactly that.
 */
export default function usePlacementEditor({ projectId, stem, index, buildKey, enabled, revision }) {
  const [source, setSource] = useState("");
  const [state, setState] = useState("idle"); // idle | loading | ready | failed
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState([]);
  const [changes, setChanges] = useState(0);
  const [lastChange, setLastChange] = useState("");
  const sourceRef = useRef("");
  sourceRef.current = source;

  const file = stem ? `boards/${stem}.tsx` : "";
  const url = projectId && stem ? `/projects/${projectId}/${file}?v=${revision}` : "";

  // Load, and reload whenever the catalog says the project's files moved. A
  // rebuild rewrites nothing in this file, but the agent editing it certainly
  // does, and the offsets we hold have to be the ones on disk.
  useEffect(() => {
    if (!enabled || !url) {
      setState("idle");
      setSource("");
      setError("");
      return undefined;
    }
    let cancelled = false;
    setState("loading");
    fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(`could not read ${file} (${response.status})`);
        return response.text();
      })
      .then((text) => {
        if (cancelled) return;
        setSource(text);
        setState("ready");
        setError("");
      })
      .catch((err) => {
        if (cancelled) return;
        setSource("");
        setState("failed");
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, url, file]);

  // History is about one board's file; switching boards throws it away rather
  // than offering an undo that would land on a different file.
  useEffect(() => {
    setHistory([]);
    setChanges(0);
    setLastChange("");
  }, [file]);

  const parsed = useMemo(
    () => (state === "ready" ? parseBoardSource(source) : { ok: false, reason: "", placements: [], skipped: [] }),
    [source, state],
  );

  // Geometry is a property of the last BUILD, so it is captured when the built
  // board changes and carried forward by placement id through every edit after
  // it. Re-matching by coordinate after each drag would unbind the part that
  // was just moved — the file says one thing, the built board still says the
  // other, and that disagreement is the honest state until a rebuild.
  const [snapshot, setSnapshot] = useState(() => ({ byId: new Map(), reasonById: new Map() }));
  // Which built board the snapshot describes.
  //
  // `buildKey`, not the index object: writing the source bumps the catalog
  // revision, which makes the workspace refetch circuit.json and hand back a
  // fresh `index` object with identical contents. Keying on identity therefore
  // re-derived geometry by coordinate right after a drag and unbound the part
  // that had just been moved — one move per part, and no undo. Caught by
  // dragging a real board, twice; invisible to every unit test.
  const snapshotForRef = useRef("");
  useEffect(() => {
    if (!index || !buildKey) {
      snapshotForRef.current = "";
      setSnapshot({ byId: new Map(), reasonById: new Map() });
      return;
    }
    if (!parsed.ok || snapshotForRef.current === buildKey) return;
    snapshotForRef.current = buildKey;
    setSnapshot(geometrySnapshot(bindPlacements(parsed.placements, index)));
  }, [index, buildKey, parsed]);

  const binding = useMemo(
    () => (parsed.ok ? rebindPlacements(parsed.placements, snapshot) : EMPTY_BINDING),
    [parsed, snapshot],
  );

  /**
   * Send one set of edits and adopt whatever the file says afterwards.
   *
   * `delta` is what this edit does to the count of changes waiting on a
   * rebuild. A lock is 0: it changes the file but not the geometry, so
   * offering to spend minutes rebuilding for it would be a lie about what
   * changed.
   */
  const write = useCallback(
    async (edits, note, delta = 0) => {
      if (!edits.length) return true;
      setBusy(true);
      setError("");
      try {
        const result = await callApi("board_source_write", {
          id: projectId,
          file,
          edits,
          sourceLength: sourceRef.current.length,
        });
        // Belt and braces: the server splices the same edits we did, so if the
        // two disagree the file is not what this hook thinks it is.
        const predicted = applyEdits(sourceRef.current, edits);
        if (result?.text !== predicted) {
          setError("the board file on disk is not what this view predicted — reloading");
          setSource(String(result?.text ?? ""));
          return false;
        }
        setSource(result.text);
        setChanges((n) => Math.max(0, n + delta));
        if (note) setLastChange(note);
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [projectId, file],
  );

  /** Move a placement to an absolute board position, recording the undo. */
  const move = useCallback(
    async (placementId, x, y, note) => {
      const placement = binding.byId.get(placementId);
      if (!placement) return false;
      const edits = moveEdits(source, placement, x, y);
      if (!edits.length) return true;
      const undoEntry = { kind: "move", placementId, x: placement.x, y: placement.y, label: placement.label };
      const ok = await write(edits, note, +1);
      if (ok) setHistory((list) => [...list, undoEntry].slice(-50));
      return ok;
    },
    [binding, source, write],
  );

  /**
   * Lock or unlock a placement. The lock is a comment written above the
   * element, so it lands in the file and in the diff — the next agent reads
   * the board source, not this app's state.
   */
  const setLock = useCallback(
    async (placementId, locked) => {
      const placement = binding.byId.get(placementId);
      if (!placement) return false;
      const edits = lockEdits(source, placement, locked);
      if (!edits.length) return true;
      const undoEntry = { kind: "lock", placementId, locked: placement.locked, label: placement.label };
      const ok = await write(edits, `${placement.label} ${locked ? "locked in place" : "unlocked"}`, 0);
      if (ok) setHistory((list) => [...list, undoEntry].slice(-50));
      return ok;
    },
    [binding, source, write],
  );

  const undo = useCallback(async () => {
    const entry = history[history.length - 1];
    if (!entry) return false;
    const placement = binding.byId.get(entry.placementId);
    if (!placement) {
      setError("that part is not in the board file any more — nothing to undo onto");
      return false;
    }
    const edits =
      entry.kind === "lock"
        ? lockEdits(source, placement, entry.locked)
        : moveEdits(source, placement, entry.x, entry.y);
    const ok = await write(edits, `undid: ${entry.label}`, entry.kind === "move" ? -1 : 0);
    if (ok) setHistory((list) => list.slice(0, -1));
    return ok;
  }, [history, binding, source, write]);

  /** Called once a rebuild has been asked for — the file is no longer ahead
   *  of the board on screen from the user's point of view. */
  const markBuilding = useCallback(() => {
    setChanges(0);
    setLastChange("");
  }, []);

  return {
    file,
    state,
    error,
    busy,
    ready: state === "ready" && parsed.ok,
    // Why the file is not editable, in words a person can act on.
    reason: state === "failed" ? error : parsed.ok ? "" : parsed.reason,
    placements: binding,
    unmatched: binding.unmatched,
    skipped: parsed.skipped,
    changes,
    lastChange,
    canUndo: history.length > 0,
    move,
    setLock,
    undo,
    markBuilding,
    clearError: () => setError(""),
  };
}
