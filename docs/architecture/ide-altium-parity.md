# The board IDE, measured against Altium

Build-ready specs for the gaps between our board workspace and the tool an
electrical engineer already has ten years of muscle memory in. The study that
found the gaps is `viewer/src/client/components/board/ALTIUM-NOTES.md` (looking
at a board), `docs/altium-placement-editing.md`, `docs/altium-routing-notes.md`,
`docs/altium-edit-safety.md` and `docs/altium-input-grammar.md` (changing one).
This file is the other half: what we build, exactly.

Two house rules carry through every section. Every Altium behaviour cites the
page it came from, and says **unverified** where Altium publishes the field but
not the value. Every claim about our own code cites `file:line`.

Sections are appended, never rewritten. Add yours at the end.

---

## Spacebar rotates the part; the delta origin moves to `Insert`

**Ship order: before the next placement gesture lands.** Three defects sit on
one handler, one of them shipping today, and every one of them gets more
expensive after another key is bound.

Line numbers in this section were read from the working tree at `b829662`
(`PcbCanvas.jsx` and `BoardWorkspace.jsx` both modified, uncommitted). The
write-path workstream is editing the same files, so grep the quoted code rather
than trusting an offset.

### Altium: Spacebar turns whatever is on the cursor, by one preference-set step

> "Rotates the object being placed/moved counterclockwise. Rotation is in
> accordance with the value for the Rotation Step."
> "Shift+Spacebar: Rotates the object being placed/moved clockwise."
> ([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors))

The step is a preference, not a constant:

> "This is the amount of rotation, in degrees, applied to objects floating on
> the cursor when the Spacebar is pressed." Default **90 degrees**, minimum
> resolution 0.001 degrees, at `Preferences » PCB Editor – General » Other`.
> ([pcb-editor-general-preferences](https://www.altium.com/documentation/altium-designer/pcb-editor-general-preferences?version=22))

Two things Altium's docs do **not** publish, so we choose and say so:

- Whether holding Spacebar auto-repeats the rotation — **unverified**. We ignore
  `event.repeat`: one press, one step.
- What Spacebar does with an object merely *selected* and not on the cursor.
  Altium's wording is "being placed/moved", so idle Spacebar appears to do
  nothing, but no page states it — **unverified**. We deliberately differ; see
  "Two entry points" below.

During interactive routing the same two keys mean corner style and corner
direction instead
([pcb/tracks-arcs](https://www.altium.com/documentation/altium-designer/pcb/tracks-arcs)).
We have no routing command, so that meaning is out of scope — but it is the
reason this spec keeps rotation inside an explicit mode rather than making
Space a global key.

### Today Spacebar zeroes the delta origin, and it fires while you type in chat

Three defects, all in `viewer/src/client/components/board/PcbCanvas.jsx:448-465`:

```js
// Space zeroes the delta origin (KiCad's relative-origin gesture).
useEffect(() => {
  const onKey = (event) => {
    if (event.key === "Escape" && dragRef.current?.mode === "move") { … }
    if (event.key === " " && cursor && document.activeElement?.tagName !== "INPUT") {
      setDeltaOrigin(cursor);
    }
    if (event.key === "Insert" && cursor) setDeltaOrigin(cursor);
  };
```

1. **Collision.** Space is spent on KiCad's relative-origin gesture, credited as
   such in the comment at `PcbCanvas.jsx:448`. Altium's own key for the same job
   is already bound one line below at `PcbCanvas.jsx:461` — "Insert: Resets the
   Delta Origin point for the Heads Up Display feature to 0,0"
   ([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors)).
   Space costs us nothing to hand over.

2. **Shift+Space is shadowed, not free.** `event.key` for Shift+space is still
   `" "`, and the handler tests only `event.key`. Clockwise rotate would land on
   the same branch and zero the origin — so this is not a gap we can fill later
   without first changing this line.

3. **A shipping bug.** The typing guard is
   `document.activeElement?.tagName !== "INPUT"`, and the chat composer is a
   `<textarea>` — `viewer/src/client/components/chat/ChatInput.jsx:4` imports
   `Textarea`, used through `textareaRef` at `ChatInput.jsx:30`. Type a space in
   chat with the pointer anywhere over the PCB canvas (the `cursor` guard is the
   only other condition, and `cursor` is set on every `pointermove` at
   `PcbCanvas.jsx:319`) and the delta origin silently jumps to the pointer.
   Every Δ in the HUD (`BoardInsightHud.jsx:52-55`) changes and nothing says
   why. `BoardWorkspace.jsx:82-86` already holds the correct predicate — it
   tests `input`, `textarea` **and** `isContentEditable` — and this handler does
   not use it.

The doc comment at `BoardInsightHud.jsx:13-14` ("`Space` or `Insert` zeroes the
delta origin") is part of the change.

### Two entry points: mid-drag, matching Altium, and on a selection, which is ours

**A. Mid-drag — pure Altium parity.** A drag is already the only "object on the
cursor" state we have (`PcbCanvas.jsx:292-302` sets `dragRef.current.mode =
"move"`).

| Keystroke | Effect |
| --- | --- |
| `Space` | Turn the dragged placement **counterclockwise** by one rotation step. Ghost updates immediately. |
| `Shift+Space` | Turn it **clockwise** by one step. |
| `Esc` | Abandon the whole drag — position *and* rotation. Already the behaviour at `PcbCanvas.jsx:453-457`; it now also drops the accumulated turn. |
| pointer up | Commit position **and** rotation in **one** write and **one** undo entry. |

**B. On a selected placement, with move mode on and no drag running — our
deviation, stated.** Altium reaches rotation through a place or move command; we
have no place command, so mid-drag would be the *only* way to turn a part, and
an EE who selects a connector and presses Space would get nothing. Space
therefore also turns the currently selected placement in move mode. This is
additive — it fires where Altium does nothing — so it cannot misfire against a
reflex the user arrives with.

| Precondition | Space behaviour |
| --- | --- |
| move mode on (`BoardWorkspace.jsx:457` `canEdit`), a bound placement selected, not locked, rotatable | turn by one step, preview live, commit when the burst ends |
| move mode on, placement **locked** | no turn; the edit bar says why (see "Refusals") |
| move mode on, placement **not rotatable** | no turn; the edit bar says why |
| move mode **off**, something selected | no turn; a 2.5 s hint on the HUD: `Turn parts in move mode — press E` |
| nothing selected, no drag | nothing. Nothing was asked for. |
| `measuring` true (`BoardWorkspace.jsx:139`) | nothing. Measure and move already exclude each other at `BoardWorkspace.jsx:750-756`. |

**Bursts are coalesced, never dropped.** Four taps of Space is one 360° turn,
one HTTP write, one undo entry — not four. The canvas holds the accumulated
`dRot` in the same state the drag ghost uses and flushes it on the first of:
300 ms with no further rotate key, `Enter`, a click on the canvas, selection
change, move mode turning off, or the component unmounting. `Esc` cancels the
pending turn instead of committing it, and cancelling is the *only* path that
discards one. Every other exit writes.

**Rotation step.** Default **90°**, matching Altium's published default. The
step is user-settable from a `Turn by` dropdown on `PlacementEditBar.jsx`,
directly mirroring the existing `Steps of <snap> mm` control at
`PlacementEditBar.jsx:60-75`, over a new `ROTATION_STEPS = [90, 45, 30, 15]` in
`boardSource.js` alongside `SNAP_STEPS` (`boardSource.js:37`).

**No `Alt+Space` for a fine step**, even though `Alt` is already our fine-move
modifier (`PcbCanvas.jsx:341-345`). Windows binds `Alt+Space` to the window
system menu; a modifier the OS may eat is not a modifier. The dropdown covers
fine control.

**`R` is not rebound.** It stays on block areas (`BoardWorkspace.jsx:781-783`,
advertised at `viewportTools.js:46`). KiCad rotates with `R`
([pcbnew](https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html)); Altium does not,
and Altium is our reference here. One reflex at a time.

### The delta origin keeps `Insert`, and gains a click

`Insert` is Altium's own key and it already works (`PcbCanvas.jsx:461`). But
MacBook keyboards have no `Insert` key, so conceding Space would strand the
feature for half our users. Two additions, no third invented keystroke:

- The `Δ` readout in the HUD becomes clickable and zeroes the origin at the
  cursor. `BoardInsightHud.jsx:44` is `pointer-events-none` on the wrapper;
  the `Δ` span at `BoardInsightHud.jsx:52-55` gets `pointer-events-auto`, a
  `title` of `Zero the delta origin here (Insert)`, and
  `data-slot="hud-delta-reset"`.
- The imperative `resetDelta` handle already exists at `PcbCanvas.jsx:223` and
  is unchanged.

### The rotation lands in the board file as `pcbRotation`, and the binding survives it

`pcbRotation` is degrees, **counterclockwise-positive**, and the placement's
anchor does not move. Both facts are measured from our own compiled boards, not
assumed:

- `examples/harness-puck/boards/main.tsx:47,49,50,86,88,98` place the first LED
  at `theta = 247.5`, `rot = theta + 90 = 337.5`, `RING_R = 28`. The compiled
  `pcb_component_38` in `examples/harness-puck/boards/main.circuit.json` has
  `rotation: 337.5` and `center: {x: -10.715, y: -25.869}` — exactly
  `28·(cos 247.5°, sin 247.5°)`. Its four pads sit at ±32.88° and ±147.12° from
  the component centre **after** subtracting 337.5; taking the rotation as
  clockwise instead yields −12.12°/102.12°/−77.88°/167.88°, which is not a
  rectangle. Counterclockwise is the only reading that makes the footprint
  rectangular.
- `examples/hydrate-coaster/boards/main.tsx:73` is
  `<group pcbRotation={180} pcbX={-20} pcbY={-22}>`. In
  `examples/hydrate-coaster/boards/main.circuit.json`, `pcb_group_2` and
  `pcb_group_3` both carry `anchor_position: {x: -20, y: -22}` — the anchor is
  the un-rotated `pcbX`/`pcbY`. The block's `U4` at group-relative
  `pcbX={13} pcbY={0}`
  (`examples/hydrate-coaster/blocks/rp2040-core/rp2040-core.tsx:164`) compiles to
  `pcb_component_11` at `{x: -33, y: -22}`, rotation 180 — rotated about the
  anchor, not about a bounding box.

That second fact is load-bearing. `bindPlacements` keys a placement to geometry
by its exact `(pcbX, pcbY)` pair (`boardSource.js:435`, `boardSource.js:501-560`),
so a rotation edit changes no key and **cannot** break the binding. A rotation is
strictly cheaper than a move.

The preview transform follows directly. The canvas root is
`translate(tx ty) scale(scale -scale)` (`PcbCanvas.jsx:838`), so inside it a
positive SVG `rotate()` is counterclockwise on screen — the same convention the
pad painter already uses with `ccw_rotation` at `PcbCanvas.jsx:525`. The ghost
group at `PcbCanvas.jsx:857-861` becomes:

```jsx
transform={`translate(${move.dx} ${move.dy}) rotate(${move.dRot} ${move.placement.x} ${move.placement.y})`}
```

Copper stays put, exactly as it does for a move: `movingIds` is
`placement.pcbIds` (`PcbCanvas.jsx:747`), which `boardSource.js:585-587` fills
from component elements only. The comment at `PcbCanvas.jsx:742-746` — the
traces were routed to the old placement and still are — applies unchanged to a
turn, and `PlacementEditBar.jsx:150-153` already prints it.

### Three source shapes, three edits

`readNumericProp` (`boardSource.js:165-193`) already distinguishes the cases; it
gains one field, `close`, the index of the value's closing `}`, so an insertion
has an anchor.

| Source today | Edit | Example |
| --- | --- | --- |
| `pcbRotation={180}` present, numeric | replace the literal span | `boardSource.js:397-402` pattern, one edit |
| no `pcbRotation` prop | insert one immediately after the `pcbY` prop | `pcbY={-18}` → `pcbY={-18} pcbRotation={90}` |
| `pcbRotation={rot}` or `pcbRotation="90deg"` | **refuse**, with a reason | see "Refusals" |

The insertion separator is derived, not guessed: if the run of whitespace after
the `pcbY` closing brace contains a newline, insert `"\n" + indent-of-the-pcbY-line
+ "pcbRotation={…}"`; otherwise a single space. Both example boards write props
both ways and neither should be reformatted — rule 1 of `boardSource.js:10-14`
is that only the number moves.

New exports in `boardSource.js`, beside `moveEdits` and `lockEdits`:

- `ROTATION_STEPS = Object.freeze([90, 45, 30, 15])`
- `normalizeDeg(value)` → wrapped into `[0, 360)`
- `formatDeg(value)` → `normalizeDeg`, rounded to 3 decimals (the same
  micron-scale reasoning as `formatMm` at `boardSource.js:343-349`), `-0`
  written as `0`
- `rotateEdits(source, placement, degrees)` → `[]` when nothing changes;
  replace / insert per the table; `degrees === null` **deletes** the whole
  `pcbRotation={…}` prop and the whitespace before it. Null is how undo removes
  a prop the app itself inserted, so an undone rotation leaves a byte-identical
  file rather than a stray `pcbRotation={0}`.
- `describeRotate(label, from, to)` → `"U4 turned 90° CCW (now 270°)"`, matching
  `describeMove` at `boardSource.js:636-640`

`parseBoardSource` (`boardSource.js:237-327`) adds four fields per placement:
`rotation` (number, 0 when absent), `rotationSpan` (`{start, end}` of the
literal, or null), `rotationPropSpan` (`{start, end}` covering ` pcbRotation={…}`
for deletion, or null), `rotationInsertAt` (offset after the `pcbY` closing
brace). A placement with a **non-numeric** `pcbRotation` stays a placement — it
is still draggable — and gains `rotatable: false` plus `rotationReason`. This is
narrower than the existing `pcbX`/`pcbY` rule at `boardSource.js:298-300`, which
drops the placement entirely, and deliberately so: an unturnable part should
still move.

`bindPlacements` sets `rotatable: false` with a reason for `kind === "loose"` —
mounting holes and silkscreen text (`LOOSE_TYPES`, `boardSource.js:444-451`).
Whether `<silkscreentext>` and `<MountingHole>` accept `pcbRotation` is
**unverified**; a prop tscircuit ignores would make Space look like it worked
and change nothing after the rebuild, which is the silent discard this spec
exists to prevent. Lifting that restriction costs one verification build and a
line in this file.

### The server needs no change

`board_source_write` takes arbitrary byte-range edits with an `expected` string
and a compare-and-swap on file length
(`viewer/src/server/circuit/http.mjs:192-241`). Its limits are
`MAX_SOURCE_EDITS = 8` (`http.mjs:174`) and `MAX_EDIT_TEXT = 200`
(`http.mjs:176`). A combined move-and-turn is at most three edits and the
longest insertion is about eighteen characters. The insertion offset is
`close + 1`, always past the `pcbY` value's `end`, so the "edits overlap" and
"two insertions at one point" refusals at `http.mjs:224-227` cannot fire.

`withExpected` (`boardSource.js:383-389`) is applied to rotation edits the same
way it is to moves.

### Refusals are visible, and nothing is discarded quietly

Every refusal goes through the editor's existing `error` channel
(`usePlacementEditor.js:61`, rendered at `PlacementEditBar.jsx:145-148`), in
plain words naming the file:

| Case | Message |
| --- | --- |
| angle written as an expression | `U3's angle is written as an expression in boards/main.tsx, so this app cannot turn it. Edit pcbRotation there.` |
| angle written as text (`"90deg"`) | `U3's angle is written as text, not a number. Edit pcbRotation in boards/main.tsx.` |
| locked placement | `U3 is locked. Unlock it to turn it.` — the lock button is already right there at `PlacementEditBar.jsx:77-99`. |
| loose geometry | `We have not verified that a mounting hole accepts a rotation, so this app will not write one.` |
| server refused (`SOURCE_CHANGED`) | the existing message at `http.mjs:203`, and the hook already re-reads at `usePlacementEditor.js:139-143` |

A refusal is decided **before** the preview moves. The ghost never shows a turn
that will not be written.

Non-rotatable placements are also visible before the key is pressed: the hover
outline at `PcbCanvas.jsx:909-924` gains `data-rotatable`, drawn the same way the
lock state already is at `PcbCanvas.jsx:916-918`.

### Files, and the state each one needs

| File | Change |
| --- | --- |
| `viewer/src/client/components/board/keymap.js` **(new)** | `isTypingTarget` moved here verbatim from `BoardWorkspace.jsx:82-86` and exported; plus `swallowsSpace(target)` — true for `button`, `a`, `summary`, `select`, `[role="button"]` — because Space activates a focused button and rotating *and* clicking it is a surprise. |
| `viewer/src/client/components/board/placementKeys.js` **(new)** | Pure, DOM-free, React-free, in the shape of `viewportTools.js:1-3`. One export: `placementKeyAction(event, state)` → `{type:"rotate", direction:"ccw"\|"cw"}`, `{type:"cancel"}`, `{type:"delta-origin"}`, or `null`. All the guard logic — typing target, `swallowsSpace`, `event.repeat`, `measuring`, `editing`, locked, rotatable — lives here so `node:test` covers it without a DOM. |
| `boardSource.js` | `ROTATION_STEPS`, `normalizeDeg`, `formatDeg`, `rotateEdits`, `describeRotate`; `close` on `readNumericProp`; four rotation fields plus `rotatable`/`rotationReason` on parsed placements; `rotatable: false` for loose binds. |
| `PcbCanvas.jsx` | `move` state (`PcbCanvas.jsx:116`) becomes `{placement, dx, dy, dRot}`; the `dRot` flush timer as a ref; the key handler at `:448-465` rewritten against `placementKeyAction`; ghost transform at `:857-861`; `onPointerLeave` at `:440-446` no longer clears a pending rotation, only a live drag; drop at `:385-397` sends rotation with position; new `onPlacementRotate` prop; new `onEditStateChange` callback reporting `{dragging, pendingRotation}`. |
| `usePlacementEditor.js` | `rotate(placementId, degrees, note)` beside `move` (`:159-171`); `place(placementId, x, y, degrees, note)` for the combined drop, one write, one history entry; undo entries gain `rotation` (or `null` for "the prop was absent"); writes serialised through a promise chain so a fast burst cannot race the compare-and-swap. |
| `PlacementEditBar.jsx` | `Turn by` dropdown mirroring `:60-75`; the pending-turn line while a burst is open. |
| `BoardInsightHud.jsx` | doc comment at `:13-14` corrected; clickable `Δ` at `:52-55`; a `hint` prop rendered like the measure line at `:86-88`. |
| `BoardWorkspace.jsx` | imports `isTypingTarget` from `keymap.js`; holds `rotationStep` beside `snapStep` (`:148`); `handlePlacementRotate`; `Escape` at `:715-719` does not clear the selection while the canvas reports a drag or a pending turn. |
| `viewportTools.js` | no new rail button — rotation has no view state to toggle. The `edit` tool tooltip at `:39` gains `Space to turn`. |

No change to `viewer/src/server/`. No change to `PropertiesPanel.jsx` or
`SchematicCanvas.jsx`.

### What must not break

Each of these is a working behaviour today and is on the regression list:

- **Pan.** Left-drag on empty board still pans (`PcbCanvas.jsx:305-312`).
  Rotation never touches `view`.
- **Measure.** `⌘M` arms it (`BoardWorkspace.jsx:720-724`), drag measures
  (`PcbCanvas.jsx:322-334`). Space is inert while `measuring`.
- **Selection and cross-probe.** A rotate commit must not change `selection`.
  Plain click, `⇧`-click for the net, and `⌘`/`Ctrl`-click to jump
  (`PcbCanvas.jsx:404-417`) are untouched.
- **The delta origin itself.** `Insert` still zeroes it (`PcbCanvas.jsx:461`),
  and the imperative `resetDelta` (`:221`) is unchanged.
- **Typing.** A space typed in the chat composer, the snap-step select, or any
  future input changes nothing on the board. This is the bug being fixed, so it
  is also the first test.
- **Move.** Drag-to-move, its snap (`snapDelta`, `boardSource.js:357-361`), the
  `Alt` fine step (`PcbCanvas.jsx:345`), and the single-write drop
  (`:390-395`) behave exactly as they do now when no rotate key is pressed.
- **Locks.** A locked placement refuses a turn for the same reason it refuses a
  drag (`PcbCanvas.jsx:290-292`), and still selects.
- **Undo.** `PlacementEditBar.jsx:101-111` must undo a turn, a move, and a
  combined move-and-turn, each as one press.
- **The rebuild gate.** A turn increments `changes` (`usePlacementEditor.js:145`)
  and the board on screen stays the last build until the user asks for one
  (`BoardWorkspace.jsx:501-511`). A rotation never triggers a build.

### What a test asserts

`node:test`, run by `viewer/scripts/run-tests.mjs`. The first two files are pure
and need no DOM.

**`__tests__/placementKeys.test.js` (new).**
1. `placementKeyAction` returns `null` for `{key: " ", target: {tagName: "TEXTAREA"}}` — the shipping bug, asserted directly.
2. Same for `INPUT`, for `{isContentEditable: true}`, and for `{tagName: "BUTTON"}`.
3. `{key: " ", shiftKey: false}` → `{type: "rotate", direction: "ccw"}`; `shiftKey: true` → `"cw"`. Shift is read, so Shift+Space is no longer shadowed.
4. `{key: " ", repeat: true}` → `null`.
5. `{key: " "}` with `measuring: true` → `null`; with `editing: false` → `{type: "hint"}`, never `rotate`.
6. `{key: "Insert"}` → `{type: "delta-origin"}` in every state, including while typing is *not* in progress and move mode is off.
7. `{key: " "}` never returns `delta-origin` in any state. This is the concession, asserted.

**`__tests__/boardSource.test.js` (extend; it already loads the real
hydrate-coaster board at `boardSource.test.js:27-30`).**
8. `parseBoardSource` on `examples/hydrate-coaster/boards/main.tsx` finds the `<group>` at line 73 with `rotation === 180` and a non-null `rotationSpan`, and finds `Ldo3v3` (line 70) with `rotation === 0`, `rotationSpan === null`, and a `rotationInsertAt` that falls immediately after its `pcbY={-18}` closing brace.
9. `rotateEdits(source, group, 270)` returns one edit replacing exactly the three bytes `180`, and `applyEdits` leaves every other byte of the file identical — including the forty lines of measurement prose the module header protects (`boardSource.js:10-14`).
10. `rotateEdits(source, ldo, 90)` inserts ` pcbRotation={90}` after `pcbY={-18}`, and re-parsing the result yields `rotation === 90` with the same `x`/`y`. Round-trip, not just a string match.
11. `rotateEdits(source, ldo-after-insert, null)` deletes the prop, and the result is byte-identical to the original file.
12. Multiline props: on a fixture whose `pcbX`/`pcbY` sit on their own lines, the insertion is on a new line at the same indent — no line is joined.
13. `moveEdits(...).concat(rotateEdits(...))` for one placement produces non-overlapping, correctly ordered edits; `applyEdits` accepts them and the server's `planSourceWrite` contract (sorted, no shared boundary at an insertion) holds.
14. `normalizeDeg(337.5 + 90) === 67.5`; `formatDeg(-0) === "0"`; `formatDeg(360) === "0"`.
15. A fixture with `pcbRotation={rot}` parses as a placement with numeric `x`/`y`, `rotatable === false`, and a non-empty `rotationReason` — it is still draggable.
16. A placement bound to a `pcb_hole` gets `rotatable === false`.

**`__tests__/boardSourceRotationBinding.test.js` (new, real boards).**
17. On hydrate-coaster, applying `rotateEdits(source, group, 270)` and re-running `parseBoardSource` + `bindPlacements` against the **unchanged** `main.circuit.json` binds the same placement to the same `pcb_group` — proving the `(pcbX, pcbY)` key survives a turn (`boardSource.js:435`).

**Manual, once, before merge** (a rebuild is 95.5–97.6 s quiet for
hydrate-coaster per `docs/lessons.md:57`, so it is not in CI): turn
hydrate-coaster's `<group>` from 180° to 270° in the app, rebuild, and confirm
`pcb_component_11`'s centre moves from `{-33, -22}` to `{-20, -35}` — the
counterclockwise answer. If it lands at `{-20, -9}`, the sign in this spec is
wrong and everything above the fold changes.

### Not in this change

`Tab` to edit properties mid-move, `X`/`Y` mirror, `L` to flip to the other side,
`Ctrl`+arrow nudge, and align/distribute are all separate sections. `L` in
particular is a live misfire — it opens Messages at `BoardWorkspace.jsx:777-780`
where Altium opens Layers And Colors — and is a bigger change than this one.
Flip-to-bottom is additionally blocked on a source question: whether a
per-placement bottom-side prop exists that our parser can write is
**unverified**, and no board in `examples/` uses one.

---

## `L` opens Messages, so neither of Altium's two meanings for it is reachable

**Ship order: steps 1-3 today — they touch no canvas and need no write path.**
`L` is a top-three PCB key that fires 10-20 seconds in, and ours does a third,
unrelated thing. A missing binding is a small disappointment; a binding that
does something else is a wrong-tool signal in the first minute.

> Line numbers below were measured against the working tree on 2026-08-16 while
> the write-path workstream had `BoardWorkspace.jsx`, `PcbCanvas.jsx`,
> `viewportTools.js` and `ViewportToolRail.jsx` modified and `boardSource.js`,
> `usePlacementEditor.js` and `PlacementEditBar.jsx` untracked. They drift by a
> few lines per commit — the Spacebar section above cites the same canvas key
> handler at `:432-449` where this one measures `:449-464`. Anchor on the quoted
> code, not the number.

### Altium gives `L` two meanings and picks between them by context

| When | `L` does | Source |
| --- | --- | --- |
| Idle, no command running | Opens the **Layers And Colors** tab of the View Configuration panel | [shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors) |
| An object is on the cursor, being placed or moved | "Flip the object being placed/moved to the other side of the board." | [shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors) |

Two things `L` is **not**, and must not be conflated with:

- `Ctrl+F` flips the **board view** over — the camera, not the object.
  `ALTIUM-NOTES.md:287` records it and we already ship it as
  `BoardOrientationCube.jsx` over `boardSideChange`
  (`viewer/src/client/components/board/viewportTools.js:208-219`). Leave it alone.
- During interactive routing, `L` switches layer from a multi-layer pad only,
  before the first segment is committed, and drops no via
  ([routing/interactive](https://www.altium.com/documentation/altium-designer/pcb/routing/interactive)).
  We have no routing command; that third meaning is out of scope here.

Altium publishes **no keyboard shortcut for its Messages panel** on the PCB
shortcut-keys page — **unverified**. Where our Messages key goes is therefore our
own call, not a parity question.

### Today `L` toggles the Messages drawer

`BoardWorkspace.jsx:770-773`:

```js
        case "l":
        case "L":
          setMessagesOpen((value) => !value);
          break;
```

`messagesOpen` is `useState(true)` at `BoardWorkspace.jsx:142`, so in the common
case the first `L` an EE presses **closes** a panel they had not noticed, and
the layer stack they wanted never appears.

Both Altium meanings, measured:

- **Layers And Colors — no key, no panel.** `LayerBar` is mounted
  unconditionally on the canvas tabs (`BoardWorkspace.jsx:1141-1173`, gated by
  `CANVAS_TABS` at `:76` minus `"schematic"`). Nothing opens, raises or focuses
  it from the keyboard: `grep -rn "layersOpen\|LayersAndColors"
  viewer/src/client/` returns nothing (exit 1).
- **Flip — does not exist.** `grep -rniE '\bflip'
  viewer/src/client/components/board/` returns only camera-flip code
  (`Board3DView.jsx:9,19,132,173`), orientation-cube prose
  (`BoardOrientationCube.jsx:12`), `VIBE-NOTES.md:220,273`, an unrelated "flips
  `projectId`" comment (`useBoardRevisions.js:68`), and the `Ctrl+F` row in
  `ALTIUM-NOTES.md:287`. No object flip anywhere.
- **The board source cannot say it yet either.** `boardSource.js` reads exactly
  two props, `pcbX` and `pcbY`, both as bare numeric literals (`readNumericProp`
  at `boardSource.js:165`, used at `:299-300`). `grep -n
  "readStringProp\|flipEdits\|layerSpan\|attrSpan"
  viewer/src/client/components/board/boardSource.js` returns nothing (exit 1).

Messages loses nothing by moving. It already has a mouse affordance — the
chevron at `MessagesPanel.jsx:99-108`, `data-slot="messages-toggle"`, wired to
`onToggleOpen` from `BoardWorkspace.jsx:1183`.

### The binding, keystroke by keystroke

| Keystroke | Mode | Result |
| --- | --- | --- |
| `L` / `l` | nothing on the cursor | Toggle **Layers And Colors**. If the active tab has no PCB pane, switch to `"pcb"` first, then open. |
| `L` / `l` | a placement is on the cursor (a live move drag) | **Flip that placement to the other side of the board.** The drag stays live. |
| `L` / `l` | focus is in a text field | Types `l`. Guard exists: `isTypingTarget` at `BoardWorkspace.jsx:83-86`, applied at `:713`. |
| `Shift+M` | any | Toggle the Messages panel. **Ours, not Altium's.** |
| `Esc` | Layers And Colors open | Close it. Selection untouched, measure untouched. |
| `Esc` | a placement is on the cursor | Cancel the drag. **Selection untouched** — see the bug below. |
| `Esc` | neither | Unchanged: clear selection, leave measure mode (`BoardWorkspace.jsx:715-719`). |

Unchanged, deliberately: `M` keeps cycling the highlight method
(`BoardWorkspace.jsx:767-770`), `Ctrl+M` keeps arming measure (`:720-724`), and
the delta origin keeps whatever the Spacebar section above settles on.

**Why `Shift+M` for Messages.** Altium publishes nothing here (**unverified**),
so this is internal consistency rather than a copy. Our `Shift+<letter>` branch
is already the show-or-hide-a-surface family: `Shift+S` single-layer
(`BoardWorkspace.jsx:733`), `Shift+H` the HUD (`:734`), `Shift+C` clear
(`:732`). Put `(⇧M)` in the `title` of the chevron at
`MessagesPanel.jsx:99-108`, which has no `title` today, so the new key is
learnable from the panel it opens.

### One arbiter resolves the key, and it is a pure function

Two `window` keydown listeners see every key today: `BoardWorkspace.jsx:710-791`
and `PcbCanvas.jsx:449-464`. Do not add a third, and do not put `L` in the
canvas one — modality split across two files is how the next collision gets
built.

**Add `viewer/src/client/components/board/boardKeymap.js`** — DOM-free and
React-free, like `viewportTools.js` and `boardSource.js`, so `node:test` covers
the whole contract:

```js
/**
 * @param {{key: string, shiftKey?: boolean, metaKey?: boolean,
 *          ctrlKey?: boolean, altKey?: boolean, target?: object}} event
 * @param {{typing: boolean, layersOpen: boolean,
 *          movingPlacement: object|null, viewing: boolean}} mode
 * @returns {string|null} a command id, or null when this key is not ours
 */
export function resolveBoardKey(event, mode) { … }
```

Resolution order — this ordering *is* the specification of modality:

1. `mode.typing` → `null`.
2. `metaKey || ctrlKey` → the existing branch (`"measure.toggle"`,
   `"view.fit"`), else `null`.
3. `key === "Escape"` → `"layers.close"` when `layersOpen`, else `"drag.cancel"`
   when `movingPlacement`, else `"selection.clear"`.
4. `shiftKey` → `m` → `"messages.toggle"`; `c` / `s` / `h` unchanged.
5. `l` / `L` → `movingPlacement ? "placement.flip" : "layers.toggle"`.
6. everything else → the current switch, unchanged.

`BoardWorkspace.jsx:712-788` becomes a dispatch over the returned command id.
Nothing else about that effect changes.

**Escape becomes a stack, not a boolean, in this change.** One mode exists
today, but step 3 is already ordered, so the routing and placement modes that
land later add a case instead of migrating a boolean. It costs three lines now
and a rewrite later.

### A bug this fix must also close: `Esc` during a move clears the selection

`PcbCanvas.jsx:453-456` cancels a live move drag on `Escape` and returns. That
`return` exits its own handler only — `BoardWorkspace.jsx:715-718` is a separate
`window` listener on the same event and also fires `setSelection(null)` and
`setMeasuring(false)`. Cancelling a drag silently deselects the part you were
holding. Rule 3 above fixes it: with `movingPlacement` set, the workspace
resolves `Escape` to `"drag.cancel"` and never touches the selection.

### Layers And Colors: what opens, and what is in it

A popover anchored above the layer bar. **New state: exactly one boolean,
`layersOpen`, default `false`.** Every control in it already exists and is
already wired at `BoardWorkspace.jsx:1142-1173` — this is a second,
keyboard-reachable presentation of state we own, not a new data model:

- Every layer from the `layers` memo (`BoardWorkspace.jsx:435-441`), each row
  with its `copperColor` swatch, a visibility checkbox (`hiddenLayers`, `:132`)
  and an active-layer radio (`activeLayer`, `:134`).
- The `OBJECT_CLASSES` toggles (`visibleClasses`, `:133`).
- Single-layer mode, highlight method with mask level, colour scheme, units —
  the same handlers `LayerBar.jsx:112-164` already calls.

Keyboard inside: `↑`/`↓` move the active layer, `V` toggles visibility on the
focused row, `Esc` closes, `L` closes. Focus lands on the active layer's row
when it opens and returns to the canvas when it closes.

Mouse parity: `LayerBar` gains one button, `data-slot="layers-open"`, so the
panel is reachable without the key — the same reasoning that keeps the Messages
chevron.

Rail discoverability, because the rail is where our bindings become visible
(`viewportTools.js:14-16`): add

```js
{ id: "layers", group: "layers", label: "Layers and colours", icon: "stack", key: "L", state: "toggle" },
```

first in the `layers` group of `VIEWPORT_TOOLS` (`viewportTools.js:31-53`), a
`case "layers"` in both `toolState` and `dispatchViewportTool` calling
`ctx.onToggleLayers`, and `stack: Layers2` in the `ICONS` map at
`ViewportToolRail.jsx:18-31` — `layers: Layers` at `:27` is already the
single-layer tool's icon. Do **not** add it to `SCHEMATIC_TOOLS`
(`viewportTools.js:55`); a schematic has no copper layers.

**Deliberate difference from Altium.** Altium's Layers And Colors gives every
layer its own colour picker. We ship two whole schemes instead, `studio` and
`altium` (`BoardWorkspace.jsx:1166`). Say that in one line in the panel footer
rather than leaving a control-shaped hole where an EE expects a picker.

### Flip: exactly what it writes to the board file

`boards/<stem>.tsx` is the board (`boardSource.js:1-27`). A flip is therefore a
`layer` prop in that file and nothing else — no view state, no optimistic
geometry.

**Parser additions** (`boardSource.js`):

1. Record the attribute span on every placement: `attrSpan: {start: tag.nameEnd,
   end: tag.gt}` plus `selfClosing`, all three already computed by
   `readOpeningTag` (`boardSource.js:150`).
2. Generalise `readNameProp` (`boardSource.js:202-216`) into
   `readStringProp(text, mask, from, to, prop)` returning `{start, end, value} |
   null | "non-literal"` — the same three-way shape `readNumericProp`
   (`boardSource.js:165`) already uses. `start`/`end` bound the text *between*
   the quotes.
3. Store `layerProp` on each placement from that call.

**New `flipEdits(source, placement, side)`**, beside `moveEdits`
(`boardSource.js:392`) and `lockEdits` (`:414`), obeying the same three
contracts those two obey: byte ranges over the **original** offsets, wrapped in
`withExpected` (`:383`), and `[]` when nothing changes.

| Case | Edit |
| --- | --- |
| No `layer` prop | One insert of ` layer="bottom"` at `attrSpan.end`. On a self-closing tag, insert before the `/` — reuse the backward scan `readOpeningTag` already does at `boardSource.js:147-150`, which exists precisely because `terminal-keyboard` ends its `<board>` attribute list with a block comment. |
| `layer="top"` | Replace the span between the quotes with `bottom`. |
| `layer="bottom"` | Replace it with `top`. |
| Requested side equals current side | `[]`. |

Only the value moves. That is rule 1 of `boardSource.js:31-36`: every board in
this repo carries hand-written comments recording what was measured and what was
tried, and a reformat that ate them would cost more than the flip is worth.

### When our source cannot say it, refuse out loud — never silently

| Condition | Detected at | Message, verbatim |
| --- | --- | --- |
| Bound `kind === "group"` — a golden-block instance or a `<group>` | `bindPlacements` sets `kind` at `boardSource.js:613` | `` `${label} is placed by the ${tag} block, not this board file — flipping it has to happen inside the block.` `` |
| `layerProp === "non-literal"` (`layer={SIDE}`, a template) | `readStringProp` | `` `${label}'s layer is written as an expression, not "top" or "bottom" — this app only edits plain values.` `` |
| Bound `kind === "loose"` and the geometry is `pcb_hole` or `pcb_cutout` | `LOOSE_TYPES` at `boardSource.js:444-451` | `` `${label} is a drill — it goes through the board and has no side.` `` |
| `placement.locked` | `readLock` at `boardSource.js:331` | `` `${label} is locked. Unlock it first.` `` |

Silkscreen **is** flippable and is the common loose case: `<silkscreentext …
layer="top" …>` is real board source today at
`examples/terminal-keyboard/boards/main.tsx:154` and `:326`. Allow the
silkscreen members of `LOOSE_TYPES`; refuse only the drills.

The block refusal is not a limitation to apologise for — it is rule 2 of
`boardSource.js:37-42`: a part inside a golden block is placed by the block, and
editing it here would change a file other boards share.

**Every refusal does all four of these, in this order:**

1. **The drag stays alive.** The part is still on the cursor and the move still
   commits on pointer-up. A refused flip must not also lose the move.
2. **The reason appears in the strip** — `PlacementEditBar.jsx:145-149`, the
   `data-slot="placement-edit-error"` slot that already exists — in the words
   above, naming the part.
3. **A one-click handoff.** The message carries an "Ask the agent" button
   calling `handlePrefillNote` (`BoardWorkspace.jsx:560`, already threaded to
   `MessagesPanel` at `:1181`) prefilled with the sentence the user was trying to
   say: `Move U2 to the bottom side of the board.` The chat is the only path
   that can make an edit our parser cannot, and reaching it must be one click,
   never a retype.
4. **Nothing is written.** No partial edit, no optimistic state, no undo entry.

### A flip to the bottom is a hard build error at our default tier, so it asks first

Measured, not predicted:

- `AssemblyRules.smt_sides = 1`
  (`packages/verify/src/verifylib/rules.py:42`) and `JLCPCB_ECONOMIC =
  AssemblyRules()` (`rules.py:52`) — Economic is the default.
- `_sides` raises `dfa_bottom_side` at severity **`error`**
  (`packages/verify/src/verifylib/assembly.py:169-186`): "*Economic PCBA places
  one side only — they will not be fitted, and the order will not tell you so*".
- All three reference boards are top-side only. `grep -rn 'layer='
  examples/*/boards/*.tsx` returns six hits, every one a `<silkscreentext>`, a
  `<copperpour>` or a `<GndPour>` — no component.
  `examples/harness-puck/boards/main.tsx:25` says it outright: "every part is on
  the TOP side, JLC economy single-side assembly."
- **The client cannot read the tier.** The sidecar's `fab` block is
  `{assembly, gerberSource, packet, profile, ready}` — measured on
  `examples/harness-puck/boards/main.board.json`. Nothing in it names Economic
  or Standard, so the app cannot tell whether this flip is legal. **Unverified
  from the client.**

We do not guess. **The first flip to the bottom, per board, per session, opens a
confirm** carrying that exact error sentence with `Flip anyway` / `Cancel`.
`Cancel` writes nothing. This is Altium's own pattern — the wider an edit's blast
radius, the more it interposes a confirmation (`Confirm Global Edit`,
[pcb-editor-general-preferences](https://www.altium.com/documentation/altium-designer/pcb-editor-general-preferences)).
Later flips in the same session go straight through; nobody wants a dialog per
part.

### The board on screen does not flip, and the strip says so

Altium flips the footprint under the cursor immediately. We cannot and must not:
the geometry on screen is the last build, and mirroring a footprint in the viewer
would be the canvas telling a story the board file cannot back up — the exact
reason `PcbCanvas.jsx:68-72` refuses to redraw copper after a move.

`PlacementEditBar.jsx:150-154` already says the honest version for a move ("*The
board below is still the last build — the copper has not moved with the part.*").
Extend it for a flip: "*…and the part has not flipped on screen either. Rebuild
to see it.*" A stated difference from Altium, not a shortcoming to hide.

### Publishing the live move to the workspace

`resolveBoardKey` needs `movingPlacement`, and the live move is private to the
canvas today: `const [move, setMove] = useState(null)` at `PcbCanvas.jsx:116`,
set at `:301`, cleared at `:387`, `:437`, `:442`, `:455`.

Add one prop and one effect to `PcbCanvas.jsx`, nothing more:

```js
  onMoveChange,          // beside onPlacementMove in the props list (:96)
  …
  useEffect(() => { onMoveChange?.(move); }, [move, onMoveChange]);
```

`BoardWorkspace` holds it as `movingPlacement` and passes it to
`resolveBoardKey`. If the Spacebar section's `onEditStateChange` lands first,
extend that callback instead of adding a second one — one channel, not two.

The flip itself runs through a new `editor.flip(placementId, side)` on
`usePlacementEditor.js`, alongside `move` (`usePlacementEditor.js:178-195`) and
`setLock` (`:197-212`), using the same `write()` path, the same history entry
shape (`{kind: "flip", placementId, side, label}`) and the same undo
(`:214-233`). A flip is one edit; the server caps are `MAX_SOURCE_EDITS = 8`
(`viewer/src/server/circuit/http.mjs:174`) and `MAX_EDIT_TEXT = 200` (`:176`),
so it fits with room to spare.

### What this must not break

| Works today | Where | The rule |
| --- | --- | --- |
| Left-drag pan | `PcbCanvas.jsx:305-312`, non-left buttons rejected at `:275` | `L` never touches `dragRef` for a pan. |
| Measure | `Ctrl+M` at `BoardWorkspace.jsx:720-724`, drag at `PcbCanvas.jsx:281-284` | `L` with `measuring === true` opens the layers panel and leaves `measuring` true. Measure arms the pointer, not the keyboard. |
| Selection and cross-probe | `PcbCanvas.jsx:405-425`, jump modifier at `:412` | `L` never selects, never deselects, never moves the camera. |
| `Esc` clears selection when idle | `BoardWorkspace.jsx:715-719` | Preserved as the **last** case of rule 3, not the first. |
| Delta origin | `PcbCanvas.jsx:453-461` | Untouched here; owned by the Spacebar section. |
| Messages opens by default, with a chevron | `BoardWorkspace.jsx:142`, `MessagesPanel.jsx:99-108` | Only the key moves. Default stays `true`, chevron stays, contents unchanged. |
| Move-mode drag and write | `PcbCanvas.jsx:292-303`, `usePlacementEditor.js:178` | A refused flip leaves the move intact and it commits normally on pointer-up. |
| The locked-part refusal | `PcbCanvas.jsx:290-292` | A locked part still selects on click and still refuses a flip, with wording that matches the drag refusal. |
| Every rail tool names its key | `viewportTools.js:14-16` | The new `layers` tool carries `key: "L"`; the Messages chevron gains `(⇧M)` in its `title`. |

### What a test asserts

`viewer/src/client/components/board/__tests__/` is ten `node:test` files over
pure modules with no DOM. Keep it that way — the arbiter and the edit builder
are both pure, so both are directly testable.

**New `__tests__/boardKeymap.test.js`:**

1. `resolveBoardKey({key: "l"}, {})` is `"layers.toggle"`, and **no state
   whatsoever** makes `l` / `L` return `"messages.toggle"`. Assert over the full
   mode cross-product. This is the regression the whole section exists for.
2. `resolveBoardKey({key: "L"}, {movingPlacement: {…}})` is `"placement.flip"`.
3. `{key: "m", shiftKey: true}` is `"messages.toggle"`; `{key: "m"}` is still
   `"highlight.cycle"`.
4. `Escape` precedence: `{layersOpen: true}` → `"layers.close"`;
   `{movingPlacement: {…}}` → `"drag.cancel"`; neither → `"selection.clear"`.
   Assert `"drag.cancel" !== "selection.clear"` — that pins the two-listener bug.
5. `{key: "l", target: {tagName: "TEXTAREA"}}` is `null`, and so is a
   `contentEditable` target.
6. **Collision table.** Build the full `(key, modifiers, mode) → command` table
   and assert no tuple maps to two commands and no command has two tuples. This
   is the test that stops the next binding from repeating this defect.

**Extended `__tests__/boardSource.test.js`:**

7. `flipEdits` on a self-closing tag with no `layer` prop inserts
   ` layer="bottom"` before the `/`, and `applyEdits` output differs from the
   input **only** by those bytes. Use a fixture containing a block comment and a
   `//` comment; assert both survive byte-for-byte.
8. `layer="top"` → `bottom` and `layer="bottom"` → `top` replace only the
   characters between the quotes: edit span length equals the old word length.
9. Requested side equals current side → `[]`, matching `moveEdits` and
   `lockEdits`.
10. `layer={SIDE}` yields the `"non-literal"` refusal and **zero** edits.
11. A `kind: "group"` placement, a `pcb_hole`-bound loose placement and a
    `locked` placement each yield their named refusal and zero edits.
12. Every returned edit carries `expected` equal to the exact original bytes, so
    the server's compare-and-swap can refuse a stale write
    (`viewer/src/server/circuit/http.mjs:192`).
13. Flip, then flip back, reproduces the original file byte-for-byte.

**Extended `__tests__/viewportTools.test.js`:**

14. `dispatchViewportTool("layers", {})` is `false` with no callback and `true`
    with `onToggleLayers`; `toolsForSurface("schematic")` does not contain
    `"layers"`.

**Manual acceptance, the one thing no unit test covers.** Open
`examples/harness-puck` and press `L` within ten seconds of the first click: the
layer list appears and Messages does not move. Press `Esc`: the list closes and
the selection is still there.

### Ship order

1. `boardKeymap.js` and its test. Pure, no UI. Everything else hangs off it.
2. The rebind: `L` → `layers.toggle`, `Shift+M` → `messages.toggle`, the rail
   tool, the chevron `title`. **This alone removes the surprise.**
3. The Layers And Colors popover.
4. `onMoveChange` on `PcbCanvas` plus the `Esc` precedence fix.
5. `flipEdits`, the four refusals, and the bottom-side confirm.

Steps 1-3 touch no canvas and need no write path. Steps 4-5 need `PcbCanvas.jsx`
and `boardSource.js` and should land **with** the write-path workstream rather
than after it — a lock and a flip are both source-format decisions.

### Unverified, and what we chose instead

| Unpublished | Our choice | Why |
| --- | --- | --- |
| Altium's key for the Messages panel | `Shift+M` | `Shift+<letter>` is already our show/hide-a-surface family (`BoardWorkspace.jsx:732-734`). |
| Whether Altium's Layers And Colors is modal or dockable | A popover that `Esc` closes | Matches our existing panel behaviour; nothing else in the app docks. |
| The assembly tier of the board currently open — the sidecar `fab` block does not record it | Confirm on the first flip-to-bottom per board per session | Guessing "Standard" would silently ship a board whose bottom-side parts are never fitted. |
| Whether Altium's flip mirrors silkscreen text as well as the footprint | We write `layer` and let the compiler decide | The compiler owns mirroring; the file owns the side. |

---

## Right-click on the PCB canvas returns silence, and silence reads as broken

Surprise 10/10 — the worst first-minute reflex we have. Right-click is the first
exploratory gesture an EE makes on an object, and it is the only one where our app
produces *nothing at all*: not a wrong panel, not a browser menu, nothing. A no-op is a
missing feature; silence on the very first gesture reads as a broken program.

> Line numbers below were measured against the working tree on 2026-08-16, while the
> write-path workstream is actively editing `BoardWorkspace.jsx` and `PcbCanvas.jsx` (both
> show as modified in `git status`). Every reference names the code it points at, so grep
> the anchor if a number has drifted a few lines.

### Altium spends one button on three jobs and we do none of them

Verbatim from the PCB editor shortcut table
([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors)):

| Input | Altium |
| --- | --- |
| `Right-Click` | "Access context menu for the design space or object currently under the cursor. **If currently within an interactive command, will escape from the current operation.**" |
| `Right-Click, Hold&Drag` | "Display the slider (panning) hand cursor then drag to move your view of the design space." |
| `Esc (or Right-Click)` | "Escape from the current process — either a stage of the currently running interactive command or the command itself." |
| `Ctrl+Mouse Wheel Click` | "Access the Board Insight pop-up, listing **all violations** (of defined Design Rules) **and all components and/or net objects currently under the cursor**." |

So: **menu when idle, cancel when in a command, pan when held and dragged** — and note
the two levels inside `Esc (or Right-Click)`, one for the *stage* and one for the
*command*. That ladder is the spec of the cancel behaviour below.

The object menu's own contents are documented on
[pcb/placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques),
which names as right-click commands on a PCB object: **`<ObjectType> Locked`**,
**Align**, **Properties**, **Select Component Connections**, **Select Component Nets**,
**Select Room Connections**, **Assign Net**. `Violations` and `Applicable Unary/Binary
Rules` are documented as right-click entries on other pages
([interrogating-resolving-design-violations](https://www.altium.com/documentation/altium-designer/pcb/drc/interrogating-resolving-design-violations),
[defining-scoping-managing-design-rules](https://www.altium.com/documentation/altium-designer/pcb/defining-scoping-managing-design-rules)).
The **complete ordered item list of Altium's PCB context menu is unverified** — Altium
publishes the commands, not the menu. We are not copying an order we cannot read; the
grouping below is ours and says why.

### All three meanings are dead, at two lines of code

`grep -rn 'onContextMenu\|contextmenu' viewer/src/client` returns exactly one line:

```
viewer/src/client/main.jsx:46:  document.addEventListener("contextmenu", (event) => {
```

— `event.preventDefault()` inside `suppressNativeContextMenuInProduction()`
(`main.jsx:42-49`), gated on `import.meta.env.PROD`. In the shipped desktop build,
right-clicking a pad produces literally nothing. In `vite dev` the WKWebView inspector
menu appears, which is exactly why nobody on the team has felt this.

Cancel and pan die one line earlier: `PcbCanvas.jsx:275` is
`if (event.button !== 0) return;` at the top of `onPointerDown`, so button 2 never
reaches pan, measure or move. (`SchematicCanvas.jsx:257` has the identical guard — out of
scope, see the last subsection.)

The primitive is already vendored and unused: `viewer/src/client/components/ui/context-menu.jsx`
(96 lines, Radix, exporting `ContextMenu`, `ContextMenuTrigger`, `ContextMenuContent`,
`ContextMenuItem`, `ContextMenuLabel`, `ContextMenuSeparator` at `:89-96`).
`grep -rn 'context-menu' viewer/src/client --include='*.jsx' -l` returns only that file —
nothing imports it. The work here is deciding the items, not wiring the event.

### The interaction, gesture by gesture

**Idle, pointer over the PCB canvas, right button pressed and released within 4 px** →
the board context menu opens at the pointer. 4 px is `CLICK_SLOP_PX` (`PcbCanvas.jsx:27`),
reused so left-click and right-click agree on what "a click" is.

**The press hit-tests once, and that hit is frozen for the life of the menu.** The menu
header names the object; a header that disagrees with the item under it is the worst kind
of bug to chase.

**Right-click never changes the selection.** Altium's menu acts on the object under the
cursor, which need not be the selected object; each of our items carries its own target.
This is a deliberate difference from the left-click path (`PcbCanvas.jsx:406-425`), and it
is what lets "Violations here" work without destroying the net highlight the user set up
in order to find the violation.

**Right-click while a command is live** → cancel, no menu, event consumed:

| Live state | Where it lives | Right-click does |
| --- | --- | --- |
| move drag (`dragRef.current.mode === "move"`) | `PcbCanvas.jsx:286-303` | Abandon the drag, part returns to its file position, **no write**. Same as the existing `Escape` at `PcbCanvas.jsx:451-457`. |
| measure drag (`mode === "measure"`) | `PcbCanvas.jsx:278-284` | Abandon the dimension, stay armed in measure mode. |
| pan drag (`mode === "pan"`, `moved === true`) | `PcbCanvas.jsx:305-311`, `:358-365` | Stop the pan. The camera keeps where it got to. |
| measure **armed**, no drag (`measuring`, `BoardWorkspace.jsx:139`) | `BoardWorkspace.jsx:722-725` | Leave measure mode. Altium's second `Esc` level. |
| nothing live | — | Open the menu. |

First right-click kills the *stage* (the drag), second kills the *command* (measure mode),
third opens the menu. That is Altium's sentence read literally.

**Right button pressed, moved more than 4 px, released** → pan for the duration and **no
menu on release**. Altium's `Right-Click, Hold&Drag`. The menu is cancelled the moment the
slop is exceeded and is not restored if the pointer comes back.

**Deliberate difference, written down:** left-drag keeps panning
(`PcbCanvas.jsx:305-311`); Altium's left-drag rubber-band selects. `ALTIUM-NOTES.md` §9
already set the tiebreaker — where Altium and the web disagree and our non-EE users depend
on the web convention, the web wins and we say so. Right-drag pan is **additive**: both
buttons pan.

**Inside the menu:** arrows move, `Enter` activates, `Esc` closes without acting,
click-outside closes, right-click outside closes and reopens at the new point. All Radix
`ContextMenu` behaviour, free. **Keyboard invocation (`Shift+F10`, the Menu key) is
unverified in WKWebView and out of scope for v1** — browsers synthesise a `contextmenu`
event for those keys so it may work by accident; we neither claim it nor test it.

### Three states per item, and there is no fourth

Every item is exactly one of:

1. **enabled** — clicking it does the thing, now;
2. **disabled with a one-line reason** rendered as the item's subtitle, in plain words;
3. **absent** — the capability does not exist, and the reason is written in this document
   rather than in the UI.

**No item may be enabled and then do nothing.** That failure already exists once in this
app and is the reason for the rule: `WindowMenuBar.jsx:233-234` ships menu items labelled
Undo/Redo that call `runEditCommand` at `:54-60` → `document.execCommand(command)`, inside
a try/catch whose catch body is the comment
`/* execCommand unsupported / nothing focused — no-op */`. A menu item labelled Undo that
does not undo teaches the user the app loses their work. Assertion 6 below exists to stop
this menu repeating it.

**Whenever a write item is disabled, the agent fallback in the same group is enabled** —
so the user's intent always has somewhere to go. That is the whole of "never silently
discard a user's action", and it is the same discipline the flip section above already
states for refusals.

### The nine items, in five groups

Ordered by our reflexes, not by Altium's unpublished order: identify, then select, then
edit, then findings, then view — cheapest question first, the destructive group in the
middle where the pointer does not land by accident.

Header (a `ContextMenuLabel`, not an item): `R3 · The brain · top · 12.400, −8.200 mm`.
Refdes from `index.componentBySourceId`, plain name from `partPlainName`
(`lib/plainLanguage.js`, already used at `BoardWorkspace.jsx:905`), area from the same
`boardRegions` derivation the canvas draws (`BoardWorkspace.jsx:413`, `hoverArea` at
`:809-813`), coordinate from `formatPoint(x, y, units)` (`lib/boardRender.js`, exported and
already the HUD's formatter via `BoardInsightHud.jsx:3`). Reusing `formatPoint` is not
tidiness — it is what stops the menu header and the HUD printing two different numbers for
one point.

**Group 1 — what is here**

1. **`objects-here` · "Objects under the cursor (4)"** — submenu, one row per hit-test
   candidate, top-ranked first, each row selecting that one object. This is Altium's
   `Ctrl+Mouse Wheel Click` Board Insight pop-up, "listing all violations … and all
   components and/or net objects currently under the cursor"
   ([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors)),
   reached from right-click instead because we bind the middle button nowhere
   (`grep -rn 'button === 1\|auxclick' viewer/src/client` returns nothing).
   **Needs one new pure export**, `hitTestPcbAll`, beside `hitTestPcb`
   (`viewer/src/client/lib/boardIndex.js:797`), which already computes every candidate in
   its ranked loop and throws all but the winner away. `hitTestPcbAll` returns the ranked
   list with the same `visibleLayers`/`tolerance` filters and the same per-segment trace
   layer resolution (`boardIndex.js:850-860`); `hitTestPcb` becomes
   `hitTestPcbAll(...)[0] ?? null` so the two can never disagree. Absent when the list has
   one entry.
2. **`properties` · "Properties"** — select the object so the right-hand panel switches to
   it, and raise the panel (`PropertiesPanel` is mounted at `BoardWorkspace.jsx:1201`).
   Altium documents Properties on the object right-click menu
   ([placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques));
   `F11` is its keyboard route (`ALTIUM-NOTES.md` §6) and we bind neither today — the
   keymap switch at `BoardWorkspace.jsx:738-787` has no `F11` case.

**Group 2 — select and cross-probe (all already works; the menu only makes it findable)**

3. **`select-net` · "Select the whole net `V3_3`"** — `onSelect({kind:"net", key})`, the
   call `Shift`+click already makes at `PcbCanvas.jsx:417-419`. Altium's "Select Component
   Nets" ([placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques)).
   Absent when the hit carries no `netKey`.
4. **`show-in-schematic` · "Show it in the schematic"** — `handleSelect(target, {jump:true, source:"pcb"})`
   (`BoardWorkspace.jsx:525-537`), which is what `⌘`/`Ctrl`+click does. Altium's Cross Probe
   jump-to (`ALTIUM-NOTES.md` §1). The menu is the only discoverable route to it: that
   modifier is documented nowhere inside the app.

**Group 3 — change the board (writes `boards/<stem>.tsx`)**

5. **`lock` · "Lock R3 in place" / "Unlock R3"** — `editor.setLock(placement.id, next)`
   (`usePlacementEditor.js:178-193`), which splices `LOCK_COMMENT` (`boardSource.js:33`)
   above the element. Altium's `<ObjectType> Locked`
   ([placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques));
   **no keyboard shortcut for it is published — unverified**, so we bind none either. This
   is the highest-value item in the menu and it is worth more to us than to Altium:
   Altium's lock protects a placement from your own hands, ours protects it from the
   model's next rebuild, because the lock lands in the file the agent reads
   (`BoardWorkspace.jsx:507-513` already instructs the agent to honour it).
6. **`move-exact` · "Move by an exact amount…"** — a two-field dialog (X offset, Y offset,
   signed, current units) calling `editor.move(id, placement.x + dx, placement.y + dy, note)`
   (`usePlacementEditor.js:159-171`). Altium's `M,X` / Get X/Y Offsets
   ([get-x-y-offsets](https://www.altium.com/documentation/cstu/get-x-y-offsets)), whose
   `Ctrl+Q` flips the dialog between imperial and metric — bind `Ctrl+Q` inside this
   dialog, which finally gives our `Ctrl+Q` somewhere to apply (`Q` alone already flips
   document units at `BoardWorkspace.jsx:764-767`). Offsets snap with `snapDelta(delta, step)`
   (`boardSource.js:357`) at the current `snapStep` (`BoardWorkspace.jsx:148`), **except**
   that a number typed by hand is taken exactly — someone who types 0.35 meant 0.35.
7. **`ask-agent` · "Ask the agent to change R3…"** — `prefillChatInput(text)`
   (`chatInputHelpers.js:25`, threaded as `handlePrefillNote` at `BoardWorkspace.jsx:561`),
   prefilled with the object, its coordinates, and the sentence the user was denied when
   this item stands in for a disabled one. **Altium has no equivalent, and that is the
   point** — this is the item that makes the three-state rule payable, because there is no
   intention our source cannot express that this item cannot carry.

**Group 4 — findings**

8. **`violations` · "Violations here (2)"** — submenu of the `messageRows`
   (`BoardWorkspace.jsx:404-407`, `buildMessages` at `lib/boardViolations.js:180`) whose
   `box` contains the clicked point, error-first via `severityRank`
   (`boardViolations.js:20`). Each row runs `handleLocate(row)` (`BoardWorkspace.jsx:540-552`);
   each carries a "Fix this" child calling `onPrefillNote`, exactly as `MessagesPanel.jsx:271`
   and `:328` already do. Altium's right-click » Violations
   ([interrogating-resolving-design-violations](https://www.altium.com/documentation/altium-designer/pcb/drc/interrogating-resolving-design-violations)).
   Absent when nothing under the point is flagged — an empty "Violations (0)" row is a
   worse answer than no row.

**Group 5 — view**

9. **`zoom-here` · "Zoom to this"** — `pcbRef.current.zoomToBox(box)` (`PcbCanvas.jsx:176-186`,
   exposed on the imperative handle at `:216-226`). Fit already has `F`, `Ctrl+PgDn` and a
   rail button (`BoardWorkspace.jsx:760-763`, `:726-729`), so it does not need a row;
   zoom-to-*this* has no route at all today.

**Right-click on empty design space** (`hit === null`) gets a different, shorter menu —
Altium's "context menu for the design space". Six items, all read-only, all already wired:
**Zoom to fit** (`fitAll`, `BoardWorkspace.jsx:606-609`) · **Clear the highlight**
(`setSelection(null)`, the `Shift+C` behaviour at `:733`) · **Grid on/off** (`:659`) ·
**Units mm/mil** (`:764-767`) · **Move parts: on/off** (`:650-656`, disabled while
`viewing`) · **Ask the agent about this board**.

**Not in v1, and why, so nobody adds it by reflex:**

- **Rotate 90°.** `grep -n 'rotation\|Rotation' viewer/src/client/components/board/boardSource.js`
  **returns nothing** — the module writes `pcbX`/`pcbY` numeric literals (`moveEdits`,
  `boardSource.js:392`) and the lock comment (`lockEdits`, `:414`), and nothing else. The
  dialect does carry `pcbRotation` (`examples/hydrate-coaster/boards/main.tsx:73`,
  `examples/terminal-keyboard/boards/main.tsx:211`, `examples/harness-puck/boards/main.tsx:98`),
  so this is a missing edit, not a missing concept. Until `boardSource` grows
  `rotateEdits`, rotation is **absent** from the menu and rides on `ask-agent`.
  Assertion 7 fails the day someone adds a rotate item without the write path behind it.
- **Flip to the other side.** Specified in the `L` section above, not here. When
  `flipEdits` lands, add one item to group 3 reusing that section's four refusals verbatim
  — do not write a second set of refusal strings.
- **Align / Distribute.** Altium puts Align on the right-click menu
  ([placement-editing-techniques ?version=16.1](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques?version=16.1)),
  but it needs a multi-object selection and ours is a single nullable value —
  `BoardWorkspace.jsx:130` is `const [selection, setSelection] = useState(null)`. Align is
  blocked on the selection model, not on this menu.
- **Applicable Rules / Find Similar Objects.** No rule objects exist client-side (see
  `docs/altium-edit-safety.md` on the frozen dataclasses in
  `packages/verify/src/verifylib/rules.py`). An unaskable question gets no item.
- **Delete.** `boardSource.js` cannot remove an element and must not learn to from a
  context menu. `ask-agent`.

### Which files this touches

New, and where the decisions live:

- **`viewer/src/client/components/board/boardContextMenu.js`** — pure. Exports
  `boardContextMenu(ctx)` → `[{id, items:[{id, label, reason, disabled, writes, action, payload, children}]}]`,
  plus `BOARD_CONTEXT_ACTIONS` (the frozen set of every action id the renderer's switch
  handles) and `contextMenuGesture({command, measuring, movedPx, slopPx})` →
  `"cancel" | "pan" | "open"`. No DOM, no React, no fetch: the runner has no jsdom, so a
  decision that is not in this file cannot be tested.
- **`viewer/src/client/components/board/BoardContextMenu.jsx`** — thin. Renders the model
  through the vendored `components/ui/context-menu.jsx` and maps `action` ids to callbacks
  passed in as props. Contains no `if` about content.

Changed:

- **`viewer/src/client/components/board/BoardWorkspace.jsx`** — wrap the `<PcbCanvas>`
  element inside `pcbPane` (`:844-922`) in `<BoardContextMenu>` and build `ctx` from state
  it already holds: `index`, `pcbView` (`:145`; `onViewChange={setPcbView}` at `:883` hands
  over the whole `{scale, tx, ty}`, so the caller converts client coordinates with
  `screenToBoard` (`lib/boardRender.js:165`) without asking the canvas anything),
  `messageRows` `:404`, `regions` `:413`, `visibleLayers` `:442`, `canEdit` `:461`,
  `viewing` `:368`, `editor` `:462`, `selectedPlacement` `:835-839`, `units` `:138`.
  Wire the callbacks that already exist: `handleSelect` `:525`, `handleLocate` `:540`,
  `handlePrefillNote` `:561`, `editor.setLock`, `editor.move`, `fitAll` `:606`.
- **`viewer/src/client/lib/boardIndex.js`** — add `hitTestPcbAll`; re-express `hitTestPcb`
  in terms of it.
- **`boardKeymap.js`** (the arbiter the `L` section adds) — add `menuOpen` to its `mode`
  argument and return `null` for everything while the menu is open. See "must not break".
- **`viewer/src/client/components/board/PcbCanvas.jsx`** — **the only change in a file the
  write-path workstream owns, and it is additive.** Two things and nothing else:
  (a) add `cancelCommand()` to the imperative handle at `:216-226` — clears
  `dragRef.current`, calls `setMove(null)`, returns the mode string it cancelled
  (`"move" | "measure" | "pan" | ""`); (b) relax `:275` from `if (event.button !== 0) return;`
  so button 2 starts a `"pan"` drag, with a flag on `dragRef` so `onPointerUp` suppresses
  the menu when `moved` is true. Hand this to whoever owns the file. **The menu ships
  without (b)** — right-drag pan then stays dead one release longer, which is the silence
  we already have rather than a regression.

Not changed: **`main.jsx` needs no edit.** Radix's trigger calls `preventDefault()` on the
`contextmenu` event it handles, so our menu replaces the native one in dev *and* prod,
while `suppressNativeContextMenuInProduction()` (`main.jsx:42-49`) keeps doing its real job
— killing the WKWebView "Reload/Back/Forward" menu everywhere else in the app. Do not
delete it; the reason it exists is written at `main.jsx:39-41`.

**One mechanism note the engineer will otherwise find the hard way.** Radix
`ContextMenu.Root` has no `open` prop — it is uncontrolled. Suppressing the menu (for the
in-command cancel, and for a right-drag pan) therefore has to happen *before* the event
reaches the trigger: put `onContextMenuCapture` on the wrapper, call `cancelCommand()`, and
on a non-empty return call `event.preventDefault()` **and** `event.stopPropagation()` so
React never runs the trigger's own handler. If that proves unreliable, the fallback is
`components/ui/dropdown-menu.jsx` (already vendored) driven from our own `onContextMenu`
with a zero-size anchor at the click point: fully controlled, more code, same menu model.

### What this must not break

Each of these works today and must still work with the menu closed **and** open:

- **Left-drag pan** — `PcbCanvas.jsx:305-311` (start), `:358-365` (move).
- **Wheel zoom about the cursor** — `PcbCanvas.jsx:235-246`, a native non-passive listener.
  The wrapper must not sit between it and the stage.
- **Measure** — `Ctrl+M` arm at `BoardWorkspace.jsx:722-725`, drag at `PcbCanvas.jsx:278-284`
  and `:321-333`. Right-click mid-drag cancels the dimension; right-click armed-but-idle
  leaves the mode; neither opens the menu.
- **Move mode** — `PcbCanvas.jsx:286-303` (grab), `:334-356` (drag), `:378-399` (drop →
  `onPlacementMove` → `editor.move`). A right-click mid-drag must abandon with **no write**:
  assert no `board_source_write` call (`usePlacementEditor.js:124-156`).
- **Click select, `Shift`+click net, `⌘`/`Ctrl`+click jump** — `PcbCanvas.jsx:406-425`.
  Right-click must not alter `selection`.
- **Double-click to fit** — `PcbCanvas.jsx:805`.
- **The whole keymap** — the effect at `BoardWorkspace.jsx:712-792`, guarded by
  `isTypingTarget` (`:83-86`, applied at `:714`). Radix moves focus into the menu, but our
  handler is a `window` listener, so `Q`, `F`, `2`, `3` fire *through* an open menu. Route
  it through the `L` section's `resolveBoardKey` with a `menuOpen` mode flag. Without this,
  the first EE who presses `Q` with the menu open flips units and loses the menu.
- **`Escape`** — `BoardWorkspace.jsx:716-720` (clear selection, leave measure) and
  `PcbCanvas.jsx:451-457` (abandon a move drag). With the menu open, `Escape` closes the
  menu **and nothing else**; Radix consumes it, and the same `menuOpen` flag keeps the board
  handler out.
- **The HUD** — `BoardInsightHud.jsx`, fed by `onHoverChange`. Freeze it at the clicked
  point for the life of the menu rather than blanking it: `onPointerLeave`
  (`PcbCanvas.jsx:440-446`) currently nulls hover, and a menu that covers the readout while
  also clearing it is two losses.
- **Cross-probe from everywhere else** — `BoardTreeSidebar.jsx:251`, `FunctionTab.jsx:62`
  and `:78`, `OverviewTab.jsx:448`, `MessagesPanel.jsx:233`, `:254`.
- **The dev inspector** — right-click anywhere *outside* the PCB canvas keeps today's
  behaviour exactly (dev: browser menu; prod: nothing).

### When our source cannot express the edit, the item says so and hands off

Our board is `boards/<stem>.tsx` and the canvas is a view of it (`boardSource.js:1-26`).
Several reasonable right-click intentions have no legal edit behind them. Each resolves the
same way: **disabled item + the real reason + `ask-agent` enabled in the same group.**

| Case | How we detect it | Reason shown |
| --- | --- | --- |
| Part lives inside a golden block, not the board file | the placement lookup returns null (`PcbCanvas.jsx:124-133`, `BoardWorkspace.jsx:835-839`) | "R7 is placed by the `ldo-3v3` block, not by this board file." |
| Position written as an expression (`pcbX={px}` — real, `examples/harness-puck/boards/main.tsx:98`) | `parseBoardSource` pushes it to `skipped` with exactly this wording (`boardSource.js:301`) | "Its position is written as text, not a number." |
| Placement matches zero or two anchors | `binding.unmatched` (`usePlacementEditor.js:232`), rule 3 of `boardSource.js:20-23` | "This part could not be matched to one spot in the board file." |
| An older revision is on screen | `viewing` (`BoardWorkspace.jsx:368`), which already forces `canEdit === false` (`:461`) and closes move mode (`:470-472`) | "You are looking at an older build — open the latest to change it." |
| The board file could not be read or parsed | `editor.state === "failed"` / `parsed.ok === false`, surfaced as `editor.reason` (`usePlacementEditor.js:230`) | Echo `editor.reason` verbatim; it is already plain words. |
| Rotate, flip, delete, align | no such edit exists | Item is **absent**; `ask-agent` carries the intent. |

Two more rules for the write items:

- **The menu never reports its own errors.** `editor.move` and `editor.setLock` fail into
  `editor.error`, which `PlacementEditBar.jsx:145-148` already renders — including the
  predicted-vs-actual guard at `usePlacementEditor.js:139-143` ("the board file on disk is
  not what this view predicted — reloading"). One error surface, not two.
- **A menu write is a change like any other.** It increments `editor.changes`, so Rebuild
  lights up (`PlacementEditBar.jsx:115-129`) and the honest line underneath still says the
  copper has not moved with the part (`:149-153`). A context-menu edit must never trigger a
  rebuild by itself: a build is 95.5–494 s across our three example boards
  (`docs/lessons.md:57`).

### What a test asserts

The runner is `node:test` over pure modules — `viewer/scripts/run-tests.mjs:13-16` discovers
`*.test.{js,mjs,cjs,ts,tsx}`, and all ten files in
`viewer/src/client/components/board/__tests__/` test pure modules. There is no jsdom and no
component test in the repo. So these live in `__tests__/boardContextMenu.test.js` (plus one
in the `lib` tests), and `BoardContextMenu.jsx` stays dumb enough not to need one.

1. **Component hit, bound, unlocked** → the model contains `{id:"lock", disabled:false}`
   labelled `"Lock R3 in place"`.
2. **Same hit, `placement.locked === true`** → same id, label `"Unlock R3"`. The id never
   changes with state; only the label does.
3. **Component inside a block (no binding)** → `lock` and `move-exact` present,
   `disabled === true`, `reason` a non-empty string naming the block, **and** `ask-agent`
   present with `disabled === false`. All four in one test — that pairing *is* the
   never-silently-discard rule.
4. **`viewing: true`** → every item with `writes === true` is disabled and its reason
   mentions the older build; every item with `writes === false` (`objects-here`,
   `select-net`, `show-in-schematic`, `violations`, `zoom-here`) is still enabled.
5. **Empty space (`hit === null`)** → the design-space menu: exactly six items, none with
   `writes === true`, no `lock`, no `select-net`.
6. **No enabled item is inert** — for every item in every group, if `disabled !== true` and
   it has no `children`, then `BOARD_CONTEXT_ACTIONS.has(item.action)`. This is the
   assertion that stops us shipping `WindowMenuBar.jsx:233-234` a second time.
7. **Rotation cannot appear** — `assert(!items.some((item) => /rotate|flip|mirror/i.test(item.id)))`,
   with the comment naming `boardSource.js` and the missing `rotateEdits`/`flipEdits`.
   Delete this test the day those land, and not before.
8. **`hitTestPcbAll` parity** — where `hitTestPcb` returns non-null, the list is non-empty
   and `list[0].elementId === hitTestPcb(...).elementId` under the same `visibleLayers` and
   `tolerance`. And: a point where a trace crosses a courtyard returns both, trace first
   (rank 20, `boardIndex.js:797`).
9. **The gesture ladder** — `contextMenuGesture` returns `"cancel"` for `{command:"move"}`,
   `"cancel"` for `{command:"measure"}`, `"cancel"` for `{command:"", measuring:true}`,
   `"pan"` for `{command:"", movedPx:12, slopPx:4}`, `"open"` for
   `{command:"", movedPx:2, slopPx:4}`. Boundary: `movedPx === 4` with `slopPx === 4` is
   `"open"` — the same `>` comparison `PcbCanvas.jsx:336-341` uses, so left and right agree
   on what a click is.
10. **Violations are the same rows the panel shows** — given `messageRows` from
    `buildMessages` and a point inside two of their boxes, the `violations` submenu has
    exactly those two, error before warning per `severityRank` (`boardViolations.js:20`), and
    each child's action is `locate` carrying the row's own `id`.
11. **The header echoes, it does not compute** — `boardContextMenu` is handed `pointLabel`
    as a string and returns it verbatim, so the menu and the HUD can never print two
    different numbers for one point.

Three checks a unit test cannot reach, done by hand once in the **packaged** build (not
`vite dev`, because dev is where this bug hides):

- Right-click a pad → our menu, not the WKWebView menu.
- Right-click outside the canvas → unchanged (dev: browser menu; prod: nothing).
- With the menu open, press `Q` → units do **not** flip and the menu stays put.

### Ship order

1. `boardContextMenu.js` + `hitTestPcbAll` + their tests. Pure, no UI.
2. `BoardContextMenu.jsx` and the `BoardWorkspace` wrapper, with groups 1, 2, 4 and 5 —
   every item read-only, every callback already exists. **This alone removes the 10/10
   surprise.**
3. `menuOpen` in `resolveBoardKey`, so the keymap stops firing through the menu.
4. Group 3: `lock` (zero new write code) then `move-exact` (dialog + `Ctrl+Q`).
5. `cancelCommand()` on the canvas handle → the in-command cancel ladder.
6. Button-2 pan, with the pointer workstream.

Steps 1-4 need nothing from `PcbCanvas.jsx`. Steps 5-6 are the only ones that do.

### The schematic canvas is deliberately not in this section

`SchematicCanvas.jsx:257` carries the same `if (event.button !== 0) return;` and the same
silence. Its object model is a rendered sheet, not placements we can write, so its menu is a
much shorter set — select, cross-probe to the PCB, zoom, ask the agent. Give it its own
section when someone builds it, and reuse `boardContextMenu.js`'s shape rather than
inventing a second menu model.

---

## Writing `pcbRotation` onto a block instance is a no-op nothing reports — that is 31 of the 57 placements on our own boards

**Ship order: with the Spacebar section above, not after it.** This is a companion
to "Spacebar rotates the part", not a replacement. That section settles the key,
the sign, the preview and the prop. This one settles the case it does not cover:
**most of the tags our boards actually place are not tscircuit elements, they are
our own React components, and a prop those components do not declare is dropped
in silence.** A rotate that writes a prop, returns success, increments the change
counter, survives a 95-second rebuild and comes back with the part at the same
angle is the exact silent discard both sections exist to prevent — and the
Spacebar section as written would ship it.

> Line numbers measured against the working tree on 2026-08-16, the same tree the
> two sections above measured. Anchor on the quoted code, not the number.

### 31 of 57 top-level placements are our own components, and not one of them declares or forwards `pcbRotation`

Measured by running the shipped parser over the three committed boards
(`parseBoardSource`, `boardSource.js:237`) and then reading every component
definition those boards import:

| Board | Placements | Intrinsic tag | Our component |
| --- | --- | --- | --- |
| `examples/harness-puck/boards/main.tsx` | 17 | 6 | 11 |
| `examples/hydrate-coaster/boards/main.tsx` | 28 | 19 | 9 |
| `examples/terminal-keyboard/boards/main.tsx` | 12 | 1 | 11 |
| **Total** | **57** | **26** | **31** |

Every one of the seven distinct components behind those 31 placements —
`UsbCData`, `Ldo3v3`, `Rp2040Core`, `SwTact`, `StatusLed`, `DebugPort`,
`MountingHole` — declares a props type of exactly `pcbX`/`pcbY` (+ `schX`/`schY`
and its own naming props), and every one renders a `<group>` that forwards only
those. Neither the string `pcbRotation` in the props type nor the expression
`props.pcbRotation` in the body appears in any of the 20 definitions across the
three boards (7 + 6 + 7 — hydrate-coaster places its testpoints directly and has
no `DebugPort`). Two representative pairs:

```tsx
// examples/harness-puck/blocks/ldo-3v3/ldo-3v3.tsx:58-67  — the props type
export const Ldo3v3 = (props: {
  u?: string; cin?: string; cout?: string; vinNet?: string; voutNet?: string
  pcbX?: number
  pcbY?: number
  schX?: number
  schY?: number
}) => {
// examples/harness-puck/blocks/ldo-3v3/ldo-3v3.tsx:75      — the body
    <group pcbX={props.pcbX ?? 0} pcbY={props.pcbY ?? 0} schX={props.schX ?? 0} schY={props.schY ?? 0}>
```

The same shape at `rp2040-core.tsx:149,162`, `usb-c-data.tsx:19,49`,
`sw-tact.tsx:30,43`, `status-led.tsx:13,26`, `glue.tsx:55,64` and
`examples/harness-puck/boards/main.tsx:142` (`DebugPort`, declared as literally
`(props: { pcbX?: number; pcbY?: number })`). Write `pcbRotation={90}` on any of
them and the value reaches the function, is never read, and never reaches a
`<group>`.

Exactly **one** of the 57 carries a numeric `pcbRotation` today, and it is an
intrinsic: `examples/hydrate-coaster/boards/main.tsx:73`,
`<group pcbRotation={180} pcbX={-20} pcbY={-22} …>`. So the insertion path is
not the edge case, it is 56 of 57 — and on 31 of those the insertion is worthless.

### Nothing between the edit and the fab packet would catch it

There is no typecheck anywhere in this repo's build path.
`grep -rn "tsc |--noEmit" scripts/ skills/ packages/ viewer/package.json`
(excluding `tscircuit`) returns nothing, exit 1. `toolchain/package.json` lists
`tscircuit`, `@tscircuit/cli`, `@tscircuit/checks`, `circuit-to-svg`,
`circuit-json-to-gltf`, `tsx`, `sharp` — no `typescript`. The board is compiled by
`tscircuit-cli build` (`packages/circuitpy/src/circuitpy/toolchain.py:166-176`,
driven from `generation.py:900-965`), which transpiles rather than typechecks.
The `tsconfig.json` in each example (`examples/harness-puck/tsconfig.json`) is
there for editors; `grep -rn "tsconfig" packages/circuitpy/src/circuitpy/*.py`
returns nothing.

So the whole chain is silent: TypeScript would have flagged the excess prop, and
TypeScript never runs. The user presses `Space`, the file changes, the edit bar
says `U2 turned 90° CCW (now 90°)`, the rebuild costs 95.5–97.6 s
(`docs/lessons.md:57`), and the part comes back at 0°. **This is why the gate has
to live in `boardSource.js`, before the write — nothing downstream will ever
tell us.**

### The rule: lowercase takes the prop, uppercase takes a wrapper

JSX resolves a lowercase tag to an intrinsic element and an uppercase tag to a
value in scope. That is a language rule, not a heuristic, and it is the exact
line the capability gate needs.

**Every intrinsic tscircuit element that can be a placement accepts
`pcbRotation`**, because a placement requires numeric `pcbX` and `pcbY`
(`boardSource.js:296-300`) and those come from `CommonLayoutProps`, which
declares `pcbRotation?: string | number` on the same object
(`toolchain/node_modules/@tscircuit/props/dist/index.d.ts:6986,6999`, tscircuit
pinned at `0.0.2279` in `toolchain/package.json`). Spot-checked on the five
schemas our boards actually use, each carrying its own `pcbRotation` key:
`groupProps` (`index.d.ts:22204,22213`), `resistorProps` (`:71234,71243`),
`capacitorProps` (`:90940`), `testpointProps` (`:159945,159954`),
`silkscreenTextProps` (`:190886,190895`), plus `holeProps` (`:103844,103853`) and
`cutoutProps` (`:100728`).

So `parseBoardSource` gains one more field and the Spacebar section's
`rotatable` becomes a three-way `rotateVia`:

| `rotateVia` | When | What a rotate writes |
| --- | --- | --- |
| `"prop"` | tag starts lowercase, **or** the tag already carries a numeric `pcbRotation` literal | replace or insert the literal — the Spacebar section's table, unchanged |
| `"wrap"` | tag starts uppercase and carries no numeric `pcbRotation` | wrap the element in a rotated `<group>` (below) |
| `"no"` | the existing `pcbRotation` is non-numeric (`{rot}`, `"90deg"`), **or** the bound geometry has no meaningful angle | nothing; refuse with the reason |

The "already carries a numeric literal" escape is not theoretical: a component
that *does* thread rotation proves it by having a number written on it in this
file, and that is evidence our parser can read. It is also what makes a wrapped
placement rotate cheaply the second time — see below.

### This resolves the Spacebar section's one `unverified`, and splits it in two

That section says (`docs/architecture/ide-altium-parity.md`, "Three source
shapes") that `<silkscreentext>` and `<MountingHole>` are both `rotatable: false`
because whether they accept `pcbRotation` is **unverified**. Measured, they are
different cases and only one of them is a refusal:

- **`<silkscreentext>` accepts it.** `silkscreenTextProps` declares `pcbRotation`
  at `index.d.ts:190895`. It is lowercase, it is intrinsic, it takes the `"prop"`
  path. This matters more than it sounds: silkscreen is **16 of our 26 intrinsic
  placements** (4 on harness-puck, 11 on hydrate-coaster, 1 on terminal-keyboard;
  the other 10 are 4 `<resistor>`, 3 `<testpoint>`, 2 `<capacitor>` and 1
  `<group>`), and turning a label to read along a board edge is a real thing an
  EE does in the first ten minutes. Blocking it would give up most of what we
  *can* rotate.
- **`<MountingHole>` refuses, but for the ordinary reason.** It is ours
  (`examples/harness-puck/blocks/glue.tsx:55`), it does not forward the prop, so
  it is `"wrap"` by the rule above — and then a second, narrower rule fires:
  a placement whose bound geometry is `pcb_hole` or `pcb_cutout` only
  (`LOOSE_TYPES`, `boardSource.js:444-451`) is `"no"`, because a drill is round
  and a rotation about its own centre changes nothing anybody can measure. That
  covers 13 of the 31 (`H1`–`H3`, `H1`–`H4`, `H1`–`H6`). Refusing a no-op is
  cheaper than offering one.

Net: of 57 placements, **26 rotate by prop, 18 rotate by wrap, 13 refuse as
round drills, 0 are silently dropped.** Those four numbers are the acceptance
criterion for this section.

### `rotateEdits` gains a second shape: wrap the element in a rotated `<group>`

We do not invent this idiom. It is already hand-written in our own board source,
by the author, for exactly this reason —
`examples/hydrate-coaster/boards/main.tsx:73-75`:

```tsx
    {/* ---- the brain, turned to face its neighbours ------------------------ */}
    <group pcbRotation={180} pcbX={-20} pcbY={-22} schX={0} schY={0}>
      <Rp2040Core pcbX={0} pcbY={0} schX={0} schY={0} />
    </group>
```

`Rp2040Core` does not take a rotation, so the board turns it with a group. The
app writes the same thing.

**The transform, on `examples/hydrate-coaster/boards/main.tsx:70`.** Before:

```tsx
    <Ldo3v3 pcbX={14} pcbY={-18} schX={-46} schY={22} />
```

After a 90° CCW turn:

```tsx
    <group pcbX={14} pcbY={-18} pcbRotation={90}>
      <Ldo3v3 pcbX={0} pcbY={0} schX={-46} schY={22} />
    </group>
```

**Four edits, over the original offsets, in `withExpected` form
(`boardSource.js:383-389`), exactly like `moveEdits` (`:392`):**

| # | Span | Text |
| --- | --- | --- |
| A | `[lineStart, tagStart)` — the element's own indent | `<indent>` + `<group pcbX={<X>} pcbY={<Y>} pcbRotation={<D>}>\n` + `<childIndent>` |
| B | `xSpan` | `0` |
| C | `ySpan` | `0` |
| D | `[elementEnd, elementEnd)` — a pure insertion | `\n` + `<indent>` + `</group>` |

Three rules make it deterministic:

1. **`<X>` and `<Y>` are copied byte-for-byte** from `source.slice(xSpan.start,
   xSpan.end)` and the same for `y` — never re-formatted through `formatMm`.
   `pcbX={14}` stays `14`; a `-18` stays `-18`. This is rule 1 of the module
   header (`boardSource.js:10-14`): only the number moves, and here not even that.
2. **`<childIndent>` is `indent + "  "` when the element is on one line, and
   `indent` when it spans several.** Detected by whether
   `source.slice(tagStart, elementEnd)` contains a newline. `harness-puck`'s
   second LDO is the multi-line case (`examples/harness-puck/boards/main.tsx:194-203`):
   re-indenting its nine interior lines would touch bytes we promised not to
   touch, so the child keeps its own indent and the group brackets it at the same
   level. Ragged by one level, valid TSX, and a diff a human reads in one look.
3. **The group carries no `schX`/`schY`.** A `<group>` with only pcb props is
   already legal board source in this repo (`examples/harness-puck/blocks/glue.tsx:64`),
   the child's schematic props are left byte-identical, and rotating a board
   placement must not move anything on the schematic. The hydrate example above
   carries `schX={0} schY={0}` on both halves, which is the identity, so it is
   not evidence either way — we choose the narrower form, and test 9 below is how
   we hold it.

**Only self-closing elements are wrappable in v1,** because `elementEnd` is then
exactly `tag.gt + 1` and needs no close-tag search. That costs nothing on any
committed board: all 31 of the component placements are self-closing, measured.
The one non-self-closing top-level placement in the three boards is
hydrate-coaster's `<group>` at line 73, which is intrinsic and already carries a
number — it takes the `"prop"` path and never reaches here. A non-self-closing
uppercase tag is `rotateVia: "no"` with the reason `` `${label} is written with a
closing tag; this app only wraps self-closing elements.` ``

**`parseBoardSource` additions**, beyond the four the Spacebar section already
adds: `selfClosing` (already computed at `boardSource.js:150`, just not stored),
`elementEnd`, and `rotateVia` / `rotateReason`.

**Server limits hold.** Four edits against `MAX_SOURCE_EDITS = 8`
(`viewer/src/server/circuit/http.mjs:174`); the longest text is edit A at about
55 characters for a 4-space indent against `MAX_EDIT_TEXT = 200` (`:176`). Guard
anyway: if edit A's text would exceed 200 bytes, the placement is
`rotateVia: "no"` with the reason `` `${label} is nested too deeply for this app
to wrap.` `` — no board in `examples/` indents a top-level placement past 6
spaces, so this never fires today and cannot surprise us later.

### The wrap rotates about the anchor, and hydrate-coaster proves the arithmetic

The Spacebar section establishes that `pcbRotation` leaves `anchor_position`
alone. The wrap depends on a second fact: children rotate **about the group's
anchor**, which is why the child has to be moved to `0,0` and the group has to
take its coordinates.

`examples/hydrate-coaster/boards/main.circuit.json`, against the source at
`main.tsx:73-75`:

- `pcb_group_2` — the wrapper — has `anchor_position: {x: -20, y: -22}`, the
  un-rotated `pcbX`/`pcbY`.
- `Rp2040Core`'s `U4` sits at group-relative `pcbX={13} pcbY={0}`
  (`examples/hydrate-coaster/blocks/rp2040-core/rp2040-core.tsx:164`). Un-rotated
  it would compile to `{-7, -22}`. It compiles to `pcb_component_11` at
  `{x: -33, y: -22}` — `(-20, -22)` plus `13·(cos 180°, sin 180°)`. Rotation about
  the anchor, exactly.

The binding survives, and this is measured rather than argued: running
`parseBoardSource` + `bindPlacements` over hydrate-coaster's committed source and
its committed `main.circuit.json` binds **28 of 28** placements with **0
unmatched**, and `group[1]` — the hand-written wrap — is one of them. It binds
even though the inner `Rp2040Core` group compiles to a second `pcb_group` at the
same anchor, because `bindPlacements` keeps only groups whose parent is root
(`boardSource.js:520-524`). A wrap the app writes produces the same structure as
the wrap the author wrote, so it binds the same way. Across all three boards the
same run is 57 of 57 bound, 0 unmatched.

**The second rotate of a wrapped part is cheap.** After the wrap, the draggable
unit at that anchor is the new `<group>` — intrinsic, numeric `pcbRotation` — so
the next `Space` takes the `"prop"` path and rewrites one literal. Wraps never
nest. Test 11 pins this.

### The wrap is confirmed once per placement, and it is undoable because the inverse is arithmetic

**Confirm, not silent.** A four-line structural edit to a file that carries forty
lines of hand-written measurement prose is a wider blast radius than replacing
`180` with `270`, and Altium's own pattern is that the wider the blast radius the
more it interposes a confirmation — `Confirm Global Edit`,
"Enable to open a confirmation dialog before committing a global editing action"
([pcb-editor-general-preferences](https://www.altium.com/documentation/altium-designer/pcb-editor-general-preferences)).
The dialog shows the exact diff it is about to write — the four lines above, not
a description of them — with `Wrap and turn` / `Cancel`. It fires **once per
placement per session**; the second turn of the same part goes straight through
on the `"prop"` path anyway.

A `"wrap"` placement never starts a coalescing burst (the Spacebar section's
300 ms window). The first keypress opens the dialog carrying its angle; `Cancel`
writes nothing and drops the angle.

**Undo works, and it is one new pure function.** The Spacebar section's undo
inverts a rotation by knowing the previous angle. A wrap needs the inverse of
four edits at once, over text whose offsets have all moved. Every edit already
carries the bytes it replaced (`withExpected`, `boardSource.js:383-389`), so the
inverse is arithmetic:

```js
/**
 * The edits that put `applyEdits(source, edits)` back to `source`.
 * Offsets are shifted by the cumulative length change of every earlier edit,
 * which is exactly what `applyEdits` (boardSource.js:368) did to produce them.
 * Every input edit must carry `expected`.
 */
export function invertEdits(edits) {
  const list = [...(edits || [])].sort((a, b) => a.start - b.start || a.end - b.end);
  let shift = 0;
  return list.map((edit) => {
    const text = String(edit.text);
    const start = edit.start + shift;
    shift += text.length - (edit.end - edit.start);
    return { start, end: start + text.length, text: edit.expected, expected: text };
  });
}
```

The result is a valid edit set over the **post-write** text, already carrying its
own `expected`, so the server's compare-and-swap (`http.mjs:192-241`) checks it
the same way it checks any other write. The undo entry for a wrap is
`{kind: "wrap", placementId, label, inverse}` and `usePlacementEditor.undo`
(`usePlacementEditor.js:195-214`) sends `entry.inverse` instead of recomputing
edits from a placement that no longer exists under its old id — which it would
not find, because after a wrap the placement is a `<group>`, not an `<Ldo3v3>`.

`invertEdits` is general, not wrap-specific. Every structural edit that lands
after this one — flip, array, delete — gets undo for free by recording it.

### Keystrokes: nothing new, one dialog in front of the same key

No new binding. The gate changes what `Space` does per placement, and the
Spacebar section's table is unchanged for the 26 intrinsic placements.

| Placement | `Space` / `Shift+Space` in move mode |
| --- | --- |
| intrinsic, or already numeric (`rotateVia: "prop"`) | turn by one step, coalesce the burst, one write — unchanged |
| our component, self-closing (`"wrap"`) | first turn per session opens the wrap confirm carrying the angle; after it lands, subsequent turns are `"prop"` |
| round drill, non-numeric angle, closing tag, too deep (`"no"`) | no turn, no write, reason in the edit bar |
| locked | no turn — unchanged (`PcbCanvas.jsx:292`) |

**Discoverable before the key is pressed, not after.** `PlacementEditBar.jsx:48-52`
today reads `N parts and blocks can be dragged · M cannot`. It gains a third
clause from the same binding data: `· K need a wrapper to turn`. On harness-puck
that reads `17 parts and blocks can be dragged · 11 need a wrapper to turn`,
which is the honest first impression rather than a discovery on keystroke five.
The hover outline gains `data-rotate-via` beside the `data-rotatable` the
Spacebar section adds.

### The rebuild prompt has to name the wrapper, or the agent will remove it

`BoardWorkspace.jsx:502-512` composes the rebuild instruction and it currently
says only `now has a new pcbX/pcbY pair`. A model handed a board with a
one-child `<group>` around an `<Ldo3v3>` and no explanation will inline it as a
tidy-up, and the rotation disappears on the next build. Two sentences, added to
that array:

> `Some placements are wrapped in a one-child <group> that carries pcbRotation —`
> `that is how a block with no rotation prop gets turned. Keep those wrappers`
> `exactly as they are; do not inline them.`

The existing `locked:` sentence at `BoardWorkspace.jsx:508-509` is the precedent
and the same mechanism.

### Files, and what each one needs

| File | Change |
| --- | --- |
| `boardSource.js` | `selfClosing` + `elementEnd` on parsed placements (both already computed at `:150`); `rotateVia` / `rotateReason` replacing the Spacebar section's boolean `rotatable`; the wrap branch inside `rotateEdits`; `invertEdits`; `wrapPreview(source, placement, degrees)` returning the four-line string the confirm shows. All pure — no DOM, no React, `node:test` covers every line. |
| `usePlacementEditor.js` | `rotate()` routes on `placement.rotateVia`; a `wrap` history entry carrying `inverse`; `undo` sends a recorded inverse when one is present instead of recomputing. |
| `PlacementEditBar.jsx` | the `· K need a wrapper to turn` clause at `:48-52`; the wrap confirm, rendered in the strip's own error slot region (`:145-148`) rather than a new modal layer. |
| `PcbCanvas.jsx` | one line: a `"wrap"` placement does not open a coalescing burst. Everything else the Spacebar section already specifies. |
| `BoardWorkspace.jsx` | the two rebuild-prompt sentences at `:502-512`. |
| `viewer/src/server/` | **nothing.** Four edits and 55 bytes are inside `MAX_SOURCE_EDITS = 8` and `MAX_EDIT_TEXT = 200` (`http.mjs:174,176`). |
| `PropertiesPanel.jsx`, `SchematicCanvas.jsx` | **nothing.** |

### What must not break

Every one of these works today and is on the regression list:

- **Move.** `moveEdits` (`boardSource.js:392-404`) is untouched; a `"wrap"`
  placement still drags and still writes two literals. Refusing a turn must never
  cost a drag.
- **The binding.** 57 of 57 placements bind across the three boards today. After
  a wrap, the same count must hold — that is test 10.
- **Locks.** `lockEdits` writes a comment on the line above the element
  (`boardSource.js:414-431`, `LOCK_COMMENT` at `:33`). A wrap inserts a new line
  at `lineStart`, which is where a lock comment lives. **A locked placement is
  already refused before this point** (`PcbCanvas.jsx:292`), so the two cannot
  collide — but the parser reads the lock by looking one line up
  (`readLock`, `boardSource.js:330-336`), so after a wrap the comment would sit
  above the `<group>` and still be read correctly. Test 12.
- **Comments.** Every board carries measurement prose the module header exists to
  protect (`boardSource.js:10-14`) — `examples/hydrate-coaster/boards/main.tsx:64-69`
  is six lines of it directly above the `<Ldo3v3>` this section wraps. A wrap
  inserts at `lineStart`, strictly below those bytes. Test 8 asserts the file
  differs only by the four spans.
- **Pan, measure, selection, cross-probe.** Untouched. This section adds no
  pointer handling and no keybinding.
- **Undo.** Rotate, move, combined move-and-turn, and now wrap, each one press.
- **The rebuild gate.** A wrap increments `changes` (`usePlacementEditor.js:145`)
  and never triggers a build.

### What a test asserts

`node:test` via `viewer/scripts/run-tests.mjs`. All pure; the real boards are
already loaded by `__tests__/boardSource.test.js:27-30`.

**Extending `__tests__/boardSource.test.js`:**

1. Over all three example boards, `parseBoardSource` classifies exactly **26**
   placements `rotateVia: "prop"`, **18** `"wrap"` and **13** `"no"`, and the
   `"no"` set is precisely the 13 `MountingHole` placements. Hard numbers, so a
   toolchain bump or a block refactor breaks the test rather than the user.
2. Every `"wrap"` placement has `selfClosing === true`. This is the invariant the
   whole v1 restriction rests on.
3. `rotateEdits(source, ldo3v3Placement, 90)` on hydrate-coaster returns exactly
   four edits, and `applyEdits` produces the three-line block quoted above,
   character for character.
4. Those four edits are non-overlapping when sorted, each carries `expected`
   equal to the exact original bytes, there are at most 8 of them, and no `text`
   exceeds 200 — the server contract at `http.mjs:174,176,224-227`, asserted
   client-side.
5. `<X>` and `<Y>` are copied, not reformatted: a fixture with `pcbX={014}` and
   `pcbY={-18.0}` produces a group carrying `014` and `-18.0`.
6. Multi-line: wrapping `examples/harness-puck/boards/main.tsx:194-203` leaves
   all nine interior lines byte-identical, and the child indent equals the group
   indent (no `+2`).
7. Single-line: the child indent is the group indent plus two spaces.
8. Diff containment: `applyEdits` output differs from the input **only** inside
   the four spans. Assert the six comment lines at
   `examples/hydrate-coaster/boards/main.tsx:64-69` are byte-identical.
9. The written group carries `pcbX`, `pcbY`, `pcbRotation` and **nothing else** —
   in particular no `schX`/`schY` — and the child's `schX={-46} schY={22}` are
   untouched.
10. Round-trip binding: apply the wrap, re-parse, and `bindPlacements` against
    the **unchanged** `main.circuit.json` binds all 28 hydrate-coaster placements
    with 0 unmatched, the new `group[N]` among them at the same `(14, -18)`.
11. No nested wraps: re-parse after the wrap and the new placement is
    `rotateVia: "prop"` with `rotation === 90`; a second `rotateEdits(…, 180)`
    returns **one** edit.
12. Lock interaction: a locked `<Ldo3v3>` wrapped anyway (calling `rotateEdits`
    directly, bypassing the UI guard) leaves the `locked:` comment above the new
    `<group>`, and re-parsing reports `locked === true`.
13. `invertEdits(edits)` applied to `applyEdits(source, edits)` reproduces
    `source` byte-for-byte, for the wrap's four edits, for `moveEdits`' two, and
    for `lockEdits`' single insertion.
14. `invertEdits` output is itself a legal write: sorted, non-overlapping, every
    entry carrying `expected` equal to the bytes then present.
15. Refusals return **zero** edits and a non-empty `rotateReason`, one case each:
    a `pcb_hole`-bound placement, a `pcbRotation={rot}` fixture, a
    `pcbRotation="90deg"` fixture, an uppercase tag with a closing tag, and a
    placement indented past the 200-byte guard.

**New `__tests__/blockRotationProps.test.js`.** The gate is only correct while
our blocks stay rotation-blind, and a block author could thread `pcbRotation`
tomorrow and quietly widen the `"prop"` set:

16. For every component referenced by a top-level placement in the three example
    boards, read its definition and assert `rotateVia === "wrap"` iff the props
    type does not contain `pcbRotation`. Today that is 7 components, all without
    it. The day someone adds it, this test tells them to widen the gate rather
    than leaving free capability on the floor.

**Manual, once, before merge** — the only assertion no unit test can make,
because it needs a real build (95.5–97.6 s quiet for hydrate-coaster,
`docs/lessons.md:57`): wrap-and-turn hydrate-coaster's `<Ldo3v3>` at `main.tsx:70`
by 90°, rebuild, and confirm `U2`'s `pcb_component` comes back with
`rotation: 90` and `center: {x: 14, y: -18}` unchanged. If `rotation` is `0`, the
prop did not reach the compiler and the wrap is wrong. If `center` moved, the
rotation is not about the anchor and the whole construction is wrong.

### Unverified, and what we chose instead

| Unpublished / unmeasured | Our choice | Why |
| --- | --- | --- |
| Whether `tscircuit@0.0.2279` warns at runtime about a prop a function component ignores — we could not run the CLI in this sandbox (`@tscircuit/core` in `toolchain/node_modules` has no `exports` main; `tscircuit-cli build` produced no output outside the repo tree) | Assume **no warning** and gate client-side | The code path is conclusive on its own: `Ldo3v3` never reads `props.pcbRotation` (`ldo-3v3.tsx:58-75`), so no warning could change the outcome. Gating client-side is correct either way. |
| Whether a wrapper `<group>` with no `schX`/`schY` shifts the schematic | Write no schematic props | A pcb-only `<group>` is already shipping board source (`examples/harness-puck/blocks/glue.tsx:64`) and those boards build clean. Test 9 pins the source; the manual build pins the artifact. |
| Altium's confirmation wording for a wide edit | Show the four-line diff itself | A diff is not a wording question. Altium's own principle — bigger blast radius, more confirmation — is the part we copy. |
| Whether Altium ever rewrites a design's structure to honour a rotation | n/a — Altium has no source file, so the question does not exist there | This is the one place our architecture is genuinely different, not behind. The file is the board (`boardSource.js:1-6`), so "can the source say it" is a real question Altium never has to answer, and answering it out loud beats a `rotatable: false` that looks like a missing feature. |

---

## Integration: what a first-contact Altium user now experiences

Five workstreams built the sections above in parallel, each owning different
files. This section is written after joining them, and it is the honest state
rather than the sum of five reports. `npm --prefix viewer test` → **916 tests,
916 pass, 0 fail** (up from 901; the added tests are the two dispatch/wiring
scans and the guards named below). `npx vite build` is clean.

Corrections to the specs above, made while joining them, are listed at the end.
**Where this section and an earlier one disagree, this one is the code.**

### The sixty seconds

Ten years of Altium muscle memory, in the order the hands try it.

| Reflex | Altium | Ours now | Same? |
| --- | --- | --- | --- |
| **Right-click** on empty board or an object | Context menu for the object under the cursor | Menu, opened on **release** with the object under the press | Yes |
| **Right-press and drag** | Pans (`Right-Click, Hold&Drag`) | Pans, no menu — the menu needs a press that did not travel | Yes |
| **Right-click mid-drag** | Escapes the current interactive command | Abandons the move, commits nothing | Yes |
| **Right-click with the ruler armed** | Escapes the command | Disarms measure; no menu | Yes |
| **Ctrl/⌘+Z** | Undo | Undoes one edit to `boards/<stem>.tsx`, one per press | Yes |
| **Ctrl+Y / ⇧⌘Z** | Redo | Redoes it | Yes |
| **Hold ⌘Z** | — | One undo. Auto-repeat is dropped | **Deliberately different** |
| **L** | Opens Layers And Colors | Brings the layer bar on screen and puts the keyboard on it | **Partly** |
| **Q** | mm ↔ mil | mm ↔ mil, whole workspace | Yes |
| **Space during a drag** | Turns the dragged object CCW by the rotation step | Same; `⇧Space` CW; default step 90°, settable on the strip | Yes |
| **Space with a part merely selected** | Undocumented; appears to do nothing | Nothing. The ↺/↻ buttons on the strip and in Properties do it | **Deliberately different** |
| **Double-click a part** | Opens Properties for it | Selects it, which is what fills Properties here | Yes |
| **Double-click empty board** | Nothing | Fits the board to the pane | **Deliberately different** |
| **Escape mid-drag** | Abandons the move | Abandons the move, and **keeps** the selection | Yes |
| **Escape otherwise** | Does *not* clear a filter (`Shift+C` does) | Clears the selection and disarms measure; `Shift+C` also clears | **Deliberately different** |

Every deliberate difference, in one sentence each:

- **Held ⌘Z does not repeat.** Auto-repeat fires every ~40 ms, so a one-second
  hold rewound roughly twenty-five hand placements into a hook that had no redo
  stack; a key that destroys a morning's work on a stuck keyboard is not a
  feature we owe anyone. (`[` and `]` do repeat — stepping the mask level is the
  one binding whose meaning is "keep going".)
- **`L` does not open a popover**, because our layer chips are a permanent bar
  rather than a panel — there is nothing to open, so the key puts the bar on
  screen and moves focus onto it.
- **Space does nothing on a merely selected part.** Altium's wording is "the
  object being placed/moved" and it publishes nothing about the idle case, and
  inventing a meaning for a key the reference leaves undefined is precisely how
  the misfires this work exists to remove got built.
- **Escape clears the selection.** On the web, Escape is what a stuck user
  presses; Altium's `Shift+C` is bound too, so nobody's reflex is refused.
- **Double-click on empty board fits it**, because Altium spends nothing on
  that gesture and it was our only mouse-only way to fit.

### What still misfires or is absent

Named plainly, because a list of six fixed things next to an unmarked seventh is
how a report becomes marketing.

1. **The Layers And Colors popover** (per-layer alpha, layer sets, the colour
   picker) is still unbuilt. `L` reaches the bar, not a panel.
2. **`L` mid-drag does not flip a part to the other side.** Altium's is a
   different `L`; ours does not have a flip edit at all, and `boardSource.js`
   has no `flipEdits`. Absent, and absent reads as absent.
3. **`hitTestPcbAll` does not exist**, so the menu's "Objects under the cursor"
   submenu never appears — the press resolves one hit. Correct behaviour for the
   data available, but it is a row Altium has and we do not.
4. **Rotation does not preview on the canvas.** A turn writes the file and the
   part is still drawn at the last build's angle until a rebuild. The strip and
   the Properties row both say so in words; the drawing does not.
5. **Unwrapping is not automatic.** Turning one of our own components wraps it
   in a `<group>`; turning it back to 0° removes the `pcbRotation` but leaves
   the wrapper. The file is semantically identical to where it started, not
   textually. Undo is byte-exact; a full 360° is not.
6. **No test renders anything.** The viewer's runner is bare `node:test` with no
   DOM. Dispatch and wiring are now covered by two source-reading scans
   (`boardDispatch.test.js`, `boardWiring.test.js`, both mutation-checked
   against the exact edits the audit made), which is a floor, not a render test.

### Seams closed

The failures below existed only *between* the parallel pieces. Each was
invisible to the piece that caused it.

- **`Shift+M` was Altium's Board Insight Lens.** The Messages drawer moved off
  `L` and onto another published Altium binding — the same sin one key over
  (ALTIUM-NOTES §3). Messages is now **`Shift+N`**, which nothing in Altium's PCB
  editor answers to, and the drawer's own chevron prints `⇧N` so the move is
  learnable.
- **The context menu could not be opened.** Radix's `ContextMenu.Root` is
  strictly uncontrolled (it takes no `open` prop), so it can only be opened by
  the `contextmenu` event — the very event both canvases must swallow to keep
  right-drag pan working on macOS. `BoardContextMenu.jsx` is now a **controlled
  `DropdownMenu`** anchored to a 1×1 element at the release point. It also
  imported `@/ui/dialog`, which does not exist (`@` is `src/client`; the
  component lives at `@/components/ui/dialog`) — the file could not have built.
- **Right-click mid-drag reached no handler.** `pointerdown` does not fire for a
  second button while one is held
  ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/Element/pointerdown_event)),
  so the cancel branch was unreachable and the move committed on release. The
  escape now hangs off `contextmenu` via `canvasPointer.escapeLiveCommand`,
  which refuses to cancel a right-button *pan* — otherwise macOS, which fires
  `contextmenu` on press, would kill every pan on its first frame.
- **Space misfired with a false sentence.** The canvas printed "Turning a part
  is not wired up yet" three inches from a working ↺ button, because
  `onPlacementRotate` had no producer. The workspace now supplies one, and
  **one** command shape reaches the file: `placementRotate.commitRotateStep` →
  `usePlacementEditor.rotate`, from the key, the strip, and Properties alike.
  `boardWiring.test.js` fails if any surface starts working out an angle itself.
- **The Properties panel said rotation "is not built yet".** It now shows the
  built angle, the file angle when they differ, and the same ↺/↻ pair.
- **Escape during a drag also dropped the selection.** Two `window` listeners
  answered one key. The canvas now consumes it, and listens in the **capture**
  phase so the ordering is a DOM guarantee rather than an accident of which
  React effect ran first.
- **The shortcut sheet leaked every key to the board behind it.** Radix's focus
  scope is a `tabIndex=-1` div, so one click on a heading moved focus off the
  search box and `E`/`3` reached the board through the modal — and Escape, which
  Radix dismisses on a capture-phase listener *without* `preventDefault`, closed
  the sheet **and** cleared the selection. `boardKeymap.isOverlayTarget` now
  refuses any key from inside a `dialog`/`menu`/`listbox`, which covers every
  overlay rather than this one.
- **`<select>` was not a typing target.** Typing `3` to pick a 30° rotation step
  switched the workspace to the 3D tab under the edit. Added to
  `boardKeymap.isTypingTarget`, to the sheet's own copy, and to `Board3DView`'s.
- **⌘Q was advertised inside the Move-by-exact dialog.** This is a Tauri app;
  AppKit consumes the Quit key equivalent before WKWebView sees it, so the
  tooltip taught Mac users a key that closes the app with a half-typed offset in
  the box. Now `Ctrl+Q` only — Altium's own binding — and
  `shortcutScan.js` learned to print `Ctrl` rather than `Mod` when a handler
  requires Ctrl and explicitly refuses Meta, so the sheet cannot re-introduce it.
- **`F` ran two handlers on the 3D tab** and the sheet printed two contradictory
  rows for it. The workspace now stands down on that tab; the 3D camera reset is
  the fit there, and both rows say "fit".
- **Double-click on a part fitted the whole board** instead of putting that
  part in Properties (ALTIUM-NOTES §6). It now selects the object under the
  cursor, and keeps the fit for a double-click on empty board.
- **Toggle rows were labelled as state.** "Grid: on" was the row that turned the
  grid *off*, while the object menu two functions away said "Turn on Move
  parts". Every row is imperative now.
- **`pointLabel` was in the menu's contract and not in the emitter's payload**,
  so the header lost its coordinate and the ask-the-agent prefill shipped as
  `"J1 at : "`. The model derives it from `point` + `units` through the same
  `formatPoint` the HUD uses; a caller can no longer forget it.
- **The delta origin was unreachable on a MacBook.** Space was handed to
  rotation and `Insert` is not on the keyboard, and `resetDelta` had no caller
  anywhere. The HUD's Δ is now a button.
- **The wrap confirmation acted on a stale part.** `pendingWrap` survived a
  change of selection, so "Wrap and turn" wrote four lines of structure to a
  part that was no longer highlighted. Cleared on `placement.id`.
- **`⌘Z` had no `busy` guard** while the button it duplicates has always had
  one, so a fast second press raced the write and filled the strip with
  `SOURCE_CHANGED` instead of undoing.
- **Undo had no redo.** Now it does, on `⇧⌘Z` and `Ctrl+Y`, with a button on the
  strip. Undo and redo are one function pointed two ways, so a redo cannot
  restore something slightly different from what the undo removed.
- **Nothing tested that a command id reaches a handler.** Emptying
  `case "edit.undo":` left the whole suite green. `boardDispatch.test.js` holds
  `BOARD_COMMANDS` and `BOARD_CONTEXT_ACTIONS` against the two switches in both
  directions and requires each case to run a statement; the test performs the
  audit's own mutation on the real source and asserts the scan catches it.

### Corrections to the sections above

- **"Spacebar rotates the part"** specifies Space turning a *selected*
  placement in move mode. Not built, and now deliberately not: see the table.
  The ↺/↻ pair covers the selected part, in two places.
- **The rotate section's manual check is wrong** and would report a false
  failure. It says to confirm `U2`'s `pcb_component` comes back with
  `center {x:14, y:-18}` unchanged. It comes back `{x:14, y:-18.665}` and the
  construction is nevertheless correct — every pad, silkscreen path, silkscreen
  text and trace lands on the exact 90° rotation about `(14, -18)`; only
  tscircuit's recomputed footprint bounding-box centre moves, because SOT-23-5
  is asymmetric. **Check the pads, or `pcb_group`'s `anchor_position`, which
  stays at exactly `{14, -18}`.** Never `pcb_component.center`.
- **`pcbRotation={0}` is never written.** `rotateEdits` removes the prop for
  `null` *and* for any angle normalizing to zero, so four taps of Space restore
  the file byte-for-byte instead of leaving a dead prop and offering a
  ~96-second rebuild for a board that had not moved. Verified on
  hydrate-coaster's `TP1`. The stated cost: a hand-written `pcbRotation={0}`
  comes back absent rather than byte-identical.
- **`changes` is zeroed when the file matches the text the board was built
  from**, so undoing everything, or turning a part full circle, stops offering a
  rebuild for a board that did not change.
- **`BoardInsightHud.jsx`'s doc comment** no longer credits Space with the delta
  origin.
