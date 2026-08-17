import { useCallback, useEffect, useMemo, useState } from "react";
import { KeyboardIcon } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/ui/utils";
import { comboParts } from "./shortcutScan.js";
import { buildShortcutSheet, shortcutSheetKeyAction } from "./shortcutSheet.js";

// The shortcut sheet. Altium's equivalent is Shift+F1, "a menu listing all
// valid shortcuts" — the reason an EE can carry a hundred bindings without
// ever being taught them
// (https://www.altium.com/documentation/altium-designer/shortcut-keys).
//
// Everything on screen here comes from shortcutSheet.js, which reads the real
// resolvers rather than a list somebody wrote. This file only decides how it
// looks, and must stay that way: the moment a binding is typed into JSX, the
// sheet can start lying.

/** Asks whoever mounted the sheet to open it. Dispatched by the Help menu. */
export const OPEN_SHORTCUT_SHEET_EVENT = "autonomous:open-shortcut-sheet";

/** ⌘ on a Mac, Ctrl everywhere else — the same key, spelled the way it is printed. */
function modLabel() {
  if (typeof navigator === "undefined") return "Ctrl";
  const platform = String(navigator.platform || navigator.userAgent || "");
  return /Mac|iPhone|iPad/u.test(platform) ? "⌘" : "Ctrl";
}

/** One keycap. */
function Cap({ children }) {
  return (
    <kbd className="inline-flex min-w-[1.75rem] items-center justify-center rounded border border-border/70 bg-muted/60 px-1.5 py-0.5 font-mono text-[11px] leading-none text-foreground shadow-sm">
      {children}
    </kbd>
  );
}

/** `Mod+PgDn` → ⌘ + PgDn, one cap per key, `+` between them. */
function Combo({ combo, mod }) {
  const parts = comboParts(combo).map((part) => (part === "Mod" ? mod : part));
  return (
    <span className="inline-flex items-center gap-0.5 whitespace-nowrap">
      {parts.map((part, i) => (
        <span key={`${part}-${i}`} className="inline-flex items-center gap-0.5">
          {i > 0 ? <span className="text-[10px] text-muted-foreground">+</span> : null}
          <Cap>{part}</Cap>
        </span>
      ))}
    </span>
  );
}

/**
 * The sheet itself, as a controlled dialog.
 *
 * `filter` is a plain substring match over the label, the combo and the
 * command id. With thirty bindings a search box is not strictly needed; it is
 * here because the question a user actually has is "what is the key for X",
 * and scanning five sections to answer it is the learning curve again.
 */
export function ShortcutSheet({ open, onOpenChange }) {
  const [query, setQuery] = useState("");
  const mod = useMemo(() => modLabel(), []);
  const groups = useMemo(() => buildShortcutSheet(), []);

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return groups;
    return groups
      .map((group) => ({
        ...group,
        rows: group.rows.filter((row) =>
          `${row.label} ${row.combos.join(" ")} ${row.when} ${row.note || ""} ${row.id}`
            .toLowerCase()
            .includes(needle),
        ),
      }))
      .filter((group) => group.rows.length > 0);
  }, [groups, query]);

  const total = useMemo(() => groups.reduce((sum, group) => sum + group.rows.length, 0), [groups]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-slot="shortcut-sheet"
        className="max-h-[85vh] gap-0 overflow-hidden p-0 sm:max-w-3xl"
        // Belt to `boardKeymap.isOverlayTarget`'s braces. Radix's focus scope
        // is a `tabIndex=-1` div, so one click on a group heading moves focus
        // off the search box while staying inside the trap — and from there
        // every plain letter reached the board behind the modal. The arbiter
        // now refuses anything from inside a dialog; this stops the event
        // travelling at all, so a window listener added later without that
        // guard still cannot see it.
        onKeyDown={(event) => event.stopPropagation()}
      >
        <DialogHeader className="border-b border-border/60 px-5 py-4">
          <DialogTitle className="flex items-center gap-2 text-base">
            <KeyboardIcon className="size-4 opacity-70" aria-hidden="true" />
            Keyboard shortcuts
          </DialogTitle>
          <DialogDescription className="text-xs">
            {total} keys, read out of the code that handles them. Some only work in the situation
            named next to them.
          </DialogDescription>
          <input
            type="search"
            value={query}
            autoFocus
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search a key or an action"
            aria-label="Search shortcuts"
            className="mt-2 w-full rounded border border-border/60 bg-background/60 px-2 py-1 text-[13px] outline-none focus:border-primary/60"
          />
        </DialogHeader>

        <div className="overflow-y-auto px-5 py-4">
          {shown.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No shortcut matches “{query.trim()}”.
            </p>
          ) : null}
          <div className="grid gap-6 sm:grid-cols-2">
            {shown.map((group) => (
              <section key={group.id} className="min-w-0">
                <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {group.title}
                </h3>
                <dl className="space-y-1.5">
                  {group.rows.map((row) => (
                    <div key={`${group.id}:${row.id}`} className="flex items-baseline gap-3">
                      <dt className="flex shrink-0 items-center gap-1.5 pt-0.5">
                        {row.combos.map((combo) => (
                          <Combo key={combo} combo={combo} mod={mod} />
                        ))}
                      </dt>
                      <dd className="min-w-0 text-[13px] leading-snug text-foreground">
                        {row.label}
                        {row.when ? (
                          <span className="text-muted-foreground"> — {row.when}</span>
                        ) : null}
                        {/* Where our key means something other than what the
                            same key means in Altium, say so here. A gesture
                            that exists and does something else is worse than a
                            missing one: nobody finds out until they have done
                            it. */}
                        {row.note ? (
                          <span
                            data-slot="shortcut-note"
                            className="mt-0.5 block text-[11px] leading-4 text-muted-foreground/80"
                          >
                            {row.note}
                          </span>
                        ) : null}
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            ))}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Drop-in: the key, the menu item, and the dialog in one element.
 *
 * Mount it once anywhere inside the board workspace:
 *
 *   <ShortcutSheetHost />
 *
 * It owns its own binding through `shortcutSheetKeyAction`, the same pure
 * resolver the sheet lists itself from, so the key on screen and the key that
 * works are the same fact. `className` positions the button; pass
 * `button={false}` to keep only the keyboard entry point.
 */
export function ShortcutSheetHost({ className, button = true, label = "Shortcuts" }) {
  const [open, setOpen] = useState(false);

  // The menu-bar entry point. `WindowMenuBar` renders above the workspace and
  // owns no board state, so it asks by event rather than by prop — the same
  // shape `FOCUS_CHAT_INPUT_EVENT` already uses to cross that boundary.
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const onOpen = () => setOpen(true);
    window.addEventListener(OPEN_SHORTCUT_SHEET_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_SHORTCUT_SHEET_EVENT, onOpen);
  }, []);

  useEffect(() => {
    const onKey = (event) => {
      const action = shortcutSheetKeyAction(event, { open });
      if (!action) return;
      // Radix already closes on Escape; taking the key here as well would fight
      // it. The resolver still reports `sheet.close` because the sheet's own
      // list has to name the key that shuts it.
      if (action === "sheet.close") return;
      event.preventDefault();
      setOpen((value) => !value);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const onOpenChange = useCallback((value) => setOpen(value), []);

  return (
    <>
      {button ? (
        <button
          type="button"
          data-slot="shortcut-sheet-button"
          onClick={() => setOpen(true)}
          title="Keyboard shortcuts (?)"
          aria-label="Keyboard shortcuts"
          className={cn(
            "inline-flex items-center gap-1.5 rounded border border-border/60 bg-background/60 px-2 py-1",
            "text-[11px] text-muted-foreground hover:text-foreground hover:border-border",
            className,
          )}
        >
          <KeyboardIcon className="size-3.5" aria-hidden="true" />
          {label ? <span>{label}</span> : null}
          <kbd className="font-mono text-[10px] opacity-70">?</kbd>
        </button>
      ) : null}
      <ShortcutSheet open={open} onOpenChange={onOpenChange} />
    </>
  );
}

export default ShortcutSheetHost;
