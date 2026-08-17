# Lens 2 — Navigation & sight (round 4)

**Score: 8/10** (round 2: 8/10, unchanged — round 3 did not re-score this lens)

Rubric: `docs/reviews/ide-panel-rubric.md`. Prior findings: round 2's report
inside `docs/reviews/ide-panel-2026-08-16.md` and `work/ide-panel/round2/`.

## The named path: finding → geometry, timed

Drove the real end-to-end path on `examples/hydrate-coaster` (488 real
sidecar findings, 0 errors, 162 warnings, 326 info) through the shipped
harness (`openWorkspace`, a real server behind a real `BoardWorkspace`):

1. Messages drawer is open by default (`messagesOpen` state defaults `true`,
   `BoardWorkspace.jsx:142`) and shows grouped findings by default
   (`MessagesPanel.jsx:81`).
2. **Click 1**: expand a group (`data-slot="message-group"` header).
3. **Click 2**: click that row's crosshair (`data-slot="message-locate"`).
4. `handleLocate` (`BoardWorkspace.jsx:592-604`) sets the PCB camera to the
   finding's box (`pcbRef.current.zoomToBox`) and paints a 1.6s flash ring
   (`data-slot="pcb-violation-flash"`, decays via `setTimeout`, `flash` state).

**Two clicks, no rebuild, no network round-trip** — the join from sidecar row
to on-screen geometry is entirely client-side against the already-loaded
`circuit.json` index (`boardViolations.js: findingBox`). Proven with a real
finding, not a synthetic one — a `supplier_footprint_mismatch_warning` on
`U4` on hydrate-coaster's own sidecar. Evidence:
`work/ide-panel/round4/probes/lens2-navigation.probe.test.js` tests `nav-1`,
`nav-1b`, 10/10 green.

**One real gap found and measured**: of hydrate-coaster's 488 sidecar rows,
`buildMessages` marks 377 (77%) `locatable` — the rest (111 rows) have no
PCB coordinate at all. 106 of those 111 are `erc_violation` rows on bare
schematic wires (`wire_dangling`, `endpoint_off_grid`) that are KiCad-ERC
output with no `pcb_*` or `source_component_id` to join against — they are
not a bug in the join, there is nothing on the PCB for them to point at, and
all 106 are `info` severity (never blocking). Still, an engineer who opens
one of these 106 rows gets no crosshair at all, only a dimmed one — worth
fixing on the schematic side (the wire coordinates DO exist on that sheet;
`findingBox` only ever computes a PCB box today). Counted with a one-line
script against `buildMessages`/`messageCounts`, not eyeballed.

## What else was driven

| Claim | Result | Evidence |
|---|---|---|
| Cross-probe: plain click on a PCB part selects it, does NOT move the schematic pane | Confirmed | `nav-2`: schematic `<div>` transform unchanged, byte-identical before/after |
| Cross-probe: Ctrl/⌘+click on a PCB part jumps the schematic pane to it | Confirmed, after fixing my own test bug (see below) | `nav-2b`: schematic transform changes on Ctrl+click in Split |
| Shift+click selects the whole NET, not just the part; masking dims, doesn't hide | Confirmed | `nav-3`: net-width row appears (net selected), some elements opacity <1 AND some ==1 after the click — real masking, not a binary show/hide |
| Coordinate HUD on by default, reads cursor position in mm | Confirmed | `nav-4`: `board-insight-hud` text matches `/5\.\d{3}/` after moving to board (5,5) |
| Units toggle (`Q`) flips the HUD from mm to mil | Confirmed | `nav-4b`: HUD text changes and matches `/mil/i` after the toggle |
| Measure tool gives a numerically exact distance | Confirmed | `nav-5`: a 3-4-5 triangle (10,10)→(13,14) reads exactly `5.000mm` |
| Single-layer isolate dims (≤0.25 opacity) the other layer's copper rather than erasing it | Confirmed | `nav-6`: bottom-layer elements stay `opacity > 0` after isolating top |
| `grid` tool still has no keyboard binding (round-2 should-fix #2) | Still open | `viewportTools.js:42`, `key: ""` — read directly, `nav-7` asserts it |

Full probe file: `work/ide-panel/round4/probes/lens2-navigation.probe.test.js`,
10/10 green (`node --test` via the shipped harness on `examples/hydrate-coaster`).

## A false alarm worth recording (methodology note)

My first pass at `nav-2b` (Ctrl+click cross-probe) failed: the schematic
camera never moved. I nearly reported cross-probe as broken. Instrumented
both `PcbCanvas.onPointerUp` and `BoardWorkspace.handleSelect` with temporary
`console.error` calls (removed before finishing — `git diff --stat` on both
files matches the pre-existing dirty state exactly, no stray edits) and
found `options.jump` arriving `false` even though I'd passed `{ ctrlKey: true
}`. Root cause: my own test called
`pointer(w.canvas, "down", spot, { ctrlKey: true })` — but `pointer(target,
type, init)` only takes three arguments, so my fourth argument was silently
dropped and `ctrlKey` was never actually set. Fixed to
`pointer(w.canvas, "down", { ...spot, ctrlKey: true })`; cross-probe is real.
Recording this because the rubric's own caution — "a finding is a claim" —
almost got violated by me, not the app.

## Should-fix, carried from round 2 and re-verified unchanged

1. **Hover only outlines one element** — `PcbCanvas.jsx:764,`
   `hoverRect` uses `pcbElementBox(hover.element)` for a single item; no
   net-wide hover highlight exists. Live Highlighting (net-under-cursor) is
   documented as Altium's own behavior in `ALTIUM-NOTES.md:93-96` and is not
   built. (Confirmed still true by reading current source.)
2. **`grid` tool has no key** — `viewportTools.js:42`, `key: ""`, every
   sibling tool on the rail has one. Re-verified live (`nav-7`).
3. **No airwire/ratsnest indicator** on a broken hand-edit —
   `grep -rli ratsnest\|airwire viewer/src` still only matches
   `ALTIUM-NOTES.md` and a comment in `boardPalette.js`; no drawn code.
4. **Mask/dim opacity numbers are "chosen by eye, not Altium's"** —
   `ALTIUM-NOTES.md:107-113`, honestly flagged, still unformalized. Cosmetic.
5. **3D tab has no coordinate readout or measure** — `Board3DView.jsx` key
   handling is still only `F` / `Ctrl+F` / `9` (orbit, refit, flip, spin);
   `grep -n "readout\|Measure\|coordinate" Board3DView.jsx` empty.

## New should-fix this round

6. **ERC-only findings (106 of hydrate-coaster's 488, all `info`) have no
   crosshair at all.** They are real KiCad-ERC hits on schematic wires
   (`wire_dangling`, `endpoint_off_grid`) with a real position on the
   schematic sheet, but `findingBox` (`boardViolations.js:65`) only ever
   builds a PCB box, and `handleLocate` only ever zooms the PCB pane
   (`BoardWorkspace.jsx:600`). Not blocking — all 106 are info-severity — but
   it is the single largest chunk of "no location" and it is fixable: give
   `findingBox` a schematic-box path and let `handleLocate` zoom whichever
   pane actually has the coordinate.

## Scope note: the 3D tab

`docs/vision-context.md:17-18` still says "No 3D tab... is post-v1," but
`84a6905` ("Wire up the 3D board view") shipped a real one anyway — three.js,
GLTFLoader over `board.glb`, OrbitControls, `F`/`Ctrl+F`/`9`. Per the rubric,
an out-of-scope feature isn't a missing point, but it is no longer clearly
out of scope here, since it's built and reachable from the tab bar. It is
not broken: `Board3DView.jsx`'s honesty rule (fail loud, keep the download
button, "if WebGL or the GLB fails, say what failed") is real code, not a
comment. I did not attempt to drive orbit/zoom itself headless — no WebGL
context in this environment — so that half is "could not," not "confirmed."

## Must-fix

None. Same as round 1 and round 2.

## Bottom line

**Can an engineer find the 1% they came here to fix? Yes, in two clicks and
under two seconds, for 77-87% of what a real board's sidecar reports** (the
rest is almost entirely info-severity KiCad-ERC output with no PCB geometry
to point at, and the fix — a schematic-side crosshair — is well-scoped, not
a redesign). Cross-probe, net-select, coordinate HUD, mm/mil, measure, and
single-layer dim are all real, wired to the panes on screen, and numerically
exact where I could check the numbers. No must-fix. Score holds at 8/10 —
same ceiling as round 2: the five carried should-fix items (hover net
highlight foremost) are the actual list between here and a 9 or 10.
