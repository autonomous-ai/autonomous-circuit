import { useEffect, useState } from "react";
import { ArrowRight, CircleAlert, Download, ExternalLink, Loader2 } from "lucide-react";
import { cn } from "@/ui/utils";
import { triggerUrlDownload } from "@/ui/download.js";
import Markdown from "@/components/chat/Markdown.jsx";
import { JLCPCB_QUOTE_URL } from "./boardActions.js";
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
export default function FabPacketCard({
  stem = "board",
  artifact = null,
  sidecar = null,
  groups = [],
  onOpenTab,
  className,
}) {
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
            The files a factory needs appear here once the board builds without errors.
          </p>
        )}
      </div>
    );
  }

  const fab = sidecar.fab || {};
  const ready = fab.ready === true;
  const blockers = blockingWarnings(sidecar);
  const blockingGroups = (Array.isArray(groups) ? groups : []).filter((group) => group.blocking);
  const costUsd = Number(sidecar.bom?.estimatedCostUsd);

  // Two audiences, so two groups. The fab needs gerbers/BOM/CPL to build the
  // board; an engineer needs the KiCad project to open, review and edit it in
  // a real tool, the STEP/GLB for mechanical fit, and the enclosure brief for
  // whoever models the case.
  const downloads = [
    { label: "Gerbers", url: artifact?.gerbersUrl, filename: `${stem}-gerbers.zip` },
    { label: "BOM", url: artifact?.bomUrl, filename: `${stem}-bom.csv` },
    { label: "CPL", url: artifact?.cplUrl, filename: `${stem}-cpl.csv` },
  ].filter((d) => d.url);

  const engineeringDownloads = [
    {
      label: "KiCad project",
      url: artifact?.kicadProjectUrl,
      filename: `${stem}-kicad.zip`,
      hint: "schematic + board + project — opens in KiCad 10",
    },
    {
      label: "3D model",
      url: artifact?.glbUrl,
      filename: `${stem}.glb`,
      hint: "board and parts, for mechanical fit",
    },
    {
      label: "Enclosure brief",
      url: artifact?.enclosureUrl,
      filename: `${stem}-enclosure.json`,
      hint: "outline, holes, connector edges",
    },
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
              {/* Same words as the verdict strip on every other tab. Two
                  names for one state ("Fab-ready" here, "Ready to order"
                  there) reads as two different facts. */}
              {ready ? "Ready to order" : "Not ready to order yet"}
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
            {/* The order moment, in the order it happens: get the three files,
                open the quote page, follow the walkthrough. Anything that is
                not one of those three steps is below them. */}
            <div
              data-slot="fab-order-steps"
              className="rounded-xl border border-emerald-500/40 bg-emerald-500/[0.06] p-4"
            >
              <p className="text-sm font-medium text-foreground">Three steps to boards in your hand</p>
              <ol className="mt-2 flex list-decimal flex-col gap-1.5 pl-5 text-xs leading-5 text-muted-foreground">
                <li>
                  Download the packet below — <span className="font-mono">gerbers.zip</span> is the board,{" "}
                  <span className="font-mono">bom.csv</span> is the shopping list,{" "}
                  <span className="font-mono">cpl.csv</span> says where each part goes.
                </li>
                <li>Upload the gerbers at JLCPCB. The quote appears before you pay anything.</li>
                <li>Follow the walkthrough at the bottom of this page for the exact settings.</li>
              </ol>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <a
                  href={JLCPCB_QUOTE_URL}
                  target="_blank"
                  rel="noreferrer noopener"
                  data-slot="fab-open-quote"
                  className="inline-flex h-8 items-center gap-1.5 rounded-md border border-emerald-500/50 bg-emerald-500/10 px-3 text-sm font-medium text-foreground transition-colors hover:bg-emerald-500/20"
                >
                  <ExternalLink className="size-3.5" aria-hidden />
                  Open the JLCPCB quote page
                </a>
                <span className="text-xs text-muted-foreground">
                  Nothing is ordered until you check out. You can price it and walk away.
                </span>
              </div>
            </div>

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
            {/* The plain-language version of what is left. The raw findings
                stay below it, because an engineer standing over the shoulder
                of a first-timer needs the exact DRC prose to act on. */}
            {blockingGroups.length ? (
              <div className="rounded-xl border border-amber-500/40 bg-amber-500/[0.05] p-4">
                <p className="text-sm font-medium text-foreground">
                  {blockingGroups.length === 1 ? "One thing" : `${blockingGroups.length} things`} left before you can
                  order
                </p>
                <ul className="mt-2 flex list-disc flex-col gap-1 pl-5 text-xs leading-5 text-muted-foreground">
                  {blockingGroups.map((group) => (
                    <li key={group.code}>
                      <span className="text-foreground">{group.title}</span>
                      {group.count > 1 ? `, in ${group.count} places` : ""}. {group.meaning}
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  onClick={() => onOpenTab?.("overview")}
                  className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-md border border-border/60 bg-card/60 px-3 text-sm font-medium text-foreground transition-colors hover:border-primary/60 hover:bg-accent"
                >
                  Take me to the fix
                  <ArrowRight className="size-3.5" aria-hidden />
                </button>
              </div>
            ) : null}

            <p className="mt-2 text-xs uppercase tracking-wide text-muted-foreground">
              The exact wording the checks used
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
                Nothing was recorded as stopping the order, but the files still are not signed off. Ask the chat to
                build the board again.
                {fab.gerberSource === "tscircuit"
                  ? " (Only one program has looked at these files. KiCad has to check them too before we call them ready — install it and rebuild.)"
                  : ""}
              </p>
            )}
          </div>
        )}

        {/* Engineering downloads are NOT gated on fab-readiness. Withholding
            the fab packet stops someone paying for an unverified board;
            withholding the KiCad project would only stop them looking at it —
            and a board that is not ready is exactly the one an engineer wants
            open in a real tool. */}
        {engineeringDownloads.length ? (
          <div
            data-slot="fab-engineering"
            className="rounded-xl border border-border/60 bg-card/40 p-4"
          >
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Open in a professional tool
            </p>
            <div className="flex flex-wrap gap-2">
              {engineeringDownloads.map((d) => (
                <button
                  key={d.label}
                  type="button"
                  title={d.hint}
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
          </div>
        ) : null}
      </div>
    </div>
  );
}
