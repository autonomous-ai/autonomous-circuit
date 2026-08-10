import { useState } from "react";
import { ArrowRight, Sparkles } from "lucide-react";
import { cn } from "@/ui/utils";
import { useChatStore } from "@/store/chat";
import { STARTERS, buildStarterPrompt } from "./starters";
import { startFromBrief } from "./createActions";

// Per-starter card gradient — a stand-in for real board renders so the gallery
// reads like a product shelf, not a text list.
const CARD_BG = {
  macropad: "linear-gradient(150deg,#101822,#23364a)",
  air_monitor: "linear-gradient(150deg,#0e2226,#2a5a5f)",
  motor_driver: "linear-gradient(150deg,#1a1406,#6a4a1a)",
  blinky_badge: "linear-gradient(150deg,#2a1030,#7a3b6a)",
};

// The zero-friction create front door: instead of a blank prompt box, a short
// shelf of proven board archetypes. Tapping a card sends a complete
// engineering brief to the chat — spec → board → fab packet. Typing your own
// idea stays available in the ChatInput below this.
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
      className={cn("flex w-full flex-col gap-4 overflow-y-auto overflow-x-hidden px-3.5 py-5", className)}
    >
      <div className="flex flex-col gap-1 text-center">
        <h2 className="text-base font-semibold tracking-tight">
          What do you want to build?
        </h2>
        <p className="text-balance text-xs text-muted-foreground">
          Tap one — Circuit specs it, designs the board, and hands you the fab
          packet.
        </p>
      </div>

      {notice ? (
        <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-center text-xs font-medium text-destructive">
          {notice}
        </p>
      ) : null}

      {/* The starter gallery — one tap per card. */}
      <div className="flex flex-col gap-2.5">
        {STARTERS.map((s) => (
          <button
            key={s.id}
            type="button"
            disabled={busy}
            aria-label={`Build a ${s.title}`}
            onClick={() => launch(s)}
            className={cn(
              "group relative flex items-stretch gap-3 overflow-hidden rounded-xl border border-border/60 bg-card p-2 text-left transition-all",
              "hover:-translate-y-px hover:border-primary/60 hover:shadow-sm",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary",
              "disabled:pointer-events-none disabled:opacity-60",
            )}
          >
            {/* board thumb */}
            <div
              className="relative flex w-[58px] shrink-0 items-end justify-center overflow-hidden rounded-lg"
              style={{ background: CARD_BG[s.id] || "linear-gradient(150deg,#1a2030,#2a3550)" }}
              aria-hidden
            >
              {s.popular && (
                <span className="absolute left-1 top-1 rounded bg-black/55 px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-white backdrop-blur-sm">
                  Popular
                </span>
              )}
              <span className="pb-1 text-2xl drop-shadow">{s.emoji}</span>
            </div>
            <div className="flex min-w-0 flex-1 flex-col justify-center gap-1 py-1 pr-1">
              <div className="truncate text-sm font-semibold leading-tight tracking-tight">
                {s.title}
              </div>
              <div className="line-clamp-2 text-xs text-muted-foreground">{s.pitch}</div>
              <div className="mt-0.5 inline-flex items-center gap-1 text-xs font-medium text-primary">
                {busyId === s.id ? "Starting…" : "Build it"}
                {busyId !== s.id && (
                  <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" aria-hidden />
                )}
              </div>
            </div>
          </button>
        ))}
      </div>

      <div className="flex items-center gap-2 pt-1 text-center text-[11px] text-muted-foreground">
        <span className="h-px flex-1 bg-border/60" />
        <span className="inline-flex items-center gap-1">
          <Sparkles className="size-3" aria-hidden /> or describe your own below
        </span>
        <span className="h-px flex-1 bg-border/60" />
      </div>
    </div>
  );
}
