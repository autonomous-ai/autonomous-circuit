import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  CircleCheck,
  CircleDashed,
  Loader2,
  TriangleAlert,
  Wrench,
} from "lucide-react";
import { cn } from "@/ui/utils";
import {
  IMPACT,
  boardShapeLine,
  boardVerdict,
  groupFixRequest,
  impactCounts,
  partsCostUsd,
  plainParts,
  plural,
} from "@/lib/plainLanguage.js";

const IMPACT_COPY = {
  [IMPACT.BLOCKS]: {
    label: "Stops the order",
    note: "The factory packet does not ship while any of these are open.",
  },
  [IMPACT.QUALITY]: {
    label: "Worth fixing",
    note: "The board would build, but these are real risks in the copper or the parts list.",
  },
  [IMPACT.COSMETIC]: {
    label: "Looks only",
    note: "These change how the board reads, never whether it works.",
  },
  [IMPACT.TOOLING]: {
    label: "About the checker, not the board",
    note: "Missing libraries and file-to-file bookkeeping. Nothing physical changes.",
  },
};

function Section({ title, hint, action = null, children, className }) {
  return (
    <section className={cn("flex flex-col gap-2", className)}>
      <div className="flex items-baseline gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{title}</h3>
        {hint ? <span className="truncate text-[11px] text-muted-foreground/70">{hint}</span> : null}
        {action ? <span className="ml-auto">{action}</span> : null}
      </div>
      {children}
    </section>
  );
}

/** One collapsible issue group: plain title, count, meaning, Fix. */
function IssueRow({ group, boardName, onFix, onLocate, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div
      data-slot="overview-issue"
      data-code={group.code}
      data-blocking={group.blocking ? "true" : "false"}
      className={cn(
        "rounded-lg border",
        group.blocking ? "border-amber-500/40 bg-amber-500/[0.05]" : "border-border/60 bg-card/30",
      )}
    >
      <div className="flex items-start gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="mt-0.5 shrink-0 text-muted-foreground transition-colors hover:text-foreground"
          aria-expanded={open}
          aria-label={open ? "Hide the individual findings" : "Show the individual findings"}
        >
          <Chevron className="size-3.5" aria-hidden />
        </button>
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium leading-5 text-foreground">
            {group.title}
            {group.count > 1 ? (
              <span className="ml-1.5 font-mono text-[11px] tabular-nums text-muted-foreground">×{group.count}</span>
            ) : null}
          </p>
          {group.meaning ? (
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{group.meaning}</p>
          ) : null}
          {!group.known ? (
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground/70">
              We do not have plain words for this check yet — the raw finding is below.
            </p>
          ) : null}
          {group.parts.length ? (
            <p className="mt-1 truncate font-mono text-[11px] text-muted-foreground/70" title={group.parts.join(", ")}>
              {group.parts.slice(0, 8).join(" · ")}
              {group.parts.length > 8 ? ` · +${group.parts.length - 8} more` : ""}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => onFix?.(groupFixRequest(group, { board: boardName }))}
          data-slot="overview-fix"
          title="Hand this to the chat as a repair request"
          className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-border/60 bg-card/60 px-2 text-xs font-medium text-foreground transition-colors hover:border-primary/60 hover:bg-accent"
        >
          <Wrench className="size-3 " aria-hidden />
          Fix
        </button>
      </div>
      {open ? (
        <div className="border-t border-border/40">
          {group.rows.slice(0, 40).map((row, i) => (
            <button
              key={row.id || i}
              type="button"
              onClick={() => onLocate?.(row)}
              disabled={!row.locatable}
              className={cn(
                "flex w-full items-start gap-2 px-3 py-1 text-left text-[11px] leading-5",
                row.locatable ? "hover:bg-accent/40" : "cursor-default",
              )}
            >
              <span className="w-24 shrink-0 truncate font-mono text-muted-foreground">{row.part || "board"}</span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground/80">{row.detail}</span>
            </button>
          ))}
          {group.rows.length > 40 ? (
            <p className="px-3 py-1 text-[11px] text-muted-foreground/60">
              +{group.rows.length - 40} more of the same, in the Messages panel.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * One impact bucket. "Worth fixing" opens on arrival because those are real;
 * cosmetic and checker-setup findings stay folded, because their whole point
 * is that you do not have to read them — but the count is still on screen, so
 * nothing is being hidden from you.
 */
function IssueBucket({ bucket, boardName, onFix, onLocate }) {
  const copy = IMPACT_COPY[bucket.impact];
  const total = bucket.groups.reduce((sum, group) => sum + group.count, 0);
  const [open, setOpen] = useState(bucket.impact === IMPACT.QUALITY);
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div data-slot="overview-bucket" data-impact={bucket.impact}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-baseline gap-1.5 text-left"
      >
        <Chevron className="size-3.5 shrink-0 translate-y-0.5 text-muted-foreground" aria-hidden />
        <span className="text-[12px] font-medium text-foreground">{copy.label}</span>
        <span className="font-mono text-[11px] tabular-nums text-muted-foreground">{total}</span>
        <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{copy.note}</span>
      </button>
      {open ? (
        <div className="mt-2 flex flex-col gap-1.5">
          {bucket.groups.map((group) => (
            <IssueRow key={group.code} group={group} boardName={boardName} onFix={onFix} onLocate={onLocate} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Overview — the tab for someone who does not read schematics.
 *
 * Everything else in this workspace is a view of the *design*. This is a view
 * of the *situation*: can it be made, what is it, what is on it, and what is
 * left to do. It is the first tab and the default one, because a person who
 * has never opened an EDA tool lands on Split otherwise and sees two drawings
 * with no way in.
 *
 * Every number here comes out of a file the pipeline wrote. Where a number
 * does not exist — a fab price we were never quoted — the section says who
 * quotes it instead of estimating one.
 */
export default function OverviewTab({
  sidecar = null,
  index = null,
  groups = [],
  parts = [],
  building = false,
  buildLine = null,
  historyLine = null,
  boardName = "",
  product = null,
  artifact = null,
  onFix,
  onLocate,
  onSelect,
  onOpenTab,
  className,
}) {
  const verdict = useMemo(() => boardVerdict({ sidecar, groups, building }), [sidecar, groups, building]);
  const counts = useMemo(() => impactCounts(groups), [groups]);
  const roles = useMemo(() => plainParts(index), [index]);
  const cost = useMemo(() => partsCostUsd(parts, index), [parts, index]);
  const shape = boardShapeLine({ sidecar, index });

  const rest = groups.filter((group) => !group.blocking);
  const byImpact = [IMPACT.QUALITY, IMPACT.COSMETIC, IMPACT.TOOLING]
    .map((impact) => ({ impact, groups: rest.filter((group) => group.impact === impact) }))
    .filter((bucket) => bucket.groups.length);

  const VerdictIcon =
    verdict.tone === "ready"
      ? CircleCheck
      : verdict.tone === "blocked"
        ? TriangleAlert
        : verdict.tone === "building"
          ? Loader2
          : CircleDashed;

  return (
    <div
      data-slot="board-overview"
      className={cn("scrollbar-thin min-h-0 flex-1 overflow-y-auto", className)}
    >
      <div className="mx-auto flex max-w-3xl flex-col gap-6 px-5 py-5">
        {/* --- the verdict, in full ------------------------------------- */}
        <div
          data-slot="overview-verdict"
          data-tone={verdict.tone}
          className={cn(
            "rounded-xl border p-4",
            verdict.tone === "ready"
              ? "border-emerald-500/40 bg-emerald-500/[0.06]"
              : verdict.tone === "blocked"
                ? "border-amber-500/40 bg-amber-500/[0.05]"
                : "border-border/60 bg-card/30",
          )}
        >
          <div className="flex items-start gap-3">
            <VerdictIcon
              className={cn(
                "mt-0.5 size-5 shrink-0",
                verdict.tone === "ready"
                  ? "text-emerald-500"
                  : verdict.tone === "blocked"
                    ? "text-amber-500"
                    : "text-muted-foreground",
                verdict.tone === "building" ? "animate-spin" : "",
              )}
              aria-hidden
            />
            <div className="min-w-0 flex-1">
              <h2 className="text-lg font-semibold tracking-tight text-foreground">{verdict.headline}</h2>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">
                {verdict.tone === "building" && buildLine?.text
                  ? `${buildLine.text}${buildLine.detail ? ` — step ${buildLine.detail}` : ""}.`
                  : verdict.blockingGroups.length
                    ? // The strip above already carries the one-line version.
                      // Here the list is right below, so the sentence's job is
                      // to say what the list is for.
                      "The factory packet ships once these are clear. Each one goes straight to the chat as a repair request."
                    : verdict.line}
              </p>

              {verdict.tone === "ready" ? (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => onOpenTab?.("fab")}
                    className="inline-flex h-8 items-center gap-1.5 rounded-md border border-emerald-500/50 bg-emerald-500/10 px-3 text-sm font-medium text-foreground transition-colors hover:bg-emerald-500/20"
                  >
                    Order at JLCPCB
                  </button>
                  <span className="text-xs text-muted-foreground">
                    Five boards is the usual first run. The Fab tab has the exact clicks.
                  </span>
                </div>
              ) : null}
            </div>
          </div>

          {/* The one line that makes 694 findings readable: almost none of
              them are about whether you can order the board. Stated up here
              so nobody scrolls a wall of amber thinking it is all bad news. */}
          {counts.total ? (
            <p data-slot="overview-impact" className="mt-3 text-xs leading-5 text-muted-foreground">
              The checks produced <span className="font-mono tabular-nums text-foreground">{counts.total}</span>{" "}
              findings.{" "}
              <span className="text-foreground">
                {counts.blocks
                  ? `${counts.blocks} ${counts.blocks === 1 ? "stops" : "stop"}`
                  : "None stop"}
              </span>{" "}
              the order
              {counts.quality ? (
                <>
                  , <span className="font-mono tabular-nums">{counts.quality}</span> are worth fixing
                </>
              ) : null}
              , and the other <span className="font-mono tabular-nums">{counts.cosmetic + counts.tooling}</span> are
              cosmetic or about the checker's own setup.
            </p>
          ) : null}

          {/* Where the board came from. A count on its own is a complaint; a
              count with a direction is a tool converging — or, when it went the
              other way, the one thing the user most needs told. Absent below
              two recorded rounds, because one point is not a trend. */}
          {historyLine ? (
            <p
              data-slot="overview-history"
              data-tone={historyLine.tone}
              className={cn(
                "mt-3 text-xs leading-5",
                historyLine.tone === "worse" ? "text-amber-500" : "text-muted-foreground",
              )}
            >
              {historyLine.text}
            </p>
          ) : null}

          {/* What's left — the blocking groups as a checklist. */}
          {verdict.blockingGroups.length ? (
            <div className="mt-4 flex flex-col gap-2" data-slot="overview-blockers">
              {verdict.blockingGroups.map((group) => (
                <IssueRow
                  key={group.code}
                  group={group}
                  boardName={boardName}
                  onFix={onFix}
                  onLocate={onLocate}
                />
              ))}
            </div>
          ) : null}
        </div>

        {/* --- what this board is ----------------------------------------- */}
        {product?.description || shape ? (
          <Section title="What this is">
            <div className="rounded-xl border border-border/60 bg-card/30 p-4">
              <p className="text-sm font-medium text-foreground">{product?.name || boardName || "This board"}</p>
              {product?.description ? (
                <p className="mt-1 text-sm leading-6 text-muted-foreground">{product.description}</p>
              ) : null}
              {shape ? <p className="mt-2 font-mono text-[11px] text-muted-foreground/80">{shape}</p> : null}
            </div>
          </Section>
        ) : null}

        {/* --- what's on it ------------------------------------------------ */}
        {roles.length ? (
          <Section
            title="What's on it"
            hint="click a part to find it on the board"
            action={
              <button
                type="button"
                onClick={() => onOpenTab?.("function")}
                className="shrink-0 text-[11px] text-muted-foreground underline-offset-2 transition-colors hover:text-foreground hover:underline"
              >
                Does it do what you asked?
              </button>
            }
          >
            <div className="flex flex-col divide-y divide-border/40 overflow-hidden rounded-xl border border-border/60 bg-card/30">
              {roles.map((role) => (
                <div key={role.role} data-slot="overview-role" data-role={role.role} className="flex gap-3 p-3">
                  <div className="w-36 shrink-0">
                    <p className="text-[13px] font-medium text-foreground">{role.label}</p>
                    {role.blurb ? (
                      <p className="text-[11px] leading-4 text-muted-foreground/80">{role.blurb}</p>
                    ) : null}
                  </div>
                  <div className="flex min-w-0 flex-1 flex-wrap gap-1">
                    {role.items.slice(0, 26).map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        onClick={(event) =>
                          onSelect?.({ kind: "component", key: item.key }, { jump: event.metaKey || event.ctrlKey })
                        }
                        title={[item.name, item.mpn, item.lcsc].filter(Boolean).join(" · ")}
                        className="inline-flex max-w-full items-center gap-1.5 rounded border border-border/60 bg-background/60 px-1.5 py-0.5 text-[11px] transition-colors hover:border-primary/60 hover:bg-accent"
                      >
                        <span className="font-mono text-foreground">{item.refdes}</span>
                        <span className="truncate text-muted-foreground">{item.name}</span>
                      </button>
                    ))}
                    {role.items.length > 26 ? (
                      <span className="self-center text-[11px] text-muted-foreground/70">
                        +{role.items.length - 26} more
                      </span>
                    ) : null}
                  </div>
                  <span className="w-8 shrink-0 text-right font-mono text-[11px] tabular-nums text-muted-foreground">
                    {role.count}
                  </span>
                </div>
              ))}
            </div>
          </Section>
        ) : null}

        {/* --- what the parts cost ---------------------------------------- */}
        <Section title="What it costs">
          <div className="rounded-xl border border-border/60 bg-card/30 p-4 text-sm leading-6 text-muted-foreground">
            {cost ? (
              <>
                <p className="text-foreground">
                  <span className="font-mono tabular-nums">${cost.usd.toFixed(2)}</span> of parts per board
                </p>
                <p className="text-xs">
                  {cost.complete
                    ? "Every part on the list has a checked LCSC price."
                    : `${plural(cost.unpriced, "part")} on the list has no checked price yet, so the real total is higher.`}
                </p>
              </>
            ) : Number.isFinite(Number(sidecar?.bom?.estimatedCostUsd)) &&
              Number(sidecar.bom.estimatedCostUsd) > 0 ? (
              <>
                <p className="text-foreground">
                  <span className="font-mono tabular-nums">
                    ${Number(sidecar.bom.estimatedCostUsd).toFixed(2)}
                  </span>{" "}
                  of parts per board
                </p>
                <p className="text-xs">The build's own estimate, from the BOM it exported.</p>
              </>
            ) : (
              <p className="text-xs">
                No checked prices on file yet. Ask the chat to price the parts and it will pin each one to a real
                LCSC number with stock and price.
              </p>
            )}
            <p className="mt-2 text-xs">
              The bare board and the assembly are quoted by the factory when you upload the packet — we do not
              guess those numbers here.
            </p>
          </div>
        </Section>

        {/* --- everything else the checker said ---------------------------- */}
        {byImpact.length ? (
          <Section
            title="Everything else the checks found"
            hint={`${counts.total} findings in total`}
          >
            <div className="flex flex-col gap-4">
              {byImpact.map((bucket) => (
                <IssueBucket
                  key={bucket.impact}
                  bucket={bucket}
                  boardName={boardName}
                  onFix={onFix}
                  onLocate={onLocate}
                />
              ))}
            </div>
          </Section>
        ) : null}

        {/* --- where to look when it arrives -------------------------------- */}
        {artifact?.kicadProjectUrl || roles.length ? (
          <Section title="When the boards arrive">
            <div className="rounded-xl border border-border/60 bg-card/30 p-4 text-sm leading-6 text-muted-foreground">
              <ArrivalChecklist roles={roles} />
            </div>
          </Section>
        ) : null}
      </div>
    </div>
  );
}

/**
 * "How you'll know it's alive" — built only from parts the board actually has,
 * so every line is checkable against the BOM. No line is emitted for a thing
 * that is not there, and none of them promises behaviour we cannot see in the
 * files: they say where to look, not what will happen.
 */
function ArrivalChecklist({ roles }) {
  const byRole = new Map(roles.map((role) => [role.role, role]));
  const items = [];

  const powerIn = byRole.get("power_in");
  if (powerIn?.items?.length) {
    const first = powerIn.items[0];
    items.push(`Power goes in at ${first.refdes} — ${first.name.toLowerCase()}. That is the only place a cable belongs.`);
  }
  const indicator = byRole.get("indicator");
  if (indicator?.items?.length) {
    items.push(
      `${indicator.items.length === 1 ? "The light" : `The ${indicator.items.length} lights`} (${indicator.items
        .slice(0, 6)
        .map((item) => item.refdes)
        .join(", ")}) are what the board shows you. Watch these first.`,
    );
  }
  const brain = byRole.get("brain");
  if (brain?.items?.length) {
    items.push(
      `${brain.items[0].refdes} is the chip that runs your code. Until you load firmware onto it, the board does nothing on its own.`,
    );
  }
  const control = byRole.get("control");
  if (control?.items?.length) {
    items.push(`${plural(control.items.length, "button")} to press: ${control.items.slice(0, 8).map((item) => item.refdes).join(", ")}.`);
  }
  const mech = byRole.get("mechanical");
  if (mech?.items?.length) {
    items.push(`${plural(mech.items.length, "hole or test point", "holes and test points")} for mounting and for probing with a meter.`);
  }

  if (!items.length) {
    return <p className="text-xs">Nothing on this board identifies itself yet — build it and this list fills in.</p>;
  }

  return (
    <ul className="flex list-disc flex-col gap-1.5 pl-4 text-xs leading-5">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}
