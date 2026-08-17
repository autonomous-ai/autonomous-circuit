"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Copy, Minus, Square, X } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { isEditableTarget } from "@/ui/dom";
import { transport, isTauriRuntime } from "@/lib/transport.ts";
import circuitLogoUrl from "@/assets/favicon.png";
import { OPEN_SHORTCUT_SHEET_EVENT } from "./board/ShortcutSheet.jsx";
import { BOARD_MENU_ITEMS, runBoardMenuCommand } from "./board/boardMenuCommands.js";
import { comboFor } from "./board/shortcutSheet.js";

/**
 * In-window menu bar (Windows-style row) mirroring the native macOS application
 * menu (see `desktop/src-tauri/src/menu.rs`). The native menu lives in the OS
 * global menu bar at the top of the *screen* and only shows when Panda is the
 * frontmost app; this row renders inside the webview so the same actions are
 * always reachable *on the window*. Both can coexist — this duplicates, it does
 * not replace.
 *
 * Height is fixed at `h-7` (1.75rem / 28px). `main.jsx` reserves that strip at
 * the top of the app and offsets the workspace + chat sidebar by the same
 * amount; keep the three in sync if you change it.
 */

const MENU_TRIGGER_CLASS =
  "inline-flex h-6 cursor-default select-none items-center rounded px-2 outline-none " +
  "hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent " +
  "data-[state=open]:bg-accent data-[state=open]:text-accent-foreground";

// Native-feeling window control buttons: full-bar height, ~46px wide, no
// rounding so they read as part of the chrome. Close gets a red hover.
const WINDOW_CONTROL_CLASS =
  "inline-flex h-7 w-11 cursor-default items-center justify-center text-foreground/80 " +
  "outline-none transition-colors hover:bg-accent hover:text-accent-foreground";
const WINDOW_CLOSE_CLASS =
  "inline-flex h-7 w-11 cursor-default items-center justify-center text-foreground/80 " +
  "outline-none transition-colors hover:bg-red-600 hover:text-white";

// Edit commands run against the webview's currently-focused editable element.
// Mirrors the native Edit menu's predefined items.
function runEditCommand(command) {
  try {
    document.execCommand(command);
  } catch {
    /* execCommand unsupported / nothing focused — no-op */
  }
}

export default function WindowMenuBar() {
  const [aboutOpen, setAboutOpen] = useState(false);
  const [version, setVersion] = useState("");
  const [isMaximized, setIsMaximized] = useState(false);
  // Whether the Edit menu has anything to act on. Every item in that menu runs
  // `execCommand` against a focused editable element, so with no field focused
  // all seven of them do nothing at all — and an Undo that silently does
  // nothing is worse than no Undo, because it is a promise in writing. Greyed
  // out is the honest state. Board edits are NOT undone here: the board file's
  // 50-entry history lives on the PCB edit strip and on ⌘Z over the canvas
  // (`board/boardKeymap.js` → `edit.undo`).
  const [hasEditTarget, setHasEditTarget] = useState(false);

  // Window controls only mean anything inside Tauri (outside, `windowAction`
  // no-ops); gate the render so a plain browser doesn't show dead buttons.
  const showWindowControls = isTauriRuntime();

  // Opening a dropdown steals focus from whatever field the user was editing,
  // which would make cut/copy/paste/select-all act on nothing. Snapshot the
  // focused editable element (and its selection) at pointer-down — before focus
  // moves into the menu — then restore it right before running the command.
  const editTargetRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    // Source the About version from app_info. v1 has no updater (Create-only),
    // so there's no latest.json feed to prefer.
    transport
      .app_info()
      .then((info) => {
        if (!cancelled) setVersion(String(info?.appVersion || ""));
      })
      .catch(() => {
        /* version unavailable: leave blank, About shows the tagline */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Keep the maximize/restore icon in sync with the actual window state — the
  // user can maximize via the OS (snap, double-click drag region) too, so poll
  // the window on every resize rather than just toggling our own state.
  useEffect(() => {
    if (!showWindowControls) return undefined;
    let cancelled = false;
    let unlisten;
    (async () => {
      try {
        const { getCurrentWindow } = await import("@tauri-apps/api/window");
        const appWindow = getCurrentWindow();
        const sync = async () => {
          try {
            const maximized = await appWindow.isMaximized();
            if (!cancelled) setIsMaximized(maximized);
          } catch {
            /* window gone — ignore */
          }
        };
        await sync();
        unlisten = await appWindow.onResized(() => void sync());
        if (cancelled) unlisten?.();
      } catch {
        /* not in Tauri / window API unavailable — leave default */
      }
    })();
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [showWindowControls]);

  const captureEditTarget = useCallback(() => {
    const el = document.activeElement;
    if (!isEditableTarget(el)) {
      editTargetRef.current = null;
      setHasEditTarget(false);
      return;
    }
    setHasEditTarget(true);
    const snapshot = { el, start: null, end: null, range: null };
    if (typeof el.selectionStart === "number") {
      snapshot.start = el.selectionStart;
      snapshot.end = el.selectionEnd;
    } else {
      const selection = window.getSelection();
      if (selection && selection.rangeCount > 0) {
        snapshot.range = selection.getRangeAt(0).cloneRange();
      }
    }
    editTargetRef.current = snapshot;
  }, []);

  const restoreEditTarget = useCallback(() => {
    const snapshot = editTargetRef.current;
    if (!snapshot || !snapshot.el || !document.contains(snapshot.el)) {
      return;
    }
    snapshot.el.focus();
    if (snapshot.start !== null && typeof snapshot.el.setSelectionRange === "function") {
      try {
        snapshot.el.setSelectionRange(snapshot.start, snapshot.end);
      } catch {
        /* element type without ranged selection (e.g. email input) */
      }
    } else if (snapshot.range) {
      const selection = window.getSelection();
      if (selection) {
        selection.removeAllRanges();
        selection.addRange(snapshot.range);
      }
    }
  }, []);

  const runEdit = useCallback(
    (command) => {
      restoreEditTarget();
      if (command === "paste") {
        // execCommand('paste') is blocked in most webviews; read the clipboard
        // and insert at the caret instead. Best-effort — falls back to the
        // native Edit menu / Cmd+V if clipboard access is denied.
        navigator.clipboard
          ?.readText()
          .then((text) => {
            if (text) document.execCommand("insertText", false, text);
          })
          .catch(() => runEditCommand("paste"));
        return;
      }
      runEditCommand(command);
    },
    [restoreEditTarget],
  );

  const windowAction = useCallback(async (action) => {
    if (!isTauriRuntime()) return;
    try {
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      const appWindow = getCurrentWindow();
      if (action === "minimize") await appWindow.minimize();
      else if (action === "zoom") await appWindow.toggleMaximize();
      else if (action === "close") await appWindow.close();
    } catch {
      /* window controls are Tauri-only — no-op elsewhere */
    }
  }, []);

  return (
    <div
      data-slot="window-menu-bar"
      className="flex h-7 w-full shrink-0 select-none items-center gap-0.5 border-b border-border/60 bg-background/95 px-1.5 text-xs font-medium text-foreground/90 backdrop-blur"
    >
      <img
        src={circuitLogoUrl}
        alt="Autonomous Circuit"
        draggable={false}
        className="ml-0.5 mr-1 size-4 shrink-0 rounded-[3px]"
      />

      <DropdownMenu>
        <DropdownMenuTrigger className={MENU_TRIGGER_CLASS}>Circuit</DropdownMenuTrigger>
        <DropdownMenuContent align="start" sideOffset={4} className="min-w-44">
          <DropdownMenuItem onSelect={() => setAboutOpen(true)}>About Autonomous Circuit</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <DropdownMenu>
        <DropdownMenuTrigger
          className={MENU_TRIGGER_CLASS}
          onPointerDownCapture={captureEditTarget}
          // Opening the menu from the keyboard means focus was already on this
          // trigger, so there is no field behind it — capture there too and let
          // the items grey themselves out rather than reusing a stale snapshot
          // from the last time the menu was opened with the mouse.
          onKeyDownCapture={captureEditTarget}
        >
          Edit
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          sideOffset={4}
          className="min-w-44"
          // Keep focus on the restored field instead of bouncing it back to the
          // trigger, so the caret stays where the user left it after editing.
          onCloseAutoFocus={(event) => event.preventDefault()}
        >
          <DropdownMenuItem disabled={!hasEditTarget} onSelect={() => runEdit("undo")}>
            Undo
          </DropdownMenuItem>
          <DropdownMenuItem disabled={!hasEditTarget} onSelect={() => runEdit("redo")}>
            Redo
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled={!hasEditTarget} onSelect={() => runEdit("cut")}>
            Cut
          </DropdownMenuItem>
          <DropdownMenuItem disabled={!hasEditTarget} onSelect={() => runEdit("copy")}>
            Copy
          </DropdownMenuItem>
          <DropdownMenuItem disabled={!hasEditTarget} onSelect={() => runEdit("paste")}>
            Paste
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled={!hasEditTarget} onSelect={() => runEdit("selectAll")}>
            Select All
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {/* The board's own commands. Three panel rounds reported that where
          this bar renders at all it carried nothing but the webview's text
          Cut/Copy/Paste — so "Undo" in the Edit menu above undoes typing, not
          a part move, which is a trap rather than a gap. These are the board's,
          they run the same handler the keys do, and each shows the key so the
          menu teaches the shortcut instead of replacing it. */}
      <DropdownMenu>
        <DropdownMenuTrigger className={MENU_TRIGGER_CLASS}>Board</DropdownMenuTrigger>
        <DropdownMenuContent align="start" sideOffset={4} className="min-w-56">
          {BOARD_MENU_ITEMS.map((item, at) =>
            item === null ? (
              <DropdownMenuSeparator key={`sep-${at}`} />
            ) : (
              <DropdownMenuItem
                key={item.command}
                data-slot={`board-menu-${item.command}`}
                onSelect={() => runBoardMenuCommand(item.command)}
              >
                {item.label}
                {comboFor(item.command) ? (
                  <DropdownMenuShortcut>{comboFor(item.command)}</DropdownMenuShortcut>
                ) : null}
              </DropdownMenuItem>
            ),
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      <DropdownMenu>
        <DropdownMenuTrigger className={MENU_TRIGGER_CLASS}>Help</DropdownMenuTrigger>
        <DropdownMenuContent align="start" sideOffset={4} className="min-w-44">
          {/* Altium reaches its shortcut list from Shift+F1 and from Help.
              A key you have to already know is not discoverability, and this
              bar was the one place in the app that listed nothing about keys.
              The board workspace mounts the sheet; this asks it to open.
              https://www.altium.com/documentation/altium-designer/shortcut-keys */}
          <DropdownMenuItem
            onSelect={() => window.dispatchEvent(new Event(OPEN_SHORTCUT_SHEET_EVENT))}
          >
            Keyboard Shortcuts
            <DropdownMenuShortcut>?</DropdownMenuShortcut>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Minimize / Zoom / Close Window need a desktop shell to mean
          anything; in a browser tab they are three dead rows. */}
      {showWindowControls ? (
      <DropdownMenu>
        <DropdownMenuTrigger className={MENU_TRIGGER_CLASS}>Window</DropdownMenuTrigger>
        <DropdownMenuContent align="start" sideOffset={4} className="min-w-44">
          <DropdownMenuItem onSelect={() => void windowAction("minimize")}>Minimize</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void windowAction("zoom")}>Zoom</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => void windowAction("close")}>Close Window</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      ) : null}

      {/* Draggable region: fills the gap between the menus and the window
          controls so the user can move the (undecorated, Windows-only) window
          by dragging the bar. `data-tauri-drag-region` is a no-op outside Tauri. */}
      <div className="h-full flex-1 self-stretch" data-tauri-drag-region />

      {showWindowControls && (
        // -mr-1.5 cancels the bar's right padding so the close button reaches
        // the window edge, the way native controls do.
        <div className="-mr-1.5 flex items-center self-stretch">
          <button
            type="button"
            aria-label="Minimize"
            title="Minimize"
            className={WINDOW_CONTROL_CLASS}
            onClick={() => void windowAction("minimize")}
          >
            <Minus className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label={isMaximized ? "Restore" : "Maximize"}
            title={isMaximized ? "Restore" : "Maximize"}
            className={WINDOW_CONTROL_CLASS}
            onClick={() => void windowAction("zoom")}
          >
            {isMaximized ? (
              <Copy className="h-3 w-3" aria-hidden="true" />
            ) : (
              <Square className="h-3 w-3" aria-hidden="true" />
            )}
          </button>
          <button
            type="button"
            aria-label="Close"
            title="Close"
            className={WINDOW_CLOSE_CLASS}
            onClick={() => void windowAction("close")}
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      )}

      <Dialog open={aboutOpen} onOpenChange={setAboutOpen}>
        <DialogContent className="max-w-xs">
          <DialogHeader>
            <DialogTitle>Autonomous Circuit</DialogTitle>
            <DialogDescription>
              {version ? `Version ${version}` : "Chat → spec → board → fab packet."}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button size="sm" onClick={() => setAboutOpen(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
