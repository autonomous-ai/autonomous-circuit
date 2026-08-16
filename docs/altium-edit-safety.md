# Altium's edit-safety surface, and every place ours is missing

Companion to `viewer/src/client/components/board/ALTIUM-NOTES.md`. That file covers
**looking** at a board. This one covers **changing** one without scrapping it — the
machinery that stands between an EE's hands and a lost afternoon.

Same house rules. Every Altium claim carries a source URL; anything Altium does not
publish says **unverified** rather than a made-up number. Every claim about our app
carries `file:line` and, where the evidence is an *absence*, the grep that returns
nothing.

The test is the same test: an EE with ten years of Altium muscle memory sits down at our
app. Every reflex that does not fire is a defect.

**The headline.** Of the seven safety systems below, our app has **zero**. Not "a weaker
version" — zero. `grep -rn "undo\|redo" viewer/src/client/components/board/` returns
nothing at all. The one that matters most is `Ctrl+Z`, and it is also the one closest to
free.

---

## 1. Undo / redo

`Ctrl+Z` undo, `Ctrl+Y` redo, in both editors
([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors)).
These are the two most-pressed keys in the application and Altium documents almost
nothing about them beyond the binding.

| Question | Answer |
| --- | --- |
| Default undo depth | **unverified** |
| What counts as one step | **unverified** |
| Whether the stack survives a save | **unverified** |
| Whether the stack survives an ECO | **unverified** |

On depth: Altium's PCB Editor – General preferences page for AD 16.1–17.1 is indexed
with an **Undo/Redo** stack-size field — "the current number of previous operations that
can be undone/redone… edit this field to define the number of undos possible", settable
to zero to empty the stack or disable undo. A direct fetch of that same page
([pcb-editor-general-preferences?version=17.1](https://www.altium.com/documentation/altium-designer/pcb-editor-general-preferences?version=17.1))
does **not** render the field, and the current-version page
([pcb-editor-general-preferences](https://www.altium.com/documentation/altium-designer/pcb-editor-general-preferences))
does not list it either. So: the field existed, it is user-settable, and **its default
value is unverified.** Do not quote a number.

On granularity, Altium's own command pages for Undo
([pcb-cmd-undo](https://www.altium.com/documentation/altium-designer/pcb-cmd-undoundo-ad?version=22))
say nothing about what a step contains. Whether one interactive-routing session collapses
to one undo is **unverified**.

**What that means for us.** We cannot copy a number we cannot read, so the design call
has to come from first principles rather than parity, and there is one principle that is
not in doubt: *undo depth is never 1.* A tool where `Ctrl+Z` gets you back exactly one
edit is a tool people stop trusting on the second edit.

> **What we have.** Nothing.
>
> - `grep -rn "undo\|redo\|Undo\|Redo" viewer/src/client/components/board/` → **no
>   matches** (exit 1). The board IDE has no undo of any kind.
> - The two `Undo` / `Redo` items in `WindowMenuBar.jsx:233-234` call
>   `runEdit("undo")` → `runEditCommand` at `WindowMenuBar.jsx:54-60`, which is
>   `document.execCommand(command)`. That is the browser's **text-caret** undo inside a
>   focused input. It cannot see the board, and it will silently do nothing — or worse,
>   quietly undo typing in the chat box — when an EE presses `⌘Z` after moving a part.
>   A menu item labelled Undo that does not undo the last thing you did is worse than no
>   menu item.
> - No server command undoes anything. The whole IPC surface is 20 commands
>   (`viewer/src/server/circuit/http.mjs:379-533`): `app_info`, `app_prereq_check`,
>   `build_status`, `build_revisions`, `app_settings_read`, `app_settings_write`,
>   `app_set_model`, `app_set_effort`, `project_list`, `project_create`, `project_open`,
>   `project_rename`, `project_delete`, `catalog_read`, `project_catalog_read`,
>   `chat_start_turn`, `chat_approve_plan`, `chat_request_plan_changes`,
>   `chat_cancel_turn`, `chat_session_state`. Nothing writes board source and nothing
>   rewinds it.
> - There *is* a revert marker in the chat store — `noteRevert(label)` at
>   `viewer/src/client/store/chat.js:1240`, reducer case `note_revert` at `:871`, rendered
>   as "↩ Reverted to <label>" by `chat/ChatTurn.jsx:144-164`. `grep -rn "noteRevert"
>   viewer/src/client` returns **exactly one line — the definition.** It has no caller.
>   The UI for a feature that does not exist.

---

## 2. Online DRC vs Batch DRC

Two checkers, not one, and EEs use both without thinking about it.

**Online DRC** "runs in the background, in real-time, flagging and/or automatically
preventing design rule violations… especially helpful when interactively routing your
board, to immediately highlight clearance, width and parallel segment violations."

It needs three things true at once
([drc/setting-up-running](https://www.altium.com/documentation/altium-designer/pcb/drc/setting-up-running)):

1. the rule is enabled in the PCB Rules and Constraints Editor,
2. that rule type has its **Online** box ticked in the Design Rule Checker dialog,
3. **Online DRC** is on in `Preferences » PCB Editor – General`
   ([pcb-editor-general-preferences](https://www.altium.com/documentation/altium-designer/pcb-editor-general-preferences)):
   *"Enable to have software monitor all PCB design rules interactively as you work and
   immediately highlight any rule violations."*

The one gotcha Altium states outright: **"Online DRC only detects new violations –
violations that are created after the feature is enabled – while Batch DRC allows a check
to be manually run at any time during the board design process."** Online DRC is a
*guard on your hands*, not an audit of the board.

Some checks ship off by default for speed — the docs note "certain Online DRC checks
[are] disabled to improve PCB Editor performance." **Which checks: unverified.**

**How a violation looks while you edit.** Two independent channels, meant to be used
together:

- **custom violation graphics** — rule-specific graphics drawn on the affected layers,
  sometimes carrying the constraint value itself;
- **violation overlay** — a pattern laid over the offending primitives, styled
  **None / Solid / Style A / Style B**.

The documented workflow is to read the overlay zoomed out to find *where*, then zoom in
and read the graphic to find *what*.

**Batch DRC** (`Tools » Design Rule Check`) is the audit. Report Options include
**Create Report File** (HTML/TXT/XML), **Create Violations**, **Stop when *n* violations
found (default 500)**, Report Broken Planes, Dead Copper, Starved Thermals, Report
Drilled SMT Pads. Altium's own guidance: *"board design should begin and end with a Batch
DRC"*, and *"a batch mode design rule check always be performed prior to generating final
artwork"* ([pcb/drc](https://www.altium.com/documentation/altium-designer/pcb/drc)).

> **What we have.** Batch only, and the batch is slow enough that it is not a substitute.
>
> Our checker is the build. `validation.warnings` in `<stem>.board.json` is the only
> severity authority (647 warnings on `examples/harness-puck/boards/main.board.json`),
> and it appears **only after a full rebuild**. Measured rebuild wall clock, from our own
> logs:
>
> | Board | Time | Source |
> | --- | --- | --- |
> | harness-puck, default effort | ~90 s | `docs/night-log-2026-08-11.md:96-97` |
> | harness-puck, 5x effort | 1240 s | `docs/night-log-2026-08-11.md:96-97` |
> | hydrate-coaster, quiet machine | 95.9 / 95.5 / 97.6 s | `docs/lessons.md:57` |
> | hydrate-coaster, 16 busy processes | 291.9 / 273.9 / 288.7 s | `docs/lessons.md:57` |
> | terminal-keyboard | 298–494 s | `docs/lessons.md:57` |
>
> The build budget is 5400 s (`docs/lessons.md:57`). Even the best case — 90 seconds —
> is three orders of magnitude away from the per-edit feedback an EE gets from Online
> DRC. Move a part in Altium and the clearance ring appears before you let go of the
> mouse. Move a part in our app, wait a minute and a half.
>
> Nor do we have Altium's *other* half. There is no per-rule Online column because there
> is no rule table (§3). There is no violation overlay on the canvas: MessagesPanel rows
> select and flash the offender (`MessagesPanel.jsx`), but nothing renders a clearance
> graphic at the geometry that broke.
>
> **The honest read.** A model-routed board needs online DRC *less* than a hand-routed
> one — the model can be made to re-check itself. But the moment a human drags a
> component, we are hand editing, and a hand edit with a 90-second feedback loop is a
> hand edit with no feedback loop.

---

## 3. The rule system, scoping, and the Constraint Manager

This is the part of Altium that people underestimate until they need it, and it is the
deepest hole in our product.

**Rules are queries, not settings.** A rule's scope is written in a keyword query
language — `InNet('VBAT') And OnLayer('Bottom Layer')` — behind dropdown shortcuts
(All / Net / Net Class / Layer) and a **Custom Query** escape hatch. **Unary** rules
constrain one object; **binary** rules constrain a pair and therefore carry two scopes,
labelled **"Where The First Object Matches"** and **"Where The Second Object Matches"**
([defining-scoping-managing-design-rules](https://www.altium.com/documentation/altium-designer/pcb/defining-scoping-managing-design-rules)).

**Conflicts resolve by explicit priority.** When several rules of one type match, the
system "goes through the rules from highest to lowest priority and picks the first one
whose scope expression(s) match the object(s) being checked." A new rule lands at
priority 1 — highest — and pushes the rest down. `Priorities` button in the PCB Rules and
Constraints Editor opens **Edit Rule Priorities**.

**You can ask which rule wins, from either end.** Right-click any object →
**Applicable Unary Rules** / **Applicable Binary Rules**, with a tick beside the
highest-priority match. Or, from the rule's side, **Test Queries** → Test Queries Result,
with Mask/Dim highlighting of exactly what the scope caught. Being able to *interrogate*
the rule system is what makes a 40-rule board tractable.

**Default rules cannot be deleted, only disabled.** New PCB documents carry defaults that
"must exist for the correct functioning of the Design Rule Check system"; delete one and
it is recreated on close. The documented practice is the **Enable** toggle.

**Constraint Manager** (`Design » Constraint Manager`, from *either* the schematic or the
PCB) is the spreadsheet view over all of it — "a document-based, spreadsheet-like user
interface that allows you to view, create, and manage the design constraints used for
your PCB designs"
([sch-pcb/constraint-manager](https://www.altium.com/documentation/altium-designer/sch-pcb/constraint-manager)).
Views: **Clearances** (a class × class matrix, seeded with a single All-to-All entry),
**Physical** (widths, gaps, via styles, polygon connect per net / diff pair / xNet and
their classes), **Electrical** (topology, impedance, layer sets, via counts, length; diff
pairs; xSignals), and **All Rules** (PCB only, the query-expression view). Priority here
is automatic and ordered **All (lowest) → object class → object (highest)**. Constraints
cross the schematic/PCB boundary through the same ECO path as everything else (§5).
Constraint Management must be enabled when the project is created.

> **What we have.** A hard-coded fab profile and an explicit admission that we do not
> have net classes.
>
> - Our rules are frozen Python dataclasses compiled into the checker:
>   `packages/verify/src/verifylib/rules.py` — `AssemblyRules` with
>   `body_to_edge_mm = 2.5`, `smd_to_smd_mm = 0.3`, `min_pin_pitch_mm = 0.4`,
>   `min_board_mm = 10.0`, `smt_sides = 1`, plus `JLCPCB_ECONOMIC` / `JLCPCB_STANDARD`
>   tiers, every number carrying the JLCPCB capability page it was read from and the date
>   (2026-08-11). Good discipline, wrong shape: **not editable, not scoped, no priority,
>   not visible as a table.**
> - Scoping does not exist. `packages/verify/src/verifylib/netclass.py:20-22` says it in
>   our own words: *"Altium expresses this as net classes plus a width rule. KiCad
>   expresses it as a custom rule with a `track_width` constraint scoped by `A.NetClass`.
>   **We have neither**, so this module computes the requirement directly."* And the
>   measurement that motivated it, `netclass.py:4-8`: **every one of the 1443 / 1052 /
>   3100 routed segments on our three example boards is 0.15 mm** — V5, GND, V3_3 and a
>   button signal all the same copper.
> - The user-facing surface is four read-only rows.
>   `PropertiesPanel.jsx:225-229` renders a `Constraints` section with **Min track**,
>   **Edge clr**, **Pad clr** read off `board.min_trace_width`,
>   `board.min_board_edge_clearance`, `board.min_pad_edge_to_pad_edge_clearance`.
>   `grep -c "<input\|<select\|<textarea\|onChange\|contentEditable"
>   viewer/src/client/components/board/PropertiesPanel.jsx` → **0**. There is no way for
>   a user to say "this net is 0.5 mm wide" anywhere in the product.
>
> **This is the one an EE will hit in the first hour.** Everything else on this page is a
> safety net. This one is the job: a PCB tool where the engineer cannot state a
> constraint is a renderer with a chat box.

---

## 4. Locked objects

Three layers, and they are distinct on purpose.

**The object's own `Locked` property.** "Design objects can be locked from being moved or
being edited on the PCB document by enabling their Locked attributes." Set it by clicking
the **padlock icon in the Properties panel**, or right-click the object → **`<ObjectType>
Locked`**, which shows a tick when already locked
([pcb/placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques)).
With the property on but protection off, "if you attempt to move or rotate a design
object that has its Locked property enabled, a dialog appears asking for confirmation to
proceed with the edit." **Exact dialog wording: unverified.** **Shortcut key for the
toggle: unverified** — the PCB shortcut-keys page lists none.

**`Protect Locked Objects`** in `Preferences » PCB Editor – General`: *"Enable to ignore
any selected locked objects if they are part of a selection that is being moved."* With
it on, the KB adds, "the object cannot be selected or graphically edited," and a mixed
selection moves only the unlocked members
([KB: Unable to move specific components in the PCB Editor](https://www.altium.com/documentation/knowledge-base/altium-designer/unable-to-move-specific-components-in-the-pcb-editor)).
The same option exists on the schematic side with different wording: *"When enabled,
locked objects are not to be moved and are to be ignored if they are part of a selection
that is being moved"* — and, notably, *"Disable this option and you will be prompted with
a warning dialog if you attempt to move locked objects"*
([schematic-general-preferences](https://www.altium.com/documentation/altium-designer/schematic-general-preferences)).
So the preference chooses between **silently skipped** and **asks first**. Both are
safe; neither is "it moved and you did not notice."

**`Protect Locked Primitives In Component`**: *"Enable to prevent modification of
component primitives."* This is the footprint-integrity lock — a component whose
`Primitives` option is locked in the Properties panel has "all or most properties of
these primitives" frozen, to "prevent occasional changes of component primitives that can
result in incorrect assembly and fabrication outputs."

Two neighbours belong in the same family, from the same preferences page:
**`Confirm Global Edit`** — *"Enable to open a confirmation dialog before committing a
global editing action"* (the guard on Find Similar Objects, which can rewrite a thousand
primitives in one click) — and **Move Rooms Options → "Ask when moving rooms containing
No Net/Locked Objects."**

> **What we have.** No lock, at any layer.
>
> `grep -rniE "\block(ed)?\b" packages/circuitpy/src packages/verify/src` returns only
> the **BOM parts lock** — `spec.py:138` ("the locked BOM identities"),
> `source_hash.py:84`, `checks.py:1335-1361`. In our vocabulary "lock" means
> `parts.json`, never an object on a canvas. There is no `locked` field in the board
> source, none in the sidecar, none in the selection model.
>
> This matters more for us than for Altium, not less. Our edits come from **two agents at
> once** — a human dragging a part and a model rewriting the `.tsx`. Altium's lock
> protects you from your own hands. Ours would have to protect a hand-placed USB
> connector from the model's next rebuild, which is a stronger requirement and has no
> equivalent upstream to copy.

---

## 5. ECO — the schematic ↔ PCB sync

The single most important safety idea in the whole tool, and the one that is structural
rather than cosmetic: **changes cross between documents as a reviewed, itemised,
validated, abortable list.** Never as a silent write.

`Design » Update PCB Document <PCBDocumentName>` from the schematic (and
`Design » Update Schematics` the other way) opens the **Engineering Change Order** dialog
([sch-pcb](https://www.altium.com/documentation/altium-designer/sch-pcb)).

The shape of that dialog is the whole lesson:

| Element | What it does |
| --- | --- |
| One row per modification | Every add / remove / change is separately listed |
| Per-row enable checkbox | Take this change, skip that one |
| **Validate Changes** | Dry-run every enabled row |
| Status → **Check** column | Pass/fail per row |
| Status → **Message** column | Why it failed, e.g. *"Footprint Not Found"* |
| **Execute Changes** | Apply, and only now does the PCB change |
| Status → **Done** column | What actually landed |
| **Report Changes** | Write the list out |

The documented loop when validation fails is: close the dialog, go fix the design, come
back. **Nothing is half-applied.** What counts as a difference at all is itself
configurable, in `Project Options » Comparator`, which "defines which types of
differences to find and which to ignore."

> **What we have.** No boundary to cross, and therefore no gate — which sounds like an
> advantage and is not.
>
> We have one source of truth, the board `.tsx`. Schematic and PCB are both *renders* of
> it (`source.kind: "tsx"`, `source.path: "boards/main.tsx"` in
> `examples/harness-puck/boards/main.board.json`). So there is genuinely no
> forward-annotation problem to solve, and we should say so rather than build an ECO
> dialog out of parity instinct.
>
> But the *ECO pattern* is exactly what our write path needs, for a different reason. The
> writer is a model. A model editing a `.tsx` in response to "move C4 left a bit" can
> change anything in the file. Altium's answer to "an agent is about to modify my board"
> is already written: **enumerate the changes, validate them, show them, let me deselect
> some, then execute.** The rows are different (ours are diffs of a source file, not
> netlist deltas); the four columns and the two buttons are identical.
>
> Today: `chat_start_turn` (`http.mjs:490`) fires, the model rewrites the file, the build
> runs, artifacts change. There is no list, no validate step, no per-change opt-out, and
> no "Done" column. The user's only control is `chat_cancel_turn`.

---

## 6. Annotation

Designators are identity. If they shift, every BOM line, every assembly drawing and every
piece of test documentation shifts with them, which is why Altium treats renumbering as a
guarded operation with its own dialog rather than a text edit
([schematic/annotating-design-components](https://www.altium.com/documentation/altium-designer/schematic/annotating-design-components)).

`Tools » Annotation »` —

| Command | Behaviour |
| --- | --- |
| **Annotate Schematics** | Opens the Annotate dialog: assign designators to all or selected parts on selected sheets, "ensures that designators are unique and ordered based on their position" |
| **Annotate Schematics Quietly** | Same, no dialog |
| **Reset Schematic Designators** | Strip designators, including duplicates |
| **Force Annotate All Schematics** | Documented as exactly "Reset Schematic Designators followed immediately by Annotate Schematics Quietly", reusing the settings last set in the Annotate dialog |
| **Back Annotate Schematics** | PCB → schematic, driven by a **`*.WAS`** (Was-Is) or **`*.ECO`** file |
| **Number Schematic Sheets** | Sheet numbering |

The Annotate dialog also carries order-of-processing and matching options, multi-part
component packaging, and index/suffix control. **Exact option lists and defaults:
unverified.** The Back Annotate button lives inside the Annotate dialog.

Note the safety architecture: back annotation does not reach into the schematic directly,
it goes through a *file that records the mapping* — and the resulting designator changes
then travel to the PCB as an ECO (§5). Renumbering is never a silent in-place rewrite.

> **What we have.** Nothing, and no duplicate check either.
>
> Designators are string literals the model types into the `.tsx` (`name="U1"`). There is
> no annotate command, no reset, no back-annotate, and
> `grep -rniE "duplicate.*(designator|refdes)" packages` finds **one** hit —
> `packages/router/src/routerlib/compositions/netclass.py:199`, which dedupes *net class
> labels*, not refdes. Two parts named `R3` are, as far as this repo can prove, not
> detected anywhere.
>
> Our version of the problem is worse than Altium's in one specific way: the model may
> renumber parts between builds. When `R7` becomes `R6`, every human note, every pinned
> selection, and every message row referring to `R7` silently retargets. A Was-Is file is
> the exact right shape for that, and we do not write one.

---

## 7. The Differences panel

Compare two documents, look at what changed *before* deciding to change anything.

Open the compare with **right-click a project in the Projects panel → Show Differences**,
which raises **Choose Documents To Compare**. The panel itself is
`View » Panels » Differences`, or the **Panels** button, bottom right
([differences-panel](https://www.altium.com/documentation/altium-designer/differences-panel?version=15.1)).

- The **Differences between** dialog's **Explore Differences** button is what populates
  the panel — "click this button to investigate further the differences found by the
  Comparator prior to generating an ECO."
- The panel is a tree. Top-level folder = total count. Sub-folders by *type* of
  difference. Leaves = the specific objects responsible.
- **Clicking an object entry cross-probes to it** on the open document, "using a zoom and
  dim effect" — the same masking vocabulary as ALTIUM-NOTES §2, reused here.
- Each difference gets a **Decision**: **Update Schematic**, **Update PCB**, or
  **No Change**.
- **Create Engineering Change Order** turns the decisions into the §5 dialog.

Read the ordering: *compare → explore → decide per difference → ECO → validate →
execute.* Five separate places to stop, before one byte of the board changes.

> **What we have.** A read-only history pager, and the raw material for a real diff.
>
> - `boardRevisions.js:15` — `REVISION_LIMIT = 8` builds kept per board;
>   `:18` `MAX_DOTS = 7` before the dot strip windows. Identity is the artifact URL's
>   `?v=<mtime>-<size>` token (`revisionToken`, `:26-30`); each entry is summarised by
>   error/warning/info counts (`summarizeRevision`, `:53-`). Served by `build_revisions`
>   (`http.mjs:424-431`) out of `<project>/.circuit/revisions.jsonl`.
> - It shows **counts changing**, which is genuinely the right headline for a repair loop
>   ("6 errors three builds ago, 1 now" — `http.mjs:420`). It does not show **what
>   changed**, and there is no restore:
>   `grep -rniE "restore|rewind|revert" viewer/src/server` returns only
>   `transport.test.mjs:151` (`resetTransport`). Eight revisions you can look at and none
>   you can go back to.
> - The comparator input already exists and is unused. Every sidecar carries
>   `source.hash` and `source.fingerprint` (SHA-256 of the board source plus its local TS
>   imports plus the bible plus the parts lock — `packages/circuitpy/src/circuitpy/
>   source_hash.py:84-89`). Two revisions with different hashes have a real, computable
>   diff sitting on disk.

---

## 8. The backstop: auto-save and Local History

Worth naming because it is the layer beneath undo, and it is cheap.

`Preferences » Data Management » Backup`
([preferences/data-management](https://www.altium.com/documentation/altium-designer/preferences/data-management)):

- **Auto Save** — "check this option to enable the auto save function", interval in
  **Minutes**, and "enter or scroll to the maximum number of versions you want to keep",
  with a selectable path. Copies of modified-but-unsaved documents are written each
  cycle; the original stays dirty until you save. Default path
  `\Users\<ProfileName>\AppData\Roaming\Altium\Altium Designer <GUID>\Recovery`.
  **Default interval and default version count: unverified.**
- **Local History** — "saves a copy of the previous version, each time a document is
  saved," ZIP-compressed, retained "for a number of days." **Default retention:
  unverified.**
- Built-in **SVN** for real version control.

> **What we have.** Project directories are ordinary folders (`projects.mjs:36` skips
> `.git` when scanning, which is the only mention of git in the server). Every rebuild
> **overwrites `<stem>.circuit.json` in place** — stated in `boardRevisions.js:8-11`,
> which is why the revision ring exists at all. No auto-save, no local history, no
> per-save copy. If the model writes a bad `.tsx`, the previous good one is gone unless
> the user happened to have the project under their own git.

---

## Which of these is non-negotiable in *any* PCB tool

Ranked by what an EE would refuse to work without, hardest first. All seven are currently
absent from our app; the ranking is what to fix in what order.

**1. Multi-level undo.** Not negotiable in any editor of anything, and the bar is set by
every other program on the machine, not by Altium. Also the cheapest thing on this list
and the only one where a bad menu item actively lies today
(`WindowMenuBar.jsx:233-234`).

**2. A way to state a constraint.** "This net is 0.5 mm." Scoping sophistication is
negotiable — query language, priority table, class matrix, all of it can wait. The
*existence* of a user-settable rule cannot. We currently ship one trace width for
everything and have measured it: 1443 / 1052 / 3100 segments all at 0.15 mm
(`netclass.py:4-8`).

**3. Locked objects.** One bit per object. Non-negotiable the moment a board has a
connector that must land on the enclosure cutout — and doubly so for us, because our
second editor is a model that rewrites the whole file.

**4. Changes reviewed before they apply.** Altium's ECO. We have no schematic/PCB split
so we do not need *their* dialog, but we have something they never had — an agent writing
the source — and the four-column validate-then-execute pattern is the right answer to it.

**5. Stable, unique designators.** At minimum: detect duplicates, and record a Was-Is map
when the model renumbers. Currently neither.

**6. Feedback while editing.** Altium's Online DRC. Our floor is 90 seconds
(`night-log-2026-08-11.md:96`), which is not feedback. A cheap geometric pre-check on the
dragged object — clearance and courtyard only, no rebuild — would cover most of what
Online DRC is actually used for. Ranked sixth only because a model-routed board reduces
how much hand routing happens, not because a minute-and-a-half loop is acceptable.

**7. A diff before you commit.** The Differences panel. We have the hashes
(`source_hash.py:84`) and eight kept revisions (`boardRevisions.js:15`) and no diff
between any two of them.

---

## Open items — things Altium does not publish

- **Undo depth default.** The preference field is documented in AD 16.1–17.1 indexing;
  the value is not, on any page.
- **What one undo step contains.** Whether an interactive-routing session collapses to
  one step is unstated.
- **Which Online DRC checks ship disabled** "to improve PCB Editor performance."
- **The locked-object confirmation dialog's exact wording.**
- **Any keyboard shortcut for toggling Locked.** Not on the PCB shortcut-keys page.
- **Auto Save default interval and default version count**; **Local History default
  retention in days.**
- **Annotate dialog defaults** — order of processing, matching options.
