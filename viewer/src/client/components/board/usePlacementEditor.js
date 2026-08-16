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
  describeRotate,
  geometrySnapshot,
  invertEdits,
  lockEdits,
  moveEdits,
  parseBoardSource,
  pendingMoves,
  rebindPlacements,
  remapSnapshot,
  rotateEdits,
  wrapPreview,
} from "./boardSource.js";
import { rotateRefusal } from "./placementRotate.js";

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
  // Undone edits, newest last, each one ready to be re-applied. Without it
  // ⌘Z is a one-way door: the key is bound, the button is on the strip, and
  // the only way back is to redo the drag by hand.
  const [future, setFuture] = useState([]);
  const [changes, setChanges] = useState(0);
  const [lastChange, setLastChange] = useState("");
  // The gate's answer about the board as it now stands, and whether one is in
  // flight. Null means nobody has asked yet — which is not the same as clean,
  // and the strip says so.
  const [verdict, setVerdict] = useState(null);
  const [checking, setChecking] = useState(false);
  // Latest-wins. A drag can finish while the previous check is still running,
  // and a stale answer arriving second would paint the board legal after the
  // move that broke it.
  const checkRunRef = useRef(0);
  // Turns are counted, not measured: the gate translates elements, it cannot
  // rotate them, so a pending rotation is geometry no verdict here has seen.
  // Better to name the gap than to let a green chip imply it was covered.
  const [turnsPending, setTurnsPending] = useState(0);
  const turnsRef = useRef(0);
  turnsRef.current = turnsPending;
  const bindingRef = useRef(EMPTY_BINDING);
  const wantCheckRef = useRef(false);
  const sourceRef = useRef("");
  sourceRef.current = source;
  // The file as the board on screen was built from. `changes` is a count of
  // edits, but the question the rebuild button is really asking is "does the
  // file differ from the board?" — and four taps of Space, or an undo of
  // everything, brings it back to the same bytes while the count says 4.
  // Offering a ninety-second rebuild for a file that did not change is a lie
  // about what changed, so the count is zeroed when the text comes home.
  const buildBaselineRef = useRef("");

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
        buildBaselineRef.current = text;
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
    setFuture([]);
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
  // Read by `checkNow`, which must not be re-created on every parse: it is
  // called from `write`, and a new identity there would rebuild every callback
  // that writes.
  bindingRef.current = binding;

  /**
   * Send one set of edits and adopt whatever the file says afterwards.
   *
   * `delta` is what this edit does to the count of changes waiting on a
   * rebuild. A lock is 0: it changes the file but not the geometry, so
   * offering to spend minutes rebuilding for it would be a lie about what
   * changed.
   */
  /**
   * Ask the gate whether the board, as the file now has it, is legal.
   *
   * The board on disk was compiled from the old positions, so the moves the
   * file is holding go with the request (`pendingMoves`) and the gate grades
   * predicted geometry — the thing on screen, not the thing on disk. It is the
   * same gate the build runs, minus the one check that costs 1.5 seconds, and
   * it carries its own blind spots in `notChecked`.
   *
   * Latest wins: a second drag can land while the first check is still out, and
   * an older answer arriving second would paint the board legal after the move
   * that broke it.
   */
  const checkNow = useCallback(async () => {
    if (!projectId || !file) return null;
    const run = (checkRunRef.current += 1);
    setChecking(true);
    try {
      const result = await callApi("board_fast_check", {
        id: projectId,
        file,
        moves: pendingMoves(bindingRef.current),
      });
      if (run !== checkRunRef.current) return null;
      setVerdict({ ...result, turnsPending: turnsRef.current });
      return result;
    } catch (err) {
      if (run !== checkRunRef.current) return null;
      setVerdict({
        status: "unavailable",
        reason: err instanceof Error ? err.message : String(err),
        turnsPending: turnsRef.current,
      });
      return null;
    } finally {
      if (run === checkRunRef.current) setChecking(false);
    }
  }, [projectId, file]);

  // One render after a write, which is the first moment the binding describes
  // the file the server now holds — and the request's whole content is the
  // difference between that binding and the board that was built.
  useEffect(() => {
    if (!wantCheckRef.current) return;
    wantCheckRef.current = false;
    void checkNow();
  }, [binding, checkNow]);

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
        // A structural edit can rename placements — wrapping an `<Ldo3v3>` in a
        // `<group>` removes an `Ldo3v3[n]` and inserts a `group[n]`, renumbering
        // every later `group[…]`. Geometry is keyed by id, so it has to be
        // carried across by position or the wrapped part inherits a neighbour's
        // footprint, refdes list and cross-probe set. Only when the ids
        // actually moved: every ordinary move and lock leaves them alone.
        const before = parseBoardSource(sourceRef.current).placements;
        const after = parseBoardSource(result.text).placements;
        if (before.length !== after.length || before.some((p, at) => p.id !== after[at].id)) {
          setSnapshot((prev) => remapSnapshot(prev, before, after));
        }
        setSource(result.text);
        setChanges((n) => (result.text === buildBaselineRef.current ? 0 : Math.max(0, n + delta)));
        if (note) setLastChange(note);
        // Saving and grading are one gesture from the user's side. An edit that
        // changed geometry and reported only "saved" is the state this app
        // shipped in until 2026-08-16: the gate existed, worked, and nothing
        // ever called it. A lock changes the file and not the board, so it does
        // not spend seconds of CPU on a verdict that cannot have moved.
        //
        // Flagged, not called: the moves the request carries are read off the
        // binding, and the binding for the text just written does not exist
        // until React has re-rendered with it. Calling here sent `moves: []`
        // and asked the gate about the board as it was before the drag.
        if (delta !== 0) wantCheckRef.current = true;
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [projectId, file, checkNow],
  );

  /**
   * Record a new edit.
   *
   * A fresh edit forks the timeline, so anything that was undone is no longer
   * reachable — the standard rule everywhere else, and the honest one: a redo
   * stack kept across a new drag would re-apply an edit onto a file that has
   * since moved under it.
   */
  const pushHistory = useCallback((entry) => {
    setHistory((list) => [...list, entry].slice(-50));
    setFuture([]);
  }, []);

  /** Move a placement to an absolute board position, recording the undo. */
  const move = useCallback(
    async (placementId, x, y, note) => {
      const placement = binding.byId.get(placementId);
      if (!placement) return false;
      const edits = moveEdits(source, placement, x, y);
      if (!edits.length) return true;
      const undoEntry = { kind: "move", placementId, x: placement.x, y: placement.y, label: placement.label };
      const ok = await write(edits, note, +1);
      if (ok) pushHistory(undoEntry);
      return ok;
    },
    [binding, source, write, pushHistory],
  );

  /**
   * Turn a placement to an absolute angle, recording the undo.
   *
   * The refusal is re-checked here even though the caller checked it. This is
   * the last line before bytes reach the file, and the failure it guards
   * against is the expensive one: a `pcbRotation` written on one of our own
   * components is dropped by JSX in silence, survives a 95-second rebuild, and
   * comes back at the same angle with nothing in the chain having said a word.
   * Refusing loudly here costs one comparison.
   *
   * Two undo shapes, because there are two edit shapes. A prop edit inverts by
   * knowing the previous angle — `null` when there was no prop, so undo removes
   * the one this app inserted and the file goes back byte-for-byte. A wrap
   * inverts by replaying the recorded bytes, because after it the placement id
   * names a `<group>` and the tag it wrapped is no longer a placement at all.
   */
  const rotate = useCallback(
    async (placementId, degrees, note) => {
      const placement = binding.byId.get(placementId);
      if (!placement) return false;
      const refusal = rotateRefusal(placement);
      if (refusal) {
        setError(refusal.reason);
        return false;
      }
      const edits = rotateEdits(source, placement, degrees);
      if (!edits.length) return true;
      const undoEntry =
        placement.rotateVia === "wrap"
          ? { kind: "wrap", placementId, label: placement.label, inverse: invertEdits(edits) }
          : {
              kind: "rotate",
              placementId,
              label: placement.label,
              rotation: placement.rotationSpan ? placement.rotation : null,
            };
      const ok = await write(edits, note || describeRotate(placement.label, placement.rotation, degrees), +1);
      if (ok) {
        pushHistory(undoEntry);
        setTurnsPending((n) => n + 1);
      }
      return ok;
    },
    [binding, source, write, pushHistory],
  );

  /** The four lines a wrap is about to write, so a confirm can show the diff
   *  itself rather than a description of it. Empty for every other path. */
  const previewRotate = useCallback(
    (placementId, degrees) => wrapPreview(source, binding.byId.get(placementId), degrees),
    [binding, source],
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
      if (ok) pushHistory(undoEntry);
      return ok;
    },
    [binding, source, write, pushHistory],
  );

  /**
   * Apply one history entry and hand back the entry that would undo it.
   *
   * Undo and redo are the same operation pointed the other way, so they are
   * one function. Writing them twice is how a redo ends up restoring a
   * slightly different thing than the undo took away — and the reciprocal has
   * to be computed from the placement as it is *now*, before the write, which
   * is exactly what the forward edit did when it was first recorded.
   *
   * @returns {object|null} the reciprocal entry, or null when nothing happened.
   */
  const applyEntry = useCallback(
    async (entry, verb, delta) => {
      if (!entry) return null;
      // A structural edit carries its own inverse, recorded when it was
      // written. Recomputing one from a placement would fail: the id in the
      // entry names the tag as it was BEFORE the edit, and after a wrap that
      // tag is no longer a top-level placement. Inverting the inverse is what
      // makes a wrap redoable.
      if (entry.inverse) {
        const reciprocal = { kind: "wrap", placementId: entry.placementId, label: entry.label, inverse: invertEdits(entry.inverse) };
        const ok = await write(entry.inverse, `${verb}: ${entry.label}`, delta);
        // Undoing a turn takes the board back towards what was built; redoing
        // one takes it further away. The count follows the geometry, not the
        // number of gestures.
        if (ok) setTurnsPending((n) => Math.max(0, n + (delta < 0 ? -1 : 1)));
        return ok ? reciprocal : null;
      }
      const placement = binding.byId.get(entry.placementId);
      if (!placement) {
        setError(`that part is not in the board file any more — nothing to ${verb === "undid" ? "undo" : "redo"} onto`);
        return null;
      }
      const reciprocal =
        entry.kind === "lock"
          ? { kind: "lock", placementId: entry.placementId, label: entry.label, locked: placement.locked }
          : entry.kind === "rotate"
            ? {
                kind: "rotate",
                placementId: entry.placementId,
                label: entry.label,
                rotation: placement.rotationSpan ? placement.rotation : null,
              }
            : { kind: "move", placementId: entry.placementId, label: entry.label, x: placement.x, y: placement.y };
      const edits =
        entry.kind === "lock"
          ? lockEdits(source, placement, entry.locked)
          : entry.kind === "rotate"
            ? rotateEdits(source, placement, entry.rotation)
            : moveEdits(source, placement, entry.x, entry.y);
      const ok = await write(edits, `${verb}: ${entry.label}`, entry.kind === "lock" ? 0 : delta);
      if (ok && entry.kind === "rotate") {
        setTurnsPending((n) => Math.max(0, n + (delta < 0 ? -1 : 1)));
      }
      return ok ? reciprocal : null;
    },
    [binding, source, write],
  );

  const undo = useCallback(async () => {
    const entry = history[history.length - 1];
    if (!entry) return false;
    const reciprocal = await applyEntry(entry, "undid", -1);
    if (!reciprocal) return false;
    setHistory((list) => list.slice(0, -1));
    setFuture((list) => [...list, reciprocal].slice(-50));
    return true;
  }, [history, applyEntry]);

  const redo = useCallback(async () => {
    const entry = future[future.length - 1];
    if (!entry) return false;
    const reciprocal = await applyEntry(entry, "redid", +1);
    if (!reciprocal) return false;
    setFuture((list) => list.slice(0, -1));
    setHistory((list) => [...list, reciprocal].slice(-50));
    return true;
  }, [future, applyEntry]);

  /** Called once a rebuild has been asked for — the file is no longer ahead
   *  of the board on screen from the user's point of view. */
  const markBuilding = useCallback(() => {
    buildBaselineRef.current = sourceRef.current;
    setChanges(0);
    setLastChange("");
    setTurnsPending(0);
  }, []);

  // A new build makes every earlier verdict a statement about a board that no
  // longer exists — its findings were measured on the old copper with the old
  // moves predicted on top. Dropping it is the honest default; the strip then
  // says "not checked" rather than showing a green chip that has expired.
  useEffect(() => {
    checkRunRef.current += 1;
    setVerdict(null);
    setChecking(false);
    setTurnsPending(0);
  }, [buildKey]);

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
    canRedo: future.length > 0,
    // The gate's last word, whether one is in flight, how many turns it could
    // not see, and the way to ask again.
    verdict,
    checking,
    turnsPending,
    checkNow,
    move,
    rotate,
    previewRotate,
    setLock,
    undo,
    redo,
    markBuilding,
    clearError: () => setError(""),
  };
}
