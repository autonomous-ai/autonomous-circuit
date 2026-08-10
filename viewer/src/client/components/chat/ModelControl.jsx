"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronDown, Cpu, Loader2 } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/ui/utils";
import { transport } from "@/lib/transport.ts";
import {
  DEFAULT_MODEL,
  labelForModel,
  MODEL_CHOICES,
} from "./modelChoices.js";

// v1 is Create-only and local-only: the switcher offers only the models the
// user's own Claude Code runs. Hosted/proxy tiers (and their upgrade CTA)
// return with the network, post-v1.
const LOCAL_CHOICES = MODEL_CHOICES.filter((choice) => !choice.requiresPandaSignIn);

/**
 * Compact pill in the chat composer footer showing which Claude model the next
 * turn will use, with a dropdown to switch between the offered models. The
 * choice is persisted in AppSettings (`app_set_model`); the driver reads it
 * fresh at each turn spawn, so a switch takes effect on the next turn — no need
 * to block switching mid-turn.
 */
export default function ModelControl({ className }) {
  // Active model value; null until the first settings read resolves. Falls back
  // to the default for display when unset/unrecognized.
  const [model, setModel] = useState(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const settings = await transport.app_settings_read();
      setModel(settings?.model ?? DEFAULT_MODEL);
    } catch {
      // Leave the current display in place; the driver still resolves its own
      // default when unset.
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // `model` is the persisted selection id; unknown/legacy ids display as the
  // default.
  const active = LOCAL_CHOICES.some((choice) => choice.id === model)
    ? model
    : DEFAULT_MODEL;

  const pick = useCallback(
    async (id) => {
      if (busy || id === active) return;
      setBusy(true);
      try {
        const next = await transport.app_set_model(id);
        setModel(next?.model ?? id);
      } catch {
        // Leave the prior selection in place on failure.
      } finally {
        setBusy(false);
      }
    },
    [busy, active],
  );

  return (
    <DropdownMenu
      onOpenChange={(open) => {
        if (open) {
          void refresh();
        }
      }}
    >
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={cn(
            "inline-flex items-center gap-1 rounded-full border border-border/60 px-2 py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-background/55 hover:text-foreground",
            className,
          )}
          data-testid="model-trigger"
          title="Model"
        >
          {busy ? (
            <Loader2 className="size-3 animate-spin" aria-hidden />
          ) : (
            <Cpu className="size-3" aria-hidden />
          )}
          {labelForModel(active)}
          <ChevronDown className="size-3 opacity-60" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="cad-solid-popover min-w-40">
        <DropdownMenuLabel className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Your Claude Code
        </DropdownMenuLabel>
        {LOCAL_CHOICES.map((choice) => (
          <DropdownMenuItem
            key={choice.id}
            onSelect={() => void pick(choice.id)}
            data-testid={`model-option-${choice.id}`}
            className="justify-between gap-3"
          >
            <span>{choice.label}</span>
            {choice.id === active ? <Check className="size-3.5" aria-hidden /> : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
