import { useEffect, useMemo, useRef, useState } from "react";
import {
  CircleCheck,
  CircleHelp,
  Hammer,
  Loader2,
  Lock,
  LockOpen,
  Redo2,
  RotateCcw,
  RotateCw,
  TriangleAlert,
  Undo2,
  X,
} from "lucide-react";
import { cn } from "@/ui/utils";
import { ROTATION_STEPS, SNAP_STEPS } from "./boardSource.js";
import { CCW, CW, DEFAULT_ROTATION_STEP, commitRotateStep, rotateRefusal } from "./placementRotate.js";

/**
 * What the gate said about the board as the file now has it.
 *
 * The whole point of the strip is that a drag writes a real file, and the
 * question that follows a real write is "is it still legal". The gate answers
 * that in a second or so on predicted geometry — the board on disk plus the
 * moves the file is holding — so this is the one place in the app where an
 * engineer finds out before a rebuild.
 *
 * Three rules the copy here obeys, because a verdict that overstates itself is
 * worse than none:
 *
 *  - **"Not checked" is a state.** Null is not clean. Before anyone has asked,
 *    and after a rebuild has made the last answer expire, it says so.
 *  - **The geometry is named.** `predicted` means the board on screen has not
 *    been compiled with these positions; the word is the gate's, not ours.
 *  - **The turns are named.** The gate translates elements, it cannot rotate
 *    them, so a pending rotation is geometry no verdict has seen — and a green
 *    chip that quietly excluded it would be the exact lie this panel exists to
 *    catch.
 */
function VerdictChip({ verdict, checking, turnsPending, onCheck, busy }) {
  const [open, setOpen] = useState(false);
  const counts = verdict?.counts || null;
  const blocking = Number(counts?.error || 0);
  const state = checking
    ? "checking"
    : !verdict
      ? "unknown"
      : verdict.status === "unavailable"
        ? "unavailable"
        : blocking
          ? "blocked"
          : "legal";

  const label =
    state === "checking"
      ? "checking…"
      : state === "unknown"
        ? "not checked"
        : state === "unavailable"
          ? "check unavailable"
          : blocking
            ? `${blocking} blocking`
            : "legal";

  const findings = Array.isArray(verdict?.warnings) ? verdict.warnings : [];
  const worst = findings
    .filter((one) => one.severity === "error")
    .concat(findings.filter((one) => one.severity === "warning"))
    .slice(0, 6);

  return (
    <>
      <span className="flex items-center gap-1" data-slot="placement-verdict" data-state={state}>
        <button
          type="button"
          onClick={() => (verdict || checking ? setOpen((value) => !value) : onCheck?.())}
          disabled={busy && !verdict}
          data-slot="placement-verdict-chip"
          title={
            state === "unknown"
              ? "Ask whether the board, with your changes, is still legal on copper"
              : "What the check found, and what it could not see"
          }
          className={cn(
            "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-medium transition-colors",
            state === "legal" && "border-emerald-500/50 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
            state === "blocked" && "border-destructive/50 bg-destructive/15 text-destructive",
            (state === "unknown" || state === "checking" || state === "unavailable") &&
              "border-border/60 text-muted-foreground hover:text-foreground",
          )}
        >
          {state === "checking" ? (
            <Loader2 className="size-3 animate-spin" aria-hidden />
          ) : state === "legal" ? (
            <CircleCheck className="size-3" aria-hidden />
          ) : state === "blocked" ? (
            <TriangleAlert className="size-3" aria-hidden />
          ) : (
            <CircleHelp className="size-3" aria-hidden />
          )}
          {label}
        </button>
        {verdict && !checking ? (
          <button
            type="button"
            onClick={() => onCheck?.()}
            data-slot="placement-verdict-recheck"
            title="Check again"
            className="rounded border border-border/60 px-1 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"
          >
            recheck
          </button>
        ) : null}
      </span>

      {open && verdict ? (
        <div data-slot="placement-verdict-detail" className="w-full space-y-1 text-[11px]">
          <p className="text-muted-foreground">
            {verdict.status === "unavailable" ? (
              <>The check could not run: {verdict.reason || "no reason given"}.</>
            ) : (
              <>
                {verdict.geometry === "predicted"
                  ? `Graded on the built board with your ${verdict.moves?.length || 0} ${
                      (verdict.moves?.length || 0) === 1 ? "move" : "moves"
                    } applied — nothing has been recompiled.`
                  : "Graded on the board as it was last built."}
                {typeof verdict.elapsedMs === "number" ? ` ${Math.round(verdict.elapsedMs)}ms.` : ""}
                {" "}
                {counts?.warning || 0} to look at, {counts?.info || 0} noted.
              </>
            )}
          </p>
          {turnsPending ? (
            <p className="text-amber-600 dark:text-amber-400">
              {turnsPending} {turnsPending === 1 ? "turn is" : "turns are"} NOT in this check — the gate moves parts,
              it cannot turn them. Rebuild to have a turn checked.
            </p>
          ) : null}
          {worst.length ? (
            <ul className="space-y-0.5">
              {worst.map((one, at) => (
                <li key={`${one.kind}-${one.part}-${at}`} className="flex gap-1.5">
                  <span
                    className={cn(
                      "shrink-0 font-mono",
                      one.severity === "error" ? "text-destructive" : "text-muted-foreground",
                    )}
                  >
                    {one.part || "board"}
                  </span>
                  <span className="min-w-0 text-muted-foreground">{one.detail || one.kind}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {Array.isArray(verdict.notChecked) && verdict.notChecked.length ? (
            <p className="text-muted-foreground/80">
              Not checked: {verdict.notChecked.map((entry) => entry.what).join(", ")}.
            </p>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

/**
 * What a turn will do, and what it will write.
 *
 * The second half is the point. `pcbRotation` counts counterclockwise, which is
 * the file's convention and the right one, so turning a part clockwise from 0°
 * writes **270**. That is correct and it reads as a bug: an engineer who turns
 * a part clockwise, opens the board file and finds 270 concludes the button is
 * wrong. Saying it on the button costs nothing and answers the question before
 * it is asked.
 */
function turnTitle(placement, direction, step) {
  const command = commitRotateStep(placement, direction, step);
  const way = direction === CW ? "clockwise" : "counterclockwise";
  if (!command) return `${placement.label} is already there`;
  return (
    `Turn ${placement.label} ${step}° ${way} — writes pcbRotation={${command.to}} ` +
    `(the board file counts counterclockwise from 0°)`
  );
}

/**
 * The strip that appears above the PCB canvas in move mode.
 *
 * It exists to make one sentence unmissable before anyone drags anything:
 * **this edits your board file, and here is its name.** A canvas that silently
 * rewrote a source file would be the worst kind of surprise in this app,
 * because the file is also where the agent's work lives and where forty
 * hard-won comments about what was already tried are written down.
 *
 * Everything else on the strip follows from that: the undo, the lock, and the
 * rebuild are the three things you need after a drag, and none of them is
 * discoverable on the canvas itself.
 */
export default function PlacementEditBar({
  editor,
  placement = null,
  snapStep = 0.5,
  onSnapStep,
  rotationStep,
  onRotationStep,
  onRebuild,
  rebuilding = false,
  canRebuild = true,
  onClose,
  className,
}) {
  const { file, ready, reason, busy, error, changes, lastChange, canUndo, canRedo } = editor;
  const total = editor.placements?.byId?.size ?? 0;
  const unmatched = editor.unmatched?.length ?? 0;

  // The step lives here until someone above wants it. An uncontrolled default
  // beside a controlled prop means the keymap can lift it out later without
  // this file changing shape.
  const [ownStep, setOwnStep] = useState(DEFAULT_ROTATION_STEP);
  const turnBy = Number(rotationStep ?? ownStep) || DEFAULT_ROTATION_STEP;
  const setTurnBy = (value) => (onRotationStep ? onRotationStep(value) : setOwnStep(value));

  // A wrap writes four lines of new structure rather than one changed literal,
  // so the first one on a given placement shows the diff and asks. The second
  // turn of the same part rewrites a literal and never reaches here.
  const [pendingWrap, setPendingWrap] = useState(null);
  const confirmedWraps = useRef(new Set());

  // A confirm panel names one placement and writes four lines of structure to
  // it. Selecting a different part on the canvas while it is open leaves the
  // panel pointing at the old one, so "Wrap and turn" would land a structural
  // edit on a part that is no longer highlighted. Clearing it on any change of
  // selection makes the panel and the canvas describe the same object, always.
  useEffect(() => {
    setPendingWrap(null);
  }, [placement?.id]);

  const refusal = placement ? rotateRefusal(placement) : null;
  const wrapCount = useMemo(() => {
    let count = 0;
    for (const bound of editor.placements?.byId?.values() ?? []) if (bound.rotateVia === "wrap") count += 1;
    return count;
  }, [editor.placements]);

  const turn = (direction) => {
    const command = commitRotateStep(placement, direction, turnBy);
    if (!command) return;
    // A refusal outranks the confirm: asking someone to approve a wrap and
    // then telling them the part is locked would be two dialogs for one no.
    if (refusal) {
      setPendingWrap(null);
      editor.turnBy(command.placementId, direction, turnBy);
      return;
    }
    if (command.confirm && !confirmedWraps.current.has(command.placementId)) {
      setPendingWrap({ ...command, preview: editor.previewRotate?.(command.placementId, command.to) || "" });
      return;
    }
    setPendingWrap(null);
    // The step, not the angle. `command.to` was computed from the angle on
    // screen, and the angle on screen is one round trip behind the file while
    // a tap is in flight — so four fast taps all asked for the same 270°, three
    // of them found it already written, and three taps disappeared. `turnBy`
    // applies the step inside the edit queue, to whatever the file says then.
    editor.turnBy(command.placementId, direction, turnBy);
  };

  return (
    <div
      data-slot="placement-edit-bar"
      data-ready={ready ? "true" : "false"}
      className={cn(
        "flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs",
        className,
      )}
    >
      <span className="flex items-center gap-1.5 font-medium text-foreground">
        <Hammer className="size-3.5 text-amber-500" aria-hidden />
        Moving parts edits
        <code className="rounded bg-black/25 px-1 py-0.5 font-mono text-[11px]">{file || "the board file"}</code>
      </span>

      {ready ? (
        <span className="text-muted-foreground" data-slot="placement-edit-count">
          {total} {total === 1 ? "part or block" : "parts and blocks"} can be dragged
          {unmatched ? ` · ${unmatched} cannot` : ""}
          {/* Said before the key is pressed, not discovered on keystroke five:
              most of what our boards place is one of our own components, and
              turning one means writing a wrapper around it. */}
          {wrapCount ? ` · ${wrapCount} need a wrapper to turn` : ""}
        </span>
      ) : (
        <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
          <TriangleAlert className="size-3.5" aria-hidden />
          {reason || "reading the board file…"}
        </span>
      )}

      <label className="ml-auto flex items-center gap-1 text-muted-foreground">
        Steps of
        <select
          value={String(snapStep)}
          onChange={(event) => onSnapStep?.(Number(event.target.value))}
          data-slot="placement-snap-step"
          className="rounded border border-border/60 bg-transparent px-1 py-0.5 font-mono text-[11px]"
        >
          {SNAP_STEPS.map((step) => (
            <option key={step} value={step}>
              {step} mm
            </option>
          ))}
        </select>
        <span className="hidden text-[11px] text-muted-foreground/70 sm:inline">(hold ⌥ for 0.01)</span>
      </label>

      {placement ? (
        <span className="flex items-center gap-1 text-muted-foreground" data-slot="placement-rotate">
          Turn
          <select
            value={String(turnBy)}
            onChange={(event) => setTurnBy(Number(event.target.value))}
            data-slot="placement-rotate-step"
            className="rounded border border-border/60 bg-transparent px-1 py-0.5 font-mono text-[11px]"
          >
            {ROTATION_STEPS.map((step) => (
              <option key={step} value={step}>
                {step}°
              </option>
            ))}
          </select>
          {/* Both buttons stay live even when the source cannot express the
              turn. A disabled control says "not for you" and stops; a click
              that answers with the reason says which file to edit instead. */}
          <button
            type="button"
            disabled={busy}
            onClick={() => turn(CCW)}
            data-slot="placement-rotate-ccw"
            data-rotate-via={placement.rotateVia}
            title={refusal ? refusal.reason : turnTitle(placement, CCW, turnBy)}
            className={cn(
              "rounded border border-border/60 p-0.5 transition-colors hover:text-foreground disabled:opacity-40",
              refusal ? "opacity-50" : "",
            )}
          >
            <RotateCcw className="size-3" aria-hidden />
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => turn(CW)}
            data-slot="placement-rotate-cw"
            data-rotate-via={placement.rotateVia}
            title={refusal ? refusal.reason : turnTitle(placement, CW, turnBy)}
            className={cn(
              "rounded border border-border/60 p-0.5 transition-colors hover:text-foreground disabled:opacity-40",
              refusal ? "opacity-50" : "",
            )}
          >
            <RotateCw className="size-3" aria-hidden />
          </button>
        </span>
      ) : null}

      {placement ? (
        <button
          type="button"
          disabled={busy}
          onClick={() => editor.setLock(placement.id, !placement.locked)}
          data-slot="placement-lock"
          data-locked={placement.locked ? "true" : "false"}
          title={
            placement.locked
              ? "Unlock — this part can be dragged again, and an agent may move it"
              : "Lock — writes a comment in the board file telling the next agent to leave this placement alone"
          }
          className={cn(
            "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 transition-colors",
            placement.locked
              ? "border-amber-500/50 bg-amber-500/20 text-foreground"
              : "border-border/60 text-muted-foreground hover:text-foreground",
          )}
        >
          {placement.locked ? <Lock className="size-3" aria-hidden /> : <LockOpen className="size-3" aria-hidden />}
          {placement.locked ? `${placement.label} is locked` : `Lock ${placement.label}`}
        </button>
      ) : null}

      <VerdictChip
        verdict={editor.verdict}
        checking={editor.checking}
        turnsPending={editor.turnsPending}
        onCheck={editor.checkNow}
        busy={busy}
      />

      <button
        type="button"
        disabled={!canUndo || busy}
        onClick={() => editor.undo()}
        data-slot="placement-undo"
        title="Put the last change back"
        className="inline-flex items-center gap-1 rounded border border-border/60 px-1.5 py-0.5 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40"
      >
        <Undo2 className="size-3" aria-hidden />
        Undo
      </button>

      {/* Undo without redo is a one-way door, and the door this one opens
          rewrites a file. ⇧⌘Z / Ctrl+Y reach the same `editor.redo`. */}
      <button
        type="button"
        disabled={!canRedo || busy}
        onClick={() => editor.redo()}
        data-slot="placement-redo"
        title="Do it again (⇧⌘Z / Ctrl+Y)"
        className="inline-flex items-center gap-1 rounded border border-border/60 px-1.5 py-0.5 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40"
      >
        <Redo2 className="size-3" aria-hidden />
        Redo
      </button>

      {/* A build is minutes, so it is never automatic. Until it runs, the board
          on screen is the OLD placement — saying so is the honest label. */}
      <button
        type="button"
        disabled={!changes || busy || rebuilding || !canRebuild}
        onClick={() => onRebuild?.()}
        data-slot="placement-rebuild"
        title={
          canRebuild
            ? "Ask the agent to rebuild the board from the file as it now stands"
            : "The agent is busy — wait for the current turn to finish"
        }
        className="inline-flex items-center gap-1 rounded border border-primary/50 bg-primary/15 px-1.5 py-0.5 font-medium text-foreground transition-colors hover:bg-primary/25 disabled:opacity-40"
      >
        {rebuilding ? <Loader2 className="size-3 animate-spin" aria-hidden /> : null}
        {changes ? `Rebuild (${changes} ${changes === 1 ? "change" : "changes"})` : "Rebuild"}
      </button>

      {onClose ? (
        <button
          type="button"
          onClick={onClose}
          title="Leave move mode"
          data-slot="placement-edit-close"
          className="rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
        >
          <X className="size-3.5" aria-hidden />
        </button>
      ) : null}

      {busy ? <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-label="Saving" /> : null}

      {/* The diff itself, not a description of it. This is a structural edit to
          a file that carries the board's engineering record in its comments,
          and the wider an edit's blast radius the more it is worth interposing
          a confirmation — Altium's own Confirm Global Edit principle. */}
      {pendingWrap ? (
        <div data-slot="placement-wrap-confirm" className="w-full space-y-1">
          <p className="text-muted-foreground">
            {pendingWrap.label} does not take an angle of its own, so turning it means wrapping it in a{" "}
            <code className="font-mono">&lt;group&gt;</code> that carries one. This is what will be written to {file}:
          </p>
          <pre className="overflow-x-auto rounded bg-black/25 p-1.5 font-mono text-[11px] leading-snug">
            {pendingWrap.preview}
          </pre>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={busy}
              data-slot="placement-wrap-accept"
              onClick={() => {
                confirmedWraps.current.add(pendingWrap.placementId);
                editor.rotate(pendingWrap.placementId, pendingWrap.to);
                setPendingWrap(null);
              }}
              className="rounded border border-primary/50 bg-primary/15 px-1.5 py-0.5 font-medium text-foreground hover:bg-primary/25 disabled:opacity-40"
            >
              Wrap and turn
            </button>
            <button
              type="button"
              data-slot="placement-wrap-cancel"
              onClick={() => setPendingWrap(null)}
              className="rounded border border-border/60 px-1.5 py-0.5 text-muted-foreground hover:text-foreground"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {error ? (
        <p data-slot="placement-edit-error" className="w-full text-destructive">
          {error}
        </p>
      ) : lastChange ? (
        <p data-slot="placement-edit-note" className="w-full text-muted-foreground">
          {lastChange}.
          {changes
            ? " The board below is still the last build — the copper has not moved with the part, and a turn does not show on screen until you rebuild."
            : ""}
        </p>
      ) : null}
    </div>
  );
}
