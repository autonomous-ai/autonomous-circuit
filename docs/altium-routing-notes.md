# Altium interactive routing, read for a tool where a router already did the work

Companion to `viewer/src/client/components/board/ALTIUM-NOTES.md`. That file covers
**looking** at a board. This one covers **changing** one — specifically the interactive
routing surface, which is the largest single block of Altium muscle memory an EE carries
and the one our app has none of.

Same house rules as the companion file: every Altium claim carries a source URL, anything
unpublished says **unverified**, and every claim about our app carries file:line.

The last section is the one that matters for planning. Our autorouter places 99% of the
copper; a human touches the last 1%. Most of Altium's routing surface is built for the
opposite ratio, and copying it wholesale would be building the wrong tool very carefully.

---

## 0. The measurement first — what we have today

Every claim in this section is a grep or a file:line, run 2026-08-16 on
`/Users/d/code/autonomous-circuit`.

**Nothing in the interactive-routing vocabulary exists anywhere in the client.** A
case-insensitive `grep -ril` over `viewer/src/client` for each term returns **0 files**
for: `Shift+R`, `conflict resolution`, `walkaround`, `hugNpush`, `push obstacle`,
`corner style`, `gloss`, `retrace`, `length tuning`, `differential pair`, `diff pair`,
`via placement`, `unwind`, `look-ahead`, `ripup`, `rip-up`. `accordion` matches once and
it is `viewer/src/client/components/ui/accordion.jsx` — the Radix disclosure widget.
`autoroute` matches zero times in the client (it appears only in `docs/` and `evals/`).

**The canvas has two drag modes and neither of them edits anything.**
`PcbCanvas.jsx:60-64` states the contract in its own header comment:

```
 *   · drag           pan            · wheel        zoom about the cursor
 *   · click          select + cross-probe, camera stays put
 *   · ⌘/Ctrl+click   select + jump (zoom the other pane to it)
 *   · ⇧+click        select the whole net under the cursor
 *   · measure mode   drag to measure, live readout
```

`PcbCanvas.jsx:257` sets `dragRef.current = { mode: "pan", … }` and `:253` sets
`{ mode: "measure", … }`. Those are the only two values `mode` ever takes.

**A track segment cannot be selected.** `hitTestPcb` *can* return a trace —
`boardIndex.js:797` gives `pcb_trace` a hit rank of 20, and `:850-860` walks the route
polyline to report the layer of the segment actually under the cursor. But the click
handler throws that resolution away. `PcbCanvas.jsx:327-329`:

```js
if (wantNet && hit.netKey) onSelect?.({ kind: "net", key: hit.netKey }, { jump, source: "pcb" });
else if (hit.componentKey) onSelect?.({ kind: "component", key: hit.componentKey }, { jump, source: "pcb" });
else onSelect?.(null, { jump: false, source: "pcb" });
```

Two selection kinds exist, `net` and `component`. Clicking a track selects its **whole
net**. There is no segment, no vertex, no via as a selectable object — so there is no atom
for a routing edit to act on. That is the first thing that has to change and it is
upstream of every keystroke below.

**Backspace, Tab and Spacebar are unbound.** `grep -rn "Backspace"` and `grep -rn '"Tab"'`
over `viewer/src/client/components/board/` (excluding `__tests__`) each return **0**.
Those are the three most-pressed keys in Altium's router.

**The keymap we do have** (`BoardWorkspace.jsx:621-689`) binds: `Escape` clear,
`Ctrl/⌘+M` measure, `Ctrl+PageDown` fit, `Shift+C` clear filter, `Shift+S` single-layer
cycle, `Shift+H` HUD, `1`/`2`/`3`/`0` tab switch, `F` fit, `Q` units, `M` highlight
method, `[`/`]` mask level, `L` messages panel, `R` regions.

Three of those collide with Altium routing bindings, and the collisions are worth naming
now rather than discovering them later:

| Key | Ours | Altium |
| --- | --- | --- |
| `L` | `BoardWorkspace.jsx:676` toggles the Messages panel | `Ctrl+L` pops the routing-layer list; bare `L` opens Layers And Colors ([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors)) |
| `R` | `BoardWorkspace.jsx:680` toggles regions | free in Altium; `Shift+R` is the mode cycle, so no true clash — but `R` reads as "route" to a hand that has typed it for ten years |
| `2` / `3` | `BoardWorkspace.jsx:648-654` switch to the PCB / 3D tab | during a route, `2` adds a via without changing layer and `3` cycles track width |

`1`–`9` are the busiest keys in Altium's router and all of them are ours as global tab and
mode switches. Any routing mode we build has to be **modal** — keys rebound while a route
is live — or the two schemes cannot coexist.

**The geometry to edit already exists and is already the right shape.** A `pcb_trace` in
`<stem>.circuit.json` is a vertex list, not a baked path. From
`examples/harness-puck/boards/main.circuit.json`:

```json
{"type": "pcb_trace", "pcb_trace_id": "source_net_24_0", "connection_name": "source_net_24",
 "connectsTo": ["pcb_port_217", "pcb_port_69"],
 "route": [{"route_type": "wire", "x": 15.5, "y": 3.8499, "width": 0.15, "layer": "top",
            "start_pcb_port_id": "pcb_port_217"},
           {"route_type": "wire", "x": 14.403, "y": 2.753, "width": 0.15, "layer": "top"},
           {"route_type": "via", "x": 14.4008, "y": 1.8742, "from_layer": "bottom",
            "to_layer": "top", "via_diameter": 0.6, …}]}
```

Per-vertex `x`, `y`, `width`, `layer`, and `route_type: "wire" | "via"`. Layer changes are
via records *inside* the route array. That is structurally what Altium edits. Counted
across the three example boards:

| Board | Traces | Segments | Vias | Distinct widths in use |
| --- | --- | --- | --- | --- |
| harness-puck | 158 | 1,562 | 122 | 0.15 / 0.2 / 0.25 / 0.3 / 0.35 / 0.4 / 0.45 / 0.5 mm |
| hydrate-coaster | 113 | 1,176 | 108 | same eight |
| terminal-keyboard | 252 | 2,375 | 213 | 0.2 dominant (2,288 of 2,414 vertices) |

1,562 segments on a 70×70 mm two-layer board is the scale any editor has to survive. It is
also the scale at which "select the whole net" stops being a useful selection.

**And there is already a named, measured, unfixed routing defect on two of the three
boards** — see §13. `build.diffPair` in each sidecar reports the USB pair refused on
harness-puck (10.394 mm skew, −0.075 mm clearance) and terminal-keyboard (20.254 mm skew,
−0.100 mm clearance). This is not a hypothetical "last 1%"; it is the actual last 1%, and
it is differential-pair work.

**The clearance number a push/walkaround engine would need is in the sidecar but not on
the client.** `main.board.json` carries `build.pourClearance.requiredMm: 0.15`, and
`PropertiesPanel.jsx:228-229` renders `min_board_edge_clearance` and
`min_pad_edge_to_pad_edge_clearance` when the board object has them. There is no
netclass table, no per-net width rule, and no clearance rule object anywhere in
`viewer/src/client` — `grep -rn -i "clearance" viewer/src/client` returns those two
Properties rows, the plain-language dictionary entries, and comments. **A DRC-aware
interactive router has no rules to obey yet.**

---

## 1. Starting a route — the command layer

| Input | Action |
| --- | --- |
| `Ctrl+W` | Start routing using the Interactive Router |
| `Shift+A` | Route selected connections using the **ActiveRoute** Guided Interactive Router |
| `Ctrl+Alt+G` | **Gloss Selected** — improve the quality of selected routes |
| `Shift+Ctrl+Click&Hold` | Create a vertex (or break) in a track segment at the cursor |
| `Backspace` (not routing) | Delete a selected end-of-route object; the touching object is auto-selected, so repeat presses unwind |
| `Ctrl+Delete` | Same, but unwinds in **both** directions from the selected segment |

([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors))

The route starts wherever you click. Altium: *"the interactive routing engine attempts to
find a path from the copper closest to your click location that is on that net (pad, via,
track) up to the current cursor location."* Click a connection line and *"the Interactive
Routing will jump to the nearest end of the connection line and switch to the layer that
the object is on."*
([pcb/routing/interactive](https://www.altium.com/documentation/altium-designer/pcb/routing/interactive))

**`Shift+F1` is the whole no-learning-curve story in one key.** During any interactive
command it opens *"a menu that lists all valid shortcuts for the present stage of the
currently running interactive command."* Not a static cheat sheet — a **context-sensitive,
stage-aware** list. This is the cheapest high-value thing on this page to copy and it is
the one an EE reaches for first in an unfamiliar tool.

---

## 2. Conflict resolution — `Shift+R`

The defining routing behaviour. Seven modes, quoted verbatim from Altium's own table
([pcb/routing/interactive](https://www.altium.com/documentation/altium-designer/pcb/routing/interactive)):

| Mode | Altium's words |
| --- | --- |
| **Ignore Obstacles** | "the interactive router can place tracks anywhere, including over existing objects, displaying but allowing potential violations" |
| **Walkaround Obstacles** | "Attempt to find a path, from the last click location to the current cursor location, around existing objects such as tracks, pads and vias. The clearance to other objects is defined by the applicable Clearance design rule." |
| **Push Obstacles** | "Push existing tracks and vias to make room for the new route… Via pushing is controlled by the Allow Via Pushing option." |
| **HugNPush Obstacles** | "The routing will closely follow existing objects and only push them when there is insufficient room for the track being routed." |
| **Stop at First Obstacle** | "The routing will stop at the first obstacle that gets in the way." |
| **Autoroute Current Layer** | "Apply auto-router intelligence to the interactive router, automatically selecting between pushing and walking around to give the shortest overall route length, on the current layer." |
| **Autoroute MultiLayer** | "…automatically selecting between pushing, walking around **or switching layers** to give the shortest overall route length." |

**`Shift+R` cycles them** — during interactive routing, interactive sliding, *and* via
dragging. Three of the four failure modes share one recovery: when a mode cannot proceed
without a violation, *"an indicator appears to show the route is blocked."* The user is
never left guessing why the cursor stopped following them.

The current mode is displayed in **three** places simultaneously: the heads-up display,
the status bar, and the Properties panel. That redundancy is deliberate — a modal tool
where the mode is invisible is a trap.

Which modes are enabled by default in `PCB Editor – Interactive Routing`: **unverified**.
The page documents that modes can be individually enabled/disabled and that a disabled
mode drops out of the `Shift+R` cycle, but does not publish the shipped defaults
([pcb-editor-interactive-routing-preferences](https://www.altium.com/documentation/altium-designer/pcb-editor-interactive-routing-preferences)).

During differential-pair routing, `Shift+R` cycles the same list minus the two Autoroute
modes ([interactively-routing-differential-pairs](https://www.altium.com/documentation/altium-designer/pcb/high-speed-design/interactively-routing-differential-pairs)).

---

## 3. Corner style — `Shift+Space`, and direction on `Space`

Five styles. `Shift+Spacebar` cycles the style; `Spacebar` toggles the **direction**
sub-mode (which of the two legs goes first). Four of the five have a direction sub-mode.

| Style | Behaviour |
| --- | --- |
| **Track 45** | corner from one 45° track |
| **Track 45 with Arc** | a track plus a 45° arc; `,` / `.` change the radius, hold `Shift` to accelerate |
| **Track 90** | two tracks at 90° |
| **Track 90 with Arc** | a track plus a 90° arc; `,` / `.` change the radius |
| **Any Angle** | "Place the next segment directly from the last placed segment to the current cursor position." Altium's note: use with Strong Glossing for snake routing. |

Arc radius steps are **published numbers**, rare for Altium: `,` and `.` step by
**1 mil / 0.025 mm**; `Shift+,` and `Shift+.` step by **10 mil / 0.254 mm**
([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors)).

**Any Angle is not available** in the Differential Pair Router or the Multi-router.

During **sliding** (not routing) `Shift+Space` means something different — it cycles the
three **Hugging Style** modes, **45 Degree / Mixed / Rounded**, which control how corners
already on the board are reshaped as tracks get pushed
([pcb/routing/interactive](https://www.altium.com/documentation/altium-designer/pcb/routing/interactive)).
Same key, different noun, decided by which mode you are in. Worth knowing before copying
the binding.

---

## 4. Commit, unwind, terminate — the four-key core

| Input | Action |
| --- | --- |
| **Click** or **`Enter`** | "Commits the routing up to the current cursor position and places the tracks." |
| **`Ctrl+Click`** | "Auto-complete segments to target." If the connection cannot be auto-completed, the tool returns to the last used routing mode. |
| **`Backspace`** | "Unwinds the last committed route back to its starting point. **If any objects had been pushed through placing the last segment, they are moved back to their original positions.**" |
| **`Esc`** | "Terminates the current route. Any routing that has been committed before calling the termination is retained." |

That Backspace sentence is the whole quality bar for a push router in one line: the undo
has to restore the *pushed neighbours*, not only the track you drew. A push router whose
undo leaves the neighbours shoved is worse than no push router.

Three visual states carry the commit model
([pcb/routing/interactive](https://www.altium.com/documentation/altium-designer/pcb/routing/interactive)):

- **Hatched** — unplaced.
- **Solid** — placed, but for the connection currently being routed these are
  **soft-commits**: "the routing engine recognizes that they are placed but retains the
  ability to gloss them and to remove them if you move the cursor to a location where they
  are no longer appropriate."
- **Hollow** — the **look-ahead segment**, which "will not be placed when you next click."
  `1` toggles look-ahead mode.

Soft-commit is the subtle one. Altium is not a stack of frozen decisions; the last stretch
stays negotiable while the route is live.

Outside a route, `Backspace` on a selected end-of-route object deletes it and selects the
one it touches, so repeated presses unwind a net one object at a time. "The unwinding
process continues through vias and ends when a pad is hit." If the selected object touches
more than one other object it is simply deleted and nothing is selected —
Altium refuses to guess at a fork.

---

## 5. Width and vias during a route

| Key | Action |
| --- | --- |
| `3` | Cycle routing width source: **User Choice → Rule Minimum → Rule Preferred → Rule Maximum** |
| `Shift+W` | Choose from the predefined **Favorite Interactive Routing Widths** list |
| `4` | Cycle via size source: **User Choice → Rule Minimum → Rule Preferred → Rule Maximum** |
| `Shift+V` | Choose a User via size in the Choose Via Size dialog (constrained to the Routing Via Style min/max) |
| `2` | **Add a via without changing layer** |
| `/` | Add a **fanout via**; the tool immediately waits for the next fanout |
| `6` | Cycle possible **via types / spans** (Z-plane) during a layer change |
| `8` | Pop a menu of available via types |
| `5` | (diff pair / multi-route) cycle via patterns — aligned or staggered |

The rule that ties it together: **"If you switch layers during routing, a via is
automatically added, in accordance with the applicable Routing Via Style design
constraint."** You never place a via to change layers; you change layers and get a via.

Two automatic behaviours worth naming because they are the ones that make dense boards
routable at all:

- **Auto Shrinking** — "Necking the route to fit through a narrow gap… automatically
  narrowing the route down to just fit through the gap, with the allowed minimum being
  defined by the routing width constraint."
- **Apply Trace Centering** — routing between a pair of pads defaults to minimum clearance
  from the nearest pad; with centering on, it is spaced between them, using a clearance
  multiplier it can raise as needed.

Both are the router doing arithmetic the human would otherwise do by hand, and both are
squarely in "the tool should just handle this" territory.

---

## 6. Layer switching mid-route

| Input | Action |
| --- | --- |
| `Ctrl+Shift`+wheel | Cycle enabled signal layers, dropping a via |
| `Ctrl+L` | Pop a list of routing layers; click one to switch (dropping a via) |
| numpad `+` / `*` | Next enabled signal layer, dropping a via |
| numpad `-` | Previous enabled signal layer |
| numpad `1`–`9` | Jump to that signal layer by its `[n]` prefix, dropping a via |
| `L` | From a **multi-layer pad or via only**, switch to the next signal layer defined for that pad — works only before the first segment is committed, and drops no via |

Two navigation gestures that have no equivalent anywhere in our app and are pure gain:

- **`9`** — "Switches the cursor position from the currently selected pad or track to the
  target pad or track. If the location of the object being switched to is not within the
  current design window, the view jumps and centers around the new cursor position." Route
  the other end of the connection without hunting for it.
- **`7`** — "Cycles through the connections available for routing if the current pad has
  multiple connections." Drop this ratline, pick up the next one leaving the same pad.

---

## 7. Gloss and retrace

**Glossing** is continuous and automatic: "As you move the cursor around while defining a
new interactive route path, all of the yet-to-be committed routing is also automatically
glossed." Its stated goals are "reducing the number of corners, reducing the number of
segments, removing acute angles and reducing the overall route length."

| Input | Action |
| --- | --- |
| `Ctrl+Shift+G` | Cycle **Gloss Effort (Routed)**: **Off / Weak / Strong** |
| hold `Ctrl+Shift` | Temporarily inhibit glossing; it resumes on release. Note: "the status bar will not reflect this state" |
| `Ctrl+Alt+G` | **Gloss Selected** on existing routing |

The three levels, in Altium's words: **Off** — "typically useful at the end stage of board
layout when the ultimate level of fine-tuning is required"; **Weak** — "considering only
those tracks directly connected to or in the area of the tracks that you are currently
routing… useful for fine-tuning track layout or when dealing with critical traces";
**Strong** — "a strong emphasis on the shortest path… useful in the early stages of the
layout process."

There is a second, independent axis: **Gloss Effort (Neighbor)**, also Off/Weak/Strong,
controlling how much the *adjacent* nets get tidied when your route disturbs them.
Panel-only, no shortcut.

**During interactive sliding, glossing is automatically reduced to Weak** — Altium's
reason: "to avoid the glossing engine from fighting the designer in their attempts to
relocate the routing." A tool that keeps re-optimising while you are trying to place
something by hand is a tool people turn off. The hold-to-inhibit key exists for the same
reason.

**Gloss vs Retrace** is a clean split and the distinction is the useful part:

- **Gloss** — "focuses on improving the trace geometry… **preserves the existing trace
  width and differential pair gap**."
- **Retrace** — "assumes the overall geometry is satisfactory, focusing instead on
  verifying that the routing meets the design rules… **Retrace changes them to
  Preferred**."

Retrace is the "the rule changed, apply it to what is already there" tool: "you can 'fatten
up' that existing power routing, or update that differential pair to new width and gap
settings." Both work on a *section* — select a segment at each end of a run and only the
routing between them is processed.

Retrace also has real warnings rather than silence, e.g. *"Pre-existing Min Width
violation(s) detected"* with the honest caveat "the original thin object will have been
widened and possibly moved by the time you have a chance to click on the message. You may
need to Undo to understand what has happened."

---

## 8. Push-and-shove on existing copper — interactive sliding

Sliding is where push-and-shove is felt most, because you are moving copper that already
works.

| Input | Action |
| --- | --- |
| Click+hold&drag on a segment | Slide it |
| `Shift+R` | Cycle conflict resolution (Ignore / Push / HugNPush apply during sliding) |
| `Shift+Space` | Cycle **Hugging Style**: 45 Degree / Mixed / Rounded |
| `Space` | On a **vertex** drag, cycle **Vertex Action**: **Deform / Scale / Smooth** |
| `Ctrl+Shift+G` | Cycle gloss effort |
| `Z` | Toggle **Keep Coupled** (drag a diff pair's partner along) |
| `C` | Toggle **Include Miters** |

Vertex Action is the detail that shows how far the model goes: grabbing a *corner* is a
different verb from grabbing a *segment*, and the corner has three behaviours of its own.
**Deform** — "Break or lengthen the track segments attached to the moving vertex so that
the vertex follows the cursor movement."

Loop removal deserves its own line because it is the single most useful reroute behaviour
in the tool: start a new path anywhere on an existing route, draw a better one, come back
to the existing routing, and "the software identifies the loop created between the old
path and the new path, when you right-click or press `Esc` to terminate the route, the
redundant segments are automatically removed, including any redundant vias." You never
delete the old track. **`Shift+D`** toggles the behaviour, and Altium documents that it is
worth turning off when rerouting a differential pair, "because it needs to initially allow
track crossovers before resolving loops."

`Ctrl+W` during a route toggles **clearance boundaries** — "The no-go clearance area
defined by the existing objects + the applicable clearance rule is displayed as shaded
polygons." The published framing is exactly the question a user asks: *"Wondering why the
routing won't fit through that gap?"*

`Shift+F` enters **Follow mode**: "the next object detected under the cursor will be
followed," building the route out of tracks and arcs along a contour. `Backspace` drops
out of Follow mode back to regular routing; `Esc` aborts the route entirely.

---

## 9. Differential pairs

Launched as `Route » Interactive Differential Pair Routing`. Everything in §2–§6 applies;
these are the additions
([interactively-routing-differential-pairs](https://www.altium.com/documentation/altium-designer/pcb/high-speed-design/interactively-routing-differential-pairs)):

| Key | Action |
| --- | --- |
| `3` | Cycle width: User Choice / Rule Min / Rule Preferred / Rule Max |
| `Shift+6` | Cycle **gap**: Min Gap → Preferred Gap → Max Gap |
| `Shift+B` | Cycle **width-gap pairings** together: Min-Min → Pref-Pref → Max-Max |
| `5` | Toggle staggered vs perpendicular via patterns during a layer change |
| hold `Shift` in Any Angle | Route the pair using **tangent arcs**, shaping around existing curves |
| `Ctrl+Q` | Toggle mm/mil in the current dialog or panel |

A pair is identified structurally, not by selection: "a net naming convention that uses a
common net name with a standard pair-identifying suffix (eg `DRV1_L` & `DRV1_H`). The
suffix-pair must be declared in the Options tab of the Options for Project dialog."

The Properties panel during pair routing "displays the signal length and delay of each net
in the pair, this detail is updated at each click event during routing." Length and delay,
live, per net, per click.

The pair-aware Gloss behaviour is unusually specific and worth reading in full if we ever
build one: it recognises "zipped" portions already at the defined gap, tries to shorten the
unzipped portions, makes opposite-side unzipped portions equal in length where possible,
and — the honest bit — **"Gloss does not add meanders to the shorter side of the pair. …
If length balancing is not achieved naturally, the pair is left unbalanced."** It does not
silently invent tuning to hit a target.

**Multi-routing** (a bus at once) has its own three keys: `B` decrease bus spacing,
`Shift+B` increase — "in increments of the current snap grid" — and `C` to converge the
spacing to the minimum the Routing Width constraint allows.

---

## 10. Interactive length tuning — the accordion

`Route » Interactive Length Tuning` (and `Route » Interactive Differential Pair Length
Tuning`). Three pattern styles: **Accordion, Trombone, Sawtooth**. Placement is a gesture,
not a dialog: *"Length tuning segments are added by simply wiping the cursor along the
route path, with the dimensions and positions of the various tracks and arcs that make up
the tuning segments automatically calculated and inserted by the length tuning algorithm."*
([pcb/high-speed-design/length-tuning](https://www.altium.com/documentation/altium-designer/pcb/high-speed-design/length-tuning))

Shortcuts during tuning, verbatim from the page's own table:

| Key | Action |
| --- | --- |
| `Tab` | Open the Properties panel (all patterns) |
| `Spacebar` | Cycle the 3 tuning corner styles (Accordion & Trombone) |
| `,` | Decrease Max Amplitude (Accordion) / Actual Height (Sawtooth) by the **Step** field |
| `.` | Increase the same |
| `3` / `4` | Decrease / increase **Space** by the Step field (Accordion & Trombone) |
| `1` / `2` | Decrease / increase corner **Miter** % by the Step field |
| `S` | Toggle Single Side (Sawtooth & Trombone) |
| hold `Shift` | Switch from *placing* the pattern to *sliding* it; release to resume placing |
| `Shift+G` | Toggle the **Length Tuning Gauge** |
| `Shift+F1` | List the shortcuts valid right now |

Every one of those steps by a user-visible **Step** field rather than a hardcoded
increment, and Altium's own troubleshooting advice names the ratio: *"a sensible value for
the Step setting is around 1/10 of the Max Amplitude / Actual Height setting."*

The **Length Tuning Gauge** (also `Shift+G` during plain interactive routing) is "A red or
green slider that shows the current Routed Length of the net (during length tuning), or
the Estimated Length (during interactive routing). The slider changes from red to green
when the current length moves from being out-of-range to being within the minimum and
maximum lengths allowed." One binary, live, while your hand is moving. No dialog.

Accordion corner styles are **Mitered Lines / Mitered Arcs / Rounded**, and Altium ranks
them: "The Rounded style is the most compact and Mitered Lines is the least compact."
Rounded typically needs `Amplitude > Radius + Route Width`.

The page also links out to Howard Johnson on serpentine delays and says plainly that
"If the adjacent accordion sections are too close together for too long, then crosstalk
coupling can distort the signal" — the tool warning you about the physics of the thing it
just helped you draw.

One constraint that shapes the UI: "Once length tuning has been started (i.e. a route has
been clicked on), the selected tuning pattern cannot be changed to another pattern." You
pick the pattern before you touch the route.

---

## 11. Selecting the routing to work on

Routing edits need selections that follow copper, and Altium's are all one key.

| Input | Action |
| --- | --- |
| `Tab` (with an object selected) | Extend the selection along the net — "click once on any object in the net of interest, and then press the `Tab` key" |
| `Ctrl+H` | Select Connected Copper |
| `S` then `C` | Select Physical Connection |
| Click+drag left | Touch-select all unlocked track segments crossed by the rectangle |
| `Ctrl`+click+drag left | Select component **pads** touched by the rectangle |
| `Shift`+click+drag left | Same as touch-select, but `Shift` prevents the drag being read as Move Object |

That last row is a real ergonomic hazard Altium documents openly: without `Shift`, the
click-and-drag "can be interpreted as Move Object; if it is the component above the routes
will move." Their recommended fix is the Selection Filter — turn off selectability for
large objects. Ours has the same hazard waiting the moment drag stops meaning pan.

---

## 12. KiCad 9 cross-check

KiCad's PNS router covers the same ground with a much smaller surface, and on several
counts it is the better model for us
([docs.kicad.org/9.0/en/pcbnew](https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html)):

- **Three modes, not seven**: **Highlight Collisions**, **Shove**, **Walk Around**. KiCad
  even makes the recommendation for you: "we recommend using Shove mode for the most
  efficient routing experience or Walk Around mode if you do not want the router to modify
  tracks that are not being routed."
- **`X`** route single track. **`D`** drag (45°, uses the router). **`G`** drag free angle.
  **`End`** finish route. **`Backspace`** undo last segment. **`Esc`** exit keeping fixed
  segments.
- **`V`** place a via — "which will switch to the next layer in the active layer pair."
  **`<`** select layer and place through via. **`Shift+V`** cycle layer-pair presets.
  **`PgUp`/`PgDn`** jump to F.Cu / B.Cu.
- **`/`** switch track **posture** (Altium's "corner direction"), **`Ctrl+/`** cycle corner
  mode across 45 / 45 rounded / 90 / 90 rounded. **`Shift+Space`** toggles free-angle vs
  45° mode.
- **`W` / `Shift+W`** step track width through the Board Setup list.
- **Posture from mouse path**: "In situations where there is no obvious best posture… KiCad
  will use the movement of your mouse cursor to select the posture." And the polite
  follow-up — if you override it manually once, automatic detection turns off for the rest
  of that route. The tool stops guessing after you correct it.
- **Clearance outlines are on by default** and honest about their own limits: "only the
  largest clearance value will be shown visually."
- Modifiers during a route: hold `Ctrl` to disable grid snapping, hold `Shift` to disable
  snapping to pads and vias. Standard-web modifier semantics, unlike Altium's `Ctrl+Shift`
  hold-to-inhibit-gloss.

**The straight steal from KiCad**: three modes with a published recommendation beats seven
modes with a preferences page, and `/` for posture is a better key than `Space` because it
does not fight with anything a browser wants.

---

## 13. Which of this matters when an autorouter does 99%

This is the part that changes what we build.

Our pipeline routes the board. `docs/lessons.md:36` and `:40` record the measurements:
`autorouterEffortLevel="5x"` took terminal-keyboard from 46 to 18 blocking findings and
harness-puck from 5 to 1 with no design change; `"10x"` was tried and dropped — "46 minutes
on harness-puck against 11 at `5x`, with the remaining findings unchanged in kind." Every
board now declares `5x` in the skeleton, with stage 0b as the net. A human arriving at the
board is looking at 158–252 finished traces and a short list of specific problems.

So the user story is not "route this board." It is **"the router did this and it is wrong
here — fix this bit."** Sorting Altium's surface against that story:

**Load-bearing — build these.**

1. **A track segment and a vertex must be selectable objects.** Nothing else on this list
   is possible first. Today `PcbCanvas.jsx:327-329` collapses every copper click to a net.
2. **Drag a segment with push-and-shove** (`Shift+R` between Walkaround / Push / Ignore, or
   KiCad's three). Fixing the last 1% is overwhelmingly "this trace is 0.1 mm too close to
   that pad" — a nudge, not a re-route. This is the single highest-value routing feature
   for us and it is *sliding*, not routing.
3. **`Backspace` unwind that restores pushed neighbours.** Altium's exact guarantee. Any
   editor that shoves copper and cannot put it back will not be trusted twice, and trust is
   the entire proposition of letting a machine route your board.
4. **Reroute-with-loop-removal.** Draw a better path from anywhere on an existing trace back
   to it; the redundant old segments and vias vanish on `Esc`. For "the autorouter took a
   silly path here" this is the whole interaction, and it needs no rip-up UI, no mode, and
   no deletion gesture. Highest ratio of usefulness to surface area on this page.
5. **Clearance boundaries on `Ctrl+W`.** The user's question is always "why won't it fit."
   Blocked-indicator feedback belongs with it. Blocked on the missing rules data (§0).
6. **`Shift+F1`, the stage-aware shortcut list.** Directly answers "no learning curves." It
   is a UI affordance over a keymap we control, so it is cheap, and it is the thing that
   makes every other binding discoverable instead of memorised.
7. **The length/skew gauge on `Shift+G`.** Red-to-green, live, while the hand is moving.
   Two of our three reference boards ship with a differential pair the automated pass
   refused (§13), so the person fixing one by hand needs to see skew close in real time.
   Nothing else on this page tells them whether the fix worked.
8. **A modal keymap.** `1`–`9`, `2`, `3`, `L`, `R` are all globals in
   `BoardWorkspace.jsx:621-689` and all of them mean something else in Altium's router.
   Route mode has to take the keyboard while it is live, and say so on screen.

**The differential pair is not a second-wave problem — it is the live one.** I drafted this
section assuming length tuning was irrelevant because we have no pairs, then read the
sidecars. All three example boards carry a real USB pair, and the automated pass gave up on
two of them:

| Board | Pair | Status | Skew | Coupled | Worst clearance |
| --- | --- | --- | --- | --- | --- |
| harness-puck | USB_DP/USB_DM | **refused** | 10.394 mm | 1.2% | **−0.075 mm** |
| terminal-keyboard | USB_DP/USB_DM | **refused** | 20.254 mm | 10.4% | **−0.100 mm** |
| hydrate-coaster | USB_DP/USB_DM | routed | 13.91 → **1.954 mm** | 7.5% → **95.2%** | 0.115 → 0.128 mm |

(`examples/*/boards/main.board.json`, `build.diffPair.pairs`. A negative
`worstClearanceMm` is a real violation, not a margin.) All three also report
`USB_DP_CONN/USB_DM_CONN` as **skipped**, with the reason stated plainly in the sidecar:
"this pass routes two-terminal pairs only, so a multi-drop pair keeps the autorouter's
copper."

Read the refusal reason and the priority falls out: *"two layers: no corridor wide enough
for the pair exists between the two ends (needs 0.99mm of clear copper plus clearance, on
any of top, bottom)."* The machine did not fail at geometry. It failed because **other
copper is in the way and it will not move it.** That is the exact job of push-and-shove
sliding, and it means item 2 above is not merely the most common fix — it is the unblock
for the one class of failure our pipeline currently cannot clear on its own.

So: **skew is a shipping defect on two of three reference boards**, and the pair a human
would go fix by hand is the highest-value thing our editor could let them touch. The
Altium surface that serves it is, in order: segment selection, shove-drag to open the
corridor, reroute-with-loop-removal to lay the pair properly, `Shift+6` gap cycling, and
then — genuinely — the length-tuning gauge on `Shift+G`, because 10 mm of skew is what
the accordion exists for. Accordion *placement* can stay a batch pass like `powerWidth`;
the **gauge** cannot, because it is what tells a human whether their hand-fix worked.

**Worth having, second wave.** Corner style and direction (`Shift+Space` / `Space`, or
KiCad's `Ctrl+/` and `/`) — matters the moment anyone slides a corner. Width and via-size
cycling (`3` / `4`) — we already have eight distinct widths in use on harness-puck, so the
rule-min/preferred/max concept is real for us. Layer switch that drops a via automatically.
`Tab`-to-extend-selection along a net. Trace-centering and auto-shrink, which are the
router doing arithmetic rather than the user.

**Skip, or do differently.**

- **Seven conflict modes with a preferences page to enable them.** Three, with a default,
  and no page. KiCad's framing is the better product.
- **ActiveRoute and Route Guides.** This *is* our autorouter, just spelled as a gesture.
  Altium is explicit — "ActiveRoute is not an autorouter… it cannot place vias and does not
  include power net routing strategies." Ours can and does. Building a guide-drawing UI to
  steer a router we run automatically is solving Altium's problem, not ours. If a user wants
  to steer, the natural surface here is the chat and a constraint, not a river drawn on the
  canvas.
- **Gloss effort as a live three-way cycle.** Glossing exists because a human draws untidy
  copper. Ours is machine-drawn and already tidy. What we need from this family is
  **Retrace** — "the rule changed, apply it to the copper that exists" — and we already have
  its shape server-side in the `powerWidth` pass. Keep the concept, drop the keystroke.
- **Multi-routing bus spacing, subnet swapping, fanout patterns, via-span cycling.** All are
  for laying out a BGA by hand. Zero of the three example boards has a BGA.

**The honest summary.** Of thirteen sections of Altium routing surface, the ones an EE will
actually miss in our tool are **select a segment, drag it with shove, undo it cleanly,
reroute over the top of a bad path, see why it does not fit, watch the length gauge go
green, and press a key to find out what the keys are.** That is seven behaviours, not sixty
keystrokes — and the refused USB pairs above say which board problem they are for. Shipping those seven well
is closer to Dee's bar than shipping thirty of them shallowly — an EE who presses
`Shift+R` and gets a mode they can feel working will forgive a missing accordion; one who
clicks a trace and selects the whole net has already found the shortcoming.

---

## 14. Open items — unpublished or unmeasured

- **Which conflict-resolution modes ship enabled by default.** The Interactive Routing
  preferences page documents the enable/disable mechanism and its effect on the `Shift+R`
  cycle but not the shipped state. **Unverified.**
- **Default Gloss Effort (Routed) and (Neighbor) values.** Off/Weak/Strong are documented;
  which is default is not. **Unverified.**
- **Default Step values for the length-tuning shortcuts.** The page recommends ~1/10 of Max
  Amplitude as sensible; the shipped default is not published. **Unverified.**
- **Snap Distance default for object snapping** (`Shift+E` cycles Off / Current Layer / All
  Layers). Semantics published, number not. **Unverified.**
- **Our own rules data.** There is no netclass table, no per-net width rule and no clearance
  rule object in `viewer/src/client` — only `build.pourClearance.requiredMm: 0.15` in the
  sidecar and two Properties rows. Any DRC-aware editing gesture is blocked on that landing
  client-side first. **Measured, not unverified: it does not exist.**

---

## Sources

- [Interactive Routing](https://www.altium.com/documentation/altium-designer/pcb/routing/interactive)
- [PCB Editor shortcut keys](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors)
- [Differential Pair Routing](https://www.altium.com/documentation/altium-designer/pcb/high-speed-design/interactively-routing-differential-pairs)
- [Length Tuning](https://www.altium.com/documentation/altium-designer/pcb/high-speed-design/length-tuning)
- [ActiveRoute](https://www.altium.com/documentation/altium-designer/pcb/routing/activeroute)
- [PCB Editor – Interactive Routing preferences](https://www.altium.com/documentation/altium-designer/pcb-editor-interactive-routing-preferences)
- [KiCad 9 PCB Editor manual](https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html)
