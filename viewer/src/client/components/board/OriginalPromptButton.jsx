import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy, Loader2, MessageSquareText } from "lucide-react";
import { cn } from "@/ui/utils";
import { transport } from "@/lib/transport.ts";

const ENGINE_LABEL = { codex: "Codex", claude: "Claude Code", grok: "Grok" };

function when(at) {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/**
 * "Prompt" beside the board name: what the person asked for, one click away.
 *
 * In a Harness pane the conversation is in the agent's terminal, and after a long run the
 * original request is hundreds of turns up the scrollback. The server reads it back from the
 * engine's own session logs for this workspace (`workspace_prompts`). In the app proper the
 * chat history already has it, passed in as `fallbackText`, used when the logs have nothing.
 *
 * @param {{ projectId: string, fallbackText?: string, className?: string }} props
 */
export default function OriginalPromptButton({ projectId, fallbackText = "", className }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState({ phase: "idle", data: null, error: "" });
  const [showAll, setShowAll] = useState(false);
  const [copied, setCopied] = useState(false);
  const [anchor, setAnchor] = useState({ top: 0, left: 0 });
  const rootRef = useRef(null);
  const buttonRef = useRef(null);

  const load = useCallback(async () => {
    if (!projectId) {
      setState({ phase: "ready", data: { original: null, sessions: [] }, error: "" });
      return;
    }
    setState((s) => ({ ...s, phase: "loading" }));
    try {
      const data = await transport.workspace_prompts(projectId);
      setState({ phase: "ready", data, error: "" });
    } catch (err) {
      setState({ phase: "error", data: null, error: err?.message || String(err) });
    }
  }, [projectId]);

  const toggle = useCallback(() => {
    // The tab row scrolls horizontally, which would clip an absolutely placed panel; the panel
    // is fixed to the viewport under the button instead.
    const r = buttonRef.current?.getBoundingClientRect?.();
    if (r) setAnchor({ top: r.bottom + 4, left: Math.max(8, Math.min(r.left, (window.innerWidth || 1024) - 584)) });
    setOpen((was) => {
      if (!was) load();   // re-read every time it opens: the agent may have been given more since
      return !was;
    });
  }, [load]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") {
        setOpen(false);
      }
    };
    const onDown = (e) => rootRef.current && !rootRef.current.contains(e.target) && setOpen(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  const original = state.data?.original
    || (fallbackText ? { engine: "", at: "", text: fallbackText } : null);
  const sessions = state.data?.sessions || [];
  const total = sessions.reduce((n, s) => n + s.prompts.length, 0);

  const copy = useCallback(async () => {
    if (!original?.text) return;
    try {
      await navigator.clipboard.writeText(original.text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard refused (insecure context): the text is selectable in the panel
    }
  }, [original]);

  return (
    <div ref={rootRef} className={cn("relative shrink-0", className)} data-slot="original-prompt">
      <button
        ref={buttonRef}
        type="button"
        onClick={toggle}
        title="The request this board was made from"
        aria-expanded={open ? "true" : "false"}
        data-slot="original-prompt-button"
        className={cn(
          "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors",
          open ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground",
        )}
      >
        <MessageSquareText className="size-3" aria-hidden />
        Prompt
      </button>

      {open ? (
        <div
          data-slot="original-prompt-panel"
          style={{ top: anchor.top, left: anchor.left }}
          className="fixed z-50 flex max-h-[70vh] w-[36rem] max-w-[90vw] flex-col gap-2 overflow-hidden rounded-lg border border-border bg-popover p-3 text-xs shadow-lg"
        >
          <div className="flex items-center gap-2">
            <span className="font-semibold text-foreground">Original prompt</span>
            {original?.engine ? (
              <span className="text-muted-foreground">
                {ENGINE_LABEL[original.engine] || original.engine}
                {original.at ? ` · ${when(original.at)}` : ""}
              </span>
            ) : null}
            {state.phase === "loading" ? <Loader2 className="size-3 animate-spin text-muted-foreground" aria-hidden /> : null}
            {original?.text ? (
              <button
                type="button"
                onClick={copy}
                data-slot="original-prompt-copy"
                className="ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-muted-foreground hover:text-foreground"
              >
                {copied ? <Check className="size-3" aria-hidden /> : <Copy className="size-3" aria-hidden />}
                {copied ? "Copied" : "Copy"}
              </button>
            ) : null}
          </div>

          {state.phase === "error" ? (
            <p data-slot="original-prompt-error" className="text-destructive">{state.error}</p>
          ) : original?.text ? (
            <pre
              data-slot="original-prompt-text"
              className="scrollbar-thin min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 font-mono text-[11px] leading-5 text-foreground"
            >
              {original.text}
            </pre>
          ) : state.phase === "ready" ? (
            <p data-slot="original-prompt-empty" className="text-muted-foreground">
              No prompt found for this workspace yet. It appears once the agent has been given one.
            </p>
          ) : null}

          {total > 1 ? (
            <div className="flex min-h-0 flex-col gap-1">
              <button
                type="button"
                onClick={() => setShowAll((v) => !v)}
                data-slot="original-prompt-all-toggle"
                className="self-start text-muted-foreground hover:text-foreground"
              >
                {showAll ? "Hide" : "Show"} everything you typed here ({total} messages, {sessions.length} {sessions.length === 1 ? "session" : "sessions"})
              </button>
              {showAll ? (
                <ol data-slot="original-prompt-all" className="scrollbar-thin flex max-h-64 flex-col gap-1.5 overflow-auto">
                  {sessions.flatMap((s) =>
                    s.prompts.map((p, i) => (
                      <li key={`${s.sessionId}-${i}`} className="rounded border border-border/60 p-1.5">
                        <div className="text-[10px] text-muted-foreground">
                          {ENGINE_LABEL[s.engine] || s.engine} · {when(p.at)}
                        </div>
                        <div className="whitespace-pre-wrap break-words">{p.text.length > 600 ? `${p.text.slice(0, 600)}…` : p.text}</div>
                      </li>
                    )),
                  )}
                </ol>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
