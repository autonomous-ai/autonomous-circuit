import { useCallback, useState } from "react";
import { CircleAlert, CircleCheck, Loader2, Usb, Zap } from "lucide-react";
import { cn } from "@/ui/utils";
import { transport } from "@/lib/transport.ts";

/**
 * The Flash button on the Firmware tab — the ONLY way firmware reaches a board.
 *
 * Two clicks, on purpose. The first asks the server what is on USB and shows
 * exactly which device the recipe would write to (address, vendor/product id,
 * serial). The second writes to that address and nothing else. A board of
 * another family, two boards of this one, or no board at all is a refusal with
 * the reason on screen — the agent never flashes, and the app never guesses,
 * because the board on the desk may be running something else entirely.
 *
 * Enabled only when the board is ready (`fab.ready`) and the agent has written
 * `firmware/flash.json` (the catalog surfaces it as `firmware.flashUrl`).
 *
 * @param {{
 *   projectId: string,
 *   ready: boolean,               // sidecar fab.ready
 *   hasRecipe: boolean,           // artifact.firmware.flashUrl present
 *   className?: string,
 * }} props
 */
const FAMILY_LABEL = { esp32: "ESP32-family", rp2040: "RP2040" };

export default function FirmwareFlashButton({ projectId, ready, hasRecipe, className }) {
  const [state, setState] = useState({ phase: "idle" });

  const look = useCallback(async () => {
    if (!projectId) return;
    setState({ phase: "detecting" });
    try {
      const found = await transport.firmware_detect(projectId);
      setState({ phase: found.decision === "one" ? "confirm" : "refused", found });
    } catch (err) {
      setState({ phase: "error", message: err?.message || String(err) });
    }
  }, [projectId]);

  const write = useCallback(async () => {
    if (state.phase !== "confirm") return;
    const port = state.found.candidates[0].address;
    setState({ phase: "flashing", found: state.found, port });
    try {
      const result = await transport.firmware_flash(projectId, port);
      setState({ phase: "done", result });
    } catch (err) {
      setState({ phase: "error", message: err?.message || String(err) });
    }
  }, [projectId, state]);

  const cancel = useCallback(() => setState({ phase: "idle" }), []);

  const busy = state.phase === "detecting" || state.phase === "flashing";
  const disabled = !ready || !hasRecipe || busy;
  const title = !ready
    ? "The board has to check out first — the pin map is read off finished copper"
    : !hasRecipe
      ? "The agent has not written firmware/flash.json (the recipe: family, fqbn, sketch, build)"
      : "Look at what is on USB, then flash exactly that board";

  return (
    <div className={cn("flex flex-col items-end gap-2", className)} data-slot="firmware-flash">
      <button
        type="button"
        onClick={state.phase === "confirm" ? cancel : look}
        disabled={disabled}
        title={title}
        data-slot="firmware-flash-button"
        className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-50"
      >
        {busy ? <Loader2 className="size-3 animate-spin" aria-hidden /> : <Zap className="size-3" aria-hidden />}
        {state.phase === "flashing" ? "Flashing…" : state.phase === "detecting" ? "Looking…" : state.phase === "confirm" ? "Cancel" : "Flash"}
      </button>

      {state.phase === "confirm" ? (
        <div data-slot="firmware-flash-confirm" className="w-[26rem] max-w-full rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-xs leading-5">
          <p className="flex items-start gap-1.5">
            <Usb className="mt-0.5 size-3.5 shrink-0 text-amber-500" aria-hidden />
            <span>
              {/* The family is the fact (it came from the vendor id); the board name is
                  arduino-cli's guess from a product id many boards share — a C3 SuperMini
                  reads as "Ozobot DRVKit" — so it is shown as a guess, after the fact. */}
              Found an <strong>{FAMILY_LABEL[state.found.family] || state.found.family}</strong> device at{" "}
              <code className="font-mono">{state.found.candidates[0].address}</code>
              {state.found.candidates[0].vid ? ` (vid ${state.found.candidates[0].vid}${state.found.candidates[0].pid ? ` · pid ${state.found.candidates[0].pid}` : ""}${state.found.candidates[0].serial ? ` · ${state.found.candidates[0].serial}` : ""})` : ""}
              {state.found.candidates[0].boards?.length ? `; arduino-cli calls it "${state.found.candidates[0].boards[0]}"` : ""}.
              Write <strong>{state.found.recipe}</strong> to it?
            </span>
          </p>
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" onClick={cancel} className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:text-foreground">
              Cancel
            </button>
            <button
              type="button"
              onClick={write}
              data-slot="firmware-flash-go"
              className="inline-flex items-center gap-1 rounded-md bg-amber-500 px-2.5 py-1 text-xs font-semibold text-black hover:bg-amber-400"
            >
              <Zap className="size-3" aria-hidden />
              Flash this board
            </button>
          </div>
        </div>
      ) : null}

      {state.phase === "refused" ? (
        <p data-slot="firmware-flash-refused" className="flex w-[26rem] max-w-full items-start gap-1.5 text-xs leading-5 text-muted-foreground">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-amber-500" aria-hidden />
          <span>{state.found.hint || "Nothing to flash."}</span>
        </p>
      ) : null}

      {state.phase === "error" ? (
        <p data-slot="firmware-flash-error" className="flex w-[26rem] max-w-full items-start gap-1.5 text-xs leading-5 text-destructive">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>{state.message}</span>
        </p>
      ) : null}

      {state.phase === "done" ? (
        <div data-slot="firmware-flash-result" className="w-[26rem] max-w-full rounded-lg border border-border/60 bg-card/30 p-3 text-xs leading-5">
          <p className="flex items-center gap-1.5 font-medium">
            {state.result.ok ? <CircleCheck className="size-3.5 text-emerald-500" aria-hidden /> : <CircleAlert className="size-3.5 text-destructive" aria-hidden />}
            {state.result.ok ? `Flashed ${state.result.port}` : `Flash failed at ${state.result.steps.at(-1)?.name || "the first step"}`}
          </p>
          <ul className="mt-1 flex flex-col gap-0.5">
            {state.result.steps.map((step) => (
              <li key={step.name} className="flex items-center gap-1.5">
                <span className={cn("size-1.5 rounded-full", step.code === 0 ? "bg-emerald-500" : "bg-destructive")} aria-hidden />
                <span className="font-mono">{step.name}</span>
                <span className="text-muted-foreground">{step.code === 0 ? "ok" : `exit ${step.code}`}</span>
              </li>
            ))}
          </ul>
          {state.result.steps.some((s) => s.code !== 0) ? (
            <pre className="scrollbar-thin mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-muted/50 p-2 font-mono text-[11px]">
              {state.result.steps.find((s) => s.code !== 0)?.output}
            </pre>
          ) : null}
          {state.result.serial ? (
            <div className="mt-2">
              <p className="text-muted-foreground">Board said ({state.result.serial.note}):</p>
              {state.result.serial.text ? (
                <pre data-slot="firmware-flash-serial" className="scrollbar-thin max-h-48 overflow-auto whitespace-pre-wrap rounded bg-muted/50 p-2 font-mono text-[11px]">
                  {state.result.serial.text}
                </pre>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
