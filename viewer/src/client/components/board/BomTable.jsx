import { useEffect, useMemo, useState } from "react";
import { ExternalLink, Loader2 } from "lucide-react";
import { cn } from "@/ui/utils";
import { partPlainName } from "@/lib/plainLanguage.js";
import { lcscNumber, lcscUrl, normalizeParts, parseBomCsv, partsByLcsc } from "./boardData.js";

/**
 * The BOM tab: the fab packet's bom.csv (JLCPCB columns —
 * `Comment,Designator,Footprint,LCSC Part #`) rendered as a table, enriched
 * from parts.json when the catalog surfaces it (basic/extended badge, stock,
 * unit price, extended price — ActiveBOM's columns, ALTIUM-NOTES §8). Both URLs
 * carry the ?v= cache-bust, so a rebuilt packet refetches on its own.
 *
 * The table is a third cross-probe surface: a row is highlighted when its
 * designator is in the current selection, and clicking a row selects that
 * component everywhere.
 *
 * @param {{
 *   bomUrl?: string,       // entry.artifact.bomUrl (absent pre-fab)
 *   partsUrl?: string,     // parts.json catalog URL (absent pre-lock)
 *   index?: object|null,   // buildBoardIndex output, for refdes → component
 *   highlight?: object|null,
 *   onSelect?: (selection: object|null, options?: object) => void,
 *   className?: string,
 * }} props
 */
export default function BomTable({ bomUrl = "", partsUrl = "", index = null, highlight = null, onSelect, className }) {
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

  // What one board's worth of parts costs, summed off the same rows the table
  // renders. Null when nothing is priced — a total that silently skips half
  // the BOM is worse than no total.
  const total = useMemo(() => {
    if (!Array.isArray(rows) || !rows.length) return null;
    let usd = 0;
    let priced = 0;
    let unpriced = 0;
    for (const row of rows) {
      const part = byLcsc.get(lcscNumber(row.lcsc));
      const quantity = row.designator.split(/[,;]/).filter((value) => value.trim()).length || 1;
      if (part?.unitPriceUsd != null) {
        usd += part.unitPriceUsd * quantity;
        priced += 1;
      } else {
        unpriced += 1;
      }
    }
    return priced ? { usd, priced, unpriced } : null;
  }, [rows, byLcsc]);

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
            {/* "What it is" is first after the designator on purpose: every
                other column in a BOM is an identifier, and an identifier is
                not an answer to "what am I buying". */}
            {["Designator", "What it is", "Comment", "Footprint", "LCSC", "Type", "Stock", "Unit price", "Extended"].map((label) => (
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
          {rows.map((row, order) => {
            const part = byLcsc.get(lcscNumber(row.lcsc));
            const link = lcscUrl(row.lcsc);
            const designators = row.designator
              .split(/[,;]/)
              .map((value) => value.trim())
              .filter(Boolean);
            const selected = Boolean(
              highlight?.refdes?.size && designators.some((refdes) => highlight.refdes.has(refdes)),
            );
            const component = index ? index.componentByRefdes.get(designators[0]) : null;
            const quantity = designators.length || 1;
            const extended = part?.unitPriceUsd != null ? part.unitPriceUsd * quantity : null;
            return (
              <tr
                key={`${row.designator}-${order}`}
                data-slot="bom-row"
                data-refdes={designators[0] || ""}
                aria-selected={selected || undefined}
                onClick={() => {
                  if (component) onSelect?.({ kind: "component", key: component.key }, { source: "bom" });
                }}
                className={cn(
                  "border-b border-border/40 transition-colors",
                  component ? "cursor-pointer" : "",
                  selected ? "bg-primary/15" : component ? "hover:bg-accent/40" : "",
                )}
              >
                <td className="px-4 py-2.5 font-mono text-foreground">{row.designator}</td>
                <td className="px-4 py-2.5 text-foreground">
                  {component ? partPlainName(component) : <span className="text-muted-foreground/50">—</span>}
                </td>
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
                <td className="px-4 py-2.5 font-mono text-[12px] tabular-nums text-muted-foreground">
                  {part?.stock != null ? part.stock.toLocaleString() : "—"}
                </td>
                <td className="px-4 py-2.5 font-mono text-[12px] tabular-nums text-muted-foreground">
                  {part?.unitPriceUsd != null ? `$${part.unitPriceUsd.toFixed(4)}` : "—"}
                </td>
                <td className="px-4 py-2.5 font-mono text-[12px] tabular-nums text-muted-foreground">
                  {extended != null ? `$${extended.toFixed(3)}` : "—"}
                </td>
              </tr>
            );
          })}
          {!rows.length ? (
            <tr>
              <td colSpan={9} className="px-4 py-6 text-center text-sm text-muted-foreground">
                bom.csv is empty.
              </td>
            </tr>
          ) : null}
        </tbody>
        {total ? (
          <tfoot className="sticky bottom-0 border-t border-border/60 bg-background">
            <tr data-slot="bom-total">
              <td className="px-4 py-2.5 text-[12px] font-medium text-foreground" colSpan={7}>
                Parts per board
                {total.unpriced ? (
                  <span className="ml-2 text-[11px] font-normal text-muted-foreground">
                    ({total.unpriced} of {rows.length} lines have no checked price, so the real total is higher)
                  </span>
                ) : null}
              </td>
              <td />
              <td className="px-4 py-2.5 font-mono text-[12px] font-medium tabular-nums text-foreground">
                ${total.usd.toFixed(2)}
              </td>
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
}
