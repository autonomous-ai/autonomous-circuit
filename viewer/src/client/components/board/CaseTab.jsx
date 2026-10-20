import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Download, Eye, EyeOff, Package, RefreshCw } from "lucide-react";
import { cn } from "@/ui/utils";
import CaseScene from "./CaseScene.jsx";
import { transport } from "@/lib/transport.ts";
import { defaultVisibility, describeStatus, formatBytes, orderParts, printNote, roleLabel } from "./caseView.js";

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
  good: { icon: CheckCircle2, className: "text-emerald-400" },
  bad: { icon: AlertTriangle, className: "text-red-400" },
  warn: { icon: RefreshCw, className: "text-amber-400" },
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

function EmptyState({ hasContract, onOpenTab, className }) {
  return (
    <div data-slot="case-empty" className={cn("grid min-h-0 flex-1 place-items-center", className)}>
      <div className="flex max-w-sm flex-col items-center gap-3 px-6 text-center">
        <Package className="size-6 text-muted-foreground" aria-hidden />
        <p className="text-sm font-medium text-foreground">No case yet</p>
        <p className="text-sm leading-6 text-muted-foreground">
          {hasContract
            ? "The board knows where its screen, buttons and ports are. The agent models a printable case around them after the firmware is built, and it shows up here."
            : "The case comes after the board and its firmware: it is modelled around where the screen, buttons and ports actually are, so it waits for finished copper."}
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
  const partsKey = parts.map((p) => p.name).join("|");
  const [visible, setVisible] = useState(() => defaultVisibility(parts));
  const [exploded, setExploded] = useState(false);
  const [seeThrough, setSeeThrough] = useState(true);
  const [showBoard, setShowBoard] = useState(true);
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
  useEffect(() => { setShowBoard(!handwired); }, [handwired, projectId, boardName]);

  // New parts (a rebuild added a plunger): keep what the person chose, default the rest.
  useEffect(() => {
    setVisible((prev) => ({ ...defaultVisibility(parts), ...Object.fromEntries(Object.entries(prev).filter(([k]) => parts.some((p) => p.name === k))) }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [partsKey]);

  if (!enclosure || !parts.length) {
    return <EmptyState hasContract={Boolean(enclosure?.contractUrl)} onOpenTab={onOpenTab} className={className} />;
  }

  const tone = TONE[status.tone] || TONE.idle;
  const StatusIcon = tone.icon;
  const coupon = parts.find((p) => p.role === "coupon");

  return (
    <div data-slot="case" className={cn("flex min-h-0 flex-1", className)}>
      <Scene
        contractUrl={enclosure.contractUrl || ""}
        parts={parts}
        visible={visible}
        exploded={exploded}
        seeThrough={seeThrough}
        showBoard={showBoard}
        className="min-h-0 flex-1"
      />

      <aside className="scrollbar-thin flex w-80 shrink-0 flex-col gap-3 overflow-y-auto border-l border-border/60 p-3">
        <div data-slot="case-status" data-state={displayState} className="flex gap-2.5 rounded-xl border border-border/60 bg-card/30 px-3 py-2.5">
          <StatusIcon className={cn("mt-0.5 size-4 shrink-0", tone.className)} aria-hidden />
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">{status.title}</p>
            <p className="text-xs leading-5 text-muted-foreground">{status.detail}</p>
          </div>
        </div>

        {errors.length ? (
          <ul data-slot="case-errors" className="flex flex-col gap-1 rounded-xl border border-red-500/30 bg-red-500/5 px-3 py-2">
            {errors.map((e, i) => (
              <li key={i} className="text-[11px] leading-4 text-red-200/90">
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
        {saved ? <div data-slot="case-saved" className="rounded-md border border-border/60 px-2 py-2 text-xs break-all">
          Saved: {saved.path}
          <div className="mt-1 flex flex-wrap gap-2">
            <button type="button" disabled={saving} onClick={() => openSaved(transport.export_reveal)} className="underline disabled:opacity-40">Show in Finder</button>
            {saved.format === "3mf" && saved.canOpenBambu ? <button type="button" disabled={saving} data-slot="case-open-bambu" onClick={() => openSaved(transport.export_open_bambu)} className="underline disabled:opacity-40">Open in Bambu Studio</button> : null}
          </div>
        </div> : null}
        {exportError ? <p role="alert" className="text-xs text-red-300">{exportError}</p> : null}

        {coupon ? (
          <div data-slot="case-coupon" className="rounded-xl border border-amber-400/30 bg-amber-400/5 px-3 py-2.5">
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
                on ? "border-primary/50 bg-primary/10 text-foreground" : "border-border/60 text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>

        <ul aria-label="Case parts" className="flex flex-col gap-1.5">
          {parts.map((part) => {
            const shown = visible[part.name] !== false;
            return (
              <li key={part.name} data-slot="case-part" data-part={part.name} className="rounded-lg border border-border/50 px-2.5 py-2">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    data-slot="case-part-toggle"
                    aria-pressed={shown ? "true" : "false"}
                    title={shown ? "Hide in the view" : "Show in the view"}
                    onClick={() => setVisible((prev) => ({ ...prev, [part.name]: !shown }))}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    {shown ? <Eye className="size-3.5" aria-hidden /> : <EyeOff className="size-3.5" aria-hidden />}
                  </button>
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-mono text-[11px] text-foreground">{part.name}</p>
                    <p className="text-[10px] text-muted-foreground">
                      {roleLabel(part.role)} · {formatBytes(part.bytes)}
                    </p>
                  </div>
                  <button
                    type="button" disabled={saving || !projectId} onClick={() => save("stl", part.name)}
                    data-slot="case-download-stl"
                    className="inline-flex items-center gap-1 rounded-md border border-border/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground hover:text-foreground"
                  >
                    <Download className="size-3" aria-hidden />
                    STL
                  </button>
                  {part.stepUrl ? (
                    <button
                      type="button" disabled={saving || !projectId} onClick={() => save("step", part.name)}
                      data-slot="case-download-step"
                      className="inline-flex items-center gap-1 rounded-md border border-border/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground hover:text-foreground"
                    >
                      STEP
                    </button>
                  ) : null}
                </div>
                <p className="mt-1 text-[10px] leading-4 text-muted-foreground">{part.printable === false ? "Reference model · excluded from Print all." : `To print: ${printNote(part)}.`}</p>
              </li>
            );
          })}
        </ul>

        <p className="text-[10px] leading-4 text-muted-foreground">
          Checked in the model, not yet on a desk: a print is the only test of a fit.
        </p>
      </aside>
    </div>
  );
}
