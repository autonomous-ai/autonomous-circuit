import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Cpu, ExternalLink } from "lucide-react";
import { cn } from "@/ui/utils";
import { lcscUrl, normalizeParts } from "./boardData.js";

/**
 * Collapsible parts panel, right of the board rail — the donor CastPanel with
 * parts.json (the locked BOM identities, owned wholly by parts-book) in place
 * of the series bible. When the catalog surfaces parts.json, fetch it (URL
 * used verbatim — it carries the ?v= cache-bust) and render one card per part:
 * id, mfr + package, LCSC link, basic/extended badge, stock and unit price.
 * Without a lock yet, an empty state explains what will appear.
 *
 * @param {{
 *   partsEntry?: {url?: string}|null,
 *   open: boolean,
 *   onToggle: () => void,
 * }} props
 */
export default function PartsPanel({ partsEntry, open, onToggle }) {
  const url = String(partsEntry?.url || "");
  const [parts, setParts] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!url) {
      setParts([]);
      setError("");
      return undefined;
    }
    let cancelled = false;
    // NEVER strip the query — ?v= is the cache-bust; a rewritten lock gets a
    // new URL and this effect re-fetches on it.
    fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(`parts lock read failed (${response.status})`);
        return response.json();
      })
      .then((data) => {
        if (cancelled) return;
        setParts(normalizeParts(data));
        setError("");
      })
      .catch((err) => {
        if (cancelled) return;
        setParts([]);
        setError(err instanceof Error ? err.message : "Could not read parts.json");
      });
    return () => {
      cancelled = true;
    };
  }, [url]);

  if (!open) {
    return (
      <button
        type="button"
        onClick={onToggle}
        title="Show parts"
        aria-label="Show parts"
        data-slot="parts-panel-toggle"
        className="flex w-9 shrink-0 flex-col items-center gap-2 border-r border-border/60 py-3 text-muted-foreground transition-colors hover:text-foreground"
      >
        <Cpu className="size-4" aria-hidden />
        <ChevronRight className="size-3.5" aria-hidden />
      </button>
    );
  }

  return (
    <aside
      data-slot="parts-panel"
      className="flex w-60 shrink-0 flex-col border-r border-border/60"
    >
      <header className="flex h-9 shrink-0 items-center gap-2 border-b border-border/60 px-3">
        <Cpu className="size-3.5 text-muted-foreground" aria-hidden />
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Parts
        </span>
        <button
          type="button"
          onClick={onToggle}
          title="Hide parts"
          aria-label="Hide parts"
          className="ml-auto grid size-6 place-items-center rounded text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <ChevronLeft className="size-3.5" aria-hidden />
        </button>
      </header>

      <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto p-2">
        {!url || (!parts.length && !error) ? (
          <p
            data-slot="parts-panel-empty"
            className="px-2 py-6 text-center text-xs leading-5 text-muted-foreground"
          >
            Parts appear once the BOM is locked in parts.json.
          </p>
        ) : error ? (
          <p className="px-2 py-6 text-center text-xs text-destructive">{error}</p>
        ) : (
          <div className="flex flex-col gap-2">
            {parts.map((part) => {
              const link = lcscUrl(part.lcsc);
              return (
                <div
                  key={part.id}
                  data-slot="part-card"
                  className="flex flex-col gap-1 rounded-lg border border-border/60 bg-card/60 p-2.5"
                >
                  <div className="flex items-center gap-1.5">
                    <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                      {part.id}
                    </p>
                    <span
                      className={cn(
                        "shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                        part.basic
                          ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                          : "bg-amber-500/10 text-amber-600 dark:text-amber-400",
                      )}
                    >
                      {part.basic ? "basic" : "extended"}
                    </span>
                  </div>
                  {part.mfr || part.pkg ? (
                    <p className="truncate text-[11px] leading-4 text-muted-foreground">
                      {[part.mfr, part.pkg].filter(Boolean).join(" · ")}
                    </p>
                  ) : null}
                  <div className="flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
                    {link ? (
                      <a
                        href={link}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="inline-flex items-center gap-0.5 text-primary underline-offset-2 hover:underline"
                      >
                        {part.lcsc}
                        <ExternalLink className="size-2.5" aria-hidden />
                      </a>
                    ) : (
                      <span>{part.lcsc || "no LCSC #"}</span>
                    )}
                    {part.unitPriceUsd != null ? <span>${part.unitPriceUsd.toFixed(2)}</span> : null}
                    {part.stock != null ? <span>{part.stock.toLocaleString()} in stock</span> : null}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </aside>
  );
}
