import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, CircleHelp, Download, Package, Printer, RefreshCw } from "lucide-react";
import { cn } from "@/ui/utils";
import CaseScene from "./CaseScene.jsx";
import { transport } from "@/lib/transport.ts";
import {
  cssColor, defaultVisibility, describeStatus, formatBytes, groupPartsByPlate, orderParts, partColors, plateTitle, printNote, roleLabel, summaryChips,
} from "./caseView.js";

/**
 * The Case tab: the printable enclosure the agent models after the firmware,
 * shown around the board it was built for, with every part a download away.
 *
 * Read straight from `case/` (catalog: `artifact.case`). The status line is the
 * tile's checker's, not the agent's word: "Fits the board" only when a check of
 * these exact files against this exact board passed. The test-fit coupon is
 * listed first and hidden from the assembly — it is the thing to print first.
 *
 * Nothing here prints or slices: the tab shows what was modelled.
 *
 * @param {{
 *   artifact?: object|null,   // catalog entry.artifact; reads .case
 *   boardName?: string,
 *   onOpenTab?: (id: string) => void,
 *   Scene?: Function,         // the 3D view; tests pass a stub (no WebGL in node)
 *   className?: string,
 * }} props
 */

const TONE = {
  good: { icon: CheckCircle2, className: "text-emerald-700 dark:text-emerald-400" },
  bad: { icon: AlertTriangle, className: "text-red-700 dark:text-red-400" },
  warn: { icon: RefreshCw, className: "text-amber-700 dark:text-amber-400" },
  idle: { icon: Package, className: "text-muted-foreground" },
};

const CHIP_TONE = {
  good: "border-emerald-500/50 text-emerald-700 dark:text-emerald-300",
  warn: "border-amber-500/50 text-amber-700 dark:text-amber-300",
  bad: "border-red-500/50 text-red-700 dark:text-red-300",
};

/**
 * A card that is one line: icon, title, a short state on the right and a "?"
 * that opens the explanation. The words are there for whoever wants them, not
 * in everyone's way. `footer` stays visible (live data such as printer jobs).
 */
function Explained({ slot, state, icon, title, aside, children, footer }) {
  return (
    <section data-slot={slot} data-state={state} className="rounded-xl border border-border bg-card px-3 py-2 shadow-sm">
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
          {icon}
          <span className="min-w-0 truncate text-sm font-medium text-foreground">{title}</span>
          {aside ? <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">{aside}</span> : null}
          {children ? (
            <CircleHelp className={cn("size-3.5 shrink-0 text-muted-foreground group-open:text-foreground", aside ? "" : "ml-auto")} aria-label="What this means" />
          ) : null}
        </summary>
        {children ? <div className="mt-1.5 text-xs leading-5 text-muted-foreground">{children}</div> : null}
      </details>
      {footer}
    </section>
  );
}

/** The checker's errors, when the report is there and the case is not passing. */
function useReportErrors(url, wanted) {
  const [errors, setErrors] = useState([]);
  useEffect(() => {
    if (!url || !wanted) {
      setErrors([]);
      return undefined;
    }
    let cancelled = false;
    fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .then((report) => {
        if (!cancelled) setErrors(Array.isArray(report?.errors) ? report.errors.slice(0, 20) : []);
      })
      .catch(() => {
        if (!cancelled) setErrors([]);
      });
    return () => {
      cancelled = true;
    };
  }, [url, wanted]);
  return errors;
}

function usePrintStatus(projectId, boardName, hasCase) {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    setStatus(null);
    if (!projectId || !hasCase) return undefined;
    let cancelled = false;
    const refresh = async () => {
      try {
        const next = await transport.print_status(projectId, boardName);
        if (!cancelled) setStatus(next);
      } catch (error) {
        if (!cancelled) setStatus({ ready: false, reason: error.message || String(error), parts: [], plates: [], jobs: [], account: { state: "unavailable" } });
      }
    };
    refresh();
    const timer = setInterval(refresh, 15_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [projectId, boardName, hasCase]);
  return status;
}

function EmptyState({ hasContract, onOpenTab, className }) {
  return (
    <div data-slot="case-empty" className={cn("grid min-h-0 flex-1 place-items-center", className)}>
      <div className="flex max-w-sm flex-col items-center gap-3 px-6 text-center">
        <Package className="size-6 text-muted-foreground" aria-hidden />
        <p className="text-sm font-medium text-foreground">No case yet</p>
        <p className="text-sm leading-6 text-muted-foreground">
          {hasContract
            ? "The case team uses the board's screen, button and port locations. Checked case parts appear here when ready."
            : "The case team models the enclosure around the board's screen, buttons and ports. Checked parts appear here when ready."}
        </p>
        <button type="button" onClick={() => onOpenTab?.("firmware")} className="text-xs text-primary underline-offset-4 hover:underline">
          See the firmware
        </button>
      </div>
    </div>
  );
}

export default function CaseTab({ artifact = null, boardName = "board", projectId = "", onOpenTab, Scene = CaseScene, className }) {
  const enclosure = artifact?.case || null;
  const parts = useMemo(() => orderParts(enclosure?.parts || []), [enclosure?.parts]);
  const plateParts = useMemo(() => groupPartsByPlate(parts, enclosure?.plates), [parts, enclosure?.plates]);
  const partsKey = parts.map((p) => p.name).join("|");
  const [visible, setVisible] = useState(() => defaultVisibility(parts));
  const [exploded, setExploded] = useState(false);
  const [seeThrough, setSeeThrough] = useState(false);
  const [showBoard, setShowBoard] = useState(true);
  const [activePlate, setActivePlate] = useState(null);
  const [sizeMm, setSizeMm] = useState(null);
  const colors = useMemo(() => partColors(parts), [parts]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(null);
  const [exportError, setExportError] = useState("");
  const context = useRef(""); context.current = `${projectId}|${boardName}`;
  useEffect(() => { setSaved(null); setExportError(""); setSaving(false); }, [projectId, boardName]);
  const save = async (format, part) => {
    const startedIn = context.current;
    setSaving(true); setExportError("");
    try {
      const result = await transport.export_case(projectId, boardName, format, part);
      if (context.current !== startedIn) return;
      setSaved({ ...result, format });
      if (format === "3mf" && result.canOpenBambu) await transport.export_open_bambu(result.path);
    }
    catch (error) { if (context.current === startedIn) setExportError(error.message || String(error)); }
    finally { if (context.current === startedIn) setSaving(false); }
  };
  const openSaved = async (command) => {
    const startedIn = context.current;
    setSaving(true); setExportError("");
    try { await command(saved.path); }
    catch (error) { if (context.current === startedIn) setExportError(error.message || String(error)); }
    finally { if (context.current === startedIn) setSaving(false); }
  };
  const handwired = enclosure?.nativePCBCheckerApplies === false;
  const readiness = enclosure?.printStatus || enclosure;
  const displayState = handwired ? readiness?.state : enclosure?.state;
  const status = describeStatus(displayState, handwired ? readiness?.reason : enclosure?.reason);
  if (handwired && displayState === "passed") { status.title = "Handwired case checked"; status.detail = readiness.reason; }
  const errors = useReportErrors((handwired ? enclosure?.printReportUrl : enclosure?.reportUrl) || "", displayState === "failed");
  const printStatus = usePrintStatus(projectId, boardName, Boolean(enclosure?.parts?.length));
  useEffect(() => { setShowBoard(!handwired); }, [handwired, projectId, boardName]);

  // New parts (a rebuild added a plunger): keep what the person chose, default the rest.
  useEffect(() => {
    setVisible((prev) => ({ ...defaultVisibility(parts), ...Object.fromEntries(Object.entries(prev).filter(([k]) => parts.some((p) => p.name === k))) }));
    setActivePlate(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [partsKey]);

  if (!enclosure || !parts.length) {
    return <EmptyState hasContract={Boolean(enclosure?.contractUrl)} onOpenTab={onOpenTab} className={className} />;
  }

  const tone = TONE[status.tone] || TONE.idle;
  const StatusIcon = tone.icon;
  const coupon = parts.find((p) => p.role === "coupon");
  const chips = summaryChips({ parts, plates: plateParts.groups, state: displayState, sizeMm });
  const focusPlate = (group) => {
    setVisible(Object.fromEntries(parts.map((part) => [part.name, group.parts.some((member) => member.name === part.name)])));
    setActivePlate(group.index);
  };
  const printedPlate = (index) => {
    if (!printStatus?.ready) return null;
    const plate = printStatus.plates.find((item) => item.index === index);
    return !plate ? null : plate.status === "printed" ? "Confirmed printed" : plate.status === "partly_printed" ? "Partly printed" : "Needs print";
  };
  const showAll = () => {
    setVisible(defaultVisibility(parts));
    setActivePlate(null);
  };
  const partRow = (part) => {
    const shown = visible[part.name] !== false;
    const printPart = printStatus?.parts?.find((item) => item.name === part.name);
    const howToPrint = part.printable === false ? "Reference model · excluded from Print all." : `To print: ${printNote(part)}.`;
    // The print record gets its own line only when it says something; "No confirmed
    // print" on every row of a new case is noise, so that one is in the tooltip.
    const printLine = !printPart ? ""
      : printStatus.inventoryTrusted === false ? "Print records unavailable"
        : `${printPart.confirmedPrintedQuantity ? `Confirmed printed ×${printPart.confirmedPrintedQuantity}` : "No confirmed print"}${printPart.fitStatus === "fits" ? " · fits" : printPart.fitStatus === "failed" ? " · fit failed" : ""}`;
    const printNews = printStatus?.inventoryTrusted === false || Boolean(printPart?.confirmedPrintedQuantity) || ["fits", "failed"].includes(printPart?.fitStatus);
    return (
      <li
        key={part.name}
        data-slot="case-part"
        data-part={part.name}
        title={`${part.name} · ${roleLabel(part.role)} · ${formatBytes(part.bytes)}\n${howToPrint}${printLine ? `\n${printLine}` : ""}`}
        className="rounded-lg border border-border bg-card px-2 py-1.5"
      >
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            data-slot="case-part-toggle"
            checked={shown}
            aria-label={`Show ${part.name} in the 3D view`}
            onChange={() => { setVisible((prev) => ({ ...prev, [part.name]: !shown })); setActivePlate(null); }}
            className="size-3.5 shrink-0 cursor-pointer accent-primary"
          />
          <span data-slot="case-part-swatch" aria-hidden className="size-3 shrink-0 rounded-sm" style={{ backgroundColor: cssColor(colors[part.name]) }} />
          <p className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">{part.name}</p>
          <button
            type="button" disabled={saving || !projectId} onClick={() => save("stl", part.name)}
            data-slot="case-download-stl" title="Save this part as STL"
            className="inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-[11px] font-medium text-foreground hover:bg-accent disabled:opacity-40"
          >
            <Download className="size-3" aria-hidden />
            STL
          </button>
          {part.stepUrl ? (
            <button
              type="button" disabled={saving || !projectId} onClick={() => save("step", part.name)}
              data-slot="case-download-step" title="Save this part as STEP"
              className="inline-flex items-center rounded-md border border-border px-1.5 py-0.5 text-[11px] font-medium text-foreground hover:bg-accent disabled:opacity-40"
            >
              STEP
            </button>
          ) : null}
        </div>
        {printLine && printNews ? <p data-slot="print-part-status" className="mt-0.5 pl-[3.25rem] text-[11px] leading-4 text-muted-foreground">{printLine}</p> : null}
      </li>
    );
  };

  const printTeamDetail = !printStatus ? "" : [
    !printStatus.ready
      ? printStatus.reason || "A checked print project is needed before tracking plates."
      : "Ask the lead which plate to print. Tell the lead what finished and whether it fits; it records each part and revision.",
    printStatus.account?.state === "connected" && printStatus.account.experimental ? "Live status from the experimental local Bambu monitor." : "",
    printStatus.account?.state === "connected" && printStatus.account.reason === "no_status" ? "Printer connected; waiting for its next status update." : "",
    printStatus.account?.state !== "connected"
      ? `${printStatus.account?.reason === "not_configured" ? "Pair the A1 once on this Mac for live progress. " : "Printer status is unavailable. "}The lead can still track what you confirm.`
      : "",
  ].filter(Boolean).join(" ");
  const printerState = !printStatus ? "checking…"
    : printStatus.account?.state === "connected" ? "printer connected"
      : printStatus.account?.reason === "not_configured" ? "printer not paired" : "no printer status";

  return (
    <div data-slot="case" className={cn("flex min-h-0 flex-1 bg-background text-foreground", className)}>
      <div className="relative flex min-h-0 min-w-0 flex-1">
        <Scene
          contractUrl={enclosure.contractUrl || ""}
          parts={parts}
          visible={visible}
          exploded={exploded}
          seeThrough={seeThrough}
          showBoard={showBoard}
          onSize={setSizeMm}
          className="min-h-0 flex-1"
        />
        {chips.length ? (
          <ul data-slot="case-chips" aria-label="Case summary" className="pointer-events-none absolute left-2 top-2 flex max-w-[calc(100%-1rem)] flex-wrap gap-1.5">
            {chips.map((chip) => (
              <li
                key={chip.id}
                data-slot="case-chip"
                data-chip={chip.id}
                title={chip.title}
                className={cn(
                  "pointer-events-auto rounded-full border bg-background/80 px-2.5 py-0.5 text-[11px] font-medium backdrop-blur",
                  CHIP_TONE[chip.tone] || "border-border text-foreground",
                )}
              >
                {chip.label}
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <aside className="scrollbar-thin flex w-[300px] max-w-[42%] shrink-0 flex-col gap-2.5 overflow-y-auto border-l border-border bg-muted/30 p-3">
        <Explained slot="case-status" state={displayState} icon={<StatusIcon className={cn("size-4 shrink-0", tone.className)} aria-hidden />} title={status.title}>
          <p>{status.detail}</p>
          <p className="mt-1">Checked in the model, not yet on a desk: a print is the only test of a fit.</p>
        </Explained>

        {errors.length ? (
          <ul data-slot="case-errors" className="flex flex-col gap-1 rounded-xl border border-red-500/30 bg-red-500/5 px-3 py-2">
            {errors.map((e, i) => (
              <li key={i} className="text-[11px] leading-4 text-red-700">
                {e.message || e.kind}
              </li>
            ))}
          </ul>
        ) : null}

        <button type="button" data-slot="case-print-all" disabled={saving || !projectId || readiness?.state !== "passed"} onClick={() => save("3mf")}
          title="Saves a 3MF and opens Bambu Studio when available. Printable parts laid out separately; reference models excluded. Choose your printer in the slicer, then slice."
          className="rounded-md border border-primary/50 bg-primary/10 px-3 py-2 text-sm font-medium disabled:opacity-40">
          {saving ? "Preparing…" : "Print all · Bambu Studio"}
        </button>
        {saved ? <div data-slot="case-saved" className="rounded-md border border-border bg-card px-2 py-2 text-xs break-all">
          Saved: {saved.path}
          <div className="mt-1 flex flex-wrap gap-2">
            <button type="button" disabled={saving} onClick={() => openSaved(transport.export_reveal)} className="underline disabled:opacity-40">Show in Finder</button>
            {saved.format === "3mf" && saved.canOpenBambu ? <button type="button" disabled={saving} data-slot="case-open-bambu" onClick={() => openSaved(transport.export_open_bambu)} className="underline disabled:opacity-40">Open in Bambu Studio</button> : null}
          </div>
        </div> : null}
        {exportError ? <p role="alert" className="text-xs text-red-700">{exportError}</p> : null}

        <Explained slot="case-print-team" icon={<Printer className="size-4 shrink-0 text-muted-foreground" aria-hidden />} title="Print team" aside={printerState}
          footer={printStatus?.jobs?.length ? <ul className="mt-1.5 space-y-1" aria-label="Printer observations">
            {printStatus.jobs.map((job, index) => <li key={`${job.jobId || "job"}:${job.observedAt}:${index}`} data-slot="print-job" className="text-[11px] text-muted-foreground">
              {job.printerName || "Printer"}{job.jobName ? ` · ${job.jobName}` : ""} reports {job.status}{job.progressPct == null ? "" : ` · ${job.progressPct}%`}{job.currentLayer != null && job.totalLayers != null ? ` · layer ${job.currentLayer}/${job.totalLayers}` : ""}{job.remainingMinutes != null ? ` · ${job.remainingMinutes} min left` : ""}. {job.matchedParts?.length ? "Awaiting your part confirmation." : "Not linked to this case."}
            </li>)}
          </ul> : null}>
          {printTeamDetail ? <p>{printTeamDetail}</p> : null}
        </Explained>

        {coupon ? (
          <div data-slot="case-coupon" className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 dark:border-amber-500/50 dark:bg-amber-500/10">
            <p className="text-sm font-medium text-foreground">Print the test-fit coupon first</p>
            <p className="text-xs leading-5 text-muted-foreground">A few minutes. Hold it against the real board before printing the case.</p>
          </div>
        ) : null}

        <div className="flex flex-wrap gap-1.5">
          {[
            ["Exploded", exploded, setExploded, "case-explode"],
            ["See-through", seeThrough, setSeeThrough, "case-see-through"],
            ["Board", showBoard, setShowBoard, "case-board"],
          ].map(([label, on, set, slot]) => (
            <button
              key={slot}
              type="button"
              data-slot={slot}
              aria-pressed={on ? "true" : "false"}
              onClick={() => set((v) => !v)}
              className={cn(
                "rounded-md border px-2 py-1 text-[11px] font-medium transition-colors",
                on ? "border-primary/50 bg-primary/10 text-foreground" : "border-border bg-card text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {plateParts.groups.length ? (
          <div className="flex flex-col gap-2.5" data-slot="case-plate-list">
            <div className="flex items-baseline justify-between gap-2">
              <h2 className="text-xs font-semibold uppercase tracking-[0.12em] text-foreground">Print plates</h2>
              <span className="text-[11px] text-muted-foreground">{plateParts.groups.length} plates · {parts.length - plateParts.other.length} parts</span>
            </div>
            {activePlate !== null ? (
              <button type="button" data-slot="case-show-all" onClick={showAll}
                className="self-start rounded-md border border-primary/40 bg-card px-2.5 py-1 text-xs font-medium text-primary hover:bg-accent">
                Show all assembly parts
              </button>
            ) : null}
            {plateParts.groups.map((group) => {
              const plate = plateTitle(group.name);
              return (
                <section key={group.index} data-slot="case-plate" data-plate={group.index} className="rounded-xl border border-border bg-muted/50 p-2">
                  <div className="mb-1.5 flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                        <span className="whitespace-nowrap">Plate {group.index} · {group.parts.length} parts</span>
                        {plate.tag ? <span data-slot="case-plate-tag" className="rounded bg-primary/10 px-1 text-[10px] font-medium text-primary">{plate.tag}</span> : null}
                        {printedPlate(group.index) ? <span data-slot="print-plate" className="whitespace-nowrap">{printedPlate(group.index)}</span> : null}
                      </p>
                      <h3 data-slot="case-plate-title" title={plate.full} className="mt-0.5 line-clamp-2 break-words text-sm leading-5 text-foreground">{plate.title}</h3>
                    </div>
                    <button type="button" data-slot="case-plate-focus" aria-pressed={activePlate === group.index ? "true" : "false"}
                      onClick={() => focusPlate(group)}
                      className="shrink-0 rounded-md border border-primary/40 bg-card px-2 py-1 text-[11px] font-medium text-primary hover:bg-accent">
                      Show only
                    </button>
                  </div>
                  <ul aria-label={`Plate ${group.index} parts`} className="flex flex-col gap-1">
                    {group.parts.map(partRow)}
                  </ul>
                </section>
              );
            })}
            {plateParts.other.length ? (
              <section data-slot="case-other-parts" className="rounded-xl border border-border bg-muted/40 p-2">
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-[0.12em] text-foreground" title="Not in the production plates above.">Test & reference</h3>
                <ul aria-label="Test and reference parts" className="flex flex-col gap-1">
                  {plateParts.other.map(partRow)}
                </ul>
              </section>
            ) : null}
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <h2 className="text-xs font-semibold uppercase tracking-[0.12em] text-foreground">Case parts</h2>
            <ul aria-label="Case parts" className="flex flex-col gap-1">{parts.map(partRow)}</ul>
          </div>
        )}
      </aside>
    </div>
  );
}
