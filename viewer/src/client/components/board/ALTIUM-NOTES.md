# Altium Designer, read for parts we can actually build

Study notes for the Autonomous Circuit board workspace. The brief was "study Altium,
they are the best software." This is what an electrical engineer's hands already know,
what of it is documented (with the source), what is guesswork, and what we chose to do
about each. KiCad 9 and Flux are here as cross-checks where they do it better.

Everything below is sourced. Where a number is not published anywhere, it says
**unverified** instead of a made-up figure.

---

## 1. Cross-probing and cross-select — two systems, not one

Altium ships **two** separate mechanisms and EEs use both without thinking about it.

**Cross Probe** (`Tools » Cross Probe`) is a modal cursor tool with two sub-modes:

| Input | Behaviour |
| --- | --- |
| click / `Enter` (continuous) | Highlight the matching object on the *other* document. **The source document stays active.** Keep probing. `Esc` exits. |
| `Ctrl`+click / `Ctrl`+`Enter` (jump-to) | Highlight **and make the target document active**. Single-shot. |

One modifier rule does all the work: **plain click = stay here, `Ctrl` = go there.**
Cross Probe is also reachable from the Messages panel, the Projects panel, the ECO
dialog, the Differences panel and the Constraint Manager — anywhere a list names an
object, double-click or right-click takes you to it.
([cross-probing-selecting-schematics-pcb](https://www.altium.com/documentation/altium-designer/cross-probing-selecting-schematics-pcb),
[cross-probe-cross-select-tools](https://www.altium.com/documentation/cstu/cross-probe-cross-select-tools))

**Cross Select Mode** is a persistent, bidirectional *selection mirror*: select on the
PCB and the same object selects on the schematic, and back. Toggled by
`Tools » Cross Select Mode`, **`Shift+Ctrl+X`**, or `Preferences » System – Navigation »
Cross Selection`. When on, a blue box surrounds the menu icon — the mode is visible, not
hidden state.

`Tools » Select PCB Components` from the schematic side does the full move: activates
the PCB, selects the matching footprints, **and zooms to them**. That is the "jump and
fit" gesture worth stealing wholesale.

The *visual* result is configurable, not hardcoded. `Preferences » System – Navigation`
offers highlight methods **Selecting**, **Connective Graph** (with `Include Power
Parts`), **Zooming** (a Far↔Close slider) and **Dimming** (None↔Invisible slider), plus
`Reposition selected component in PCB` and `Focus document containing selection if
visible`. **Default slider positions: unverified** — Altium documents the semantics, not
the defaults. ([system-navigation-preferences](https://www.altium.com/documentation/altium-designer/system-navigation-preferences))

**What feels good and why.** Highlight-without-camera-move is the right default. A view
that jumps every time you click destroys your sense of where things are; you lose the
mental map of the board. Making the jump an explicit modifier means the camera only ever
moves because you asked it to.

> **What we built.** Click = select + highlight in the other pane, camera still. `Ctrl`
> (or `⌘`) + click = select **and** zoom the other pane to fit the selection. Selection
> mirrors across Schematic, PCB, BOM and Properties simultaneously, because our panes
> are side by side rather than being separate documents — Cross Select Mode is always
> on, which is what everyone turns on anyway.

---

## 2. Net masking — the single most Altium thing there is

Three highlight methods, with Altium's own words for what each one renders:

- **Normal** — "filtered objects are visible… the appearance of unfiltered objects
  remains unchanged."
- **Mask** — "filtered objects are highlighted… with all other objects made
  **monochrome**." Masked objects also become **non-selectable**.
- **Dim** — "filtered objects are highlighted… with all other objects **retaining their
  colors but shaded**."

([pcb-panel-selection-and-highlight-controls](https://www.altium.com/documentation/knowledge-base/altium-designer/pcb-panel-selection-and-highlight-controls))

Triggers and — just as important — how you get out:

| Input | Effect |
| --- | --- |
| `Ctrl`+click a net on the board | Highlight that net across all signal layers, dim/mask everything else |
| `Shift`+`Ctrl`+click | Add another net to the highlight |
| `Ctrl`+click empty space | Restore |
| **`Shift+C`** | Clear the filter on the active document |
| **`]` / `[`** | Increase / decrease mask level *live*, while the filter is applied |

Note what is **not** on that list: `Esc` does **not** clear an Altium filter. `Shift+C`
does. ([your-view-of-the-board](https://www.altium.com/documentation/altium-designer/pcb/your-view-of-the-board),
[shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors))

The depth sliders live in `View Configuration » View Options » Mask and Dim Settings` —
three of them, **Dimmed Objects**, **Highlighted Objects**, **Masked Objects**; leftmost
is maximum dimming. **Default percentages: unverified** — not published; you would have
to read a shipped `.PCBSysColors`/view-config to get them.

Alongside masking there is **Live Highlighting**: hover-highlight of the net under the
cursor, with an option "Live Highlighting only when Shift Key Down", plus `Highlight In
Full` (fill vs outline), `Use Transparent Mode When Masking` and `Show All Primitives In
Highlighted Nets`. **Default checkbox states: unverified.**
([pcb-editor-board-insight-display-preferences](https://www.altium.com/documentation/altium-designer/pcb-editor-board-insight-display-preferences))

A third, orthogonal channel: **net colour override** — assign a colour per net, and
**`F5`** globally toggles the override in *both* schematic and PCB.
([using-net-highlight-color-schematics-pcb](https://www.altium.com/documentation/altium-designer/using-net-highlight-color-schematics-pcb?version=19.1))

**Why this is the defining move.** A dense two-layer board is visual noise until you ask
it a question. Masking turns "where does GND actually go" from a tracing exercise into a
glance. It is the fastest way a tool can answer the question an EE actually has.

> **What we built.** Selecting a net dims everything unconnected in **both** panes at
> once — that is more than Altium does, because our schematic and PCB are on screen
> together. Three methods (`Normal` / `Dim` / `Mask`) on a cycle, `Shift+C` to clear,
> `Esc` also clears because on the web a user who is stuck presses `Esc`. `[` and `]`
> step the mask level live, at Altium's own binding. Defaults: dim = 22% opacity for
> unselected, mask = 10% and desaturated. Those are **our** numbers chosen by eye, not
> Altium's — the real ones are unpublished, and we would rather say so than pretend.

---

## 3. Board Insight — the heads-up display

The HUD shows **cursor X/Y**, **dX/dY from the last click**, **current layer** and
**current snap grid**. Pausing the cursor enters **Hover mode**, which adds the net,
component, primitive details, applicable shortcuts and any rule violations under the
cursor. It can be parked at a fixed screen position or track the cursor.
([board-insight-system](https://www.altium.com/documentation/altium-designer/pcb/board-insight-system))

| Key | Action |
| --- | --- |
| `Shift+H` | Toggle the heads-up display |
| `Shift+G` | Toggle HUD tracking (fixed ↔ follows cursor) |
| `Shift+D` | Toggle delta-origin coordinates |
| `Insert` | Reset the delta origin to the cursor |
| `Shift+X` | Browse objects under the cursor |
| `Shift+V` | Browse violations under the cursor |
| `F2` | Open the Board Insight menu at the cursor |

`Preferences » PCB Editor – Board Insight Modes` exposes **Heads Up Opacity** and
**Hover Opacity** in **1% increments**, `Use Background Color`, and a Hover Mode Delay
slider in **100 ms** increments. **All default values: unverified.**
([board-insight-modes-preferences](https://www.altium.com/documentation/altium-designer/pcb-editor-board-insight-modes-preferences?version=19.1))

The **Insight Lens** (`PCB Editor – Board Insight Lens`) is a magnifier window at the
cursor: `Shift+M` shows it, `Alt`+wheel changes its zoom, `Shift+Ctrl+M` auto-zoom,
X/Y size in pixels. **Defaults: unverified.**

**Single Layer Mode — `Shift+S`** cycles four states: full → **Hide Other Layers** →
**Grayscale Other Layers** → **Monochrome Other Layers** → full. In single-layer mode,
`Ctrl+Shift`+wheel steps through layers.

> **What we built.** A translucent HUD pinned to a corner of the PCB canvas: live X/Y in
> mm, dX/dY from the delta origin, the net under the cursor, the object under the
> cursor, and the active layer. `Shift+H` toggles it. A measure tool on `Ctrl+M` (see §7)
> drag-measures with a live readout. We took KiCad's decision to keep the readout in a
> fixed corner rather than have it chase the cursor — on a small web pane a floating HUD
> covers the thing you are looking at.
>
> *Amended when the viewport tool rail landed (VIBE-NOTES §3):* the corner is now
> **top**-left, not bottom-left. The bottom edge belongs to the rail and the board-side
> widget, and in Split view — where the PCB pane is half-width — a bottom-left HUD and a
> bottom-centre rail overlap outright. The permanent shortcut legend under the readout is
> gone with it: every binding it listed is now a tooltip on the tool that owns it, which
> is where someone actually looks for it.

---

## 4. Layers, colours, and actual hex values

`L` opens **Layers And Colors**; `Ctrl+D` opens **View Options**. Layer tabs run along
the bottom of the design space, grouped by a **LayerSets** dropdown with a `+` tab.
Single letters toggle layers when the panel is active. `+` / `−` (numpad) step to the
next/previous enabled layer; `*` steps signal layers only.

Altium **does not publish a hex table.** Colour profiles are `*.PCBSysColors` INI files
under `AppData\Roaming\Altium\…\ViewConfigurations`
([pcb-editor-layer-colors-preferences](https://www.altium.com/documentation/altium-designer/pcb-editor-layer-colors-preferences?version=19.1)).
Two independent real profile files were decoded to get the table below —
[bananello/X2-31869 "Altium Default.PCBSysColors"](https://github.com/bananello/X2-31869/blob/master/Altium%20Default.PCBSysColors)
and [ETH Zurich PCB-Editor_Layer-Colors.PCBSysColors](https://gitlab.phys.ethz.ch/clepeter/Altium_Configuration_and_Documentation/blob/master/01_Preferences_Configuration/PCB-Editor_Layer-Colors.PCBSysColors).
The encoding is Delphi `TColor`, i.e. **`00BBGGRR`** — you must swap the byte pairs.
The two files agree on everything except mechanical layers. Treat this as the classic
default set, **community-sourced rather than vendor-published**.

| Layer | Raw | Hex |
| --- | --- | --- |
| Top Layer | `000000FF` | **#FF0000** |
| Bottom Layer | `00FF0000` | **#0000FF** |
| Mid-Layer 1 | `00008EBC` | **#BC8E00** |
| Mid-Layer 2 | `00FADB70` | **#70DBFA** |
| Mid-Layer 3 | `0066CC00` | **#00CC66** |
| Mid-Layer 4 | `00FF6699` | **#9966FF** |
| Top Overlay (silk) | `0000FFFF` | **#FFFF00** |
| Bottom Overlay | `00008080` | **#808000** |
| Top Paste | `00808080` | **#808080** |
| Bottom Paste | `00000080` | **#800000** |
| Top Solder (mask) | `00800080` | **#800080** |
| Bottom Solder | `00FF00FF` | **#FF00FF** |
| Keep-Out | `00FF00FF` | **#FF00FF** |
| Mechanical 1 | `00FF00FF` | **#FF00FF** |
| Multi-Layer | `00C0C0C0` | **#C0C0C0** |
| Drill Guide | `00000080` | **#800000** |
| Drill Drawing | `002A00FF` | **#FF002A** |
| Ratsnest / Connect | `0075A19E` | **#9EA175** |
| **Background** | `00000000` | **#000000** |
| DRC Error Layer | `0000FF00` | **#00FF00** |
| Highlight / Selection | `00FFFFFF` | **#FFFFFF** |
| Grid (fine / coarse) | `005C4D4D` / `00908D91` | **#4D4D5C** / **#918D90** |
| Pad hole / via hole | `00909100` / `00006281` | **#009190** / **#816200** |

Workspace colours from the same files: enclose-selection rectangle **#0000FF**,
touch-selection rectangle **#008000**, waived DRC markers **#B0E020**.

**KiCad 9 cross-check** — `s_defaultTheme` in
[builtin_color_themes.h](https://gitlab.com/kicad/code/kicad/-/raw/master/common/settings/builtin_color_themes.h):
F.Cu **#C83434**, B.Cu **#4D7FC4**, In1 **#7FC87F**, In2 **#CE7D2C**,
F.SilkS **#F2EDA1**, B.SilkS **#E8B2A7**, F.Mask **#D864FF @0.4**,
F.Paste **#B4A09A @0.9**, F.CrtYd **#FF26E2**, B.CrtYd **#26E9FF**,
Edge.Cuts **#D0D2CD**, **background #001023**, grid #848484,
ratsnest **#00F8FF @0.35**, DRC error **#D75B6B @0.8**, DRC warning **#FFD042 @0.8**,
select overlay **#04FF43**, cursor #FFFFFF.

**The design call.** Altium's classic set is pure primaries on pure black — #FF0000 on
#000000 vibrates, and at web DPI on an LCD it is genuinely tiring. KiCad's defaults keep
**exactly the same semantic assignment** (top is red, bottom is blue, silk is pale
yellow) at calibrated values, and already carry per-layer alpha. So we default to the
Altium assignment at KiCad-grade values, and ship **Altium Classic** as a one-click
palette for anyone who wants the real thing. Both are in `boardPalette.js` with these
hex values verbatim.

---

## 5. Messages panel / DRC

`Tools » Design Rule Check` runs the checker; results land in the **Messages** panel with
columns **Class, Document, Source, Message, Time, Date, No.**
**Double-clicking a message cross-probes to the object(s) causing the violation** — jump,
zoom, centre. Right-click a violating object → **Violations** opens Violation Details:
the rule, its constraint, the offending primitives with location and layer, a
**Highlight** button (momentary emphasis against a monochrome background) and a **Jump**
button. Violations can be waived with author, timestamp and reason, and waived ones
render in their own colour. `Shift+V` browses violations under the cursor.
([interrogating-resolving-design-violations](https://www.altium.com/documentation/altium-designer/pcb/drc/interrogating-resolving-design-violations))

Severity is a per-rule report mode: **Fatal Error / Error / Warning / No Report**.
**Row colour coding per severity: unverified** — the panel documentation shows a
screenshot only ([messages-panel](https://www.altium.com/documentation/altium-designer/messages-panel)).
KiCad's documented DRC colours (**#D75B6B** error, **#FFD042** warning) are the
substitute we used.

> **What we built.** A Messages panel fed by `validation.warnings` from the `.board.json`
> sidecar (the contract's severity authority) and located by joining against the
> `*_error` / `*_warning` elements inside the circuit JSON, which carry
> `pcb_component_ids`, `pcb_smtpad_ids` and sometimes an explicit `center`. Click a row →
> select the offender; double-click → zoom to it and flash it. Rows we genuinely cannot
> place (KiCad ERC prose like "Horizontal Wire, length 0.0300 mm") show a struck-through
> locate icon rather than pretending. **And the thing Altium cannot do: every row has a
> "Fix this" button that hands the finding to the chat as a repair request.** That is our
> advantage over the reference and it survived the rewrite intact.

---

## 6. Properties panel

Right-docked, `F11` or double-click to open. It "dynamically determines its content… based
on the document or object that is currently selected." With **nothing** selected (Board
mode) it shows the **Selection Filter** (object-type toggles that control what is even
selectable), **Snap Options**, **Board Information** (dimensions, component counts,
density), Grid Manager and Guide Manager. With an object selected it switches to that
object's mode, in **collapsible regions** — General, Location, Parameters, Graphical,
Part Choices — and edits multiple selected objects at once. Exact multi-select header
wording: **unverified**.
([properties-panel](https://www.altium.com/documentation/altium-designer/properties-panel))

> **What we built.** Same three-state shape. Nothing selected → board info (size, layer
> count, thickness, component/net/pad counts, DRC constraint summary). Component selected
> → refdes, value, footprint, layer, rotation, LCSC with stock and price from
> `parts.json`, pad count, and the nets it sits on (each one clickable). Net selected →
> pin count, total routed length in mm, via count, and every connected pin as a
> click-through list.

---

## 7. 2D/3D and the numeric view keys

| Key | Action |
| --- | --- |
| `1` / `2` / `3` | Board Planning / **2D Layout** / **3D Layout** |
| `Ctrl+Alt+2` / `Ctrl+Alt+3` | Switch 2D/3D keeping location and orientation |
| numpad `0` / `9` / `8` | View from above zero rotation / rotated 90° / orthogonal |
| `Ctrl+F` | Flip the board over |
| `Shift+Z` | Toggle 3D model visibility |

Other bindings worth honouring, all from
[shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors):
**`Ctrl+PgDn` zoom to fit all objects**, `PgUp`/`PgDn` zoom about the cursor,
**`Q` toggle mm ↔ mil**, **`Ctrl+M` measure distance between two points**, `Ctrl+G` grid
editor, `Shift+C` clear filter, `Shift+S` single-layer cycle, `L` layers panel,
`Ctrl+click` highlight the whole routed net, **`Ctrl+H` select all electrical objects on
the same copper**, `F5` net colour override. Zoom-to-selection has **no documented
default binding** — unverified.

> **What we built.** `2` / `3` switch 2D and 3D. `Ctrl+PgDn` and `F` both zoom to fit
> (`F` because a browser eats `PgDn` in too many contexts). `Q` toggles mm/mil across the
> whole workspace. `Ctrl+M` arms the measure tool. `Shift+S` cycles single-layer mode.
> 3D is behind a seam, not shipped — see the caveat in the report.

---

## 8. ActiveBOM

Columns: **Line #, Description, Designator, Quantity, Manufacturer, Manufacturer Part
Number, Supplier, Supplier Part Number, Unit Price, Extended Price, Stock, Lifecycle**,
toggled from the Properties panel's Columns tab. A **solution** binds design component →
manufacturer part → suppliers, with **each supplier a separate colour-coded tile** on the
row. Solutions **rank automatically "from highest to lowest based on the part's
availability, price and manufacturer lifecycle state"**, overridable by star rating. A
**BOM Checks** region summarises violations by type and count, and an always-visible
status column carries clear/warning/error/fatal icons.
([activebom](https://www.altium.com/documentation/altium-designer/activebom))

> **What we built.** The BOM table now carries the supplier data inline — LCSC number
> linked to JLCPCB, Basic/Extended as the lifecycle analogue, stock, unit price and
> extended price — and rows cross-select with the canvases. Automatic solution ranking is
> not ours to do: `parts.json` is owned wholly by the parts-book skill, which already
> picked the part. We surface its choice and the date it was checked.

---

## 9. What KiCad 9 does better, and we took

From [docs.kicad.org/9.0/en/pcbnew](https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html):

- **Appearance panel, three tabs** — Layers / Objects / Nets. The Objects tab has
  **per-object-type opacity sliders** (tracks, vias, pads, zones) that multiply with the
  layer alpha. A cleaner model than one global dim slider, and it maps straight onto an
  SVG/canvas alpha stack. *Taken: our layer chips carry both a visibility toggle and the
  layer's own alpha.*
- Non-active layers render **normal / dimmed / hidden**, cycled by one key (`Ctrl+H`).
  Three states, no dialog. *Taken, on Altium's `Shift+S` binding.*
- Net highlight on **`` ` ``**, clear on **`~`**, and an option for `Esc` to clear.
  *Taken — `Esc` clears, because web.*
- **`U`** expands selection to connected copper progressively. *Not taken yet.*
- Selection modifiers are standard-web: `Ctrl`/`⌘` toggle, `Shift` add. *Taken.*
- A **fixed status bar** with X, Y, zoom, dx, dy, dist, grid, units — instead of a
  floating HUD. *Taken, as described in §3.*
- **`Space`** zeroes the relative origin for quick measuring. *Taken.*

**Flux.ai** — public docs are thin. The marketing pages claim browser-native
schematic↔layout sync, continuous DRC where "click any violation to jump to it", and a
live BOM with real-time inventory and pricing
([flux.ai/p/pcb-design-software/online](https://www.flux.ai/p/pcb-design-software/online)).
No documented keybindings or cross-probe mechanics — **unverified**. The two ideas worth
copying are structural, not visual: **BOM rows priced at view time**, and
**URL-addressable selection** so a net, part or violation is a link you can paste to a
colleague. The second one is a good fit for us and is on the next-three list.

---

## 10. Where Altium stops being the reference

Everything above makes the workspace credible to an engineer. None of it
answers the question a non-engineer actually arrives with — **can I get this
made, and if not, what is wrong?** Altium has no opinion on that question
because Altium's user already knows how to answer it. Ours does not, and the
product's promise is that every generated board reaches `fab.ready: true`, so
the state of that boolean is the headline of a board rather than a field on a
status page.

The translation layer is `lib/plainLanguage.js`, and it is pure and tested:

- **An issue dictionary.** ~35 KiCad DRC/ERC codes (`hole_clearance`,
  `endpoint_off_grid`, `lib_symbol_issues`, …) mapped to a plain title and one
  sentence of meaning. Unknown codes get an honest fallback — the code, spaced
  out, and *no* invented meaning.
- **A second axis, `impact`.** Severity comes from the sidecar and is never
  rewritten here; `impact` is a separate question — does this stop an order
  (`blocks`), is it a real risk (`quality`), is it only how the board looks
  (`cosmetic`), or is it about the checker's own setup and not the board at all
  (`tooling`)? On harness-puck that splits 694 findings into 2 / 147 / 545, and
  that sentence is the single most useful thing on the screen.
- **`groupFindings`.** One row per *kind* of problem, not per instance, sorted
  blocking-first. Grouped is the Messages panel's default mode; "Every" restores
  the flat Altium-shaped list in one click.
- **`boardVerdict`.** The sentence at the top of the workspace.
  `sidecar.fab.ready` is the only gate — no count, no heuristic and no severity
  arithmetic can make a board read as orderable when the pipeline says it is
  not, and the ready copy names the outstanding non-blocking findings rather
  than claiming a clean sheet the user can see is not clean.
- **`partRole` / `plainParts`.** "The brain", "power in", "the lights" — read
  off the source file's own `ftype` and the manufacturer part number, in that
  order of trust, falling back to the reference-designator letter and then to
  an honest "other". Feeds the Overview part list, the Properties header, the
  BOM's "What it is" column and the HUD line under the cursor.

Surfaces built on it: **`BoardVerdict`** (a strip under the tab row, on every
tab), **`OverviewTab`** (first and default), the grouped **Messages** mode, and
**`StartHere`** — the empty stage, which is a three-step explanation when idle
and an eight-row build checklist when running. That checklist has one row the
pipeline does not report: the model choosing parts and writing the board
program, which is where most of the wall clock goes and which left the list
entirely grey until it was added.

Two rules the layer must keep, both learned the hard way:

1. **Never state a number we were not given.** No estimated fab price, no
   guessed part cost. `partsCostUsd` returns `null` rather than a total when
   parts.json has no prices, and says how many lines it could not price when it
   has some.
2. **Plain does not mean vague.** Every plain title sits next to the real code,
   every group expands to the raw DRC prose, and the Properties panel keeps
   every field it had. The plain line is added above the engineering, never
   instead of it.

---

## 11. Open items — things nobody publishes

- Default numeric values for the Dimmed / Masked / Highlighted sliders, HUD opacity,
  hover delay, Insight Lens size and zoom. All unpublished. Parity would mean reading a
  shipped `ViewConfigurations` folder.
- Messages panel severity colour coding. Unpublished; we used KiCad's DRC colours.
- `Shift+H`: the Board Insight page says it toggles the HUD, another page renders it as a
  snap toggle. We followed the Board Insight page.

---

## 12. Move mode — where we stop following Altium

Altium's canvas is the design. Ours is a *view* of `boards/<stem>.tsx`, and that is a
deliberate difference, not a limitation: the board is code, the agent writes that code,
and a canvas that owned placement in parallel would immediately disagree with the file
the next build reads.

So a drag ends as an edit to the source. `boardSource.js` parses the board file, finds
the JSX elements that carry `pcbX`/`pcbY`, binds each to what the compiled board drew at
that anchor, and turns a drop into a byte-range replacement of exactly one numeric
literal. `board_source_write` (the only command in this app that writes a file the user
owns) refuses the write unless the expected text is still at those offsets, and refuses
outright while a build is running.

What that buys, and what it costs:

- **Only the board file's own placements move.** A part inside a golden block is placed
  by the block; moving it would mean editing a file other boards share. The draggable
  unit is a direct child of `<board>` — a block instance, a `<group>`, or a part the
  board wrote itself. On the three example boards that is 17 / 12 / 28 placements, all
  of them bound.
- **The copper does not follow.** Traces were routed against the old placement and they
  still are. Drawing them snapped to the new position would be the canvas promising a
  board nobody has built. A moved part is drawn where the *file* now puts it, its copper
  where the *last build* put it, and the two visibly disagreeing is what "rebuild" means.
- **Geometry is captured per build, not per parse.** The moment a drag writes a new
  `pcbX`, source coordinates and compiled anchors stop matching — correctly. Re-matching
  by coordinate after every edit unbinds the part that was just dragged, which allows
  exactly one move per part and no undo. `rebindPlacements` carries geometry forward by
  placement id instead, and the id is the tag plus its ordinal in the file, so it
  survives a coordinate change.
- **The lock is a comment in the source.** `{/* locked: placed by hand … */}` above the
  element. A lock the next agent cannot read is not a lock, and the next agent reads the
  board file, not this app's memory.
- **Rebuild is a button, never a side effect.** A build is minutes.

Move mode and measure mode both own the drag, so turning on either turns off the other.
