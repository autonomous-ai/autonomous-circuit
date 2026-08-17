# Lens 6 — Discoverability, round 4

Question: can an engineer find a feature without being told? Driven cold —
app on `:4179`, headless mounts of the real `BoardWorkspace` via
`boardWorkspace.test-helper.js` (dispatching real pointer events, not
calling handlers directly), `curl` against the live server, and the
`shortcut-report.mjs` generator plus `shortcutSheet.test.js`. No app code
touched; no example board left modified (`git status --porcelain
examples/hydrate-coaster examples/pixel-badge` is clean after every probe).

**Score: 7/10** (round 2 held this at 7/10; round 3 did not re-score this
lens). Same score, different shape: this round the two round-2 must-fixes
(no app menu off Windows, no Board commands in it) are still both open —
three rounds running now — but I found a new, live, verified must-fix the
prior rounds missed: the plain-language fallback isn't a synthetic worry,
it fires today on the shipped `hydrate-coaster` example.

## What a cold engineer actually finds, driven live

**Getting into edit mode.** The tool rail's Move tool carries
`title="Move parts (edits your board file) (E)"` — hover teaches the
gesture, the consequence, and the key in one string (`ViewportToolRail.jsx:124`,
`viewportTools.js:39`). Confirmed by mounting the real workspace:

```
MOVE TOOL title= "Move parts (edits your board file) (E)"
MOVE TOOL aria-pressed= true
```

**Which parts can move, and why not.** Before touching anything, the edit
strip states the count: `"28 parts and blocks can be dragged · 1 cannot ·
5 need a wrapper to turn"` (driven live on hydrate-coaster,
`placement-edit-count` slot). Clicking a part the file can't move gets a
reason keyed to the actual cause — locked, router-drawn copper, block
membership, or "no pcbX/pcbY in the board file — it is placed in code"
(`placementDrag.js:166-224`). Locked was driven end to end: locking `J1`
via the lock button, then attempting to drag it, put this text right next
to the X/Y coordinate fields on screen:

```
J1 +6 is locked by hand — unlock it to change these numbers
```

That is the correct discoverability shape — the refusal appears where the
user's attention already is, not in a toast they have to notice.

**Rotate.** Not in the right-click menu (checked `boardContextMenu.js` —
no "rotate"/"turn" item exists there at all). Reachable two ways: the
turn buttons in the edit strip once a part is selected (icon + hover
title, e.g. `"Turn R30 90° clockwise — writes pcbRotation={270} (the board
file counts counterclockwise from 0°)"`), or `Space`/`Shift+Space` while
dragging, taught by the shortcut sheet. An engineer who never opens move
mode or the sheet has no path to rotation — the context menu, which is
otherwise the most complete surface in the app, is silent on it.

**Exact coordinates.** Right-click → "Move … by an exact amount…" opens a
dialog with `Ctrl+Q` to flip mm/mil, declared and pinned in
`INLINE_BINDINGS` (`BoardContextMenu.jsx:353`). Typing an out-of-range
value in the Properties panel's X/Y field shows the reason under the box
before Enter is even needed to hit the server:

```
5000 mm is off the board — ±1000 mm is as far as this one goes
```

(`placementFields.js:106-108`, `parseMmField`). Server-side, the same
class of write is refused with a line number:
`"line 74: X is 1e+30mm, which is off any board — positions must be within
±1000mm"` (confirmed live by the round-4 integrity judge; I re-read the
code path rather than re-driving it, since it was proven this round
already).

**Undo.** `title="Put the last change back"`, disabled with the same
title when there is nothing to undo (`PlacementEditBar.jsx:494` region,
driven live: `UNDO disabled? true title= "Put the last change back"`).
Redo mirrors it and names its own keys: `"Do it again (⇧⌘Z / Ctrl+Y)"`.

**The coloured chip.** Two chips exist and both explain themselves without
a doc. The always-on strip under the tabs (`BoardVerdict.jsx`) carries a
literal `"What is this?"` / `"What's left"` button
(`data-slot="verdict-explain"`) next to `"Fix it"` / `"Order at JLCPCB"` —
the gate (`fab.ready`) is the only thing that can make the ready styling
or the Order button appear, so the chip cannot lie about being orderable.
The move-mode verdict chip (`placement-verdict-chip`) opens a detail panel
on click; cold, before any check has run, it reads `"not checked"` with
title `"Ask whether the board, with your changes, is still legal on
copper"` — the exact affordance a first click needs. Once checked, the
panel spells out geometry (`predicted` vs `built`), pending-turn exclusion,
and — new this round — the warning-tier "N of them only a rebuild can see"
line the verdict judge's report describes fixing today.

**Autosave, with no Save button.** Confirmed the claim is real (no `save`
verb anywhere in the placement editor's UI strings) and confirmed the app
does almost nothing to say so. There is a transient `aria-label="Saving"`
spinner while a write is in flight (`PlacementEditBar.jsx:548`) and, after
it lands, a one-line note describing the gesture (`"J1 +6 locked in
place."`, driven live) — but no persistent "saved" state, no checkmark, and
the landing pitch screen (`StartHere.jsx`'s `Pitch`, the first thing a new
user sees) never mentions that edits autosave at all; its only promise is
about the fab-ready gate. An engineer who has never used this app and asks
"did that stick?" gets an inference from a disappearing spinner and a
sentence describing *what* happened, never a sentence that it was *saved*.

**A never-built project.** `pixel-badge`'s installed project (per the
verdict judge, unbuilt: `boards/` holds only `main.tsx`) has a catalog with
one `tsx` entry and no board artifacts — driven live:
`project_catalog_read` returns `{"entries":[{"file":"boards/main.tsx",...}],
"revision":0}`. `selectBoardEntries` finds no board entry from that, so the
workspace falls back to `StartHere`'s generic "Describe a device" pitch —
identical to what a brand-new, empty project shows. An engineer reopening
a project they already asked for gets the first-timer's onboarding screen
with no acknowledgement that a board file already exists and was never
built.

## The shortcut sheet — driven, not read

`node scripts/shortcut-report.mjs` and `shortcutSheet.test.js` (19/19)
both run clean against the current source. `ShortcutSheetHost` is mounted
unconditionally in `BoardWorkspace.jsx:1518`, and the tab-strip button
(`data-slot="board-shortcuts"`) is unconditional too (`:1253-1256`) — the
round-1/round-2 "no entry point" defect stays fixed. I looked for the
reverse case the brief asks about (a binding the app has that the sheet
doesn't, or vice versa) by walking every `keydown`/`onKeyDown` registration
outside `components/board/` (`WindowMenuBar.jsx`, `color-picker.jsx`,
`sidebar.jsx`, `ChatInput.jsx`) — the only board-relevant one,
shadcn's unused `Sidebar` component's `Cmd/Ctrl+B`, isn't mounted anywhere
in the app (`grep` for `SidebarProvider`/`ui/sidebar` imports outside the
component itself returns nothing), so it's dead code, not a live mismatch.
I found no reverse case this round — the generator's own coverage walk
(`shortcutScan.js`'s discovery pass) is doing its job.

**One transient anomaly, not scored.** One run of a probe crashed with
`ReferenceError: state is not defined` inside `resolveBoardKeyRaw`, thrown
from `ShortcutSheetHost`'s mount. It did not reproduce over three
subsequent identical runs, the current source has no such reference, and
`git log`/`stat` show `boardKeymap.js` was mid-edit (uncommitted, +12
lines) at that exact wall-clock minute — almost certainly another judge's
or fixer's concurrent write landing mid-render during a shared-repo panel
round, not a defect in the shipped code. Noted per the rubric's own
housekeeping rule rather than scored.

## Must-fix, ranked

1. **Unmapped finding codes render as a mangled identifier — verified live
   on the shipped `hydrate-coaster` example, not synthetic.** Of the 36
   distinct finding codes on that board's real `main.board.json`, 16 have
   no entry in `plainLanguage.js`'s `ISSUES` map and fall through to
   `plainIssue`'s raw fallback. The most common is
   `supplier_footprint_mismatch_warning` — **27 occurrences**, severity
   `warning`, meaning U4's footprint doesn't match the supplier's real
   part (a genuine fab risk) — which renders in the Messages panel and the
   right-click "Violations here" list as the title `"supplier footprint
   mismatch warning"` with an empty `meaning`. Also unmapped:
   `gerber_silk_over_pad` (the exact kind the round-4 verdict judge flagged
   as typo'd elsewhere in the pipeline), `power_width_widened`,
   `dfm_power_trace_width`, and 12 others. This is the same defect round 2
   found with a synthetic example (`hole_clearance_min`) two rounds ago;
   it is real, live, and on the app's own example board today.
2. **No app menu of any kind renders off Windows** (`main.jsx:272`,
   `isWindowsPlatform()`). Unfixed for three rounds. Partially mitigated —
   the tool rail, right-click menu and always-visible shortcut button carry
   real weight — but a menu-driven engineer on macOS/Linux still finds
   nothing.
3. **Where the menu does render (Windows), it still has no Board menu**
   (`WindowMenuBar.jsx:230-271`) — Edit is text-field Undo/Redo/Cut/Copy/Paste
   only, wired to `document.execCommand`, and has nothing to do with the
   board. A user opening it expecting "Undo my drag" gets silent
   text-field undo instead, which reads as broken rather than absent.
4. **Autosave is real but the app never says so, anywhere.** No "Saved"
   state, no mention in the first-run pitch screen. Confirmed live — the
   only signals are a transient spinner and a gesture-description note
   that never uses the word "saved."
5. **A never-built project reopens as the first-timer pitch screen**, with
   no acknowledgement that a board file already exists. Confirmed live via
   `project_catalog_read` on the installed `pixel-badge` project. (Also
   flagged by the round-4 verdict judge from a different angle — worth
   fixing once, closes two lenses' findings.)

## Should-fix

1. `http.mjs:74` `"internal error"` fallback — unreachable path (no
   message on a caught error), unchanged since round 1/2, low priority.
2. `http.mjs:268` `"an edit is longer than a placement change can be"` —
   still doesn't say what to do next. Unchanged since round 2.
3. Rotation has no entry in the right-click menu — the app's best
   discoverability surface is silent on one of the four things this brief
   asks an engineer to find unaided.

## Worst copy, verbatim, driven live

`"supplier footprint mismatch warning"` — the on-screen title for a real,
27-times-repeated `warning`-severity finding on the app's own shipped
example board (`hydrate-coaster`), with `meaning: ""`. A real EE reading
this has no idea it means a part's footprint disagrees with the actual
supplier's part they're about to pay for.

## The one sentence

Handed only the URL, an EE would find move mode, rotation (eventually, via
the strip), locked/unmovable parts explained in place, undo, and the
verdict chip's own "what is this" button without ever opening a doc — but
they'd have no menu bar unless on Windows, no on-screen confirmation that
their drag was ever saved, and on a real board today they can hit a DRC
finding that renders as a raw code with an empty explanation.
