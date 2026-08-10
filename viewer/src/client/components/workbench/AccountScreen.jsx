"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, Check, HardDrive } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { transport } from "@/lib/transport.ts";
import { cn } from "@/ui/utils";
import ThemeSetting from "@/components/workbench/ThemeSetting.jsx";
import { useProjectsStore } from "@/store/projects.ts";
import { DEFAULT_MODEL, MODEL_CHOICES } from "@/components/chat/modelChoices.js";

// v1 is Create-only and local-only: the only selectable models are the ones
// the user's own Claude Code runs (no hosted/proxy tiers, no accounts).
const LOCAL_MODEL_CHOICES = MODEL_CHOICES.filter(
  (choice) => !choice.requiresPandaSignIn,
);

/**
 * Full-screen settings overlay. v1 has no account and no publish — this
 * screen is the settings shell only: Appearance (theme), Model
 * (app_set_model), and Autopilot (autoBuild) via app_settings_read/write,
 * plus a "Local workspace" note. Opened from the workspace header's user
 * card. (The donor's render-provider picker is gone: the board pipeline is
 * local tooling, no hosted providers.)
 */
export default function AccountScreen({ open, onOpenChange }) {
  const projectCount = useProjectsStore((state) => state.projects.length);
  // The full AppSettings snapshot, re-read on open; writes send the whole
  // object back so unrelated fields survive the round-trip.
  const [settings, setSettings] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [busyModel, setBusyModel] = useState(false);
  const [busyAutopilot, setBusyAutopilot] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setSettings(await transport.app_settings_read());
      setLoadError("");
    } catch (err) {
      setSettings(null);
      setLoadError(
        err && typeof err === "object" && "message" in err
          ? String(err.message || "Could not read settings")
          : String(err || "Could not read settings"),
      );
    }
  }, []);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  const activeModel = (() => {
    const stored = settings?.model;
    return LOCAL_MODEL_CHOICES.some((choice) => choice.id === stored)
      ? stored
      : DEFAULT_MODEL;
  })();

  const pickModel = useCallback(
    async (id) => {
      if (busyModel || id === activeModel) return;
      setBusyModel(true);
      try {
        const next = await transport.app_set_model(id);
        setSettings((prev) => ({ ...(prev || {}), ...(next || {}), model: next?.model ?? id }));
      } catch {
        // Leave the prior selection in place on failure.
      } finally {
        setBusyModel(false);
      }
    },
    [busyModel, activeModel],
  );

  // Autopilot defaults ON (matches the driver's default when unset).
  const autopilot = settings?.autoBuild !== false;

  const toggleAutopilot = useCallback(
    async (nextValue) => {
      if (busyAutopilot || !settings) return;
      setBusyAutopilot(true);
      const next = { ...settings, autoBuild: nextValue };
      try {
        await transport.app_settings_write(next);
        setSettings(next);
      } catch {
        // Keep showing the persisted value on failure.
      } finally {
        setBusyAutopilot(false);
      }
    },
    [busyAutopilot, settings],
  );

  if (!open) return null;

  return (
    <div className="flex h-full flex-col bg-background/80 backdrop-blur-sm">
      <div className="flex items-center gap-3 border-b border-border px-6 py-4">
        <button
          type="button"
          onClick={() => onOpenChange?.(false)}
          className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <ArrowLeft className="size-4" aria-hidden="true" />
          Back
        </button>
        <h1 className="text-lg font-semibold text-foreground">Settings</h1>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="mx-auto max-w-xl p-6">
          {/* Local workspace note — v1 keeps everything on this machine. */}
          <div className="mb-6 flex items-center gap-3 rounded-xl border border-border bg-card/50 px-4 py-3">
            <HardDrive className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <div className="flex flex-col">
              <span className="text-sm font-medium text-foreground">
                Local workspace
                <span className="ml-2 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Local
                </span>
              </span>
              <span className="text-xs text-muted-foreground">
                {projectCount} {projectCount === 1 ? "project" : "projects"} on this
                machine. Sharing arrives with the network — later.
              </span>
            </div>
          </div>

          {loadError ? (
            <p className="mb-4 text-sm text-destructive" role="alert">
              {loadError}
            </p>
          ) : null}

          {/* Appearance */}
          <section className="mb-6">
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Appearance
            </h2>
            <ThemeSetting />
          </section>

          {/* Model */}
          <section className="mb-6">
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Model
            </h2>
            <div className="flex flex-col gap-1 rounded-xl border border-border bg-card/50 p-2">
              {LOCAL_MODEL_CHOICES.map((choice) => (
                <button
                  key={choice.id}
                  type="button"
                  onClick={() => void pickModel(choice.id)}
                  disabled={busyModel}
                  data-testid={`settings-model-${choice.id}`}
                  className={cn(
                    "flex items-center justify-between rounded-lg px-3 py-2 text-sm transition-colors",
                    choice.id === activeModel
                      ? "bg-accent text-foreground"
                      : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                  )}
                >
                  <span>{choice.label}</span>
                  {choice.id === activeModel ? (
                    <Check className="size-4 text-emerald-500" aria-hidden="true" />
                  ) : null}
                </button>
              ))}
            </div>
            <p className="mt-1.5 text-xs text-muted-foreground">
              Runs through your own Claude Code sign-in. Takes effect on the
              next turn.
            </p>
          </section>

          {/* Autopilot */}
          <section className="mb-6">
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Autopilot
            </h2>
            <label className="flex items-center justify-between gap-4 rounded-xl border border-border bg-card/50 px-4 py-3">
              <span className="flex flex-col">
                <span className="text-sm font-medium text-foreground">
                  Build without approval
                </span>
                <span className="text-xs text-muted-foreground">
                  On: after your answers, boards build and review unattended.
                  Off: every plan waits for your approval first.
                </span>
              </span>
              <Switch
                checked={autopilot}
                disabled={busyAutopilot || !settings}
                onCheckedChange={(value) => void toggleAutopilot(Boolean(value))}
                data-testid="settings-autopilot"
              />
            </label>
          </section>

          <div className="flex justify-end">
            <Button variant="outline" onClick={() => onOpenChange?.(false)}>
              Done
            </Button>
          </div>
        </div>
      </ScrollArea>
    </div>
  );
}
