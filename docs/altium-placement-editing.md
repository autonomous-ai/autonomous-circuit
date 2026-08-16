# Altium placement editing, keystroke by keystroke

Companion to `viewer/src/client/components/board/ALTIUM-NOTES.md`. That study covers
**looking** at a board — cross-probe, net mask, Board Insight, layers, DRC, Properties,
2D/3D, ActiveBOM. This one covers **changing** it: move, rotate, flip, align, array,
room, lock, snap, grid.

Same house rules. Every Altium claim carries a source URL; anything not published
anywhere says **unverified** rather than inventing a keystroke or a default. Every claim
about our app carries `file:line` and the code, and a grep that returns nothing is stated
as the evidence it is.

Scope note: a separate workstream is building the write path (move a component, it
persists). Nothing here is about that plumbing. This is about the *hand* — the ~30
reflexes an EE fires without looking, and which of them our keyboard currently answers,
ignores, or answers with the wrong thing.

---

## 0. The headline: we have three direct key collisions, not three gaps

A missing feature is a gap. A key that does *something else* is worse — it is the
"surprise" in Dee's bar. Our `BoardWorkspace.jsx` keymap was built against the *viewing*
half of Altium and took three keys that Altium spends on placement.

| Key | Altium (placement) | Ours today | Evidence |
| --- | --- | --- | --- |
| `M` | Opens the **Move** menu. `M`,`C` = Move Component; `M`,`X` = Move Selection by X,Y | Cycles the net highlight method (Normal/Dim/Mask) | `BoardWorkspace.jsx:668-670` `case "m": case "M": setHighlightMethod(...)` |
| `L` | **Layers And Colors** when idle; **flip to the other side of the board** while an object is on the cursor | Toggles the Messages panel | `BoardWorkspace.jsx:677-680` `case "l": case "L": setMessagesOpen(...)` |
| `Space` | **Rotate the object being placed/moved counterclockwise** | Zeroes the delta origin (KiCad's gesture) | `PcbCanvas.jsx:348-350` `if (event.key === " " && cursor …) setDeltaOrigin(cursor)` |

`R` is a fourth, softer one: Altium uses `R` to cycle placement modes during Reposition
Selected Components; we use it to toggle region visibility (`BoardWorkspace.jsx:681-683`).

Everything our keymap *does* get right is a viewing key, and it gets them right:
`Q` units (`BoardWorkspace.jsx:664-667`), `Shift+C` clear filter, `Shift+S` single-layer,
`Shift+H` HUD (`BoardWorkspace.jsx:641-645`), `[` / `]` mask level, `Ctrl+M` measure,
`Ctrl+PgDn` fit (`BoardWorkspace.jsx:631-639`). The placement half of the keyboard is
empty or wrong.

**What is not bound at all**, verified by grep over
`viewer/src/client/components/board/`:

- `Tab` — `grep -rnE '"Tab"' *.jsx *.js` → no matches. Altium's mid-command properties key.
- Arrow keys / `Ctrl`+arrow / `Ctrl+Shift`+arrow — no arrow handling anywhere.
- `X`, `Y` mirror, `L` flip, `Shift+Space` — no matches.
- Align and distribute — `grep -rniE "align|distribute"` returns only `textAlign`,
  `alignItems`, `anchor_alignment` and prose in comments. No command.
- Snap of any kind — `grep -rniE "\bsnap"` returns only comments about camera snapping in
  `BoardOrientationCube.jsx:9-10` and `viewportTools.js:168-169`, and unrelated uses of
  "snapshot".
- Grid — the only grid in the PCB canvas is a decorative SVG pattern,
  `PcbCanvas.jsx:668-678`: a `<path>` in a `<pattern>` painted as a background `<rect>`,
  drawn only when `showGrid && gridPx >= 6`. It is a picture of a grid. Nothing snaps to it.
- Component lock — `grep -rniE "\block(ed)?\b"` finds only the *download packet* lock
  (`boardActions.js:31-35`, `BoardActions.jsx:114-132`) and the parts.json "lock" file.
  No object-level lock.
- Rooms, arrays, paste — no matches.

And there is no multi-object selection to align *with*: selection is a single nullable
value, `const [selection, setSelection] = useState(null)` at `BoardWorkspace.jsx:127`,
cleared wholesale at `:225` and `:370`. Every align, distribute, array and group-move
reflex below needs a set, not a scalar.

---

## 1. Move: two different verbs, and the menu-accelerator habit

Altium separates **Move** from **Drag**, and EEs use both deliberately:

- **Move** — "move any object in the current document. Any nets associated to an object
  will remain connected." Attached tracks are *rubber-banded by net*, not dragged.
- **Drag** — "move any object in the current document. If the object has connected tracks
  and/or arcs … these will remain connected as the object is moved." The copper comes
  with it.

([placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques))

Which one a plain click-drag performs is a *preference*, not a fixed behaviour: **Comp
Drag** on `Preferences » PCB Editor – General` chooses `None` (component moves alone,
tracks disconnect) or `Connected Tracks`
([pcb-editor-general-preferences](https://www.altium.com/documentation/altium-designer/pcb-editor-general-preferences?version=22)).

**Click-drag** itself is documented as: "Move the single object currently under the
cursor (or group of selected objects if the object is part of that selection)"
([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors)).
Note the parenthesis — grabbing one member of a selection moves the whole selection. That
is the behaviour people rely on and it is the reason a scalar `selection` will not do.

**The `E`,`M` habit.** Altium's menus are keyboard-accelerated: `E` opens Edit, `M` opens
the Move submenu, and the next letter picks the command — `E`,`M`,`M` = Move,
`E`,`M`,`D` = Drag, `E`,`M`,`C` = Move Component. In the PCB design space `M` alone is
enough; Altium's own tutorial writes "type in the shortcut 'm' to get into the Move menu"
and "I typed in the shortcut 'mc' (Move Component)"
([part-placement-shortcuts](https://resources.altium.com/p/part-placement-shortcuts-altium-designer)).
The one Move-submenu sequence Altium documents as a shortcut outright is **`M`,`X` =
Move Selection by X, Y**, which opens the Get X/Y Offsets dialog — X Offset and Y Offset
fields, positive or negative, with **`Ctrl+Q`** switching the dialog between imperial and
metric ([get-x-y-offsets](https://www.altium.com/documentation/cstu/get-x-y-offsets)).

The full **Edit » Move** submenu: Move · Drag · Component · Move Selection · Move
Selection by X, Y · Rotate Selection · Flip Selection. Altium's placement page lists
**no keyboard shortcuts** for the submenu entries other than the `M`,`X` case above —
recorded here as documented-absent, not unverified
([placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques?version=16.1)).

Keyboard movement without the mouse at all:

| Key | Action (verbatim) |
| --- | --- |
| Arrow keys | "Move cursor by one snap grid unit" |
| `Shift`+arrows | "Move cursor by 10 snap grid units" |
| `Ctrl`+arrows | Move **selection** by one grid unit |
| `Shift+Ctrl`+arrows | Move selection by 10 grid units |
| `Alt` (held, during a move) | "constrain the direction of movement to the horizontal or vertical axis" |

([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors),
[placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques))

Two more move-time comforts from `Preferences » PCB Editor – General`:

- **Snap To Center** — "enable to have the cursor jump automatically to a defined
  reference point on the object when you select it and be 'held' by that point as you
  reposition it."
- **Smart Component Snap** — "enable so that when you click to select a component, the
  crosshair cursor appears on the nearest pad of this associated component with respect
  to the cursor."
- **Snap to Room Hot Spots** — cursor jumps to room hot spots.

Default on/off states for these three: **unverified** — the page documents the semantics,
not the shipped checkbox states.

> **Where we are.** `PcbCanvas.jsx:244-266` `onPointerDown` has exactly two modes:
> `dragRef.current = { mode: "measure", … }` when the measure tool is armed, otherwise
> `{ mode: "pan", … }`. `onPointerMove` at `:268` handles `"measure"` then `"pan"` then
> falls through to `hitTestPcb` for hover. There is no third branch. A left-drag on a
> component pans the board.

---

## 2. Spacebar: the reflex that defines the tool

While an object is floating on the cursor — being placed *or* being moved:

| Key | Action (verbatim) |
| --- | --- |
| `Spacebar` | "Rotates the object being placed/moved counterclockwise" |
| `Shift+Spacebar` | Rotates the object being placed/moved clockwise |

([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors),
[placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques))

**How much it rotates is a setting, and it is published.** `Preferences » PCB Editor –
General » Other » Rotation Step`: "This is the amount of rotation, in degrees, applied to
objects floating on the cursor when the **Spacebar** is pressed." **Default 90°**, with a
minimum resolution of **0.001°**
([pcb-editor-general-preferences](https://www.altium.com/documentation/altium-designer/pcb-editor-general-preferences?version=22)).

That is the whole model, and it is worth stating plainly because it is easy to get wrong:
rotation during a move is *not* a free-angle drag and *not* a hardcoded 90. It is one
preference-controlled step, applied per keypress, defaulting to 90° and settable to a
thousandth of a degree.

For a rotation that is not a multiple of the step, the command is **Edit » Move » Rotate
Selection**, which opens a *Rotation Angle (Degrees)* dialog
([placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques?version=16.1)).

> **Where we are.** Space is spent: `PcbCanvas.jsx:348-350` binds it to
> `setDeltaOrigin(cursor)`. Rotation exists in our code only as *rendering* —
> `PcbCanvas.jsx:420` and `:433` emit `rotate(${rotation})` / `rotate(${NUM(element.ccw_rotation)})`
> into the SVG transform from data. Nothing writes a rotation.

**The call this forces.** KiCad puts the relative-origin gesture on Space; Altium puts
rotate there. We took KiCad's, from ALTIUM-NOTES §9. Once a component can move, Space
must become rotate — it is the single highest-frequency key in the placement half of the
tool, and an EE will press it within the first ten seconds. The relative origin already
has a second, Altium-native binding in our code — `Insert`, at `PcbCanvas.jsx:351`, which
is Altium's own "reset the delta origin" key (ALTIUM-NOTES §3). Space can be handed over
at zero cost.

---

## 3. Tab: edit the thing while you are still holding it

**`Tab`** — "Access the associated mode of the Properties panel in which properties for
the object being placed/moved can be changed on-the-fly"
([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors)).
The placement page puts it as: Tab "pauses placement in order for you to make any
required edits for the object"
([placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques)).

It also appears in the placement-tools documentation as "Edit component properties before
placement"
([advanced-tools](https://www.altium.com/documentation/altium-designer/pcb/placing-components/advanced-tools)).

The related discoverability key: **`Shift+F1`** during any interactive command "access a
menu listing all valid shortcuts for that stage of the interactive command"; `Esc` closes
it "without impact on the currently running command." `F1` over anything — menu command,
dialog, panel, design object — opens its documentation
([shortcut-keys](https://www.altium.com/documentation/altium-designer/shortcut-keys)).

`Shift+F1` is the direct answer to "no learning curve." Altium's own solution to a
1000-key surface is a context-sensitive, in-command key list. We should copy the idea
outright, and it costs us nothing because our command set is small.

> **Where we are.** No `Tab` handler exists (`grep -rnE '"Tab"'` → no matches), and there
> is nothing for it to open: `PropertiesPanel.jsx` contains **0** `<input>`, `<select>`,
> `<textarea>` or `contentEditable` nodes (`grep -cE "<input|<select|<textarea|contentEditable"`
> → `0`). The panel is a read-only readout.

---

## 4. Flip and mirror: three different keys, three different meanings

| Key | Action (verbatim) |
| --- | --- |
| `L` | "Flip the object being placed/moved to the other side of the board" |
| `X` | "Mirror the object being placed/moved along the X-axis" |
| `Y` | "Mirror the object being placed/moved along the Y-axis" |

([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors);
the placement page words `X`/`Y` as "flip the object along the X-axis or Y-axis where
applicable" and `L` as "flip the object to the other side of the board (where
applicable)" —
[placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques))

The command form for a selection is **Edit » Move » Flip Selection**: "flip the selected
object(s) horizontally (around the Y-axis) to the corresponding layer on the opposite
side" ([placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques)).

`L` is modal by context: idle it opens Layers And Colors (ALTIUM-NOTES §4), mid-move it
flips sides. An EE's hands know both and never think about the difference. Our `L` does
neither — it opens Messages (`BoardWorkspace.jsx:677-680`).

---

## 5. Align and distribute: the one place Altium *does* publish shortcuts

The placement page lists the full command set with no shortcuts. The shortcut-keys page
lists the shortcuts. Both are Altium; the shortcut page is the authority for bindings.

| Shortcut | Action |
| --- | --- |
| `Shift+Ctrl+L` | Align by left edges |
| `Shift+Ctrl+R` | Align by right edges |
| `Shift+Ctrl+T` | Align by top edges |
| `Shift+Ctrl+B` | Align by bottom edges |
| `Shift+Ctrl+H` | "Make the horizontal spacing of selected objects equal" |
| `Shift+Ctrl+V` | "Make the vertical spacing of selected objects equal" |
| `Shift+Ctrl+D` | "Move selected components to the nearest point on the required component placement grid" |

([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors))

The full **Edit » Align** submenu, no shortcuts documented for these:
Align Left / Right / Top / Bottom · the same four again as **(maintain spacing)**
variants · Align Horizontal Centers · Align Vertical Centers · Distribute Horizontally ·
Distribute Vertically · Increase Horizontal Spacing · Increase Vertical Spacing ·
Decrease Horizontal Spacing · Decrease Vertical Spacing · Align To Grid · Move All
Components Origin To Grid. The **Align Objects dialog** — one dialog with the horizontal
and vertical choice together — is reached by right-clicking a selection → Align, or
`Edit » Align » Align`, or the Active Bar. **No dedicated shortcut for the dialog:
documented-absent**
([placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques?version=16.1)).

The "(maintain spacing)" variants are the subtle ones and the reason a naive
align-left is not enough: plain Align Left collapses a column's spacing to whatever the
alignment produces; the maintain-spacing form moves the group to the leftmost edge while
keeping the gaps the engineer already set.

> **Where we are.** Nothing. `grep -rniE "align|distribute"` over the board directory
> returns only CSS (`textAlign`, `alignItems`, `align="end"` at `BoardActions.jsx:110`,
> `align-top` at `FunctionTab.jsx:343`), the data field `anchor_alignment`
> (`PcbCanvas.jsx:489`), and prose in comments. No align command exists, and with
> `selection` a single nullable value (`BoardWorkspace.jsx:127`) there is nothing to align.

---

## 6. Arrays and Paste Special

**Paste Special** (`Edit » Paste Special`) controls, on paste: layer assignment, whether
net names are kept, whether designators are duplicated, and whether pasted components
join a component class. **Paste Array** is a *button inside* the Paste Special dialog —
not a separate menu item — and produces linear or circular arrays with a chosen item
count, spacing, and text-increment rule
([placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques),
[?version=16.1](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques?version=16.1)).

Plain paste is `Ctrl+V`. **A shortcut for Paste Special itself: documented-absent** — the
placement page gives the menu path only.

The text-increment rule is the part that matters in practice: an array of 8 mounting
holes or a 4×4 key matrix is placed once and stamped, with designators incrementing, and
that is a minute of work versus twenty.

---

## 7. Rooms

A room is "a defined area on one of the PCB surface layers" that is simultaneously a
graphical object and an automatically created **Room Definition** design rule. The rule
defines which objects it applies to, whether they must stay inside or outside, and which
layer it is on. Delete either half and the other goes with it
([pcb-room](https://www.altium.com/documentation/altium-designer/pcb-room?version=20.0)).

**Design » Rooms** submenu: Place Rectangular Room · Place Polygonal Room · Create
Rectangular Room from selected components · Create Orthogonal Room from selected
components · Create Non-Orthogonal Room from selected components. **Place Rectangular
Room carries the menu-accelerator sequence `D`,`M`,`R`**
([placement-rule-types](https://www.altium.com/documentation/altium-designer/pcb/design-rule-types/placement)).

Membership is automatic and class-based. Enclose components completely inside the room
and "those components are assigned to that room constraint, as a component class." If the
enclosed set is already a component class, that class is used; if not, a new class is
created with those components as members. An empty room's rule starts with a Full Query
of `All`, which you edit to target a class you defined first
([pcb-room](https://www.altium.com/documentation/altium-designer/pcb-room?version=20.0),
[placement-rule-types](https://www.altium.com/documentation/altium-designer/pcb/design-rule-types/placement)).

The payoff behaviour: **"if you click and drag to move that room, all of the components
in the associated component class will also move."** That is the reason rooms exist —
move the power stage, not eleven parts.

Room-aware placement commands, from
[advanced-tools](https://www.altium.com/documentation/altium-designer/pcb/placing-components/advanced-tools):

- **Arrange Within Room** — arranges the room's members inside its boundary; if the room
  is too small, packs them as close as possible so you can resize after.
- **Arrange Within Rectangle** — arranges a selection inside a rectangle you drag; the
  rectangle auto-resizes if the parts do not fit.
- **Arrange Outside Board** — moves a selection outside the keepout boundary, normally
  the board outline. This is the "dump the unplaced parts off the board and work through
  them" gesture.
- **Reposition Selected Components** — steps through the selection **in the order you
  selected it**, handing you one component at a time on the cursor. Pairs with Cross
  Select Mode: select a functional block on the schematic, then place its parts one after
  another on the PCB without ever hunting for them.

We already have the cross-probe half of that last one (ALTIUM-NOTES §1). Reposition
Selected Components is the placement half, and it is the strongest single argument for
building schematic-driven placement rather than a generic drag.

---

## 8. Locking

Two independent locks.

**Object lock.** "Design objects can be locked from being moved or being edited on the
PCB document by enabling their Locked attributes." Set it by clicking the **padlock icon**
in the Properties panel, or right-click the object and choose the **`<ObjectType>
Locked`** context command. Attempting to move or rotate a locked object raises a
confirmation dialog rather than silently refusing
([placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques),
[unable-to-move-specific-components](https://www.altium.com/documentation/knowledge-base/altium-designer/unable-to-move-specific-components-in-the-pcb-editor)).

That confirmation is escalated by a preference: with **Protect Locked Objects** enabled
on `Preferences » PCB Editor – General` — "Enable to ignore any selected locked objects if
they are part of a selection that is being moved" — the object "cannot be selected or
graphically edited" at all, and you must unlock it from the Properties padlock or turn the
preference off
([pcb-editor-general-preferences](https://www.altium.com/documentation/altium-designer/pcb-editor-general-preferences?version=22)).
Default state of that checkbox: **unverified.**

**Primitive lock.** Separately, a component's *Primitives* option in the Component mode of
the Properties panel locks the pads, silk and courtyard inside the footprint: with it on,
"all or the most properties of these primitives cannot be modified using graphical … and
non-graphical … editing methods"
([placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques)).
It is the guard that stops someone nudging a single pad out of a footprint by accident.

**No keyboard shortcut for either lock: documented-absent.**

Locking matters more for us than for Altium, and this is the one place where our situation
is genuinely different. Our boards are regenerated by a model. A connector position an
engineer set by hand against a real enclosure must survive the next generation. A lock in
our tool is not just an edit guard — it is an instruction to the generator, and it has to
be written into board source, not held in view state.

---

## 9. Grids: three kinds, and the manager that owns them

Altium's PCB editor distinguishes **visible** grids (navigation), **snap** grids
(placement), and the **electrical** grid (connection-making). The electrical grid
*overrides* the snap grid, because it exists precisely so you can connect to something
that is off-grid; toggle it with **View » Grids » Toggle Electrical Grid**. Visible grid
lines vs dots is **View » Grids » Toggle Visible Grid Kind**
([grids-guides](https://www.altium.com/documentation/altium-designer/pcb/grids-guides)).

| Key | Action |
| --- | --- |
| `G` | Quick menu of snap grid settings |
| `Ctrl+G` | "Access the dedicated grid editor dialog for the snap grid currently under the cursor" |
| `Shift+Ctrl+G` | "Set the X (horizontal) and Y (vertical) step values simultaneously to a chosen value" — the *Snap Grid (1..1000)* dialog |
| `Ctrl+Shift` (held) | "Temporarily disables the grid" |
| `Q` | "Toggle the measurement units for the current document between metric (mm) and imperial (mil)" |
| `Ctrl+Q` | Toggle units inside a dialog |

([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors),
[grids-guides](https://www.altium.com/documentation/altium-designer/pcb/grids-guides),
[get-x-y-offsets](https://www.altium.com/documentation/cstu/get-x-y-offsets))

Note `Ctrl+G` is scoped "for the snap grid currently under the cursor" — because grids in
Altium are **local objects**, not one global setting.

**The Grid Manager**, a region of the Properties panel with nothing selected (the same
Board-mode panel described in ALTIUM-NOTES §6), "is command central for defining and
organizing the grids for use with your board." Two custom grid types — **Cartesian**
(X/Y steps) and **Polar** (angular and radial steps, with automatic component rotation
around the centre) — sit on top of a **Global Board Snap Grid**, the default Cartesian
grid that applies wherever no local grid covers.

Each grid carries two checkboxes, **Comp** and **Non Comp**, and the four combinations are
the whole model:

| | Non Comp off | Non Comp on |
| --- | --- | --- |
| **Comp off** | Not visible; applied per snap settings | Visible and applied for non-component objects only |
| **Comp on** | Visible and applied for component actions only | Visible and applied for all object types |

([grids-guides](https://www.altium.com/documentation/altium-designer/pcb/grids-guides))

That table is the answer to "snap grid vs component grid": they are not two systems, they
are one grid list where each entry declares which object kinds it governs. A 0.5 mm
component-placement grid and a 0.05 mm routing grid coexist, and the right one appears
depending on what you grabbed. `Shift+Ctrl+D` then means something precise — snap the
selected components to *the component placement grid*, not to whatever grid is drawn.

Default grid step values: **unverified** — the page documents the dialogs, not the shipped
numbers.

> **Where we are.** `PcbCanvas.jsx:668-678` is a `<pattern>` containing
> `<path d={`M ${gridPx} 0 L 0 0 0 ${gridPx}`} …>` painted as a full-canvas `<rect>` when
> `showGrid && gridPx >= 6`. One grid, screen-space, decorative, no step value exposed, no
> object snaps to it. `Q` is correctly bound (`BoardWorkspace.jsx:664-667`); `G`, `Ctrl+G`
> and `Shift+Ctrl+G` are unbound.

---

## 10. The cursor-snap system: grids, guides, objects — in that order

Altium calls it one unified system with three levels of snap point, in ascending priority:

1. **Grids** — "the active **Grid** provides the base-level reference plane for snapping."
2. **Guides** — "snap **Guides** provide a method for the user to define precise,
   localized reference lines or points," and take priority over grids.
3. **Objects** — "the third, and often the most useful points of reference for snapping,
   are the objects that have already been placed."

Object snapping works off **hotspots** at "meaningful locations, such as the center of a
pad or via and the endpoints of track segments," and is "a dual-axis system where the
mouse cursor must be within the **Snap Distance** on both the X and Y axes"
([pcb-grids-system](https://www.altium.com/documentation/altium-designer/pcb-grids-system)).

The **Snap Options** region of the Board-mode Properties panel:

- **Grids** / **Guides** / **Axes** — three enables. **Axes** makes the cursor
  "axially-align (in either the X or Y direction) to the enabled **Objects for snapping**."
- **Snapping** — three buttons: Off · Current Layer · All Layers.
- **Objects for snapping** — per-object-type checkboxes choosing which hotspots are live.
- **Snap Distance** — "when the cursor is within this distance from an enabled **Objects
  for Snapping** … the cursor will snap to that point."
- **Axis Snap Range** — the distance at which the "dynamic guideline" appears during axial
  alignment.

**Snap Distance and Axis Snap Range defaults: unverified** — Altium documents the fields,
not the numbers.

| Key | Action (verbatim) |
| --- | --- |
| `Shift+E` | "Cycle to the next mode of object Hotspot Snapping" (Off → Current Layer → All Layers) |
| `Ctrl+E` | "Access a pop-up window in which to define which objects to be used for snapping purposes" / "display a palette of snap options" |
| `Ctrl` (held) | "inhibit object snapping" |

([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors),
[pcb-grids-system](https://www.altium.com/documentation/altium-designer/pcb-grids-system))

Two modifiers, two escapes: `Ctrl` kills object snap, `Ctrl+Shift` kills the grid. An EE
who wants a part *exactly* where the mouse is holds both. Any snapping we ship needs both
escapes on day one, or the first time our snap guesses wrong the user is stuck.

---

## 11. Placement guides — the two kinds, and the two held keys

**Snap Guides** are persistent, author-defined:

- **Linear Guide** — a horizontal, vertical or ±45° line.
- **Point Guide** — a single hotspot marker.

Placed from **Place » Work Guides** or the **Guide Manager** region of the Properties
panel, and individually disable-able without deleting
([grids-guides](https://www.altium.com/documentation/altium-designer/pcb/grids-guides)).

**Interactive alignment lines** are transient, appearing only while you drag, and are
summoned by held modifiers during Reposition Selected Components:

| Held key | Shows |
| --- | --- |
| `Ctrl` | Alignment lines from **component boundaries** |
| `Shift` | Alignment lines from **component pads** |
| `R` | Cycles placement modes |
| `Tab` | Edit component properties before placement |
| `Esc` | Exit swapping mode |

([advanced-tools](https://www.altium.com/documentation/altium-designer/pcb/placing-components/advanced-tools))

Note the tension worth flagging: during placement `Ctrl` inhibits object snapping
(§10) *and* shows boundary alignment lines (§11). Altium documents both on separate pages
and does not reconcile them. Which wins in which command: **unverified.** If we adopt
`Ctrl` for either meaning, pick one and say so in the tooltip.

---

## 12. Selection, because none of the above works without it

| Key | Action |
| --- | --- |
| `Ctrl+A` | Select all objects |
| `Ctrl+B` | "Select all objects that reside within the boundary of the defined board shape" |
| `Shift`+click | Add to selection (standard) |

Plus the Board-mode **Selection Filter** already covered in ALTIUM-NOTES §6 — object-type
toggles that control what is even selectable — and two General preferences that change
click semantics: **Click Clears Selection** ("Left mouse click clears current selection")
and **Shift Click To Select** ("Requires Shift key to select specific primitives")
([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors),
[pcb-editor-general-preferences](https://www.altium.com/documentation/altium-designer/pcb-editor-general-preferences)).

`Ctrl+B` deserves a note: "everything actually on the board" is a different set from
"everything in the document," because unplaced parts sit outside the outline. That is the
selection an EE uses before Arrange Outside Board.

> **Where we are.** `const [selection, setSelection] = useState(null)`,
> `BoardWorkspace.jsx:127`. One object or none. `Escape` and `Shift+C` both clear it
> (`BoardWorkspace.jsx:624-626`, `:642`). No additive selection, no rubber-band, no
> selection filter. This is the prerequisite for §5, §6 and §7 and it is a *data model*
> change, not a keybinding.

---

## 13. What we owe, in order

Ordered by how early an EE's hands would hit it, not by build cost.

1. **Take Space back for rotate**, move the delta origin to `Insert` alone (already bound,
   `PcbCanvas.jsx:351`). Add `Shift+Space` for clockwise. Rotation step 90° default,
   settable — Altium's own default and resolution are published, so we can match exactly.
2. **A third drag mode in `PcbCanvas.jsx:244`.** Today `pan` and `measure`. Move is the
   missing branch, and grabbing a member of a selection must move the selection.
3. **Multi-select.** `selection` becomes a set. Shift-click adds, rubber-band selects,
   `Ctrl+A` / `Ctrl+B`. Without it, five of the sections above cannot be built at all.
4. **Free `M` and `L`.** `M` belongs to Move, `L` to layers-idle / flip-in-move. Our
   highlight-method cycle and Messages toggle need different keys; neither is a reflex an
   Altium user brings with them, so both can move without cost.
5. **`Tab` → an editable Properties panel.** The panel has 0 inputs today. Tab is
   worthless until it opens something you can type into, and the write path is what makes
   it possible.
6. **A real snap grid** with a step value, `Q` already correct, `G` / `Ctrl+G` /
   `Shift+Ctrl+G` to set it, and both escapes (`Ctrl` for objects, `Ctrl+Shift` for grid).
   The pattern at `PcbCanvas.jsx:668` becomes a real coordinate quantiser.
7. **Align and distribute on Altium's exact seven shortcuts.** Cheap once selection is a
   set, and it is the difference between a toy and a layout tool.
8. **Lock, written to board source.** Ours has to survive regeneration, which makes it a
   source-format question, not a UI one — the one item here that must be designed with the
   write-path workstream rather than after it.
9. **`Shift+F1`.** A context list of the keys valid *right now*. Altium's own answer to
   "no learning curve", and the cheapest item on this list.

---

## 14. Open items — Altium does not publish these

- Default checkbox states for Snap To Center, Smart Component Snap, Snap to Room Hot
  Spots, Protect Locked Objects, Click Clears Selection, Shift Click To Select.
- Default **Snap Distance** and **Axis Snap Range**.
- Default grid step values for the Global Board Snap Grid.
- The full entry list of the **Graphical Editing Hot Key List (PCB)** dialog. The docs
  page for it describes how it is reached but does not reproduce the list; it would have
  to be read off a running install.
- Whether `Ctrl` inhibits object snapping or shows boundary alignment lines when both
  behaviours are in scope during Reposition Selected Components.
- Keyboard shortcuts for: the Edit » Move submenu entries other than `M`,`X`; the Align
  Objects dialog; Paste Special; either lock. These are **documented-absent** rather than
  unverified — Altium lists the commands and gives menu paths only.
