"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronDown, Gauge } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/ui/utils";
import { getEffort, setEffort } from "@/store/chat.js";
import { DEFAULT_EFFORT, EFFORT_HINTS, EFFORT_LABELS, EFFORT_LEVELS } from "./effortChoices.js";

/**
 * Reasoning-effort pill, immediately right of the model pill — Vibe's
 * placement (`GenerationEffortPicker` renders on the model picker's row).
 *
 * Vibe only offers it while a bring-your-own Claude subscription is connected,
 * because there it is a paid-tier control. Ours is always live: Circuit runs on
 * the user's own Claude Code, so the effort is theirs to spend either way.
 *
 * The pick takes effect on the next turn (see effortChoices.js for where it
 * goes and why it is not an AppSettings field yet).
 */
export default function EffortControl({ className }) {
  const [effort, setLocal] = useState(DEFAULT_EFFORT);

  // The store holds the live value; localStorage is only its backing.
  useEffect(() => {
    setLocal(getEffort());
  }, []);

  const pick = useCallback((level) => {
    setLocal(setEffort(level));
  }, []);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={cn(
            "inline-flex items-center gap-1 rounded-full border border-border/60 px-2 py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-background/55 hover:text-foreground",
            className,
          )}
          data-testid="effort-trigger"
          title="How hard the next turn should think"
        >
          <Gauge className="size-3" aria-hidden />
          {EFFORT_LABELS[effort]}
          <ChevronDown className="size-3 opacity-60" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="cad-solid-popover min-w-56">
        <DropdownMenuLabel className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Effort
        </DropdownMenuLabel>
        {EFFORT_LEVELS.map((level) => (
          <DropdownMenuItem
            key={level}
            onSelect={() => pick(level)}
            data-testid={`effort-option-${level}`}
            className="flex-col items-start gap-0"
          >
            <span className="flex w-full items-center justify-between gap-3">
              <span>{EFFORT_LABELS[level]}</span>
              {level === effort ? <Check className="size-3.5" aria-hidden /> : null}
            </span>
            <span className="text-[11px] leading-4 text-muted-foreground">{EFFORT_HINTS[level]}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
