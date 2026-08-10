import { useEffect, useState } from "react";
import { CircleAlert, Download, Loader2 } from "lucide-react";
import { cn } from "@/ui/utils";
import { triggerUrlDownload } from "@/ui/download.js";
import Markdown from "@/components/chat/Markdown.jsx";
import { blockingWarnings } from "./boardData.js";

/**
 * The Fab tab: the order-ready packet. When the sidecar says `fab.ready`,
 * download buttons for the packet members (gerbers.zip / bom.csv / cpl.csv)
 * plus the rendered ORDER.md walkthrough and the estimated cost line. When
 * the packet is NOT fab-ready (`fab.ready === false` or errors outstanding),
 * the blocking findings list renders instead of downloads — never hand the
 * user gerbers the pipeline wouldn't ship.
 *
 * @param {{
 *   stem?: string,                    // board stem, used in download filenames
 *   artifact?: object|null,           // catalog entry.artifact (URLs carry ?v=)
 *   sidecar?: object|null,            // parsed .board.json
 *   className?: string,
 * }} props
 */
export default function FabPacketCard({ stem = "board", artifact = null, sidecar = null, className }) {
  const orderUrl = String(artifact?.orderUrl || "");
  const [orderMd, setOrderMd] = useState("");

  useEffect(() => {
    if (!orderUrl) {
      setOrderMd("");
      return undefined;
    }
    let cancelled = false;
    fetch(orderUrl)
      .then((response) => (response.ok ? response.text() : ""))
      .then((text) => {
        if (!cancelled) setOrderMd(text);
      })
      .catch(() => {
        if (!cancelled) setOrderMd("");
      });
    return () => {
      cancelled = true;
    };
  }, [orderUrl]);

  if (!sidecar) {
    return (
      <div className={cn("grid min-h-0 flex-1 place-items-center", className)}>
        {artifact?.metadataUrl ? (
          <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden />
        ) : (
          <p className="max-w-xs px-6 text-center text-sm leading-6 text-muted-foreground">
            The fab packet lands once the board builds clean.
          </p>
        )}
      </div>
    );
  }

  const fab = sidecar.fab || {};
  const ready = fab.ready === true;
  const blockers = blockingWarnings(sidecar);
  const costUsd = Number(sidecar.bom?.estimatedCostUsd);

  const downloads = [
    { label: "Gerbers", url: artifact?.gerbersUrl, filename: `${stem}-gerbers.zip` },
    { label: "BOM", url: artifact?.bomUrl, filename: `${stem}-bom.csv` },
    { label: "CPL", url: artifact?.cplUrl, filename: `${stem}-cpl.csv` },
  ].filter((d) => d.url);

  return (
    <div data-slot="fab-packet" className={cn("scrollbar-thin min-h-0 flex-1 overflow-y-auto", className)}>
      <div className="mx-auto flex max-w-2xl flex-col gap-4 p-4">
        {/* Status + cost line. */}
        <div
          className={cn(
            "flex items-center gap-2.5 rounded-xl border px-4 py-3",
            ready
              ? "border-emerald-500/40 bg-emerald-500/5"
              : "border-amber-500/40 bg-amber-500/5",
          )}
        >
          <span
            className={cn(
              "size-2 shrink-0 rounded-full",
              ready ? "bg-emerald-500" : "bg-amber-400",
            )}
            aria-hidden
          />
          <div className="flex min-w-0 flex-col">
            <span className="text-sm font-medium text-foreground">
              {ready ? "Fab-ready" : "Not fab-ready yet"}
              {fab.profile ? (
                <span className="ml-2 font-mono text-[11px] uppercase text-muted-foreground">
                  {fab.profile}{fab.assembly ? " · assembly" : " · bare PCB"}
                </span>
              ) : null}
            </span>
            {Number.isFinite(costUsd) && costUsd > 0 ? (
              <span data-slot="fab-cost" className="text-xs text-muted-foreground">
                Estimated parts cost ${costUsd.toFixed(2)}
              </span>
            ) : null}
          </div>
        </div>

        {ready ? (
          <>
            {/* Packet downloads — the asset route serves them; ?v= verbatim. */}
            <div className="flex flex-wrap gap-2" data-slot="fab-downloads">
              {downloads.map((d) => (
                <button
                  key={d.label}
                  type="button"
                  onClick={() => {
                    try {
                      triggerUrlDownload(d.url, { filename: d.filename });
                    } catch {
                      /* blocked download — the button stays usable */
                    }
                  }}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 bg-card/60 px-3 py-2 text-sm font-medium text-foreground transition-colors hover:border-primary/60 hover:bg-accent"
                >
                  <Download className="size-3.5" aria-hidden />
                  {d.label}
                </button>
              ))}
            </div>

            {/* ORDER.md — the exact-clicks walkthrough, rendered. */}
            {orderMd ? (
              <div data-slot="fab-order" className="rounded-xl border border-border/60 bg-card/40 p-4">
                <Markdown source={orderMd} />
              </div>
            ) : null}
          </>
        ) : (
          <div data-slot="fab-blockers" className="flex flex-col gap-2">
            <p className="text-sm text-muted-foreground">
              These findings block the packet — fix them in chat (click a chip
              below the board, or just describe the fix):
            </p>
            {blockers.length ? (
              blockers.map((warning, index) => (
                <div
                  key={`${warning.part}-${index}`}
                  className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2"
                >
                  <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-destructive" aria-hidden />
                  <div className="min-w-0">
                    <p className="font-mono text-[12px] text-foreground">
                      {warning.part || "board"}{" "}
                      <span className="text-muted-foreground">({warning.kind})</span>
                    </p>
                    {warning.detail ? (
                      <p className="text-xs leading-5 text-muted-foreground">{warning.detail}</p>
                    ) : null}
                  </div>
                </div>
              ))
            ) : (
              <p className="text-xs text-muted-foreground">
                No blocking findings recorded — rebuild the board to refresh the
                packet{fab.gerberSource === "tscircuit" ? " (gerbers need kicad-cli to verify)" : ""}.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
