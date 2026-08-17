import { useEffect, useMemo, useRef, useState } from "react";
import { ExternalLink, Lock, LockOpen, RotateCcw, RotateCw } from "lucide-react";
import { cn } from "@/ui/utils";
import NetWidthRow from "./NetWidthRow.jsx";
import { anchorPortForNet } from "./netWidth.js";
import { partPlainName, partRole } from "@/lib/plainLanguage.js";
import { lcscUrl } from "./boardData.js";
import { refusalForHit } from "./placementDrag.js";
import { CCW, CW } from "./placementRotate.js";
import { parseMmField, placementFields, placementMoveTo, placementSummary } from "./placementFields.js";

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

/**
 * A row that cannot be typed into, with the sentence that says why.
 *
 * The reason is the whole point of the component. A greyed field with no
 * explanation is the single fastest way to make someone believe a tool is
 * broken — they cannot tell "this part is drawn by a loop and has no number"
 * from "this app has a bug".
 */
function LockedRow({ label, value, reason }) {
  return (
    <div className="py-[3px]" data-slot="property-locked-row">
      <div className="flex items-baseline gap-2">
        <span className="w-20 shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground/70">{label}</span>
        <span className="min-w-0 flex-1 break-words font-mono text-[12px] text-muted-foreground">
          {value ?? <span className="text-muted-foreground/40">—</span>}
        </span>
      </div>
      {reason ? (
        <p data-slot="property-locked-reason" className="ml-[88px] text-[10px] leading-[14px] text-muted-foreground/70">
          {reason}
        </p>
      ) : null}
    </div>
  );
}

/**
 * One typed millimetre, committed into the board file.
 *
 * Enter commits and Escape abandons, the way they do everywhere else in this
 * workspace (`ChatInput` sends on Enter; `BoardWorkspace` and the canvas both
 * treat Escape as "abandon what you are doing"). Blur commits too — an
 * inspector that quietly discards a number you typed and clicked away from is
 * worse than one that writes it, and the write is undoable from the edit bar.
 *
 * A rejected value stays in the box with its reason underneath. Clearing the
 * field back to the old number would hide the mistake and lose the typing.
 */
function MmField({ label, field, hint, onCommit, disabled }) {
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState("");
  const inputRef = useRef(null);
  // A committed write comes back through `field.text`; dropping the draft when
  // it changes is what makes the box show the file rather than the typing.
  const committed = field.text;
  useEffect(() => {
    setDraft(null);
    setError("");
  }, [committed]);

  if (!field.editable) {
    return <LockedRow label={label} value={field.text || null} reason={field.reason} />;
  }

  const shown = draft === null ? committed : draft;
  const preview = draft === null ? null : parseMmField(draft, { limitMm: field.limitMm });

  const commit = () => {
    if (draft === null) return;
    const parsed = parseMmField(draft, { limitMm: field.limitMm });
    if (!parsed.ok) {
      setError(parsed.reason);
      return;
    }
    setError("");
    setDraft(null);
    if (parsed.value !== field.value) onCommit(parsed.value);
  };

  return (
    <div className="py-[3px]" data-slot="property-mm-field">
      <div className="flex items-baseline gap-2">
        <span className="w-20 shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground/70">{label}</span>
        <input
          ref={inputRef}
          type="text"
          inputMode="decimal"
          spellCheck={false}
          disabled={disabled}
          value={shown}
          aria-label={label}
          aria-invalid={error ? "true" : undefined}
          data-slot={`property-input-${String(label).toLowerCase()}`}
          onChange={(event) => {
            setDraft(event.target.value);
            if (error) setError("");
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              commit();
              return;
            }
            if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              setDraft(null);
              setError("");
              inputRef.current?.blur();
            }
          }}
          onBlur={commit}
          className={cn(
            "min-w-0 flex-1 rounded border bg-background/60 px-1.5 py-0.5 font-mono text-[12px] text-foreground",
            "outline-none focus:border-primary/60 disabled:opacity-40",
            error ? "border-destructive/70" : "border-border/60",
          )}
        />
        <span className="shrink-0 text-[10px] text-muted-foreground/60">mm</span>
      </div>
      {error ? (
        <p data-slot="property-input-error" className="ml-[88px] text-[10px] leading-[14px] text-destructive">
          {error}
        </p>
      ) : preview?.ok && preview.rounded ? (
        // Rounding is real but it is never silent: `formatMm` keeps three
        // decimals, which is a micron, and the field says so before Enter.
        <p data-slot="property-input-preview" className="ml-[88px] text-[10px] leading-[14px] text-muted-foreground/70">
          will be written {preview.written}
        </p>
      ) : hint ? (
        <p className="ml-[88px] text-[10px] leading-[14px] text-muted-foreground/70">{hint}</p>
      ) : null}
    </div>
  );
}

/**
 * The Placement section: the two numbers the board file actually owns, the
 * lock that tells the next agent a human chose them, and — as read-only rows
 * with their reasons — everything the file does not own.
 *
 * Position here is the *source* anchor, not the compiled centre. For a block
 * instance those differ: `<StatusLed pcbX={-45.8}>` is the frame, and LED1's
 * own centre is wherever the block puts it inside that frame. Typing into the
 * frame moves the whole block, which is why the summary line names the parts
 * before the number is committed.
 */
function PlacementSection({
  placement,
  editor,
  canEdit,
  viewing,
  board,
  builtRotation,
  refusal,
  units,
  onPlacementMove,
  onPlacementRotate,
}) {
  const fields = useMemo(
    () =>
      placementFields({
        placement,
        editor: {
          viewing,
          editing: canEdit,
          ready: Boolean(editor?.ready),
          reason: editor?.reason || "",
          busy: Boolean(editor?.busy),
        },
        board,
        builtRotation,
        refusal,
      }),
    [placement, editor?.ready, editor?.reason, editor?.busy, canEdit, viewing, board, builtRotation, refusal],
  );
  const summary = placementSummary(placement);

  const commitAxis = (axis, value) => {
    if (!placement) return;
    // Null when the typed number is the one already in the file. Same null the
    // canvas gets for a drag that snapped back to its own start, and it means
    // the same thing: nothing to write.
    const edit = placementMoveTo(placement, { [axis]: value });
    if (edit) onPlacementMove?.(placement, edit);
  };

  const withLimit = (field) => ({ ...field, limitMm: fields.limitMm });

  return (
    <Section title="Placement in the board file">
      {summary ? (
        <p data-slot="property-placement-summary" className="mb-1 text-[10px] leading-[14px] text-muted-foreground">
          <span className="font-mono text-foreground">{summary.label}</span> · {summary.moves}
        </p>
      ) : null}
      <MmField
        label="X"
        field={withLimit(fields.x)}
        disabled={fields.busy}
        // The units button switches the read-outs to mil; these two boxes are
        // the literals in the .tsx, which the compiler reads as millimetres.
        // Letting that go unsaid is how someone types 1800 meaning 45.8 mm.
        hint={units === "mil" ? "millimetres — the board file is mm whatever the units button says" : ""}
        onCommit={(value) => commitAxis("x", value)}
      />
      <MmField
        label="Y"
        field={withLimit(fields.y)}
        disabled={fields.busy}
        onCommit={(value) => commitAxis("y", value)}
      />
      {/* Two angles, on purpose. The big number is what the last BUILD drew —
          where the pads on screen actually are. The trailing note is what the
          board file says now, shown only when the two have parted company,
          which is the same honesty the X/Y rows keep about a moved part whose
          copper has not followed. */}
      <LockedRow
        label="Rotation"
        value={
          fields.rotation.value == null ? null : (
            <span className="inline-flex items-baseline gap-2">
              <span>{fields.rotation.value}°</span>
              {fields.rotation.fileValue != null && fields.rotation.fileValue !== fields.rotation.value ? (
                <span data-slot="property-rotation-pending" className="text-[10px] text-amber-500/90">
                  file says {fields.rotation.fileValue}° — rebuild to see it
                </span>
              ) : null}
              {fields.rotation.editable ? (
                <span className="inline-flex items-center gap-1">
                  {[
                    { direction: CCW, Icon: RotateCcw, title: "Turn left one step (Space while dragging)" },
                    { direction: CW, Icon: RotateCw, title: "Turn right one step (⇧Space while dragging)" },
                  ].map(({ direction, Icon, title }) => (
                    <button
                      key={direction}
                      type="button"
                      disabled={fields.busy}
                      data-slot={`property-rotate-${direction}`}
                      title={title}
                      // The same `commitRotateStep` the canvas key and the edit
                      // bar call, reached through the workspace's one handler.
                      // Three producers of a turn is three chances to disagree
                      // about which way is which.
                      onClick={() => onPlacementRotate?.(placement, { direction })}
                      className="rounded border border-border/60 p-0.5 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40"
                    >
                      <Icon className="size-3" aria-hidden />
                    </button>
                  ))}
                </span>
              ) : null}
            </span>
          )
        }
        reason={fields.rotation.reason}
      />
      {fields.lock.editable ? (
        <button
          type="button"
          disabled={fields.busy}
          onClick={() => editor?.setLock?.(placement.id, !fields.lock.value)}
          data-slot="property-lock"
          data-locked={fields.lock.value ? "true" : "false"}
          title={
            fields.lock.value
              ? "Unlock — this placement can be typed into and dragged again"
              : "Lock — writes a comment in the board file telling the next agent to leave this placement alone"
          }
          className={cn(
            "mt-1 inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] transition-colors",
            fields.lock.value
              ? "border-amber-500/50 bg-amber-500/20 text-foreground"
              : "border-border/60 text-muted-foreground hover:text-foreground",
          )}
        >
          {fields.lock.value ? <Lock className="size-3" aria-hidden /> : <LockOpen className="size-3" aria-hidden />}
          {fields.lock.value ? "Locked by hand" : "Lock this placement"}
        </button>
      ) : null}
      {/* The disagreement between the file and the drawing is the honest state
          after an edit, so it is printed rather than smoothed over. */}
      {summary?.pending ? (
        <p data-slot="property-placement-pending" className="mt-1 text-[10px] leading-[14px] text-amber-600 dark:text-amber-400">
          {summary.pending}
        </p>
      ) : null}
      {/* A refused write - SOURCE_CHANGED because an agent edited the file
          first, or BUILD_RUNNING - is the one failure the field itself cannot
          show, because it happens after the number left the box. */}
      {editor?.error ? (
        <p data-slot="property-placement-error" className="mt-1 text-[10px] leading-[14px] text-destructive">
          {editor.error}
        </p>
      ) : null}
    </Section>
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
 *
 * It is also the typing half of the IDE. A drag on the canvas and a number
 * typed here are the same edit — `placementMoveTo` builds it, and
 * `BoardWorkspace.handlePlacementMove` writes it — so this panel never learns
 * anything about the board file beyond what `usePlacementEditor` already knows.
 * A fourth mode, Placement, exists for the two things a drag can move that no
 * selection can carry: a mounting hole and a silkscreen label own no component
 * and no net, so clicking one leaves `selection` null.
 */
export default function PropertiesPanel({
  index = null,
  sidecar = null,
  selection = null,
  partsByLcscMap = null,
  units = "mm",
  onSelect,
  editor = null,
  canEdit = false,
  viewing = false,
  activePlacementId = "",
  onPlacementMove,
  onPlacementRotate,
  // What each net is routed at and what it could be — measured on demand by
  // `useNetWidths`, which the workspace owns because it is keyed to the build.
  netWidths = null,
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

  // The placement that owns the selected part — the block frame, not the part.
  // Resolved from the selection rather than from whatever was last clicked in
  // move mode, so the header and the coordinates can never describe two
  // different things.
  const componentPlacement = useMemo(() => {
    if (!component || !editor?.placements) return null;
    const id = editor.placements.byComponentKey?.get(component.key);
    return id ? editor.placements.byId?.get(id) || null : null;
  }, [component, editor?.placements]);

  const loosePlacement = useMemo(() => {
    if (component || net || !canEdit || !activePlacementId) return null;
    return editor?.placements?.byId?.get(activePlacementId) || null;
  }, [component, net, canEdit, activePlacementId, editor?.placements]);

  // Why this part has no numbers to type into, asked the same way the canvas
  // asks it. A synthetic hit is enough — `refusalForHit` only reads the
  // component key and the element type, and both are things a selection has.
  const refusal = useMemo(() => {
    if (!component || componentPlacement || !canEdit || !editor?.placements) return null;
    return refusalForHit(index, editor.placements, {
      componentKey: component.key,
      elementId: component.pcbId || "",
      element: null,
    });
  }, [component, componentPlacement, canEdit, editor?.placements, index]);

  const fmt = (mm) => (units === "mil" ? `${(NUM(mm) / 0.0254).toFixed(0)} mil` : `${NUM(mm).toFixed(2)} mm`);

  const placementProps = {
    editor,
    canEdit,
    viewing,
    units,
    board: index?.board || null,
    onPlacementMove,
    onPlacementRotate,
  };

  return (
    <aside
      data-slot="properties-panel"
      className={cn("scrollbar-thin flex w-64 shrink-0 flex-col overflow-y-auto border-l border-border/60 bg-card/30", className)}
    >
      <div className="flex h-8 shrink-0 items-center border-b border-border/60 px-3">
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          {component ? "Component" : net ? "Net" : loosePlacement ? "Placement" : "Board"}
        </span>
        {component ? <span className="ml-auto font-mono text-[12px] text-foreground">{component.refdes}</span> : null}
        {net ? <span className="ml-auto truncate font-mono text-[12px] text-foreground">{net.name}</span> : null}
        {!component && !net && loosePlacement ? (
          <span className="ml-auto truncate font-mono text-[12px] text-foreground">{loosePlacement.label}</span>
        ) : null}
      </div>

      {component ? (
        <ComponentProperties
          component={component}
          index={index}
          partsByLcscMap={partsByLcscMap}
          fmt={fmt}
          onSelect={onSelect}
          placement={componentPlacement}
          refusal={refusal}
          placementProps={placementProps}
        />
      ) : null}
      {net ? (
        <NetProperties
          net={net}
          index={index}
          fmt={fmt}
          onSelect={onSelect}
          editor={editor}
          canEdit={canEdit}
          netWidths={netWidths}
        />
      ) : null}
      {!component && !net && loosePlacement ? (
        <>
          <div className="border-b border-border/40 px-3 py-2">
            <p data-slot="property-plain-name" className="text-[13px] font-medium leading-5 text-foreground">
              {loosePlacement.label}
            </p>
            <p className="text-[11px] leading-4 text-muted-foreground">
              {`<${loosePlacement.tag}> in the board file — drawn, but not a part`}
            </p>
          </div>
          <PlacementSection placement={loosePlacement} builtRotation={null} {...placementProps} />
        </>
      ) : null}
      {!component && !net && !loosePlacement ? <BoardProperties index={index} sidecar={sidecar} fmt={fmt} /> : null}
    </aside>
  );
}

function ComponentProperties({ component, index, partsByLcscMap, fmt, onSelect, placement, refusal, placementProps }) {
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
      <PlacementSection placement={placement} refusal={refusal} builtRotation={NUM(component.rotation)} {...placementProps} />
      <Section title="As built">
        <Row label="Layer">{component.layer}</Row>
        {/* The compiled centre, which is NOT the number above it whenever a
            block placed this part: the block's frame is at the anchor and the
            part sits wherever the block puts it. Both are true, and after an
            edit and before a rebuild they disagree on purpose. */}
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

function NetProperties({ net, index, fmt, onSelect, editor, canEdit, netWidths }) {
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
        {/* The EE review's finding 4, where an engineer can act on it: what
            this net is routed at, what the placement will let it be, and a box
            to change it. Only in move mode — it writes the board file. */}
        {canEdit && netWidths ? (
          <NetWidthRow
            netName={net.name}
            widths={netWidths}
            editor={editor}
            anchorPort={anchorPortForNet(index, net.key)}
            onMeasure={netWidths.measure}
          />
        ) : null}
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
