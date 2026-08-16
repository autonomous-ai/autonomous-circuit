# Altium's input grammar, and every key of it our app answers

Companion to `viewer/src/client/components/board/ALTIUM-NOTES.md`. That study covers
**looking** at a board — cross-probe, net mask, Board Insight, layers, DRC, Properties,
2D/3D, ActiveBOM. Its three siblings in this folder cover placement editing, interactive
routing, and edit safety.

This one covers the layer underneath all of them: **the input grammar**. The mouse
buttons, the wheel modifiers, the accelerator sequences, the nudge keys, the snap keys,
the two status bars and the three ways Altium tells you what a key does. None of it is a
feature. All of it is reflex — the part an EE never thinks about, and therefore the part
where a mismatch reads as the tool being broken rather than the tool being different.

Same house rules. Every Altium claim carries a source URL; anything Altium does not
publish says **unverified** rather than a made-up keystroke or default. Every claim about
our app carries `file:line` and the code, and where the evidence is an absence, the grep
that returns nothing is quoted as the evidence it is. All greps run 2026-08-16 on
`/Users/d/code/autonomous-circuit`.

Scope note: a separate workstream is building the write path (move a component, it
persists). Nothing here is that plumbing. This is the hand.

> **Status, 2026-08-16 evening — this is a study, not a bug list, and part of it
> has been fixed since it was written.** Everything below was measured before
> `ec67807`/`6a209dc`. Since then the mouse grammar landed: right-drag pans,
> right-click opens the context menu when idle and abandons the command when one
> is running, Spacebar and Shift+Spacebar rotate the part on the cursor, and both
> canvases route their keys through one arbiter (`boardKeymap.js`) instead of two
> private handlers. `Ctrl+Z`/`Ctrl+Y` undo and redo a move. The count in §0 ("18
> keys", "14 do nothing") is therefore historical: `cd viewer && node
> scripts/shortcut-report.mjs` prints what the app answers *today*, derived from
> the resolvers rather than from a list anyone maintains.
>
> What §0 says about the zoom buttons **still stands**: the tool rail prints `+`
> and `−` beside them (`viewportTools.js:33-34`) and no handler answers either —
> and in Altium those two keys step the layer stack, so printing them next to a
> zoom control teaches a reflex that is wrong in both tools at once.
>
> Read the rest as the standing to-do list it is. Re-measure before acting on any
> single line of it.

---

## 0. The headline

Our app has **three** keyboard handlers in the entire board workspace:

```
$ grep -rn "keydown" viewer/src/client/components/board/
components/board/BoardWorkspace.jsx:690
components/board/Board3DView.jsx:191
components/board/PcbCanvas.jsx:353
```

Between them they bind **18 keys**. Altium's PCB shortcut table alone is over 100 rows,
and the mouse grammar — which is most of the muscle memory — is not in the table at all.

Of the 22 reflexes studied below: **3 fire correctly, 5 fire partially, 14 do nothing or
do the wrong thing.** Four are outright collisions, where our app answers an Altium key
with a different action, and one is a *false advertisement*: the tool rail prints `+` and
`−` next to the zoom buttons (`viewportTools.js:33-34`) as if they were keys, and no
handler for either exists — while in Altium those two keys step the layer stack.

And the whole mouse grammar is missing. Right-drag (Altium's primary pan), right-click
(context menu when idle, cancel when in a command), middle-drag, `Ctrl`+middle-click —
zero handlers. `PcbCanvas.jsx:246` and `SchematicCanvas.jsx:257` both open with
`if (event.button !== 0) return;`, which discards every non-left button before anything
else runs.

---

## 1. Two-key accelerator sequences

**Altium.** Menu captions carry an ampersand accelerator — "An accelerator key is
specified as part of a menu (main or sub) or command's caption by adding the ampersand
(&) character immediately before the letter" — and sequences are written with commas:
"use of the comma (,) symbol denotes pressing each key in the sequence in succession. For
example, T, V, U means press the T key, then press the V key, and then press the V key."
Documented worked examples: `T,V,U`, `V,D` (fit document), `P,P` (Components panel). Main
menus need `Alt`+letter, "though some also support pop-up access with just the key
alone."
([shortcut-keys](https://www.altium.com/documentation/altium-designer/shortcut-keys))

The pairs `P,T` (Place Track) and `E,M` (Edit Move) are **unverified** — they are not
published as literal strings on the shortcut-keys page, the Tracks & Arcs page, or the
placement/routing tutorial. The *mechanism* is verified; those two exact sequences are
inferred from the menu accelerators and must never be quoted to an EE as sourced.

**Ours.** No sequence machinery of any kind. `BoardWorkspace.jsx:647-688` is one flat
`switch (key)` with no pending-prefix state, no timeout, no chord buffer. Press `V` and
nothing happens; press `V` then `D` and nothing happens twice.

There is also nothing for a sequence to *accelerate into*. The app has no menu bar with
captions. `WindowMenuBar.jsx` is Windows-chrome only (`main.jsx:272`,
`isWindowsPlatform()`) and carries window controls, not commands:

```
$ grep -n "MENUS\|title:\|items:\|shortcut" viewer/src/client/components/WindowMenuBar.jsx
(no output)
```

**Verdict: missing.** This is the single largest structural gap. An EE does not memorise
100 shortcuts — they memorise ~8 menu letters and read the rest off the menu, and the
accelerator sequence is how the reading turns into the typing. Without a command menu
there is nothing to hang accelerators on, so this is a two-part build: a command
palette/menu with captions first, sequences second.

---

## 2. Right-click, which is two things

**Altium.** "Right-Click: Access context menu for the design space or object currently
under the cursor. If currently within an interactive command, will escape from the
current operation."
([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors))
During interactive routing the tutorial documents a two-stage version: "Right-Click drops
the current connection and keeps interactive routing active", then "Right-Click again to
exit interactive routing mode."

**Ours.** Nothing, and in production actively suppressed:

```
$ grep -rn "onContextMenu\|contextmenu\|ContextMenu" viewer/src/client/components/board/
(no output)
```

`main.jsx:42-49` registers a document-level `contextmenu` handler that calls
`event.preventDefault()` under `import.meta.env.PROD`. The comment is honest about why —
the WKWebView's native menu offers Reload/Back/Forward, which throws away in-flight chat
state — but the consequence is that in the shipped desktop app, right-clicking a pad does
literally nothing. In dev you get the inspector menu, which is worse than nothing for
this test because it hides the defect from us.

A `ContextMenu` primitive is already vendored at
`viewer/src/client/components/ui/context-menu.jsx` and is used by no board component.

**Verdict: missing.** The suppression is right and the fix is not to remove it — it is to
mount a real board context menu on the canvases so the suppressed native menu is replaced
rather than merely blocked.

---

## 3. Right-click hold-and-drag is the pan gesture

**Altium.** "Right-Click, Hold&Drag: Display the slider (panning) hand cursor then drag
to move your view."
([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors))
This is Altium's primary pan. Left-drag in the PCB editor is a selection rubber band, not
a pan.

**Ours.** Dead, and the two gestures are swapped. `PcbCanvas.jsx:246`:

```js
if (event.button !== 0) return;
```

Right-button pointerdown returns before anything. Left-drag is our pan
(`PcbCanvas.jsx:257-263`, `mode: "pan"`), which is the gesture Altium spends on rubber-band
selection — so the EE's two most-used mouse moves are exchanged. `SchematicCanvas.jsx:257`
has the identical guard.

**Verdict: missing** (right-drag pan), and the left-drag half is an active collision:
there is no rubber-band select anywhere in the board directory.

---

## 4. Middle mouse button

**Altium.** The official shortcut table lists Mouse Wheel, `Shift`+Wheel,
`Shift`+`Ctrl`+Wheel and `Ctrl`+Mouse Wheel Click — **no plain middle-drag entry**.
`Ctrl`+Mouse Wheel Click: "Access the Board Insight pop-up, listing all violations (of
defined Design Rules) and all components and/or net objects currently under the cursor."
([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors))
Altium's resources blog states "You can zoom in by pressing the mouse scroll wheel down
and dragging up or down" — middle-drag is **zoom** in Altium, the opposite of
KiCad/Eagle/most CAD. Plain middle-drag pan: **unverified** as an Altium default.

**Ours.** Nothing:

```
$ grep -rn "auxclick\|button === 1" viewer/src/client/components/board/
(no output)
```

Middle-drag hits the same `event.button !== 0` guard at `PcbCanvas.jsx:246`.
`Ctrl`+middle-click likewise does nothing — and note that this is the one middle-button
binding Altium actually publishes in its table, and it is the Board Insight popup, which
is a surface we have most of the data for already (`BoardInsightHud.jsx`, `messageRows`
in `BoardWorkspace.jsx:393`).

**Verdict: missing.** Low priority for plain middle-drag — since Altium's own default is
zoom-not-pan, an EE arriving from Altium is not reaching for middle-drag to pan, and
whichever we pick will surprise half the audience. `Ctrl`+middle-click Board Insight is
the one worth building, because it is published, unambiguous, and nearly free.

---

## 5. Wheel conventions, which are not the web's

**Altium.** Plain wheel: "Scroll vertically within the design space." `Shift`+wheel:
"Scroll horizontally within the design space." `Ctrl`+wheel: zoom the main editing
window, documented as a default on the Mouse Wheel Configuration preferences page, which
also states every action's modifier is user-configurable ("any keyboard button
combination (Ctrl and/or Shift and/or Alt)").
([mouse-wheel-configuration-preferences](https://www.altium.com/documentation/altium-designer/mouse-wheel-configuration-preferences))
The specific default checkbox states per action are **unverified** beyond
`Ctrl`+wheel = zoom.

**Ours.** `PcbCanvas.jsx:209-214`:

```js
const onWheel = (event) => {
  event.preventDefault();
  const rect = node.getBoundingClientRect();
  const factor = Math.exp(-event.deltaY * 0.0016);
  setView((prev) => zoomAt(prev, event.clientX - rect.left, event.clientY - rect.top, factor));
};
```

No modifier is read at all — not `shiftKey`, not `ctrlKey`. `SchematicCanvas.jsx:182-193`
is the same shape. So:

- **Plain wheel = zoom about the cursor.** Altium scrolls. This is the web/KiCad
  convention and is a deliberate deviation, but it is a deviation.
- **`Ctrl`+wheel = zoom** — matches Altium, by accident. The handler ignores the
  modifier and the `preventDefault()` also stops the browser's own page zoom, so the
  right thing happens for the wrong reason. Trackpad pinch (which browsers deliver as
  `ctrlKey`+wheel) therefore zooms too, which is correct.
- **`Shift`+wheel = nothing useful.** Browsers convert `Shift`+wheel into horizontal
  scroll — `deltaX` non-zero, `deltaY` zero — so `Math.exp(-0 * 0.0016)` is `1.0` and
  the view does not move. On any platform that still delivers `deltaY`, it zooms. Neither
  is Altium's horizontal pan. There is no code path in either canvas that pans
  horizontally from the wheel; `deltaX` is never read:

```
$ grep -rn "deltaX" viewer/src/client/components/board/
(no output)
```

**Verdict: partial.** Ctrl+wheel correct, plain wheel deliberately different,
`Shift`+wheel a dead key where a reflex fires ~every 10 seconds on a wide board.

---

## 6. `Shift`+`Ctrl`+wheel cycles layers

**Altium.** "Cycle through the enabled layers. As you roll the mouse wheel
upward/downward, the next/previous enabled layer (at the bottom of the main design
window) will become the active layer."
([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors))
The interactive-routing page confirms the same gesture works mid-route:
"Ctrl+Shift+Wheelroll – change routing layer before or during routing."

**Ours.** Absent. The wheel handler (§5) reads no modifiers, so `Shift`+`Ctrl`+wheel
falls into the same `deltaY` zoom path — i.e. it zooms, or does nothing, depending on
what the browser puts in `deltaX`. The state it would drive exists and is already wired:
`activeLayer` (`BoardWorkspace.jsx:131`), `setActiveLayer` passed to `LayerBar`
(`:1026`), and the ordered layer list at `:424-429`. This is a handler, not a feature.

**Verdict: missing.**

---

## 7. Numpad layer stepping — three keys, three scopes

**Altium.** "`+` : Switch to the next enabled layer" · "`−` : Switch to the previous
enabled layer" · "`*` : Switch to the next enabled signal layer" · "`Shift+*` : Switch to
the previous enabled signal layer."
([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors))
The distinction matters: `+`/`−` walk every enabled layer including silk, mask and
mechanical; `*` walks copper only. The interactive-routing page adds "1-9 (numeric
keypad) – jump to specific layer".

**Ours.** No handler for any of them:

```
$ grep -rn "Numpad\|event.code" viewer/src/client/components/board/
(no output)
```

`BoardWorkspace.jsx:647-688` has cases for `1 2 3 0 f q m [ ] l r` and nothing else.

**And it is worse than absent.** The tool rail advertises both keys:
`viewportTools.js:33-34` declares `{ id: "zoom-out", …, key: "−" }` and
`{ id: "zoom-in", …, key: "+" }`, and `ViewportToolRail.jsx:85` renders that into the
tooltip: `title={tool.key ? \`${tool.label} (${tool.key})\` : tool.label}`. So the app
tells the user "Zoom out (−)", the user presses `−`, nothing happens — and in the tool
they came from, `−` steps the layer stack. One string produces a dead key and a
contradicted expectation at the same time.

**Verdict: missing, plus a false affordance.** Either bind `+`/`−` to zoom and accept the
Altium collision knowingly, or bind them to layer stepping and fix the rail labels. What
cannot stand is advertising a key that does nothing.

---

## 8. `Q` toggles units

**Altium.** "Toggle the measurement units for the current document between metric (mm)
and imperial (mil)." Document-scoped, and works mid-command — the interactive routing
page lists `Q` among the in-route bindings. In the *schematic* editor the equivalent is
different: `Ctrl+Q` toggles metric/imperial but only inside dialogs; plain `Ctrl+Q` in
the design space opens the Selection Memory dialog.
([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors))

**Ours.** `BoardWorkspace.jsx:664-667`:

```js
case "q":
case "Q":
  setUnits((value) => (value === "mm" ? "mil" : "mm"));
  break;
```

`units` flows into `PcbCanvas` (`:745`), the HUD (`:775`), `PropertiesPanel` (`:1078`) and
`LayerBar` (`:1043`), and the rail shows the live value as its own glyph
(`ViewportToolRail.jsx:33-35`).

**Verdict: have** — and better than the reference, because the current unit is visible on
the rail rather than being invisible state. Two small deviations, both defensible: ours is
workspace-scoped rather than document-scoped (we have one document), and it fires on the
schematic tab too, where Altium's plain `Q` is not the units key.

---

## 9. `Ctrl+M` measures

**Altium.** "Measure and display the distance between any two points in the current
document." Documented identically for the schematic editor. It is a modal tool — arm it,
then **click two points**; `Esc`/right-click leaves it.
([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors))

**Ours.** Armed at `BoardWorkspace.jsx:631-634` (`Ctrl`/`⌘`+`M`, with `preventDefault`),
and the gesture is a **drag**, not two clicks: `PcbCanvas.jsx:249-255` starts the
measurement on pointerdown and `:274-285` tracks it to pointerup. `Esc` disarms
(`BoardWorkspace.jsx:627`). Live readout at `PcbCanvas.jsx:806-817`, formatted in the
active units at `:822-832`.

The schematic pane has no measure tool at all — `measuring` is not passed to
`SchematicCanvas` (`BoardWorkspace.jsx:717-728`), and `viewportTools.js:50` excludes
`measure` from `SCHEMATIC_TOOLS`. Altium documents `Ctrl+M` for both editors.

**Verdict: partial.** Right key, right modality, wrong gesture (drag vs two clicks —
which also means our measure cannot span a distance longer than one uninterrupted drag,
and cannot be chained), and PCB-only.

---

## 10. `Backspace` removes the last thing you committed

**Altium.** Three related meanings. Shortcut table: "Backspace: Delete a single, selected
end-of-route object (component-free track, arc, via, or pad)." During track placement:
"Press the Backspace key to remove the last vertex." During interactive routing: "Press
Backspace to undo the last commit, freeing those segments to again be under the control
of the interactive router and the glossing routines." The schematic editor matches:
"Backspace or Delete removes the last placed vertex" while placing wires/lines/polygons.

**Ours.** Unbound:

```
$ grep -rn "Backspace\|\"Delete\"" viewer/src/client/components/board/
(no output)
```

**Verdict: missing.** Nothing in our app is placed vertex-by-vertex yet, so there is
currently no vertex to pop — but the key must be reserved now. The moment the write-path
workstream lands any multi-point placement, `Backspace` is the key an EE will press
first, and if it is still falling through to the browser it will be a navigation event.

---

## 11. `Esc` semantics, narrower than a web developer expects

**Altium.** "Esc (or Right-Click): Escape from the current process — either a stage of
the currently running interactive command or the command itself." Two levels: one `Esc`
backs out of a *stage*, another backs out of the *command*. Crucially `Esc` does **not**
clear a highlight/mask filter — that is `Shift+C` (ALTIUM-NOTES §2).

**Ours.** `BoardWorkspace.jsx:625-629`:

```js
if (key === "Escape") {
  setSelection(null);
  setMeasuring(false);
  return;
}
```

One press, two unrelated things, no stack. Clearing the selection *is* clearing our
filter — the deliberate web deviation documented in ALTIUM-NOTES §2 — but it happens in
the same keystroke that cancels the measure tool, so an EE who arms measure, selects a
net to check clearance against, then presses `Esc` to leave measure loses the net
highlight too and has to re-select it.

`Shift+C` is bound (`:642`) and does the same thing as one half of `Esc`, correctly.

**Verdict: partial.** Right key, right instinct, but it is a boolean where Altium is a
stack. With one mode this is survivable; the write path will add placing / moving /
routing modes and then a flat `Esc` becomes a data-loss key.

---

## 12. There are TWO status bars

**Altium.** The Status Bar "displays summary information, such as the coordinate position
of the cursor, command prompts, shortcut key information", toggled by `View » Status Bar`.
The Command Status bar separately "provides information about the command currently being
used. When a command is not currently being used, this will be reflected in the bar
through the status Idle state", toggled by `View » Command Status`. The cursor-snap page
adds that the Status Bar shows the active snap grid and, when object snapping is on,
literally prints "(Hotspot Snap)" or "(Hotspot Snap (All Layers))".
([the-user-interface](https://www.altium.com/documentation/altium-designer/the-user-interface))

**Ours.** Half of the first one, none of the second:

```
$ grep -rn -i "status-bar\|statusbar\|command status" viewer/src/client/components/board/
(no output)
```

`BoardInsightHud.jsx` is the coordinate half — X/Y, Δ, layer, net, object, px/mm scale,
plus our own plain-language part line (`:77-85`). It is a floating HUD parked top-left
(`:44`), not a bar, and it carries **no** command prompt, **no** shortcut hints (removed
on purpose — see the component docstring), and **no** snap grid, because there is no snap
system to report (§18).

`LayerBar.jsx` is a real bottom bar but carries layers, object classes, single-layer mode,
highlight method, units and palette — state, not command state.

There is nowhere in the app that can display the word "Idle", and nowhere that would tell
you what the tool you just armed expects you to do next. The measure tool works around
this with a line inside the HUD (`BoardInsightHud.jsx:86-88`, "measure — drag across the
board · ⌘M off"), which is the right content in the wrong place: it is the only
command-status text in the app, and it lives in a panel that `Shift+H` can hide.

**Verdict: partial.** Coordinate readout: have. Command status: missing entirely.

---

## 13. Discovery — `Shift+F1`, `F1`, the Shortcuts panel

**Altium.** `Shift+F1`: "Access a menu that lists all valid shortcuts for the present
stage of the currently running interactive command." Note *present stage* — the list
changes as the command progresses. `F1` opens the Graphical Editing Hot Key List dialog.
The Shortcuts panel (`View » Help » Shortcuts`) "is populated with the list of shortcuts
that apply to the currently open and focused document" and "while editing a document, the
panel will also display a list of shortcuts that apply to a current interactive command."
([shortcuts-panel](https://www.altium.com/documentation/altium-designer/shortcuts-panel))

**Ours.** No function key is bound anywhere:

```
$ grep -rn "\"F1\"\|\"F2\"\|\"F5\"\|\"F11\"\|\"F12\"" viewer/src/client/components/board/
(no output)
```

Our only discovery surface is tooltips. `ViewportToolRail.jsx:85` prints a tool's key in
its `title`, and `LayerBar.jsx:118/134/147` names `Shift+S`, `M`, `[`, `]` and `Q`, and
the selection chip names `Shift+C` and `Esc` (`BoardWorkspace.jsx:901`).

Counting what that actually reaches: of the 18 bindings the three handlers register,
**eight are named nowhere on screen** — `0`, `1`, `2`, `3` (tab switching,
`BoardWorkspace.jsx:648-659`), `Ctrl+PgDn` (`:635-638`), `L` (`:678-680`), `Space` and
`Insert` (`PcbCanvas.jsx:348-351`) — plus the 3D view's `9` and `Ctrl+F`
(`Board3DView.jsx:180-184`). A key nobody can find is a key nobody has.

**Verdict: missing.** A single "?" / `F1` sheet listing every binding is a few hours and
would close the largest discoverability hole in the app. Stage-aware `Shift+F1` needs
modes to exist first.

---

## 14. `Tab` and `Shift+Tab` — two different things

**Altium.** While placing or moving: `Tab` "Access the associated mode of the Properties
panel in which properties for the object being placed/moved can be changed on-the-fly" —
the schematic page phrases it as `Tab` "pauses interactive tasks to access Properties",
and the routing page confirms `Tab` mid-route opens routing layer, via diameter, width,
corner style and glossing. While an object is already selected: "With an initial object
selected in the design, extend the selection to include the next higher-level object."
`Shift+Tab`: "Single select the next design object in a set of co-located (overlapping)
objects."

**Ours.** Unbound:

```
$ grep -rn "\"Tab\"" viewer/src/client/components/board/
(no output)
```

Neither meaning exists, and the default browser behaviour makes it an active misfire:
pressing `Tab` over the canvas moves DOM focus to the next focusable element — a tab
button, a layer chip — so the reflex produces a visible focus ring somewhere unrelated.

`Shift+Tab` is the more painful loss because **the data is already computed and thrown
away**. `hitTestPcb` (`lib/boardIndex.js:810-870`) walks every drawable under the cursor
and keeps exactly one:

```js
let best = null;
let bestRank = -1;
for (…) {
  if (rank < bestRank) continue;
  …
  best = element;
}
```

Every co-located candidate the loop rejects at `:819` is precisely the list `Shift+Tab`
cycles. Returning the array instead of the winner is a small change to one pure,
already-tested function (`components/board/__tests__/boardIndex.test.js`).

**Verdict: missing**, with the hard half already done.

---

## 15. `Spacebar` rotates

**Altium.** "Rotates the object being placed/moved counterclockwise. Rotation is in
accordance with the value for the Rotation Step" and "Shift+Spacebar: Rotates the object
being placed/moved clockwise." The rotation step is a preference, not a fixed 90° — the
schematic editor's is documented as 90°, the PCB's is settable. **During routing** the
same two keys mean something else: "Press Shift+Spacebar to cycle through the five
available corner modes" and "Press Spacebar to toggle between the two corner direction
sub-modes."

**Ours.** `Space` is taken, by KiCad's gesture. `PcbCanvas.jsx:346-355`:

```js
const onKey = (event) => {
  if (event.key === " " && cursor && document.activeElement?.tagName !== "INPUT") {
    setDeltaOrigin(cursor);
  }
  if (event.key === "Insert" && cursor) setDeltaOrigin(cursor);
};
```

Three findings in ten lines:

1. **Collision.** Altium `Space` = rotate; ours = zero the delta origin. ALTIUM-NOTES §9
   records this as a deliberate KiCad borrow, taken before the placement half of Altium
   was studied. Once the write path lands "move a component", the two meanings are in
   direct conflict on the same gesture.
2. **`Shift+Space` is the same key.** `event.key` for `Shift`+space is still `" "`, and
   this handler tests only `event.key`, so `Shift+Space` also zeroes the delta origin.
   Altium's clockwise-rotate is therefore not merely unbound, it is shadowed.
3. **The typing guard leaks.** It excludes `INPUT` only. The chat composer is a
   `<textarea>` (`components/chat/ChatInput.jsx:277` → `components/ui/textarea.jsx:10`),
   so typing a space in the chat while the pointer happens to rest over the PCB canvas
   silently moves the delta origin and every Δ reading in the HUD. `BoardWorkspace.jsx:80-83`
   already has the correct predicate — `isTypingTarget`, covering `input`, `textarea` and
   `isContentEditable` — and this handler does not use it.

`Insert` — Altium's own documented "reset the delta origin to the cursor"
(ALTIUM-NOTES §3) — is bound correctly on the next line.

**Verdict: missing** (rotate), **collision** (Space), **bug** (the textarea leak, which is
a one-line fix: import `isTypingTarget`).

---

## 16. `Ctrl+Z`, `Ctrl+Y`, `Delete`

**Altium.** `Ctrl+Z` Undo, `Ctrl+Y` Redo, documented identically in the PCB and schematic
shortcut tables. `Delete` is "Delete selection". These are document-level operations,
distinct from the in-command `Backspace` vertex pop (§10).

**Ours.**

```
$ grep -rn -i "undo\|redo" viewer/src/client/components/board/
components/board/boardSource.js:572:/** One-line summary of a move, for the edit bar and the undo stack. */
```

One comment, in an uncommitted file belonging to the write-path workstream. No handler, no
stack, no `Delete`. `docs/altium-edit-safety.md` covers this ground in depth and reaches
the same measurement.

**Verdict: missing.** Out of scope for this workstream to build — flagged here because
`Ctrl+Z` is the first key a human presses after any edit surface ships, and the input
grammar has to reserve it.

---

## 17. Arrow keys — the precision nudge system

**Altium.** Two families. Cursor movement: arrow keys "move cursor in increments of one
snap grid unit", `Shift`+arrows "move cursor in increments of 10 snap grid units". Object
movement: "Ctrl+Arrow Keys: Move selected objects in corresponding directions by one snap
grid unit." The unit is always the **snap grid**, never a pixel and never a fixed
distance — change the grid and the nudge changes with it.

**Ours.** Unbound:

```
$ grep -rn "ArrowUp\|ArrowDown\|ArrowLeft\|ArrowRight" viewer/src/client/components/board/
components/board/FabPacketCard.jsx:2:import { ArrowRight, … } from "lucide-react";
components/board/FabPacketCard.jsx:233:  <ArrowRight className="size-3.5" aria-hidden />
```

Two icon imports. Zero key handlers. And there is no snap grid for a nudge to be measured
in — see §18. The visual grid (`PcbCanvas.jsx:592-593`, `gridStepMm(view.scale)`) is
zoom-derived decoration: it changes as you zoom, so it can never be the unit of a nudge.

**Verdict: missing**, and blocked on §18. Nudge without a stated snap grid is not a
feature an EE can trust — the whole value is that `Ctrl`+`→` moves exactly one known
step.

---

## 18. The snap system — `Ctrl+E` and `Shift+E`

**Altium.** "Ctrl+E: Access a pop-up window in which to define which objects to be used
for snapping purposes" — a palette of snap points (Grids, Guides, Axes) plus
per-object-type toggles. `Shift+E` cycles object snapping Off → Current Layer → All
Layers. Both usable mid-command; the routing page lists both among the in-route bindings.
The current state is printed in the Status Bar as literal text: "(Hotspot Snap)" or
"(Hotspot Snap (All Layers))".
([cursor-snapping](https://www.altium.com/documentation/altium-designer/pcb/cursor-snapping))

**Ours.** No keys, and no snap system to bind them to. `SNAP_STEPS`
(`boardSource.js:32-33`, `[1, 0.5, 0.25, 0.1]` mm) and `snapDelta` (`:349`) exist in the
uncommitted write-path file, but they are a source-edit helper — nothing on the canvas
snaps a cursor, and `PcbCanvas.jsx` does not import from `boardSource.js`. Object snapping
(pad centres, trace endpoints, hotspots) does not exist in any form.

**Verdict: missing.** This is the foundation §17 stands on and the missing third field of
the status bar in §12. Grid snap first (cheap, and `SNAP_STEPS` is already chosen);
hotspot snap second (needs a spatial query the index can already answer —
`hitTestPcb` is the same walk).

---

## 19. `Ctrl`+click auto-completes a route

**Altium.** During interactive routing, `Ctrl`+click instructs "the router to finish the
route" from the current point to the target in one gesture. Plain left-click commits the
hatched segments placed so far.

**Ours.** No interactive routing exists (see `docs/altium-routing-notes.md`), so there is
nothing to auto-complete. But **the chord is already spent**: `PcbCanvas.jsx:321`
`const jump = event.metaKey || event.ctrlKey;` makes `Ctrl`/`⌘`+click the cross-probe
*jump* modifier (`BoardWorkspace.jsx:446-459`), which is Altium's own cross-probe rule
(ALTIUM-NOTES §1) and correct for a non-routing tool.

**Verdict: missing** (not applicable today), **but flag the reservation**: if routing ever
ships, `Ctrl`+click has to mean auto-complete while routing and jump while idle. That is
exactly the mode-scoped keymap the app does not have (§11, §13).

---

## 20. `Ctrl+W` enters Interactive Routing

**Altium.** `Ctrl+W` is the shortcut for `Route » Interactive Routing`, per the
placement/routing tutorial. Separately, *during* routing, "Ctrl+W – toggle clearance
boundary visualization" per the interactive routing page — the same chord documented for
two different things on two different pages, which is a genuine ambiguity in Altium's own
docs. Which one wins in-command: **unverified**.

**Ours.** Unbound. `BoardWorkspace.jsx:630-640` handles only `m` and `PageDown` under the
`metaKey || ctrlKey` branch and returns.

Worth noting for whenever this is built: `Ctrl+W` is the browser/Electron close-window
chord. There is no accelerator override in the repo —

```
$ grep -rn "CmdOrCtrl+W\|accelerator" --include="*.mjs" --include="*.js" --include="*.ts" desktop electron src
(no output; no such directories at the repo root)
```

— so binding it in the renderer means calling `preventDefault()` on a chord that
otherwise closes the user's window, and getting that wrong once is a lost session.

**Verdict: missing**, and the one binding on this list that should be considered
carefully rather than simply implemented.

---

## 21. Our key collisions with Altium, measured

Not predicted — read off the switch statement.

| Key | Altium | Ours | Evidence | Severity |
| --- | --- | --- | --- | --- |
| `L` | "Access the Layers And Colors tab of the View Configuration panel" | Toggles the **Messages** panel | `BoardWorkspace.jsx:678-680` | High — layers is a top-5 reflex, and we *have* a layer surface (`LayerBar`) that this key should open or focus |
| `M` | The **Move** sub-menu accelerator | Cycles highlight method Normal/Dim/Mask | `BoardWorkspace.jsx:668-671`, advertised at `viewportTools.js:43` and `LayerBar.jsx:134` | High once the write path ships |
| `1` | Board Planning mode | Switches to the **Schematic** tab | `BoardWorkspace.jsx:648-650` | Medium, and made worse by being *nearly* right: our `2` = 2D and `3` = 3D (`:651-656`) match Altium exactly, so two thirds of the pattern holds and the third breaks silently |
| `Space` | Rotate CCW while placing/moving | Zeroes the delta origin | `PcbCanvas.jsx:348-350` | High once the write path ships; see §15 |
| `F11` | Toggle the Properties panel | **unbound** | grep §13 returns nothing | Medium — the panel exists (`PropertiesPanel.jsx`), only the key is missing |
| `F12` | Toggle the PCB Filter panel | **unbound** | grep §13 returns nothing | Low — we have no filter panel to toggle |
| double-click | Open Properties for the object under the cursor (ALTIUM-NOTES §6) | **Zoom to fit** | `PcbCanvas.jsx:647`, `SchematicCanvas.jsx:369` | Medium — this one is not in the brief's list and was found while reading; it is a full-view-change fired by an Altium reflex that expects a dialog |

Two we get right and should not lose: `R` toggles block-area regions
(`BoardWorkspace.jsx:682-685`) and Altium publishes no PCB binding for `R`, so it is free;
and `F` = fit is ours, but we *also* bind Altium's real one, `Ctrl+PgDn` (`:635-638`).

One structural problem behind all of them: **the keymap has no scope.** The handler at
`BoardWorkspace.jsx:621-692` is on `window` with no `activeTab` guard anywhere inside it
(verified: `sed -n '620,700p' … | grep "activeTab\|CANVAS_TABS"` returns nothing). So `L`,
`M`, `[`, `]`, `R`, `Q`, `F` all fire while the user is reading the Parts table or the Fab
tab, where most of them mean nothing and none of them are visible.

---

## 22. The 60-second bar

Ranked by when it fires, from the moment an EE's hands touch our app. This is the
prioritisation answer.

| # | When | Reflex | State |
| --- | --- | --- | --- |
| 1 | 0–5 s | Wheel | **Works.** `Shift`+wheel is the dead one (§5) |
| 2 | 0–10 s | Right-drag pan | **Dead** — `PcbCanvas.jsx:246` rejects non-left buttons |
| 3 | 5–15 s | Right-click context menu | **Dead**, and suppressed in production at `main.jsx:46` |
| 4 | 10–20 s | `L` for layers | **Misfires** — opens Messages (`BoardWorkspace.jsx:678`) |
| 5 | 10–30 s | Layer cycling: `Shift`+`Ctrl`+wheel, numpad `+`/`−`/`*` | **Absent**, zero handlers (§6, §7) |
| 6 | 15–30 s | `Shift+Tab` through overlapping objects | **Absent**; `hitTestPcb` computes the candidates and discards them (`boardIndex.js:819`) |
| 7 | 20–40 s | `Q` units | **Works**, and better than Altium — the rail shows the live value |
| 8 | 20–40 s | Status bar read | **Half** — HUD covers coordinates; no command-status strip exists (§12) |
| 9 | 30–60 s | `Esc` | **Works** for the one mode we have, but is a boolean where it must become a stack (§11) |
| 10 | 30–60 s | `Ctrl+PgDn` fit | **Works** (`BoardWorkspace.jsx:635-638`) |
| 11 | 30–60 s | `Shift+S` single layer | **Works** (`:643`) |
| 12 | 30–60 s | `F11` properties | **Absent**, though the panel exists |

Read the table by cost. Rows 2, 3, 4, 5 and 12 are all *handler-shaped*: the state they
would drive already exists and is already wired to a button somewhere. Rows 6 and 8 are
small changes to code that is already pure and already tested. Nothing in the first minute
of an EE's session requires the write path, a routing engine, or a new data model — the
first minute is a keyboard and mouse problem, and it is the minute that decides whether
they believe the rest.

---

## 23. Open items — things nobody publishes

- `P,T` (Place Track) and `E,M` (Edit Move): the mechanism is verified, the exact literal
  sequences are not published on any Altium page reachable for this study. Inferred from
  menu accelerators; never quote them as sourced.
- Plain middle-drag as an Altium default: not in the shortcut table. Only the resources
  blog describes middle-drag, and it describes it as *zoom*.
- The per-action default modifier checkboxes on the Mouse Wheel Configuration page:
  unpublished beyond `Ctrl`+wheel = zoom.
- `Ctrl+W` in-command: documented as Interactive Routing on one page and clearance-boundary
  toggle on another. Which wins mid-route is unpublished.
- The PCB editor's default Rotation Step: the schematic's is documented as 90°, the PCB's
  is described only as "the value for the Rotation Step". Default unpublished.
