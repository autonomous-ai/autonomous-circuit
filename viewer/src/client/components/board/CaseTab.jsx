import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Download, Eye, EyeOff, Package, RefreshCw } from "lucide-react";
import { cn } from "@/ui/utils";
import CaseScene from "./CaseScene.jsx";
import { transport } from "@/lib/transport.ts";
import { defaultVisibility, describeStatus, formatBytes, groupPartsByPlate, orderParts, printNote, roleLabel } from "./caseView.js";

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
  good: { icon: CheckCircle2, className: "text-emerald-700" },
  bad: { icon: AlertTriangle, className: "text-red-700" },
  warn: { icon: RefreshCw, className: "text-amber-700" },
  idle: { icon: Package, className: "text-muted-foreground" },
};

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
    return (
      <li key={part.name} data-slot="case-part" data-part={part.name} className="rounded-lg border border-border bg-card px-2.5 py-2 shadow-sm">
        <div className="flex items-center gap-2">
          <button
            type="button"
            data-slot="case-part-toggle"
            aria-pressed={shown ? "true" : "false"}
            aria-label={`${shown ? "Hide" : "Show"} ${part.name} in the 3D view`}
            title={shown ? "Hide in the view" : "Show in the view"}
            onClick={() => { setVisible((prev) => ({ ...prev, [part.name]: !shown })); setActivePlate(null); }}
            className="text-muted-foreground hover:text-foreground"
          >
            {shown ? <Eye className="size-4" aria-hidden /> : <EyeOff className="size-4" aria-hidden />}
          </button>
          <div className="min-w-0 flex-1">
            <p className="break-all font-mono text-xs font-semibold leading-4 text-foreground">{part.name}</p>
            <p className="text-[11px] leading-4 text-muted-foreground">
              {roleLabel(part.role)} · {formatBytes(part.bytes)}
            </p>
          </div>
          <button
            type="button" disabled={saving || !projectId} onClick={() => save("stl", part.name)}
            data-slot="case-download-stl"
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] font-medium text-foreground hover:bg-[#eef4f5] disabled:opacity-40"
          >
            <Download className="size-3" aria-hidden />
            STL
          </button>
          {part.stepUrl ? (
            <button
              type="button" disabled={saving || !projectId} onClick={() => save("step", part.name)}
              data-slot="case-download-step"
              className="inline-flex items-center rounded-md border border-border px-2 py-1 text-[11px] font-medium text-foreground hover:bg-[#eef4f5] disabled:opacity-40"
            >
              STEP
            </button>
          ) : null}
        </div>
        <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{part.printable === false ? "Reference model · excluded from Print all." : `To print: ${printNote(part)}.`}</p>
        {printPart ? <p data-slot="print-part-status" className="mt-1 text-[11px] leading-4 text-muted-foreground">
          {printStatus.inventoryTrusted === false
            ? "Print records unavailable"
            : <>{printPart.confirmedPrintedQuantity ? `Confirmed printed ×${printPart.confirmedPrintedQuantity}` : "No confirmed print"}{printPart.fitStatus === "fits" ? " · fits" : printPart.fitStatus === "failed" ? " · fit failed" : ""}</>}
        </p> : null}
      </li>
    );
  };

  return (
    <div data-slot="case" className={cn("flex min-h-0 flex-1 bg-[#eef2f2] text-foreground", className)}
      style={{ "--foreground": "#25333b", "--muted-foreground": "#5c6e76", "--border": "#d5dee1", "--card": "#ffffff", "--primary": "#266789", "--primary-foreground": "#ffffff" }}>
      <Scene
        contractUrl={enclosure.contractUrl || ""}
        parts={parts}
        visible={visible}
        exploded={exploded}
        seeThrough={seeThrough}
        showBoard={showBoard}
        className="min-h-0 flex-1"
      />

      <aside className="scrollbar-thin flex w-[340px] max-w-[48%] shrink-0 flex-col gap-3 overflow-y-auto border-l border-border bg-[#f8faf9] p-3">
        <div data-slot="case-status" data-state={displayState} className="flex gap-2.5 rounded-xl border border-border bg-card px-3 py-2.5 shadow-sm">
          <StatusIcon className={cn("mt-0.5 size-4 shrink-0", tone.className)} aria-hidden />
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">{status.title}</p>
            <p className="text-xs leading-5 text-muted-foreground">{status.detail}</p>
          </div>
        </div>

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
          className="rounded-md border border-primary/50 bg-primary/10 px-3 py-2 text-sm font-medium disabled:opacity-40">
          {saving ? "Preparing…" : "Print all · Bambu Studio"}
        </button>
        <p className="text-[11px] leading-4 text-muted-foreground">Saves a 3MF and opens Bambu Studio when available. Printable parts laid out separately; reference models excluded. Choose your printer in the slicer, then slice.</p>
        {saved ? <div data-slot="case-saved" className="rounded-md border border-border bg-card px-2 py-2 text-xs break-all">
          Saved: {saved.path}
          <div className="mt-1 flex flex-wrap gap-2">
            <button type="button" disabled={saving} onClick={() => openSaved(transport.export_reveal)} className="underline disabled:opacity-40">Show in Finder</button>
            {saved.format === "3mf" && saved.canOpenBambu ? <button type="button" disabled={saving} data-slot="case-open-bambu" onClick={() => openSaved(transport.export_open_bambu)} className="underline disabled:opacity-40">Open in Bambu Studio</button> : null}
          </div>
        </div> : null}
        {exportError ? <p role="alert" className="text-xs text-red-700">{exportError}</p> : null}

        <section data-slot="case-print-team" className="rounded-xl border border-border bg-card px-3 py-2.5 shadow-sm">
          <p className="text-sm font-medium text-foreground">Print team</p>
          {!printStatus ? <p className="mt-1 text-xs text-muted-foreground">Checking print records…</p> : (
            <>
              {!printStatus.ready ? <p className="mt-1 text-xs leading-5 text-muted-foreground">{printStatus.reason || "A checked print project is needed before tracking plates."}</p> : (
                <p className="mt-1 text-[11px] leading-4 text-muted-foreground">Ask the lead which plate to print. Tell the lead what finished and whether it fits; it records each part and revision.</p>
              )}
              {printStatus.jobs.length ? <ul className="mt-2 space-y-1" aria-label="Printer observations">
                {printStatus.jobs.map((job, index) => <li key={`${job.jobId || "job"}:${job.observedAt}:${index}`} data-slot="print-job" className="text-[11px] text-muted-foreground">
                  {job.printerName || "Printer"}{job.jobName ? ` · ${job.jobName}` : ""} reports {job.status}{job.progressPct == null ? "" : ` · ${job.progressPct}%`}{job.currentLayer != null && job.totalLayers != null ? ` · layer ${job.currentLayer}/${job.totalLayers}` : ""}{job.remainingMinutes != null ? ` · ${job.remainingMinutes} min left` : ""}. {job.matchedParts?.length ? "Awaiting your part confirmation." : "Not linked to this case."}
                </li>)}
              </ul> : null}
              {printStatus.account?.state === "connected" && printStatus.account.experimental ? <p className="mt-2 text-[11px] leading-4 text-muted-foreground">Live status from the experimental local Bambu monitor.</p> : null}
              {printStatus.account?.state === "connected" && printStatus.account.reason === "no_status" ? <p className="mt-1 text-[11px] leading-4 text-muted-foreground">Printer connected; waiting for its next status update.</p> : null}
              {printStatus.account?.state !== "connected" ? <p className="mt-2 text-[11px] leading-4 text-muted-foreground">{printStatus.account?.reason === "not_configured" ? "Pair the A1 once on this Mac for live progress. " : "Printer status is unavailable. "}The lead can still track what you confirm.</p> : null}
            </>
          )}
        </section>

        {coupon ? (
          <div data-slot="case-coupon" className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2.5">
            <p className="text-sm font-medium text-foreground">Print the test-fit coupon first</p>
            <p className="text-xs leading-5 text-muted-foreground">
              A few minutes of print: the plate around the screen and the wall around the port. Hold it against the real board before printing the whole case.
            </p>
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
          <div className="flex flex-col gap-3" data-slot="case-plate-list">
            <div className="flex items-baseline justify-between gap-2">
              <h2 className="text-xs font-bold uppercase tracking-[0.12em] text-foreground">Print plates</h2>
              <span className="text-[11px] text-muted-foreground">{plateParts.groups.length} plates · {parts.length - plateParts.other.length} parts</span>
            </div>
            <p className="-mt-2 text-[11px] leading-4 text-muted-foreground">These groups show which parts belong to each print plate. The 3D view shows their assembled position.</p>
            {activePlate !== null ? (
              <button type="button" data-slot="case-show-all" onClick={showAll}
                className="self-start rounded-md border border-primary/40 bg-card px-2.5 py-1 text-xs font-medium text-primary hover:bg-[#eaf2f5]">
                Show all assembly parts
              </button>
            ) : null}
            {plateParts.groups.map((group) => (
              <section key={group.index} data-slot="case-plate" data-plate={group.index} className="rounded-xl border border-border bg-[#eaf1f3] p-2.5">
                <div className="mb-2 flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-primary">Plate {group.index} · {group.parts.length} parts</p>
                    <h3 className="mt-0.5 break-words text-sm font-semibold leading-5 text-foreground">{group.name}</h3>
                    {printedPlate(group.index) ? <p data-slot="print-plate" className="mt-0.5 text-[11px] leading-4 text-muted-foreground">{printedPlate(group.index)}</p> : null}
                  </div>
                  <button type="button" data-slot="case-plate-focus" aria-pressed={activePlate === group.index ? "true" : "false"}
                    onClick={() => focusPlate(group)}
                    className="shrink-0 rounded-md border border-primary/40 bg-card px-2 py-1 text-[11px] font-semibold text-primary hover:bg-[#dbeaf0]">
                    Show only
                  </button>
                </div>
                <ul aria-label={`Plate ${group.index} parts`} className="flex flex-col gap-1.5">
                  {group.parts.map(partRow)}
                </ul>
              </section>
            ))}
            {plateParts.other.length ? (
              <section data-slot="case-other-parts" className="rounded-xl border border-border bg-[#f4f5f2] p-2.5">
                <h3 className="mb-1 text-xs font-bold uppercase tracking-[0.12em] text-foreground">Test & reference</h3>
                <p className="mb-2 text-[11px] leading-4 text-muted-foreground">Not in the production plates above.</p>
                <ul aria-label="Test and reference parts" className="flex flex-col gap-1.5">
                  {plateParts.other.map(partRow)}
                </ul>
              </section>
            ) : null}
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <h2 className="text-xs font-bold uppercase tracking-[0.12em] text-foreground">Case parts</h2>
            <ul aria-label="Case parts" className="flex flex-col gap-1.5">{parts.map(partRow)}</ul>
          </div>
        )}

        <p className="text-[10px] leading-4 text-muted-foreground">
          Checked in the model, not yet on a desk: a print is the only test of a fit.
        </p>
      </aside>
    </div>
  );
}
