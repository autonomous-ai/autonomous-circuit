import { useMemo, useState } from "react";
import { Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/ui/utils";
import { startTurn, useChatStore } from "@/store/chat";
import { screenOptions } from "@/lib/catalogBoundary.js";
import { DELEGATE_ANSWER } from "./questionFence.js";

/**
 * Renders a set of preference questions the model asked during planning
 * (parsed from a `circuit-questions` fenced block) as clickable option chips.
 * Submitting sends the formatted answers back as a normal turn — the backend
 * stays in plan mode (session resume), so planning continues.
 *
 * @param {{ questions: Array<{question:string, header?:string, multiSelect?:boolean,
 *   options: Array<{label:string, description?:string}>}> }} props
 */
export default function QuestionCard({ questions, dropped = 0 }) {
  const turnInProgress = useChatStore((s) => s.turnInProgress);
  // selected: Map question-index -> Set(labels)
  const [selected, setSelected] = useState(() => ({}));
  const [submitted, setSubmitted] = useState(false);

  // Every option the model wrote, screened against what the library can
  // actually build. The questions are a model's prose, so an option naming a
  // radio, a battery or a light sensor is a normal event — and picking one is
  // a promise the build breaks minutes later. Screened here, once, before
  // anything is clickable.
  const list = useMemo(
    () =>
      (Array.isArray(questions) ? questions : []).map((q) => ({
        ...q,
        ...screenOptions(q?.options),
      })),
    [questions],
  );

  const toggle = (qi, label, multi) => {
    setSelected((cur) => {
      const prev = cur[qi] || [];
      let next;
      if (multi) {
        next = prev.includes(label)
          ? prev.filter((l) => l !== label)
          : [...prev, label];
      } else {
        next = prev.includes(label) && prev.length === 1 ? [] : [label];
      }
      return { ...cur, [qi]: next };
    });
  };

  const allAnswered = useMemo(
    () => list.length > 0 && list.every((_, qi) => (selected[qi] || []).length > 0),
    [list, selected],
  );

  const handleSend = async () => {
    if (!allAnswered || turnInProgress || submitted) return;
    const lines = list.map((q, qi) => {
      const picks = (selected[qi] || []).join(", ");
      return `- ${q.header || q.question}: ${picks}`;
    });
    const message = `My answers:\n${lines.join("\n")}`;
    const res = await startTurn(message);
    if (res) setSubmitted(true);
  };

  // One-click delegate: skip every preference and let Circuit pick the best.
  const handleDelegate = async () => {
    if (turnInProgress || submitted) return;
    const res = await startTurn(DELEGATE_ANSWER, { echoAs: "You decide — pick the best of each." });
    if (res) setSubmitted(true);
  };

  if (!list.length) return null;

  return (
    <div
      data-slot="chat-questions"
      className="rounded-2xl border border-border bg-muted p-3 text-sm text-foreground shadow-(--ui-shadow-soft)"
    >
      <div className="flex flex-col gap-3.5">
        {list.map((q, qi) => {
          const multi = !!q.multiSelect;
          const picks = selected[qi] || [];
          return (
            <div key={qi} data-slot="chat-question" className="flex flex-col gap-2.5">
              <p className="text-sm font-semibold leading-tight text-foreground">{q.question}</p>
              <div className="flex flex-wrap gap-2">
                {(q.options || []).map((opt) => {
                  const active = picks.includes(opt.label);
                  const blocked = opt.blockedBy || null;
                  return (
                    <button
                      key={opt.label}
                      type="button"
                      disabled={submitted || !!blocked}
                      onClick={() => toggle(qi, opt.label, multi)}
                      title={blocked ? blocked.why : opt.description || ""}
                      data-slot="chat-question-option"
                      data-active={active ? "true" : "false"}
                      data-blocked={blocked ? blocked.id : undefined}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-full border px-3 py-2 text-[13px] font-medium transition-colors",
                        active
                          ? "border-emerald-500/55 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                          : "border-border bg-foreground/[0.04] text-muted-foreground hover:border-foreground/20 hover:bg-foreground/[0.08] hover:text-foreground",
                        submitted && !active && "opacity-55",
                        blocked &&
                          "cursor-not-allowed border-dashed bg-transparent text-muted-foreground/60 hover:border-border hover:bg-transparent hover:text-muted-foreground/60",
                      )}
                    >
                      <span className={cn(blocked && "line-through decoration-1")}>{opt.label}</span>
                      {/* The reason is one line below, named per option. The
                          chip only has to say it is off the menu. */}
                      {blocked ? (
                        <span className="text-[11px] font-normal text-muted-foreground/70">
                          not yet
                        </span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
              {/* An option we cannot build is crossed off, not hidden: the
                  reader sees their idea was understood, why it is not on the
                  menu, and the nearest thing that is. A dead end is a defect;
                  every line here ends somewhere. */}
              {q.notes?.length ? (
                <div data-slot="chat-question-blocked" className="flex flex-col gap-1">
                  {q.notes.map((note) => (
                    <p key={note.id} className="text-[11px] leading-4 text-muted-foreground">
                      {note.text}
                    </p>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
      {/* Some of what the model asked did not arrive intact. Say so: a
          silently shortened list looks like the whole question set, and the
          user has no way to know an answer they cared about was dropped. */}
      {dropped > 0 ? (
        <p data-slot="chat-questions-dropped" className="mt-3 text-[11px] leading-4 text-muted-foreground">
          {dropped === 1 ? "One more question" : `${dropped} more questions`} came through broken and{" "}
          {dropped === 1 ? "is" : "are"} not shown. Answer what you can, or let Circuit choose.
        </p>
      ) : null}

      {/* Wraps. At the chat panel's default width the two buttons need 330px
          inside a 237px card, and `justify-between` has no wrap: the primary
          action — "Send answers" — was clipped 93px off the right edge, so an
          answered card looked like it had no way to send. `ml-auto` keeps the
          two-on-one-line layout wherever it fits. */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleDelegate}
          disabled={turnInProgress || submitted}
          data-slot="chat-questions-delegate"
          className="h-9 rounded-lg border-border bg-foreground/[0.02] px-4 text-muted-foreground hover:bg-foreground/[0.07] hover:text-foreground disabled:opacity-55"
        >
          Build the best (you decide)
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={handleSend}
          disabled={!allAnswered || turnInProgress || submitted}
          data-slot="chat-questions-send"
          // Fixed width so the label swap can't reflow the button (the swap
          // used to leave a "Send a…"/"Sent" ghost). Sent reads as a quiet,
          // deliberate green confirmation rather than a half-disabled button.
          className={cn(
            // The disabled state used to have no border and no fill, so the
            // card's primary action read as a caption sitting next to the
            // outlined "Build the best" pill — a first-timer looking at an
            // unanswered card could not see there was a Send button at all.
            // It is a button waiting for an answer, so it looks like one.
            "ml-auto min-w-20 rounded-lg bg-emerald-600 px-4 font-medium text-white hover:bg-emerald-500 disabled:border disabled:border-border disabled:bg-muted disabled:text-muted-foreground disabled:opacity-100",
            submitted &&
              "bg-emerald-500/15 text-emerald-700 ring-1 ring-emerald-500/30 hover:bg-emerald-500/15 disabled:bg-emerald-500/15 disabled:text-emerald-700 dark:text-emerald-300 dark:disabled:text-emerald-300",
          )}
        >
          {submitted ? (
            <>
              <Check className="size-3.5" aria-hidden />
              Sent
            </>
          ) : (
            "Send answers"
          )}
        </Button>
      </div>
    </div>
  );
}
