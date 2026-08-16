import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";

import { cn } from "@/ui/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { BOARD_CONTEXT_ACTIONS, boardContextMenu, mmToOffset, offsetToMm } from "./boardContextMenu.js";

/**
 * A row the renderer cannot run.
 *
 * Not a thrown error — losing the whole workspace because one menu row is
 * mis-wired is worse than the mis-wiring. Not silence either: silence is how
 * `WindowMenuBar`'s Undo taught users the app loses their work. The console is
 * where the person who can fix it is looking.
 */
function reportUnhandled(item, why) {
  // eslint-disable-next-line no-console
  console.error(`[board-context-menu] "${item?.label || item?.id}" did nothing: ${why}`);
}

/**
 * The right-click menu over the PCB canvas.
 *
 * Deliberately dumb. Every decision about which rows appear, what they are
 * called and why one is disabled lives in `boardContextMenu.js`, which is pure
 * and tested; this file turns that model into Radix menu items and maps each
 * `action` id onto a callback the workspace already has. There is no `if` about
 * content here, on purpose — a rule that exists in two places is a rule that
 * will disagree with itself.
 *
 * `request` is what the pointer hands over when it decides a right-click was a
 * menu rather than a cancel or a pan: `{hit, hits, point, pointLabel, client}`,
 * frozen at the release. The press hit-tests once and that hit lasts the life
 * of the menu — a header that disagrees with the row under it is the worst kind
 * of bug to chase.
 *
 * **Why this is a DropdownMenu and not Radix's ContextMenu.** Radix's
 * `ContextMenu.Root` is strictly uncontrolled — in radix-ui 1.4.x it takes
 * `{__scopeContextMenu, children, onOpenChange, dir, modal}` and holds its own
 * `useState(false)`, with no `open` prop — so the only thing that can open it
 * is the DOM `contextmenu` event landing on its trigger. That is precisely the
 * event our canvases must swallow: macOS fires `contextmenu` on *press*, before
 * the pointer has travelled, so opening there makes Altium's right-drag pan
 * impossible to tell from a right-click. The canvas therefore decides on
 * release (`canvasPointer.pointerReleaseAction`) and hands the decision over,
 * and a menu that can be *told* to open is the only kind that can receive it.
 * `DropdownMenu.Root` is controlled; a zero-size trigger pinned at the release
 * coordinates gives Radix something to anchor and collision-flip against.
 */
export default function BoardContextMenu({
  open = false,
  onOpenChange,
  request = null,
  index = null,
  placements = null,
  editor = null,
  messageRows = null,
  selection = null,
  canEdit = false,
  viewing = false,
  showGrid = true,
  units = "mm",
  boardName = "",
  onSelect,
  onProperties,
  onJump,
  onLocate,
  onZoomBox,
  onLock,
  onMoveExact,
  onPrefill,
  onFit,
  onClearSelection,
  onToggleGrid,
  onToggleUnits,
  onToggleEdit,
}) {
  const [moveTarget, setMoveTarget] = useState(null);

  const model = useMemo(
    () =>
      boardContextMenu({
        hit: request?.hit ?? null,
        hits: request?.hits ?? null,
        point: request?.point ?? null,
        pointLabel: request?.pointLabel ?? "",
        index,
        placements,
        editor,
        messageRows,
        selection,
        canEdit,
        viewing,
        showGrid,
        units,
        boardName,
      }),
    [request, index, placements, editor, messageRows, selection, canEdit, viewing, showGrid, units, boardName],
  );

  const run = useCallback(
    (item) => {
      const payload = item.payload || {};
      // Every callback below is optional, so a wiring owner who forgets one
      // ships an enabled row that swallows the click in silence. Naming the
      // prop out loud is the cheapest thing that is not silence.
      if (!BOARD_CONTEXT_ACTIONS.has(item.action)) {
        reportUnhandled(item, `"${item.action}" is not in BOARD_CONTEXT_ACTIONS`);
        return;
      }
      switch (item.action) {
        case "select":
          onSelect?.(payload.target);
          break;
        case "properties":
          // Selecting is what switches the Properties panel; a workspace that
          // also needs to raise the panel passes `onProperties`.
          (onProperties || onSelect)?.(payload.target);
          break;
        case "jump":
          onJump?.(payload.target, { jump: true, source: payload.source || "pcb" });
          break;
        case "locate":
          onLocate?.(payload.row);
          break;
        case "zoom-box":
          onZoomBox?.(payload.box);
          break;
        case "lock":
          onLock?.(payload.placementId, payload.locked);
          break;
        case "move-exact":
          setMoveTarget(placements?.byId?.get(payload.placementId) || null);
          break;
        case "prefill":
          onPrefill?.(payload.text);
          break;
        case "fit":
          onFit?.();
          break;
        case "clear-selection":
          onClearSelection?.();
          break;
        case "toggle-grid":
          onToggleGrid?.();
          break;
        case "toggle-units":
          onToggleUnits?.();
          break;
        case "toggle-edit":
          onToggleEdit?.(payload.on);
          break;
        default:
          // An enabled row that reaches here is a row that did nothing, which
          // is the one failure this menu may not have. `BOARD_CONTEXT_ACTIONS`
          // and this switch are held against each other by a test that reads
          // both; this is the runtime half, for a row built at runtime.
          reportUnhandled(item, "no case in BoardContextMenu handles this action");
          break;
      }
    },
    [
      onSelect,
      onProperties,
      onJump,
      onLocate,
      onZoomBox,
      onLock,
      onPrefill,
      onFit,
      onClearSelection,
      onToggleGrid,
      onToggleUnits,
      onToggleEdit,
      placements,
    ],
  );

  const anchor = request?.client || null;

  return (
    <>
      {/* Modal, which is Radix's default and the right one here: the click
          that dismisses a context menu must not also act on the board behind
          it. Non-modal, the dismissing left-click reached the canvas and
          changed the selection on the way out — a menu you cannot close
          without side effects. */}
      <DropdownMenu open={open && Boolean(anchor)} onOpenChange={onOpenChange}>
        {/* A 1x1 anchor pinned where the press was released. Radix needs a real
            element to measure against — that is what gives the menu its
            collision flipping near the bottom and right edges of the pane. It
            is `fixed` because `request.client` is viewport coordinates, and
            inert because nothing should ever be able to click it. */}
        <DropdownMenuTrigger
          aria-hidden
          tabIndex={-1}
          data-slot="board-context-anchor"
          style={{
            position: "fixed",
            left: anchor ? `${anchor.x}px` : 0,
            top: anchor ? `${anchor.y}px` : 0,
            width: 1,
            height: 1,
            pointerEvents: "none",
            opacity: 0,
          }}
        />
        <DropdownMenuContent
          data-slot="board-context-menu"
          align="start"
          side="bottom"
          sideOffset={0}
          className="min-w-56"
          // The board keeps its selection and its net highlight while the menu
          // is up; bouncing focus back to a 1x1 anchor on close is the only
          // sane place for it to land.
          onCloseAutoFocus={(event) => event.preventDefault()}
        >
          <DropdownMenuLabel className="flex flex-col gap-0.5">
            <span className="font-mono text-[11px] text-foreground">{model.header.label}</span>
            {model.header.detail ? (
              <span className="font-mono text-[10px] text-muted-foreground/80">{model.header.detail}</span>
            ) : null}
          </DropdownMenuLabel>
          {model.groups.map((group, order) => (
            <Fragment key={group.id}>
              {order === 0 ? null : <DropdownMenuSeparator />}
              <div data-slot="board-context-group" data-group={group.id} role="group">
                {group.items.map((item) => (
                  <MenuRow key={item.id} item={item} onRun={run} />
                ))}
              </div>
            </Fragment>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      <MoveExactDialog
        placement={moveTarget}
        units={units}
        onClose={() => setMoveTarget(null)}
        onSubmit={(next) => {
          setMoveTarget(null);
          onMoveExact?.(next);
        }}
      />
    </>
  );
}

/** One row: a plain item, or a submenu when it carries children. */
function MenuRow({ item, onRun }) {
  if (item.children) {
    return (
      <DropdownMenuSub>
        <DropdownMenuSubTrigger data-slot="board-context-submenu" data-id={item.id}>
          {item.label}
          <ChevronRight className="ml-auto size-3.5 opacity-60" aria-hidden />
        </DropdownMenuSubTrigger>
        <DropdownMenuSubContent className="min-w-52">
          {item.children.map((child) => (
            <MenuRow key={child.id} item={child} onRun={onRun} />
          ))}
        </DropdownMenuSubContent>
      </DropdownMenuSub>
    );
  }

  return (
    <DropdownMenuItem
      data-id={item.id}
      data-writes={item.writes ? "true" : "false"}
      disabled={item.disabled}
      onSelect={() => onRun(item)}
      // A reason nobody can read is not a reason: Radix's own disabled styling
      // fades the whole row to 50%, so the fade is turned off and the label
      // alone carries the disabled look.
      className={cn("flex-col items-start gap-0.5", item.disabled ? "data-[disabled]:opacity-100" : "")}
    >
      <span className={item.disabled ? "text-muted-foreground" : ""}>{item.label}</span>
      {/* A disabled row without a reason is indistinguishable from a bug. */}
      {item.disabled && item.reason ? (
        <span data-slot="board-context-reason" className="text-[11px] leading-snug text-muted-foreground/80">
          {item.reason}
        </span>
      ) : null}
    </DropdownMenuItem>
  );
}

/**
 * Altium's Get X/Y Offsets, which is how an EE moves a part by a number rather
 * than by hand ([get-x-y-offsets](https://www.altium.com/documentation/cstu/get-x-y-offsets)).
 * Its `Ctrl+Q` flips the dialog between imperial and metric, and that binding is
 * carried over here — it is the only place in this app where `Ctrl+Q` means
 * anything, `Q` alone already flipping the whole document's units.
 *
 * Offsets are taken exactly, never snapped to the move grid: the grid exists
 * because a pointer cannot be precise, and someone typing 0.35 already is.
 */
function MoveExactDialog({ placement, units, onClose, onSubmit }) {
  const [dx, setDx] = useState("0");
  const [dy, setDy] = useState("0");
  const [local, setLocal] = useState(units);

  const open = Boolean(placement);
  const unit = local === "mil" ? "mil" : "mm";
  const dxMm = offsetToMm(dx, unit);
  const dyMm = offsetToMm(dy, unit);
  const valid = dxMm !== null && dyMm !== null && (dxMm !== 0 || dyMm !== 0);

  const flipUnits = useCallback(() => {
    const next = unit === "mil" ? "mm" : "mil";
    setDx(formatOffset(offsetToMm(dx, unit), next));
    setDy(formatOffset(offsetToMm(dy, unit), next));
    setLocal(next);
  }, [dx, dy, unit]);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) return;
        onClose?.();
        setDx("0");
        setDy("0");
        setLocal(units);
      }}
    >
      <DialogContent
        data-slot="move-exact-dialog"
        className="sm:max-w-sm"
        // `ctrlKey` only — never `metaKey`. This is a Tauri app and the shell
        // installs `PredefinedMenuItem::quit` (desktop/src-tauri/src/menu.rs),
        // whose macOS key equivalent is Cmd+Q; AppKit consumes a matching key
        // equivalent in `performKeyEquivalent` before the event ever reaches
        // WKWebView, so `preventDefault()` here cannot stop the app quitting
        // with a half-typed offset in the box. Altium is Windows-only and its
        // own binding is Ctrl+Q anyway
        // (https://www.altium.com/documentation/cstu/get-x-y-offsets).
        onKeyDown={(event) => {
          if (event.ctrlKey && !event.metaKey && event.key.toLowerCase() === "q") {
            event.preventDefault();
            flipUnits();
          }
        }}
      >
        <DialogHeader>
          <DialogTitle>Move {placement?.label || "part"} by an exact amount</DialogTitle>
          <DialogDescription>
            Offsets from where the board file puts it now. Positive Y is up, the way the board reads.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!placement || !valid) return;
            onSubmit?.({
              placementId: placement.id,
              label: placement.label,
              dx: dxMm,
              dy: dyMm,
              x: placement.x + dxMm,
              y: placement.y + dyMm,
            });
          }}
        >
          <div className="flex items-end gap-2">
            <label className="flex flex-1 flex-col gap-1 text-xs text-muted-foreground">
              X offset
              <input
                autoFocus
                value={dx}
                onChange={(event) => setDx(event.target.value)}
                data-slot="move-exact-dx"
                className="rounded border border-border/60 bg-transparent px-2 py-1 font-mono text-sm text-foreground"
              />
            </label>
            <label className="flex flex-1 flex-col gap-1 text-xs text-muted-foreground">
              Y offset
              <input
                value={dy}
                onChange={(event) => setDy(event.target.value)}
                data-slot="move-exact-dy"
                className="rounded border border-border/60 bg-transparent px-2 py-1 font-mono text-sm text-foreground"
              />
            </label>
            <button
              type="button"
              onClick={flipUnits}
              data-slot="move-exact-units"
              title="Switch between mm and mil (Ctrl+Q)"
              className="rounded border border-border/60 px-2 py-1 font-mono text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              {unit}
            </button>
          </div>
          <p className="font-mono text-[11px] text-muted-foreground/80">
            {placement
              ? `${placement.x.toFixed(3)}, ${placement.y.toFixed(3)} mm → ${(placement.x + (dxMm || 0)).toFixed(3)}, ${(placement.y + (dyMm || 0)).toFixed(3)} mm`
              : ""}
          </p>
          {/* The same honest line the edit bar prints after a drag: the board on
              screen is the last build, and the copper has not moved with it. */}
          <p className="text-[11px] leading-snug text-muted-foreground/80">
            This writes the board file. The drawing stays as the last build until you rebuild.
          </p>
          <DialogFooter>
            <button
              type="button"
              onClick={() => onClose?.()}
              className="rounded border border-border/60 px-2 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!valid}
              data-slot="move-exact-apply"
              className="rounded border border-primary/50 bg-primary/15 px-2 py-1 text-xs font-medium text-foreground transition-colors hover:bg-primary/25 disabled:opacity-40"
            >
              Move it
            </button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function formatOffset(mm, units) {
  const value = mmToOffset(mm ?? 0, units);
  return units === "mil" ? value.toFixed(1) : value.toFixed(3);
}
