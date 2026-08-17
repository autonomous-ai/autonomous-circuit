import { useEffect, useState } from "react";
import { Loader2, Ruler } from "lucide-react";

import { cn } from "@/ui/utils";
import { formatWidth } from "./netWidth.js";

/**
 * How wide this net is, how wide it may be, and a way to change it.
 *
 * The EE review's finding 4 asked for the power rails at 0.5–1.0mm instead of
 * 0.2mm, and the honest answer on the board he was reviewing is *"one of them
 * can, one of them cannot"*: V5 can take 1.1mm, and V3_3 cannot exceed
 * **0.4000mm** because a track leaving the RP2040's 0.400mm-pitch pads is
 * `2 × (0.400 − 0.100 − 0.100)` wide and no wider. Trying it anyway cost
 * harness-puck 33 blocking findings once and terminal-keyboard 2 another time.
 *
 * So the ceiling is measured and shown *before* the box that sets the width,
 * with the pin that holds it named. The row asks for the measurement only when
 * a net is selected — it costs seconds, and a person is looking at one net.
 *
 * The width is a *declaration*, not copper: it reaches the router on the next
 * build, so the row says "rebuild to apply" rather than pretending the board
 * changed. That is the same honesty the verdict chip owes a pending turn.
 */
export default function NetWidthRow({ netName, widths, editor, onMeasure }) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const row = widths?.byNet?.get(netName) || null;
  const busy = Boolean(widths?.busy);

  // Ask once per net. A different net is a different question; the same net
  // twice is the same answer, and it costs seconds.
  useEffect(() => {
    setDraft("");
    setError("");
    if (netName) onMeasure?.(netName);
  }, [netName, onMeasure]);

  const declared = row?.declared_mm ?? null;
  const ceiling = row?.ceiling_mm ?? null;
  const floor = widths?.powerFloorMm ?? null;
  const wantsMore = floor !== null && row?.narrowest_mm !== null && row?.narrowest_mm < floor;
  const ceilingBelowFloor = floor !== null && ceiling !== null && ceiling + 1e-9 < floor;

  const apply = async (value) => {
    setError("");
    const result = await editor?.setNetWidth?.(netName, value);
    if (result && result.ok === false) setError(result.reason || "that width could not be written");
  };

  return (
    <div className="py-[3px]" data-slot="net-width">
      <div className="flex items-baseline gap-2">
        <span className="w-20 shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground/70">Width</span>
        <span className="font-mono text-[12px] text-foreground">
          {row?.narrowest_mm != null ? `${row.narrowest_mm}mm` : "—"}
          <span className="ml-1 text-[10px] text-muted-foreground/70">as routed</span>
        </span>
        {declared != null ? (
          <span className="font-mono text-[11px] text-muted-foreground" data-slot="net-width-declared">
            declared {formatWidth(declared)}
          </span>
        ) : null}
      </div>

      <div className="ml-[88px] mt-0.5 flex items-center gap-1.5 text-[10px] leading-[14px]">
        {busy && !row ? (
          <>
            <Loader2 className="size-3 animate-spin" aria-hidden />
            <span className="text-muted-foreground">measuring what this net can take…</span>
          </>
        ) : row ? (
          <>
            <Ruler className="size-3 opacity-60" aria-hidden />
            <span
              data-slot="net-width-ceiling"
              className={cn(ceilingBelowFloor ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground")}
            >
              can take {row.ceiling_capped ? `${row.ceiling_mm}mm+` : `${row.ceiling_mm}mm`}
              {row.ceiling_at ? ` — held by ${row.ceiling_at}` : ""}
              {ceilingBelowFloor ? `, under the ${floor}mm power floor` : ""}
            </span>
          </>
        ) : (
          <span className="text-muted-foreground/70">
            {widths?.reason || "the ceiling has not been measured"}
          </span>
        )}
      </div>

      <div className="ml-[88px] mt-1 flex items-center gap-1">
        <input
          type="text"
          inputMode="decimal"
          spellCheck={false}
          value={draft}
          placeholder={ceiling != null ? String(ceiling) : "mm"}
          aria-label="Trace width"
          data-slot="net-width-input"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              apply(Number(draft));
            }
          }}
          className="w-20 rounded border border-border/60 bg-background/60 px-1.5 py-0.5 font-mono text-[11px] outline-none focus:border-primary/60"
        />
        <button
          type="button"
          data-slot="net-width-apply"
          onClick={() => apply(Number(draft))}
          disabled={!draft.trim() || Boolean(editor?.busy)}
          className="rounded border border-primary/50 bg-primary/15 px-1.5 py-0.5 text-[11px] font-medium text-foreground hover:bg-primary/25 disabled:opacity-40"
        >
          Set
        </button>
        {declared != null ? (
          <button
            type="button"
            data-slot="net-width-clear"
            onClick={() => apply(null)}
            disabled={Boolean(editor?.busy)}
            className="rounded border border-border/60 px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            Clear
          </button>
        ) : null}
        {/* Ceiling first, floor second: an engineer typing 0.5 into a net that
            tops out at 0.4 is the exact mistake this row exists to prevent. */}
        {ceiling != null && Number(draft) > ceiling + 1e-9 ? (
          <span data-slot="net-width-over" className="text-[10px] text-amber-600 dark:text-amber-400">
            over the {ceiling}mm this net can take
          </span>
        ) : null}
      </div>

      {error ? (
        <p data-slot="net-width-error" className="ml-[88px] mt-0.5 text-[10px] leading-[14px] text-destructive">
          {error}
        </p>
      ) : declared != null ? (
        <p data-slot="net-width-note" className="ml-[88px] mt-0.5 text-[10px] leading-[14px] text-muted-foreground">
          A width is a declaration the router reads on the next build — the copper on screen has not moved.
        </p>
      ) : wantsMore ? (
        <p className="ml-[88px] mt-0.5 text-[10px] leading-[14px] text-muted-foreground/80">
          The fab profile wants {floor}mm on a power net.
        </p>
      ) : null}
    </div>
  );
}
