import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  CircleCheck,
  CircleDashed,
  Cpu,
  Plug,
  TriangleAlert,
} from "lucide-react";
import { cn } from "@/ui/utils";
import {
  FUNCTION_STATUS,
  brainSignals,
  findBrain,
  functionRows,
  functionSummary,
  looseEnds,
  railRows,
  splitSignals,
} from "@/lib/boardFunction.js";
import { plural } from "@/lib/plainLanguage.js";

const STATUS_STYLE = {
  [FUNCTION_STATUS.BRAIN]: { icon: Cpu, tint: "text-sky-400", ring: "border-sky-500/40 bg-sky-500/[0.05]" },
  [FUNCTION_STATUS.SIGNAL]: {
    icon: CircleCheck,
    tint: "text-emerald-500",
    ring: "border-border/60 bg-card/30",
  },
  [FUNCTION_STATUS.POWER]: { icon: Plug, tint: "text-muted-foreground", ring: "border-border/60 bg-card/30" },
  [FUNCTION_STATUS.ISOLATED]: {
    icon: TriangleAlert,
    tint: "text-amber-500",
    ring: "border-amber-500/40 bg-amber-500/[0.05]",
  },
};

function Section({ title, hint, children }) {
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{title}</h3>
        {hint ? <span className="truncate text-[11px] text-muted-foreground/70">{hint}</span> : null}
      </div>
      {children}
    </section>
  );
}

/** A refdes chip that selects the part everywhere else in the workspace. */
function PartChip({ refdes, componentKey, title, onSelect }) {
  return (
    <button
      type="button"
      onClick={(event) =>
        onSelect?.({ kind: "component", key: componentKey }, { jump: event.metaKey || event.ctrlKey })
      }
      title={title || refdes}
      data-slot="function-part"
      className="inline-flex max-w-full items-center rounded border border-border/60 bg-background/60 px-1.5 py-0.5 font-mono text-[11px] text-foreground transition-colors hover:border-primary/60 hover:bg-accent"
    >
      {refdes}
    </button>
  );
}

/** A net chip. Clicking one lights the whole net in both drawings. */
function NetChip({ net, netKey, pins = [], onSelect }) {
  return (
    <button
      type="button"
      onClick={(event) => onSelect?.({ kind: "net", key: netKey }, { jump: event.metaKey || event.ctrlKey })}
      title={pins.length ? `${net} — reaches the brain on ${pins.join(", ")}` : net}
      data-slot="function-net"
      className="inline-flex max-w-full items-center gap-1 rounded border border-primary/30 bg-primary/[0.07] px-1.5 py-0.5 font-mono text-[11px] transition-colors hover:border-primary/60 hover:bg-accent"
    >
      <span className="text-foreground">{net}</span>
      {pins.length ? <span className="text-muted-foreground">→ {pins.join("/")}</span> : null}
    </button>
  );
}

function AreaRow({ row, onSelect, index }) {
  const [open, setOpen] = useState(false);
  const style = STATUS_STYLE[row.status] || STATUS_STYLE[FUNCTION_STATUS.POWER];
  const Icon = style.icon;
  const Chevron = open ? ChevronDown : ChevronRight;
  const shown = open ? row.refdes : row.refdes.slice(0, 10);
  return (
    <div
      data-slot="function-area"
      data-status={row.status}
      className={cn("rounded-lg border px-3 py-2.5", style.ring)}
    >
      <div className="flex items-start gap-2.5">
        <Icon className={cn("mt-0.5 size-4 shrink-0", style.tint)} aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-baseline gap-x-2 text-[13px] font-medium leading-5 text-foreground">
            {row.label}
            {row.detail ? <span className="text-[11px] font-normal text-muted-foreground">{row.detail}</span> : null}
            {row.region?.fromBoardFile ? (
              <span
                title="These parts were written straight into the board file rather than coming from a validated block."
                className="rounded border border-border/60 px-1 text-[10px] font-normal text-muted-foreground"
              >
                on the board, not from a block
              </span>
            ) : null}
          </p>
          <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{row.sentence}</p>

          {row.signals.length ? (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {row.signals.map((signal) => (
                <NetChip
                  key={signal.netKey}
                  net={signal.net}
                  netKey={signal.netKey}
                  pins={signal.pins}
                  onSelect={onSelect}
                />
              ))}
            </div>
          ) : null}

          {row.refdes.length ? (
            <div className="mt-1.5 flex flex-wrap items-center gap-1">
              {shown.map((refdes) => {
                const component = index?.componentByRefdes?.get(refdes);
                return (
                  <PartChip
                    key={refdes}
                    refdes={refdes}
                    componentKey={component?.key || refdes}
                    title={component ? `${refdes} — ${component.mpn || component.ftype}` : refdes}
                    onSelect={onSelect}
                  />
                );
              })}
              {row.refdes.length > shown.length ? (
                <button
                  type="button"
                  onClick={() => setOpen(true)}
                  className="text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                >
                  +{row.refdes.length - shown.length} more
                </button>
              ) : null}
              {open && row.refdes.length > 10 ? (
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                >
                  <Chevron className="inline size-3" aria-hidden /> fewer
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/**
 * "Does this board do what I asked?" — the tab the verdict strip cannot answer.
 *
 * `BoardVerdict` says whether the board can be *made*. This says what it is
 * *wired to do*, and the two are completely different questions for someone
 * who cannot read a schematic: a board can be perfectly manufacturable with the
 * sensor connected to nothing.
 *
 * Every claim on this page is a fact about the netlist — a part is on a net or
 * it is not, that net reaches a named pin on the microcontroller or it does
 * not. Nothing here is a model's impression of the design, because a confident
 * sentence about a wire that does not exist is the one failure that makes this
 * feature worse than having none. Where the chain does not close, the row says
 * we could not confirm it.
 */
export default function FunctionTab({
  index = null,
  product = null,
  regions = [],
  requestText = "",
  planText = "",
  boardName = "",
  onSelect,
  onOpenTab,
  className,
}) {
  const brain = useMemo(() => findBrain(index), [index]);
  const rows = useMemo(() => functionRows(index, regions), [index, regions]);
  const summary = useMemo(() => functionSummary(rows, { brain }), [rows, brain]);
  const signals = useMemo(() => splitSignals(brainSignals(index, brain)), [index, brain]);
  const rails = useMemo(() => railRows(index, brain), [index, brain]);
  const ends = useMemo(() => looseEnds(index), [index]);
  const [showInternal, setShowInternal] = useState(false);
  const [showPlan, setShowPlan] = useState(false);

  const isolated = rows.filter((row) => row.status === FUNCTION_STATUS.ISOLATED);
  const SummaryIcon =
    summary.tone === "traced" ? CircleCheck : summary.tone === "gap" ? TriangleAlert : CircleDashed;

  return (
    <div
      data-slot="board-function"
      className={cn("scrollbar-thin min-h-0 flex-1 overflow-y-auto", className)}
    >
      <div className="mx-auto flex max-w-3xl flex-col gap-6 px-5 py-5">
        {/* --- the headline ------------------------------------------------ */}
        <div
          data-slot="function-summary"
          data-tone={summary.tone}
          className={cn(
            "rounded-xl border p-4",
            summary.tone === "traced"
              ? "border-emerald-500/40 bg-emerald-500/[0.06]"
              : summary.tone === "gap"
                ? "border-amber-500/40 bg-amber-500/[0.05]"
                : "border-border/60 bg-card/30",
          )}
        >
          <div className="flex items-start gap-3">
            <SummaryIcon
              className={cn(
                "mt-0.5 size-5 shrink-0",
                summary.tone === "traced"
                  ? "text-emerald-500"
                  : summary.tone === "gap"
                    ? "text-amber-500"
                    : "text-muted-foreground",
              )}
              aria-hidden
            />
            <div className="min-w-0 flex-1">
              <h2 className="text-lg font-semibold tracking-tight text-foreground">{summary.headline}</h2>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">{summary.line}</p>
            </div>
          </div>
          {/* The limit of the claim, stated where the claim is made rather
              than in a footnote nobody reaches. */}
          <p className="mt-3 border-t border-border/40 pt-3 text-xs leading-5 text-muted-foreground">
            This is read off the netlist the build produced: what is joined to what, and on which pin. It cannot tell
            you the firmware works, that a part value suits your use, or that what got built is what you pictured.
          </p>
        </div>

        {/* --- what was asked for ------------------------------------------ */}
        {product?.description || requestText || planText ? (
          <Section title="What you asked for">
            <div className="flex flex-col gap-2 rounded-xl border border-border/60 bg-card/30 p-4">
              {requestText ? (
                <div>
                  <p className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground/70">Your words</p>
                  <p data-slot="function-request" className="mt-1 text-sm leading-6 text-foreground">
                    “{requestText}”
                  </p>
                </div>
              ) : null}
              {product?.description ? (
                <div className={requestText ? "border-t border-border/40 pt-2" : ""}>
                  <p className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground/70">
                    Written down as the product
                  </p>
                  <p className="mt-1 text-sm leading-6 text-muted-foreground">
                    <span className="text-foreground">{product.name || boardName}</span> — {product.description}
                  </p>
                </div>
              ) : null}
              {planText ? (
                <div className="border-t border-border/40 pt-2">
                  <button
                    type="button"
                    onClick={() => setShowPlan((value) => !value)}
                    aria-expanded={showPlan}
                    data-slot="function-plan-toggle"
                    className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.12em] text-muted-foreground/70 transition-colors hover:text-foreground"
                  >
                    {showPlan ? (
                      <ChevronDown className="size-3" aria-hidden />
                    ) : (
                      <ChevronRight className="size-3" aria-hidden />
                    )}
                    The plan you approved
                  </button>
                  {showPlan ? (
                    <pre
                      data-slot="function-plan"
                      className="scrollbar-thin mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border/60 bg-background/50 p-3 text-[11px] leading-5 text-muted-foreground"
                    >
                      {planText}
                    </pre>
                  ) : null}
                </div>
              ) : null}
              <p className="text-xs leading-5 text-muted-foreground/80">
                Nothing below is matched against these words — a machine cannot check that. What it can do is show you
                every part of the board and exactly what it is wired to, so you can.
              </p>
            </div>
          </Section>
        ) : null}

        {/* --- the areas ---------------------------------------------------- */}
        {rows.length ? (
          <Section title="Every area of the board" hint="click a part or a net to find it in the drawings">
            <div className="flex flex-col gap-1.5">
              {rows.map((row) => (
                <AreaRow key={row.id} row={row} onSelect={onSelect} index={index} />
              ))}
            </div>
          </Section>
        ) : null}

        {/* --- what the brain is wired to ----------------------------------- */}
        {brain ? (
          <Section
            title={`What ${brain.refdes || "the brain"} is wired to`}
            hint={`${plural(signals.external.length, "signal")} leaving the chip`}
          >
            <div className="overflow-hidden rounded-xl border border-border/60 bg-card/30">
              {signals.external.length ? (
                <table className="w-full text-left text-xs" data-slot="function-signals">
                  <thead>
                    <tr className="border-b border-border/40 text-[11px] uppercase tracking-[0.08em] text-muted-foreground">
                      <th className="px-3 py-1.5 font-medium">Signal</th>
                      <th className="px-3 py-1.5 font-medium">Pin</th>
                      <th className="px-3 py-1.5 font-medium">What is on it</th>
                    </tr>
                  </thead>
                  <tbody>
                    {signals.external.map((signal) => (
                      <tr key={signal.netKey} className="border-b border-border/20 last:border-0">
                        <td className="px-3 py-1.5 align-top">
                          <NetChip net={signal.net} netKey={signal.netKey} onSelect={onSelect} />
                        </td>
                        <td className="px-3 py-1.5 align-top font-mono text-[11px] text-muted-foreground">
                          {signal.pins.join(", ") || "—"}
                        </td>
                        <td className="px-3 py-1.5 align-top">
                          <div className="flex flex-wrap gap-1">
                            {signal.others.slice(0, 8).map((other) => (
                              <PartChip
                                key={other.key}
                                refdes={other.refdes}
                                componentKey={other.key}
                                title={other.name}
                                onSelect={onSelect}
                              />
                            ))}
                            {signal.others.length > 8 ? (
                              <span className="self-center text-[11px] text-muted-foreground/70">
                                +{signal.others.length - 8}
                              </span>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="px-3 py-2.5 text-xs leading-5 text-muted-foreground">
                  Nothing outside the chip's own block is wired to any of its pins. Whatever this board is meant to
                  do, the program has no way to do it.
                </p>
              )}

              {signals.internal.length ? (
                <div className="border-t border-border/40">
                  <button
                    type="button"
                    onClick={() => setShowInternal((value) => !value)}
                    aria-expanded={showInternal}
                    className="flex w-full items-center gap-1.5 px-3 py-1.5 text-left text-[11px] text-muted-foreground transition-colors hover:text-foreground"
                  >
                    {showInternal ? (
                      <ChevronDown className="size-3" aria-hidden />
                    ) : (
                      <ChevronRight className="size-3" aria-hidden />
                    )}
                    {plural(signals.internal.length, "more signal")}{" "}
                    {signals.internal.length === 1 ? "stays" : "stay"} inside the chip's own block — its flash,
                    crystal and reset
                  </button>
                  {showInternal ? (
                    <div className="flex flex-wrap gap-1 px-3 pb-2">
                      {signals.internal.map((signal) => (
                        <NetChip
                          key={signal.netKey}
                          net={signal.net}
                          netKey={signal.netKey}
                          pins={signal.pins}
                          onSelect={onSelect}
                        />
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}

              {signals.empty.length ? (
                <p
                  data-slot="function-empty-pins"
                  className="border-t border-border/40 px-3 py-2 text-[11px] leading-5 text-muted-foreground"
                >
                  {plural(signals.empty.length, "pin")} named in the design with nothing attached:{" "}
                  <span className="font-mono text-foreground">
                    {signals.empty.map((signal) => signal.pins.join("/") || signal.net).join(", ")}
                  </span>
                  . Often deliberate — a debug pad kept for later — but worth a look if one of them was meant to be
                  your sensor.
                </p>
              ) : null}
            </div>
          </Section>
        ) : null}

        {/* --- power -------------------------------------------------------- */}
        {rails.length ? (
          <Section title="Power">
            <div className="flex flex-col divide-y divide-border/40 overflow-hidden rounded-xl border border-border/60 bg-card/30">
              {rails.map((rail) => (
                <div key={rail.netKey} className="flex items-baseline gap-2 px-3 py-2" data-slot="function-rail">
                  <NetChip net={rail.net} netKey={rail.netKey} onSelect={onSelect} />
                  <span className="text-xs text-muted-foreground">
                    {plural(rail.parts, "part")} sit{rail.parts === 1 ? "s" : ""} on it
                    {brain
                      ? rail.feedsBrain
                        ? `, including ${brain.refdes || "the brain"}`
                        : `. ${brain.refdes || "The brain"} is not one of them`
                      : ""}
                    .
                  </span>
                </div>
              ))}
            </div>
          </Section>
        ) : null}

        {/* --- what we could not confirm ------------------------------------
            Amber only when something is actually adrift. A deliberate debug pad
            is worth listing and is not worth a warning colour — colouring every
            loose end alarming is how a panel gets ignored. */}
        {isolated.length || ends.unconnected.length || ends.dangling.length ? (
          <Section title="What we could not confirm">
            <div
              className={cn(
                "flex flex-col gap-2 rounded-xl border p-4 text-xs leading-5 text-muted-foreground",
                isolated.length || ends.unconnected.length
                  ? "border-amber-500/30 bg-amber-500/[0.03]"
                  : "border-border/60 bg-card/30",
              )}
            >
              {isolated.length ? (
                <p data-slot="function-isolated">
                  <span className="text-foreground">
                    {plural(isolated.length, "area")}{" "}
                    {isolated.length === 1 ? "connects" : "connect"} to nothing else on the board
                  </span>{" "}
                  — {isolated.map((row) => row.label).join(", ")}. Parts that are placed but not joined to the circuit
                  get built and soldered and then do nothing.
                </p>
              ) : null}
              {ends.unconnected.length ? (
                <p data-slot="function-unconnected">
                  <span className="text-foreground">
                    {plural(ends.unconnected.length, "part")}{" "}
                    {ends.unconnected.length === 1 ? "has pins but sits" : "have pins but sit"} on no net
                  </span>{" "}
                  —{" "}
                  <span className="font-mono">
                    {ends.unconnected.slice(0, 12).map((part) => part.refdes).join(", ")}
                  </span>
                  .
                </p>
              ) : null}
              {ends.dangling.length ? (
                <p data-slot="function-dangling">
                  <span className="text-foreground">
                    {plural(ends.dangling.length, "named net")}{" "}
                    {ends.dangling.length === 1 ? "touches" : "touch"} only one part
                  </span>{" "}
                  —{" "}
                  <span className="font-mono">{ends.dangling.slice(0, 12).map((net) => net.net).join(", ")}</span>. A
                  net with one end was named and never joined to anything else. Sometimes that is deliberate — a
                  debug pad, the last link in a chain — and sometimes it is the connection you asked for.
                </p>
              ) : null}
              <p className="text-muted-foreground/80">
                Ask the chat about any of these by name and it will trace them in the source.
              </p>
            </div>
          </Section>
        ) : null}

        <p className="pb-2 text-xs leading-5 text-muted-foreground/70">
          Want the drawings instead?{" "}
          <button
            type="button"
            onClick={() => onOpenTab?.("split")}
            className="underline underline-offset-2 hover:text-foreground"
          >
            Open the schematic and the board side by side
          </button>{" "}
          — every chip above selects the same thing there.
        </p>
      </div>
    </div>
  );
}
