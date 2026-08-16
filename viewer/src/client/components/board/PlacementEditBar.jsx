import { Hammer, Loader2, Lock, LockOpen, TriangleAlert, Undo2, X } from "lucide-react";
import { cn } from "@/ui/utils";
import { SNAP_STEPS } from "./boardSource.js";

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
  onRebuild,
  rebuilding = false,
  canRebuild = true,
  onClose,
  className,
}) {
  const { file, ready, reason, busy, error, changes, lastChange, canUndo } = editor;
  const total = editor.placements?.byId?.size ?? 0;
  const unmatched = editor.unmatched?.length ?? 0;

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

      {error ? (
        <p data-slot="placement-edit-error" className="w-full text-destructive">
          {error}
        </p>
      ) : lastChange ? (
        <p data-slot="placement-edit-note" className="w-full text-muted-foreground">
          {lastChange}.
          {changes
            ? " The board below is still the last build — the copper has not moved with the part."
            : ""}
        </p>
      ) : null}
    </div>
  );
}
