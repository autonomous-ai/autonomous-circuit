import { useState } from "react";
import { ArrowRight, Sparkles, Clapperboard } from "lucide-react";
import { cn } from "@/ui/utils";
import { useChatStore } from "@/store/chat";
import {
  STARTERS,
  FEELINGS,
  buildStarterPrompt,
  buildClonePrompt,
  starterById,
  surpriseStarter,
  orderedStarters,
  ctaLabel,
  languageFromLocale,
  readStoredName,
  writeStoredName,
  inferGenreLabel,
} from "./starters";
import { startFromBrief } from "./createActions";

// Per-genre poster gradient — a stand-in for real key art so the feed reads like
// a short-drama app (ReelShort/DramaBox list card), not a text list. Replaced by
// generated posters when key art lands.
const POSTER = {
  Billionaire: "linear-gradient(150deg,#3a2b3f,#7a2233)",
  Revenge: "linear-gradient(150deg,#2a0e16,#8a2a3e)",
  "War God": "linear-gradient(150deg,#0e1a24,#233648)",
  "Rags to Riches": "linear-gradient(150deg,#1a1406,#7a5a1a)",
  "Fake Marriage": "linear-gradient(150deg,#2a1030,#9a3b6a)",
  Werewolf: "linear-gradient(150deg,#10140e,#3a4a2a)",
  Rebirth: "linear-gradient(150deg,#0e2226,#2a5a5f)",
  "Family Secrets": "linear-gradient(150deg,#241a10,#6a4a2a)",
};

// The zero-friction create front door: instead of a blank prompt box, a feed of
// proven hit shapes the creator makes theirs with one tap (docs/onboarding-ux.md).
// Tapping a card sends a complete brief to the chat — the character swap (their
// name as the lead) is the only optional field, defaulted off. Typing your own
// idea stays available in the ChatInput below this.
export default function CreateStarters({ className }) {
  const turnInProgress = useChatStore((state) => state.turnInProgress);
  const [hero, setHero] = useState(() => readStoredName());
  const [busyId, setBusyId] = useState("");
  const [notice, setNotice] = useState("");
  const [cloneOpen, setCloneOpen] = useState(false);
  // Prefill the clone hero from the remembered name so a returning user doesn't
  // retype it across the starter and clone flows.
  const [clone, setClone] = useState(() => ({ title: "", hero: readStoredName(), villain: "" }));

  const busy = turnInProgress || Boolean(busyId);
  // Feed reordered to the viewer's region, so their taste leads (never a picker).
  const locale = typeof navigator !== "undefined" ? navigator.language : "";
  const feed = orderedStarters(locale);
  // Generate in the viewer's language automatically (zero config).
  const language = languageFromLocale(locale);

  const launch = async (starter, id) => {
    if (!starter || busy) return;
    setBusyId(id);
    setNotice("");
    try {
      const ok = await startFromBrief(buildStarterPrompt(starter, hero, language));
      if (!ok) setNotice("Couldn’t start that one — please try again.");
    } finally {
      setBusyId("");
    }
  };

  const launchClone = async () => {
    if (busy || !clone.title.trim()) return;
    setBusyId("clone");
    setNotice("");
    try {
      const ok = await startFromBrief(buildClonePrompt(clone.title, clone.hero, clone.villain, language));
      if (!ok) setNotice("Couldn’t start that one — please try again.");
    } finally {
      setBusyId("");
    }
  };

  // Enter submits the clone form from any of its fields (standard form convention).
  const onCloneKeyDown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void launchClone();
    }
  };

  return (
    <div
      data-slot="create-starters"
      className={cn("flex w-full flex-col gap-4 overflow-y-auto overflow-x-hidden px-3.5 py-5", className)}
    >
      <div className="flex flex-col gap-1 text-center">
        <h2 className="text-base font-semibold tracking-tight">
          What do you feel like watching?
        </h2>
        <p className="text-balance text-xs text-muted-foreground">
          Tap one — we’ll make it about you. No writing required.
        </p>
        {language ? (
          <p className="text-[11px] font-medium text-primary">
            ✍️ We’ll write it in {language}
          </p>
        ) : null}
      </div>

      {notice ? (
        <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-center text-xs font-medium text-destructive">
          {notice}
        </p>
      ) : null}

      {/* Zero-decision path: one obvious button for the fully undecided. */}
      <button
        type="button"
        disabled={busy}
        onClick={() => launch(surpriseStarter(Date.now()), "surprise")}
        className={cn(
          "inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-3 text-sm font-semibold text-primary-foreground transition-colors",
          "hover:bg-primary/90 disabled:opacity-50",
        )}
      >
        <Sparkles className="size-4" aria-hidden />
        {busyId === "surprise" ? "Making something…" : "Surprise me — make something now"}
      </button>

      {/* Clone a drama you love — name it, swap your characters. Generates an
          ORIGINAL in that style (never a copy of the source's script/footage). */}
      <div className="rounded-xl border border-primary/40 bg-primary/5">
        {!cloneOpen ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => setCloneOpen(true)}
            className="flex w-full items-center gap-2.5 px-3.5 py-3 text-left disabled:opacity-60"
          >
            <Clapperboard className="size-4 shrink-0 text-primary" aria-hidden />
            <span className="flex-1">
              <span className="block text-sm font-semibold tracking-tight">Clone a drama you love</span>
              <span className="block text-xs text-muted-foreground">
                Name it, swap in your characters — we build your version.
              </span>
            </span>
            <ArrowRight className="size-4 shrink-0 text-primary" aria-hidden />
          </button>
        ) : (
          <div className="flex flex-col gap-2 p-3.5">
            <div className="flex items-center gap-2 text-sm font-semibold tracking-tight">
              <Clapperboard className="size-4 text-primary" aria-hidden /> Clone a drama
            </div>
            <input
              type="text"
              autoFocus
              value={clone.title}
              onChange={(e) => setClone((c) => ({ ...c, title: e.target.value }))}
              onKeyDown={onCloneKeyDown}
              placeholder="Name a drama you love (or its premise)"
              aria-label="Drama name"
              className="w-full rounded-lg border border-border/60 bg-background px-3 py-2 text-sm outline-none focus:border-primary/60"
            />
            {inferGenreLabel(clone.title) ? (
              <p className="text-[11px] text-primary">
                → We’ll make a {inferGenreLabel(clone.title)} drama in that style
              </p>
            ) : null}
            <div className="flex gap-2">
              <input
                type="text"
                value={clone.hero}
                onChange={(e) => {
                  setClone((c) => ({ ...c, hero: e.target.value }));
                  writeStoredName(e.target.value);
                }}
                onKeyDown={onCloneKeyDown}
                placeholder="You play… (name)"
                aria-label="Your character"
                className="w-1/2 rounded-lg border border-border/60 bg-background px-3 py-2 text-sm outline-none focus:border-primary/60"
              />
              <input
                type="text"
                value={clone.villain}
                onChange={(e) => setClone((c) => ({ ...c, villain: e.target.value }))}
                onKeyDown={onCloneKeyDown}
                placeholder="Who wronged you?"
                aria-label="The villain"
                className="w-1/2 rounded-lg border border-border/60 bg-background px-3 py-2 text-sm outline-none focus:border-primary/60"
              />
            </div>
            <button
              type="button"
              disabled={busy || !clone.title.trim()}
              onClick={launchClone}
              className={cn(
                "inline-flex items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground transition-colors",
                "hover:bg-primary/90 disabled:opacity-50",
              )}
            >
              {busyId === "clone" ? "Building…" : "Build my version"}
              {busyId !== "clone" && <ArrowRight className="size-3.5" aria-hidden />}
            </button>
            <p className="text-[11px] leading-snug text-muted-foreground">
              We generate an <b>original</b> story in that style with your characters — not a copy of the source’s script or footage.
            </p>
          </div>
        )}
      </div>

      {/* Optional character swap — the only field, and it's optional. */}
      <label className="flex min-h-11 items-center gap-2 rounded-lg border border-border/60 bg-muted/40 px-3 py-2.5">
        <span className="shrink-0 text-sm">⭐</span>
        <input
          type="text"
          value={hero}
          onChange={(e) => {
            setHero(e.target.value);
            writeStoredName(e.target.value);
          }}
          placeholder="Your name in the story (optional)"
          aria-label="Your name in the story"
          className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground/70"
        />
      </label>

      {/* Feeling chips — the jargon-free door for the fully undecided. */}
      <div className="flex flex-wrap gap-2">
        {FEELINGS.map((f) => (
          <button
            key={f.label}
            type="button"
            disabled={busy}
            onClick={() => launch(starterById(f.starterId), `feeling:${f.label}`)}
            className={cn(
              "inline-flex min-h-9 items-center gap-1.5 rounded-full border border-border/60 px-3.5 py-2 text-xs font-medium transition-colors",
              "hover:border-primary/60 hover:bg-primary/5 disabled:opacity-50",
            )}
          >
            <span aria-hidden>{f.emoji}</span>
            {f.label}
          </button>
        ))}
      </div>

      {/* The starter gallery — one tap per card. */}
      <div className="flex flex-col gap-2.5">
        {feed.map((s) => {
          const busy = busyId === s.id;
          return (
            <button
              key={s.id}
              type="button"
              disabled={busy}
              aria-label={`Make my version of ${s.title}, a ${s.genre} drama`}
              onClick={() => launch(s, s.id)}
              className={cn(
                "group relative flex items-stretch gap-3 overflow-hidden rounded-xl border border-border/60 bg-card p-2 text-left transition-all",
                "hover:-translate-y-px hover:border-primary/60 hover:shadow-sm",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary",
                "disabled:pointer-events-none disabled:opacity-60",
              )}
            >
              {/* poster thumb */}
              <div
                className="relative flex w-[58px] shrink-0 items-end justify-center overflow-hidden rounded-lg"
                style={{ background: POSTER[s.genre] || "linear-gradient(150deg,#2a2030,#4a3550)" }}
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
                <span className="w-fit rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary">
                  {s.genre}
                </span>
                <div className="truncate text-sm font-semibold leading-tight tracking-tight">
                  {s.title}
                </div>
                <div className="line-clamp-2 text-xs text-muted-foreground">{s.pitch}</div>
                <div className="mt-0.5 inline-flex items-center gap-1 text-xs font-medium text-primary">
                  {busyId === s.id ? "Starting…" : ctaLabel(hero)}
                  {busyId !== s.id && (
                    <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" aria-hidden />
                  )}
                </div>
              </div>
            </button>
          );
        })}
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
