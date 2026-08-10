import { useEffect, useMemo, useState } from "react";
import { ExternalLink, Loader2 } from "lucide-react";
import { cn } from "@/ui/utils";
import { lcscNumber, lcscUrl, normalizeParts, parseBomCsv, partsByLcsc } from "./boardData.js";

/**
 * The BOM tab: the fab packet's bom.csv (JLCPCB columns —
 * `Comment,Designator,Footprint,LCSC Part #`) rendered as a table, enriched
 * from parts.json when the catalog surfaces it (basic/extended badge, unit
 * price). Both URLs carry the ?v= cache-bust, so a rebuilt packet refetches on
 * its own. Before a fab packet exists there is no bom.csv — the empty state
 * says what will appear.
 *
 * @param {{
 *   bomUrl?: string,       // entry.artifact.bomUrl (absent pre-fab)
 *   partsUrl?: string,     // parts.json catalog URL (absent pre-lock)
 *   className?: string,
 * }} props
 */
export default function BomTable({ bomUrl = "", partsUrl = "", className }) {
  const [rows, setRows] = useState(null); // null = loading, [] = loaded empty
  const [error, setError] = useState("");
  const [parts, setParts] = useState([]);

  useEffect(() => {
    if (!bomUrl) {
      setRows(null);
      setError("");
      return undefined;
    }
    let cancelled = false;
    fetch(bomUrl)
      .then((response) => {
        if (!response.ok) throw new Error(`bom.csv read failed (${response.status})`);
        return response.text();
      })
      .then((text) => {
        if (cancelled) return;
        setRows(parseBomCsv(text));
        setError("");
      })
      .catch((err) => {
        if (cancelled) return;
        setRows([]);
        setError(err instanceof Error ? err.message : "Could not read bom.csv");
      });
    return () => {
      cancelled = true;
    };
  }, [bomUrl]);

  // parts.json enrichment is best-effort — the table renders without it.
  useEffect(() => {
    if (!partsUrl) {
      setParts([]);
      return undefined;
    }
    let cancelled = false;
    fetch(partsUrl)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!cancelled) setParts(data ? normalizeParts(data) : []);
      })
      .catch(() => {
        if (!cancelled) setParts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [partsUrl]);

  const byLcsc = useMemo(() => partsByLcsc(parts), [parts]);

  if (!bomUrl) {
    return (
      <div className={cn("grid min-h-0 flex-1 place-items-center", className)}>
        <p className="max-w-xs px-6 text-center text-sm leading-6 text-muted-foreground">
          The BOM lands with the fab packet — build the board first.
        </p>
      </div>
    );
  }

  if (rows === null) {
    return (
      <div className={cn("grid min-h-0 flex-1 place-items-center", className)}>
        <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden />
      </div>
    );
  }

  if (error) {
    return (
      <div className={cn("grid min-h-0 flex-1 place-items-center", className)}>
        <p className="text-sm text-destructive">{error}</p>
      </div>
    );
  }

  return (
    <div className={cn("scrollbar-thin min-h-0 flex-1 overflow-auto", className)}>
      <table data-slot="bom-table" className="w-full min-w-max border-collapse text-left text-[13px]">
        <thead className="sticky top-0 border-b border-border/60 bg-background">
          <tr>
            {["Designator", "Comment", "Footprint", "LCSC", "Type", "Unit price"].map((label) => (
              <th
                key={label}
                className="px-4 py-2.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground"
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const part = byLcsc.get(lcscNumber(row.lcsc));
            const link = lcscUrl(row.lcsc);
            return (
              <tr key={`${row.designator}-${index}`} className="border-b border-border/40">
                <td className="px-4 py-2.5 font-mono text-foreground">{row.designator}</td>
                <td className="px-4 py-2.5 text-muted-foreground">{row.comment}</td>
                <td className="px-4 py-2.5 font-mono text-[12px] text-muted-foreground">{row.footprint}</td>
                <td className="px-4 py-2.5 font-mono text-[12px]">
                  {link ? (
                    <a
                      href={link}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="inline-flex items-center gap-0.5 text-primary underline-offset-2 hover:underline"
                    >
                      {row.lcsc}
                      <ExternalLink className="size-2.5" aria-hidden />
                    </a>
                  ) : (
                    <span className="text-destructive">missing</span>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  {part ? (
                    <span
                      className={cn(
                        "rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                        part.basic
                          ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                          : "bg-amber-500/10 text-amber-600 dark:text-amber-400",
                      )}
                    >
                      {part.basic ? "basic" : "extended"}
                    </span>
                  ) : (
                    <span className="text-[11px] text-muted-foreground/60">—</span>
                  )}
                </td>
                <td className="px-4 py-2.5 font-mono text-[12px] text-muted-foreground">
                  {part?.unitPriceUsd != null ? `$${part.unitPriceUsd.toFixed(2)}` : "—"}
                </td>
              </tr>
            );
          })}
          {!rows.length ? (
            <tr>
              <td colSpan={6} className="px-4 py-6 text-center text-sm text-muted-foreground">
                bom.csv is empty.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}
