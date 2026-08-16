# The shortcut sheet reads the handlers; nobody maintains a list

Altium carries roughly a hundred keyboard bindings and an electrical engineer
picks them up without being taught, because the tool will tell you what they
are: `Shift+F1` opens "a menu listing all valid shortcuts" for wherever you
currently are
([shortcut-keys](https://www.altium.com/documentation/altium-designer/shortcut-keys)).
That list is the mechanism behind "no learning curve". It is not a document
somebody keeps up to date — it is generated from the command set.

Ours is too, and for a harder reason than tidiness. A written list of keys is
true until the next person moves a binding, and this workspace has already
shipped two keys that did something other than what they claimed. A sheet that
drifts is worse than no sheet: it converts "I have not learned this yet" into
"this tool lies to me".

## Two of the three surfaces are derived by pressing every key at them

`boardKeymap.js` and `canvasPointer.js` resolve keys through pure functions —
`resolveBoardKey(event, mode)` and `canvasKeyAction(event, state)`. So the sheet
does not read them, it **calls** them: `shortcutSheet.js` builds a synthetic
event for every key in `KEY_SPACE` × eight modifier states × each context the
resolver can be in, and records what comes back.

```
KEY_SPACE      a–z, A–Z, 0–9, 32 punctuation marks, 18 named keys, F1–F12
modifiers      plain, Shift, Ctrl, Meta, Ctrl+Shift, Meta+Shift, Alt, Alt+Shift
contexts       workspace: canUndo on/off
               pcb:       nothing / pointer on board / dragging a part
               sheet:     closed / open
```

This cannot drift. It is the same call the app makes, on the same function, and
if the binding moves the sheet moves with it in the same commit. The sheet's own
key is resolved the same way, by `shortcutSheetKeyAction` — a list that cannot
tell you how to open it is the joke version of this feature.

**A combo is printed only when it changes the answer.** Our handlers test the
modifiers they care about and ignore the rest, so `Ctrl+Shift+M` measures
exactly as `Ctrl+M` does, and `Shift+Escape` clears the selection. Those presses
genuinely work; printing them teaches a modifier that carries no meaning. The
sweep drops a press when removing any one modifier — or using the canonical
spelling of the key, `" "` for the legacy `"Spacebar"` — produces the same
command. The raw sweep returns 54 distinct presses across the three resolvers;
after the collapse, 27 are worth printing. With the six read out of the closure
handlers, the sheet is 31 rows carrying 33 combos.

## The other surfaces keep their keys in a closure, so those are read and pinned

`Board3DView.jsx`, `PropertiesPanel.jsx` and `BoardContextMenu.jsx` decide key
meanings inside a `useEffect` or an `onKeyDown` prop. There is no function to
call, so there is nothing to derive. Their bindings are declared in
`INLINE_BINDINGS` and pinned two ways by the test:

1. `shortcutScan.js` statically reads those files and the declared combo set
   must equal the scanned combo set — a key added, removed or moved fails.
2. Each entry carries `effect`, an FNV-1a hash of the exact statement that key
   runs. Change what the key *does* and the test fails with the new statement
   printed, so the wording lands back in front of a human rather than quietly
   becoming false.

`shortcutScan.js` is a small static reader, not a JS parser. It knows four
shapes — a guarded comparison with or without braces, a `switch` on the key, and
an enclosing modifier guard — and it **throws** rather than returning an empty
result when the masking pass cannot account for every brace. A reader that
silently finds nothing produces an empty sheet, which is the exact failure this
module exists to prevent.

## A new handler cannot appear without the sheet noticing

The test walks `components/board/` and finds every file that registers a
`keydown` listener or an `onKeyDown` prop. It holds them to two different bars:

| Handler | Bar |
| --- | --- |
| `window` listener | must delegate to an arbiter in `PROBES`, or be declared in `INLINE_SOURCES` |
| `onKeyDown` prop | must decide no key meanings of its own, or be declared |

`Enter` and `Space` activating a focused button is what every button does and is
not a shortcut to teach, which is why the second bar is the weaker one.

This guard has already earned itself: it failed on its first run against
`BoardContextMenu.jsx`, a file that had existed for eleven minutes and bound
`Ctrl/⌘+Q` to a unit flip inside the Move-by-exact-amount dialog.

**The bounded gap, stated rather than papered over.** A scoped handler written
as a negated guard — `if (event.key !== "x") return;` — is invisible to the
reader, because that is exactly the shape ARIA activation is written in. A
`window` listener cannot hide that way; it has to delegate to something the
sheet probes.

## What would make the last three derivable

Give each of them a pure resolver, the way `boardKeymap.js` and
`canvasPointer.js` already have one:

```js
// Board3DView.jsx
export function cameraKeyAction(event) {
  if (event.altKey) return null;
  const k = event.key.toLowerCase();
  if ((event.ctrlKey || event.metaKey) && k === "f") return "3d.flip";
  if (event.ctrlKey || event.metaKey) return null;
  if (k === "f") return "3d.home";
  if (event.key === "9") return "3d.rotate";
  return null;
}
```

Then add `{ surface: "3d", file: "Board3DView.jsx", resolve: cameraKeyAction, contexts: [...] }`
to `PROBES`, delete its `INLINE_BINDINGS` rows, and the last hand-written combos
in this feature are gone. Those files belong to other pieces of work, so the
change is named here rather than made.

## Bindings, and where each one comes from

Sourced from Altium's published PCB table unless marked. `Shift+F1` is Altium's
own key for this list. `?` is **unverified** as an Altium binding — Altium
publishes no always-available shortcut-list key — and is the web convention
(GitHub, Linear, Gmail), bound because our user is as likely to be a person who
has never opened Altium as one who has.

Run `node scripts/shortcut-report.mjs` from `viewer/` to print the current set,
the copy attached to each id, and the effect hash every inline entry must carry.
It writes nothing: a machine can tell you `Q` returns `units.toggle`, it cannot
tell you whether "Switch between mm and mil" is still an honest sentence.

## Mounting it

The sheet ships as `ShortcutSheetHost` — the key, the button and the dialog in
one element, owning its own binding. It needs one line in the workspace it
belongs to, which is a file this piece of work does not own:

```jsx
import { ShortcutSheetHost } from "./ShortcutSheet.jsx";
// …anywhere inside the workspace, next to the other header controls:
<ShortcutSheetHost />
```

Until that line lands, the sheet is complete, tested and unreachable.

## Files

| File | What it is |
| --- | --- |
| `shortcutSheet.js` | the probes, the copy, and the assembled sheet. Pure, DOM-free. |
| `shortcutScan.js` | the static reader for handlers that cannot be called. Pure, DOM-free. |
| `ShortcutSheet.jsx` | how it looks, and nothing else. No binding is ever typed into this file. |
| `__tests__/shortcutSheet.test.js` | 18 tests: the bijection, the sweep, the pins, the discovery walk. |
| `scripts/shortcut-report.mjs` | prints what the code actually binds. Writes nothing. |
