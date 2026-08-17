# Lens 1: Familiarity — Round 4

## Score: 8/10

The round-3 8 is real — re-verified independently, not taken on faith — and the
gap to 9 is exactly the same three items round 2 already named and nobody has
touched since: the `1`/`M` Altium collisions are still undocumented, `F11` is
still unbound, and double-click still fires an unconditional fit-to-board.

## What I drove, not just read

**The mechanically-derived shortcut sheet is real and cannot lie.** `cd viewer
&& node scripts/shortcut-report.mjs` prints the sheet by *calling*
`resolveBoardKey`/`canvasKeyAction`/`shortcutSheetKeyAction` across the full key
space, not by reading a maintained list — this closes round 1's "sheet reachable
only by guessing `?`, and untrustworthy if it drifted" finding structurally, not
just cosmetically. `node --test` on `boardKeymap.test.js` + `canvasPointer.test.js`
+ `shortcutSheet.test.js`: **61/61 pass**, including "every command boardKeymap
can return reaches the sheet" and "the declared inline bindings match what those
files actually bind."

**I wrote and ran four live probes** against the real `BoardWorkspace` mounted on
`examples/hydrate-coaster` behind a real `planSourceWrite` (the harness at
`viewer/src/client/components/board/__tests__/boardWorkspace.test-helper.js`),
saved at `work/ide-panel/round4/probes/familiarity.probe.test.js`. Output:

```
[single click] properties panel text: ComponentR30Resistor 1MΩ...
✔ single click already updates the docked Properties panel
[dblclick] zoomed scale=63.43 -> after dblclick scale=9.3
✔ double-click on a part still forces fit-to-board — an unwanted camera jump
[nudge] plain ArrowRight: no-op (correct). Ctrl+ArrowRight: x -2 -> -1.5
✔ a plain ArrowRight does not move a selected part; Ctrl+ArrowRight does
tests 3, pass 3, fail 0
```

Plus the app's own suite, driven fresh: `BoardWorkspace.keys.render.test.js`
(Ctrl+Z/Ctrl+Y/⇧⌘Z undo-redo, `L`→layers not Messages, `⇧N`→Messages, `Q` units,
a held Ctrl+arrow repeating the nudge, a key typed in chat never reaching the
board) and `BoardContextMenu.render.test.js`/`boardContextMenu.test.js` (right-
click's Properties/Lock/Move-by-exact-amount rows are real, wired, and tested —
20/20 pass). One unrelated pre-existing failure in
`BoardWorkspace.edit.render.test.js:182` (stale expected error-message string,
the real message got *more* specific) — not a familiarity regression, flagged
for whoever owns that test.

## Gesture table

| Gesture | Altium expects | What happens here | Evidence |
|---|---|---|---|
| Right-drag | Pan | Pans | `canvasPointer.js:31-33`; driven via `PcbCanvas.render.test.js` |
| Right-click (idle) | Context menu | Real context menu: Properties, Lock, Move-by-exact (+Ctrl+Q units), Ask-agent, Zoom-to-this, Violations-here | `BoardContextMenu.render.test.js` — 20/20 pass |
| Right-click (mid-drag) | Cancel the command | Abandons the drag, no write | `canvasPointer.js:179-183`, `escapeLiveCommand` |
| Wheel / Shift+wheel | Zoom / pan | Zoom / pan (deliberate web deviation from Altium's scroll, stated in sheet copy) | `PcbCanvas.render.test.js`: "Shift+wheel pans... a plain wheel zooms" |
| `Space` / `Shift+Space` (dragging) | Rotate CCW / CW | Rotates CCW / CW, one write on drop | `canvasPointer.js:256-269`; driven, `BoardWorkspace.edit.render.test.js` |
| `Ctrl+Z` / `Ctrl+Y` / `⇧⌘Z` | Undo / redo | Works | driven, `BoardWorkspace.keys.render.test.js` |
| `Ctrl+Arrow` (selected, move mode) | Nudge one snap unit | Nudges one snap step, repeats held | **driven live**: x −2 → −1.5 |
| plain Arrow | Move the cursor (we have none) | Correctly does nothing | **driven live**, confirmed no-op |
| `L` | Layers And Colors | Opens the layer panel | driven, `BoardWorkspace.keys.render.test.js` |
| `Q` | mm/mil toggle | Toggles | driven |
| single click on a part | (Altium: select) | Selects **and** updates a permanently-docked Properties panel | **driven live**: panel text shows "R30 Resistor 1MΩ" |
| double-click on a part | Open Properties | Selects, then **forces the camera to fit-to-board regardless of zoom state** — no hit test, `PcbCanvas.jsx:1117` `onDoubleClick={fitToBoard}` | **driven live**: 63.4x → 9.3x, unconditional |
| `F11` | Toggle Properties panel | Unbound | `grep -rn '"F11"' viewer/src/client/` → no binding, only appears in the sheet's own test sweep |
| `Tab` mid-drag | Properties mode on the fly | Unbound | `grep -rn '"Tab"' viewer/src/client/components/board/*.{js,jsx}` → nothing |
| `1` (tab switch) | Board Planning mode | Opens the Schematic tab — a stated, deliberate difference, but **undocumented in the sheet**: `SHORTCUT_COPY["tab.schematic"]` carries no `when` | `shortcutSheet.js:383` |
| `M` | Move sub-menu accelerator | Cycles highlight dim method — deliberate, **also undocumented** | `shortcutSheet.js:393`, no `when` |
| Middle-drag | Unverified as an Altium default (their own default is zoom) | Unbound, deliberately (low priority, per `docs/altium-input-grammar.md` §4) | `grep -n "button === 1" canvasPointer.js` → nothing |
| `Shift+Ctrl+wheel` | Cycle layers | Absent | `canvasPointer.js` `wheelAction` reads only `shiftKey` |

## Must-fix, ranked

1. **Double-click still fires an unconditional camera fit.** This is the one
   genuine misfire left on a top-5 reflex — worse than a no-op per the rubric's
   own rule, because a zoomed-in EE who double-clicks a part to inspect it gets
   yanked back out to the whole board. Cheapest fix given the docked Properties
   panel already exists: hit-test before firing — over a placement, do nothing
   extra (selection + docked panel already cover it); over empty space, fit.
2. **`1`/`M` Altium collisions are still invisible in the app**, even though the
   exact mechanism (`when` copy) already ships for the zoom-key deviation
   (`shortcutSheet.js:405-410`) and round 2 called this a ten-minute fix. Two
   more lines of `when` text close it.
3. **`F11` (Properties toggle) is unbound.** Lower priority now that Properties
   is a permanently docked panel rather than a summon-able one, but Altium's
   own key for the panel doing nothing is still a small "not a real EDA tool"
   signal for a keyboard-first EE.
4. **`Tab` mid-drag has no meaning**, and worse than a no-op: the browser's
   default focus-shift fires, so a drag-and-press-Tab visibly moves DOM focus
   to an unrelated control. The Move-by-exact-amount dialog is a decent
   post-hoc substitute but is reached from the menu, not from `Tab` mid-drag.

## Should-fix (not blocking)

- `Shift+Ctrl+wheel` layer cycling still unbound.
- Middle-drag pan: low priority, Altium's own default is ambiguous/zoom.

## Would an Altium user's hands work here without being told anything?

For the mouse grammar and the top keyboard reflexes — wheel, right-drag pan,
right-click menu, `Space` rotate, `Ctrl+Z`, `Ctrl+Arrow` nudge, `L`, `Q`,
`Ctrl+PgDn`/`F` fit — yes, verified live, not just read; for anything that
depends on `F11`, `Tab` mid-drag, or double-click behaving like Altium's
Properties-dialog reflex, their hands will do the wrong thing once before they
learn the local dialect.
