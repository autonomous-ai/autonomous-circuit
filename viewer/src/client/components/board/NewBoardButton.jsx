import { useCallback, useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { cn } from "@/ui/utils";
import { transport } from "@/lib/transport.ts";

/**
 * Viewer-only mode's one way to start another board: ask the Harness daemon for a new harness
 * of the same tile, in a sibling folder, on the same engine (server: `harness_new_board`).
 * The daemon does not place the new pane in a tab — the desktop does — so the reply carries the
 * hint the person needs: open it with ⌘O. The button saves four of the five New Harness steps.
 */
export default function NewBoardButton({ className = "" }) {
  const [state, setState] = useState({ phase: "idle" });

  const onClick = useCallback(async () => {
    if (state.phase === "busy") return;
    setState({ phase: "busy" });
    try {
      const result = await transport.harness_new_board();
      setState({ phase: "done", result });
    } catch (err) {
      setState({ phase: "error", message: err?.message || String(err) });
    }
  }, [state.phase]);

  return (
    <div className={cn("flex items-center gap-2", className)} data-slot="new-board">
      <button
        type="button"
        onClick={onClick}
        disabled={state.phase === "busy"}
        title="Start another board in a new harness of this tile (a sibling folder, the same engine)"
        data-slot="new-board-button"
        className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground disabled:opacity-60"
      >
        {state.phase === "busy" ? <Loader2 className="size-3 animate-spin" aria-hidden /> : <Plus className="size-3" aria-hidden />}
        New board
      </button>
      {state.phase === "done" ? (
        <span className="max-w-[28rem] truncate text-xs text-muted-foreground" title={state.result.hint} data-slot="new-board-hint">
          {state.result.hint}
        </span>
      ) : null}
      {state.phase === "error" ? (
        <span className="max-w-[28rem] truncate text-xs text-destructive" title={state.message} data-slot="new-board-error">
          {state.message}
        </span>
      ) : null}
    </div>
  );
}
