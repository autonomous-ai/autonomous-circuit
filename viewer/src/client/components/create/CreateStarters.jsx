import { useState } from "react";
import { ArrowRight } from "lucide-react";
import { cn } from "@/ui/utils";
import { useChatStore } from "@/store/chat";
import { STARTERS, buildStarterPrompt } from "./starters";
import { startFromBrief } from "./createActions";

// The zero-friction front door: instead of a blank prompt box, a short shelf
// of proven board archetypes. Tapping a card sends a complete engineering
// brief to the chat — spec → board → fab packet. Typing your own idea stays
// available in the ChatInput below this.
//
// The cards used to carry an emoji on a coloured gradient. They do not now:
// this is a professional tool, the gradients were standing in for board
// renders we do not have, and a coloured square that means nothing is worse
// than none. What replaced them is the thing a first-timer actually wants to
// know before committing ninety seconds — the parts the board will be built
// from, named.
export default function CreateStarters({ className }) {
  const turnInProgress = useChatStore((state) => state.turnInProgress);
  const [busyId, setBusyId] = useState("");
  const [notice, setNotice] = useState("");

  const busy = turnInProgress || Boolean(busyId);

  const launch = async (starter) => {
    if (!starter || busy) return;
    setBusyId(starter.id);
    setNotice("");
    try {
      const ok = await startFromBrief(buildStarterPrompt(starter));
      if (!ok) setNotice("Couldn’t start that one — please try again.");
    } finally {
      setBusyId("");
    }
  };

  return (
    <div
      data-slot="create-starters"
      className={cn("flex w-full flex-col gap-3.5 overflow-y-auto overflow-x-hidden px-3.5 py-5", className)}
    >
      <div className="flex flex-col gap-1">
        <h2 className="text-base font-semibold tracking-tight">What do you want to build?</h2>
        <p className="text-xs leading-5 text-muted-foreground">
          Tap one. Circuit asks a couple of quick questions — every one has a "let Circuit choose" — then designs the
          board and checks it against the factory's rules.
        </p>
      </div>

      {notice ? (
        <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-center text-xs font-medium text-destructive">
          {notice}
        </p>
      ) : null}

      <div className="flex flex-col gap-2">
        {STARTERS.map((s) => (
          <button
            key={s.id}
            type="button"
            disabled={busy}
            aria-label={`Build a ${s.title}`}
            onClick={() => launch(s)}
            className={cn(
              "group relative flex flex-col gap-1 rounded-lg border border-border/60 bg-card/40 px-3 py-2.5 text-left transition-colors",
              "hover:border-primary/60 hover:bg-accent/40",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary",
              "disabled:pointer-events-none disabled:opacity-60",
            )}
          >
            <div className="flex items-baseline gap-2">
              <span className="text-[13px] font-semibold leading-tight tracking-tight">{s.title}</span>
              {s.popular ? (
                <span className="rounded border border-border/60 px-1 py-px text-[9px] font-medium uppercase tracking-wide text-muted-foreground">
                  Popular
                </span>
              ) : null}
              <span className="ml-auto inline-flex items-center gap-1 text-[11px] font-medium text-primary">
                {busyId === s.id ? "Starting…" : "Build it"}
                {busyId === s.id ? null : (
                  <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" aria-hidden />
                )}
              </span>
            </div>
            <span className="text-xs leading-5 text-muted-foreground">{s.pitch}</span>
            <span className="truncate font-mono text-[10px] text-muted-foreground/70">{s.parts}</span>
          </button>
        ))}
      </div>

      <p className="pt-0.5 text-[11px] leading-4 text-muted-foreground/70">
        Or describe your own below — plain words are enough.
      </p>
    </div>
  );
}
