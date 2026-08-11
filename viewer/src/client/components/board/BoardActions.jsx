import { useCallback, useEffect, useState } from "react";
import { ChevronDown, CircuitBoard, Download, ExternalLink, Lock, Package } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/ui/utils";
import { triggerUrlDownload } from "@/ui/download.js";
import { boardActions, visibleBoardActions } from "./boardActions.js";

const BUTTON =
  "inline-flex h-7 items-center gap-1.5 rounded-md border border-border/60 bg-card/60 px-2 text-xs font-medium transition-colors";

/**
 * A hover explanation that survives `disabled`.
 *
 * `title` on a disabled `<button>` never fires — the browser suppresses
 * pointer events on it — so the "why is this grey?" sentence was unreachable
 * in exactly the state it was written for. Two greyed buttons sat in the top
 * bar of a first-time user's first board with no way to find out what they
 * were waiting on. The wrapper is not disabled, so it gets the hover.
 */
function Reason({ text, children }) {
  return (
    <span data-slot="board-action-reason" title={text || undefined} className="inline-flex">
      {children}
    </span>
  );
}

/**
 * The board's top-bar actions — the analogue of Vibe's Slice plate / Publish /
 * Open in OrcaSlicer cluster, pointed at what a PCB is actually for.
 *
 * Copied from `FloatingToolBar`: each action owns its own gate and its own
 * label, and a disabled one keeps its tooltip so it can say *why* rather than
 * just going grey.
 *
 * "Open in KiCad" downloads `kicad-project.zip` and then says what to do with
 * it, because Circuit is a web app and the server has no shell-open command
 * (the slicer/printer family was deleted in the port). The note is transient —
 * a permanent instruction under a button is a sign the button failed.
 */
export default function BoardActions({ stem = "board", artifact = null, sidecar = null, onOpenTab, className }) {
  const [note, setNote] = useState("");
  const actions = visibleBoardActions(boardActions({ stem, artifact, sidecar }));

  useEffect(() => {
    if (!note) return undefined;
    const timer = setTimeout(() => setNote(""), 9000);
    return () => clearTimeout(timer);
  }, [note]);

  const download = useCallback((url, filename) => {
    try {
      triggerUrlDownload(url, { filename });
      return true;
    } catch {
      return false;
    }
  }, []);

  return (
    <div data-slot="board-actions" className={cn("flex items-center gap-1", className)}>
      {actions.map((action) => {
        if (action.kind === "download") {
          return (
            <Reason key={action.id} text={action.enabled ? action.note || action.label : action.reason}>
              <button
                type="button"
                disabled={!action.enabled}
                onClick={() => {
                  if (download(action.url, action.filename) && action.note) setNote(action.note);
                }}
                data-slot="board-action"
                data-action={action.id}
                className={cn(
                  BUTTON,
                  action.enabled
                    ? "text-foreground hover:border-primary/60 hover:bg-accent"
                    : "cursor-not-allowed text-muted-foreground/50",
                )}
              >
                <CircuitBoard className="size-3.5" aria-hidden />
                {action.label}
              </button>
            </Reason>
          );
        }

        if (action.kind === "menu") {
          return (
            <DropdownMenu key={action.id}>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  data-slot="board-action"
                  data-action={action.id}
                  title="Download the fab packet"
                  className={cn(BUTTON, "text-foreground hover:border-primary/60 hover:bg-accent")}
                >
                  <Package className="size-3.5" aria-hidden />
                  {action.label}
                  <ChevronDown className="size-3 opacity-60" aria-hidden />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-[240px]">
                <DropdownMenuLabel className="text-xs uppercase tracking-wide text-muted-foreground">
                  Fab packet
                </DropdownMenuLabel>
                {/* The three a factory builds from are locked until the
                    board is orderable — same gate the Fab tab has always
                    used. Locked, not hidden: the row says what it is and
                    what unlocks it. */}
                {action.items.map((item) => (
                  <DropdownMenuItem
                    key={item.id}
                    data-testid={`packet-${item.id}`}
                    data-locked={item.enabled === false ? "true" : undefined}
                    disabled={item.enabled === false}
                    onSelect={() => {
                      if (item.enabled === false) return;
                      download(item.url, item.filename);
                    }}
                    className="flex-col items-start gap-0"
                  >
                    <span className="flex w-full items-center gap-2">
                      {item.enabled === false ? (
                        <Lock className="size-3.5 shrink-0" aria-hidden />
                      ) : (
                        <Download className="size-3.5 shrink-0" aria-hidden />
                      )}
                      {item.label}
                    </span>
                    <span className="pl-[22px] text-[11px] leading-4 text-muted-foreground">
                      {item.enabled === false ? item.reason : item.hint}
                    </span>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          );
        }

        return (
          <Reason
            key={action.id}
            text={action.enabled ? "Step-by-step: exactly what to click to get these made" : action.reason}
          >
            <button
              type="button"
              disabled={!action.enabled}
              onClick={() => onOpenTab?.(action.target)}
              data-slot="board-action"
              data-action={action.id}
              className={cn(
                BUTTON,
                action.enabled
                  ? "border-emerald-500/50 bg-emerald-500/10 text-foreground hover:bg-emerald-500/20"
                  : "cursor-not-allowed text-muted-foreground/50",
              )}
            >
              <ExternalLink className="size-3.5" aria-hidden />
              {action.label}
            </button>
          </Reason>
        );
      })}

      {note ? (
        <span
          data-slot="board-action-note"
          className="ml-1 truncate font-mono text-[11px] text-emerald-600 dark:text-emerald-400"
        >
          {note}
        </span>
      ) : null}
    </div>
  );
}
