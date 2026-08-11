import { useMemo } from "react";
import { ExternalLink } from "lucide-react";
import { cn } from "@/ui/utils";
import { partPlainName, partRole } from "@/lib/plainLanguage.js";
import { lcscUrl } from "./boardData.js";

const NUM = (value, fallback = 0) => (Number.isFinite(Number(value)) ? Number(value) : fallback);

function Row({ label, children, mono = true }) {
  return (
    <div className="flex items-baseline gap-2 py-[3px]">
      <span className="w-20 shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground/70">{label}</span>
      <span className={cn("min-w-0 flex-1 break-words text-[12px] text-foreground", mono && "font-mono")}>
        {children ?? <span className="text-muted-foreground/40">—</span>}
      </span>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div className="border-b border-border/40 px-3 py-2 last:border-b-0">
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{title}</p>
      {children}
    </div>
  );
}

/**
 * Properties — right-docked and context-sensitive, the way Altium's is: with
 * nothing selected it is Board mode (dimensions, counts, the fab constraints);
 * with a component selected it is that part (refdes, value, footprint, layer,
 * supplier); with a net selected it is that node (pins, routed length, vias,
 * and every pin as a click-through).
 *
 * Everything blue is a jump: clicking a net from a component, or a pin from a
 * net, moves the selection and cross-probes both canvases.
 */
export default function PropertiesPanel({
  index = null,
  sidecar = null,
  selection = null,
  partsByLcscMap = null,
  units = "mm",
  onSelect,
  className,
}) {
  const component = useMemo(() => {
    if (selection?.kind !== "component" || !index) return null;
    return index.componentBySourceId.get(selection.key) || null;
  }, [index, selection]);

  const net = useMemo(() => {
    if (selection?.kind !== "net" || !index) return null;
    return index.netByKey.get(selection.key) || null;
  }, [index, selection]);

  const fmt = (mm) => (units === "mil" ? `${(NUM(mm) / 0.0254).toFixed(0)} mil` : `${NUM(mm).toFixed(2)} mm`);

  return (
    <aside
      data-slot="properties-panel"
      className={cn("scrollbar-thin flex w-64 shrink-0 flex-col overflow-y-auto border-l border-border/60 bg-card/30", className)}
    >
      <div className="flex h-8 shrink-0 items-center border-b border-border/60 px-3">
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          {component ? "Component" : net ? "Net" : "Board"}
        </span>
        {component ? <span className="ml-auto font-mono text-[12px] text-foreground">{component.refdes}</span> : null}
        {net ? <span className="ml-auto truncate font-mono text-[12px] text-foreground">{net.name}</span> : null}
      </div>

      {component ? <ComponentProperties component={component} index={index} partsByLcscMap={partsByLcscMap} fmt={fmt} onSelect={onSelect} /> : null}
      {net ? <NetProperties net={net} index={index} fmt={fmt} onSelect={onSelect} /> : null}
      {!component && !net ? <BoardProperties index={index} sidecar={sidecar} fmt={fmt} /> : null}
    </aside>
  );
}

function ComponentProperties({ component, index, partsByLcscMap, fmt, onSelect }) {
  const part = partsByLcscMap?.get(String(component.lcsc || "").replace(/^[Cc]/, "")) || null;
  const link = lcscUrl(component.lcsc);
  const width = component.pcb ? NUM(component.pcb.width) : 0;
  const height = component.pcb ? NUM(component.pcb.height) : 0;
  const nets = [...component.netKeys].map((key) => index?.netByKey.get(key)).filter(Boolean);

  const role = partRole(component);
  const plainName = partPlainName(component);

  return (
    <>
      {/* The plain read, above the EDA fields rather than instead of them.
          An engineer's eye skips one line; everyone else needs it to know
          what they just clicked. */}
      <div className="border-b border-border/40 px-3 py-2">
        <p data-slot="property-plain-name" className="text-[13px] font-medium leading-5 text-foreground">
          {plainName}
        </p>
        <p className="text-[11px] leading-4 text-muted-foreground">
          {role.label}
          {role.blurb ? ` — ${role.blurb}` : ""}
        </p>
      </div>
      <Section title="General">
        <Row label="Designator">{component.refdes}</Row>
        <Row label="Value">{component.value || component.mpn || null}</Row>
        <Row label="Type">{component.ftype.replace(/^simple_/, "")}</Row>
        <Row label="MPN">{component.mpn || null}</Row>
      </Section>
      <Section title="Placement">
        <Row label="Layer">{component.layer}</Row>
        <Row label="Rotation">{`${NUM(component.rotation)}°`}</Row>
        <Row label="Centre">
          {component.pcb ? `${NUM(component.pcb.center?.x).toFixed(3)}, ${NUM(component.pcb.center?.y).toFixed(3)}` : null}
        </Row>
        <Row label="Body">{width && height ? `${fmt(width)} × ${fmt(height)}` : null}</Row>
        <Row label="Pads">{component.pads || null}</Row>
      </Section>
      <Section title="Supply">
        <Row label="LCSC">
          {link ? (
            <a
              href={link}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-0.5 text-primary underline-offset-2 hover:underline"
            >
              {component.lcsc}
              <ExternalLink className="size-2.5" aria-hidden />
            </a>
          ) : null}
        </Row>
        <Row label="Class">
          {part ? (
            <span className={part.basic ? "text-emerald-400" : "text-amber-400"}>{part.basic ? "Basic" : "Extended"}</span>
          ) : null}
        </Row>
        <Row label="Stock">{part?.stock != null ? part.stock.toLocaleString() : null}</Row>
        <Row label="Unit">{part?.unitPriceUsd != null ? `$${part.unitPriceUsd.toFixed(4)}` : null}</Row>
        <Row label="Checked">{part?.checked || null}</Row>
      </Section>
      <Section title={`Nets (${nets.length})`}>
        <div className="flex flex-wrap gap-1">
          {nets.map((net) => (
            <button
              key={net.key}
              type="button"
              onClick={() => onSelect?.({ kind: "net", key: net.key })}
              data-slot="property-net-link"
              className="rounded border border-border/50 bg-background/50 px-1.5 py-0.5 font-mono text-[11px] text-primary transition-colors hover:bg-accent"
            >
              {net.name}
            </button>
          ))}
          {!nets.length ? <span className="text-[11px] text-muted-foreground/50">no connections</span> : null}
        </div>
      </Section>
    </>
  );
}

function NetProperties({ net, index, fmt, onSelect }) {
  const members = [...net.componentKeys]
    .map((key) => index?.componentBySourceId.get(key))
    .filter(Boolean)
    .sort((a, b) => a.refdes.localeCompare(b.refdes));
  const vias = net.pcbElementIds.filter((id) => index?.byId.get(id)?.type === "pcb_via").length;

  return (
    <>
      <Section title="General">
        <Row label="Name">{net.name}</Row>
        <Row label="Class">{net.isGround ? "Ground" : net.isPower ? "Power" : "Signal"}</Row>
        <Row label="Named">{net.unnamed ? <span className="text-amber-400">no — synthesised</span> : "yes"}</Row>
      </Section>
      <Section title="Routing">
        <Row label="Pins">{net.pinCount || null}</Row>
        <Row label="Length">{net.lengthMm ? fmt(net.lengthMm) : null}</Row>
        <Row label="Vias">{vias || null}</Row>
        <Row label="Elements">{`${net.pcbElementIds.length} pcb · ${net.schematicElementIds.length} sch`}</Row>
      </Section>
      <Section title={`Connections (${members.length})`}>
        <div className="flex flex-wrap gap-1">
          {members.map((component) => (
            <button
              key={component.key}
              type="button"
              onClick={() => onSelect?.({ kind: "component", key: component.key })}
              data-slot="property-component-link"
              className="rounded border border-border/50 bg-background/50 px-1.5 py-0.5 font-mono text-[11px] text-primary transition-colors hover:bg-accent"
            >
              {component.refdes}
            </button>
          ))}
        </div>
      </Section>
    </>
  );
}

function BoardProperties({ index, sidecar, fmt }) {
  const board = index?.board;
  const pads = index?.elements.filter((e) => e.type === "pcb_smtpad" || e.type === "pcb_plated_hole").length || 0;
  const vias = index?.elements.filter((e) => e.type === "pcb_via").length || 0;
  const routed = index?.nets.reduce((sum, net) => sum + net.lengthMm, 0) || 0;

  return (
    <>
      <Section title="Board">
        <Row label="Name" mono={false}>
          {sidecar?.board?.name || null}
        </Row>
        <Row label="Size">{board ? `${fmt(board.width)} × ${fmt(board.height)}` : null}</Row>
        <Row label="Thickness">{board ? fmt(board.thickness) : null}</Row>
        <Row label="Layers">{board?.num_layers || null}</Row>
        <Row label="Material">{board?.material || null}</Row>
      </Section>
      <Section title="Contents">
        <Row label="Components">{index?.components.length || null}</Row>
        <Row label="Nets">{index?.nets.length || null}</Row>
        <Row label="Pads">{pads || null}</Row>
        <Row label="Vias">{vias || null}</Row>
        <Row label="Routed">{routed ? fmt(routed) : null}</Row>
      </Section>
      <Section title="Constraints">
        <Row label="Min track">{board?.min_trace_width ? fmt(board.min_trace_width) : null}</Row>
        <Row label="Min via">{board?.min_via_hole_diameter ? fmt(board.min_via_hole_diameter) : null}</Row>
        <Row label="Edge clr">{board?.min_board_edge_clearance ? fmt(board.min_board_edge_clearance) : null}</Row>
        <Row label="Pad clr">{board?.min_pad_edge_to_pad_edge_clearance ? fmt(board.min_pad_edge_to_pad_edge_clearance) : null}</Row>
      </Section>
      <Section title="Fab">
        <Row label="Profile">{sidecar?.fab?.profile || null}</Row>
        <Row label="Gerbers">{sidecar?.fab?.gerberSource || null}</Row>
        <Row label="Ready">
          {sidecar?.fab ? (
            <span className={sidecar.fab.ready ? "text-emerald-400" : "text-amber-400"}>
              {sidecar.fab.ready ? "yes" : "not yet"}
            </span>
          ) : null}
        </Row>
        <Row label="BOM">{sidecar?.bom ? `${sidecar.bom.lines} lines · ${sidecar.bom.orderable} orderable` : null}</Row>
      </Section>
    </>
  );
}
