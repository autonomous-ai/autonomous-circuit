# The board file is the only place a human edit lives

The IDE half of "agents do 99%, a human does the last 1%". This document is the
contract three engineers can build against without asking a question: where a
drag lands, what binds it to geometry, what survives a rebuild, how a human edit
and an agent edit compose, and — said plainly — what the canvas refuses to let
you touch and why.

Every number here carries how it was measured. Claims about our own code cite
`file:line`. Claims about behaviour cite a command and its output. Where I did
not run it, it says so.

Measured against the working tree at `b829662` with the placement write path
uncommitted (`git status`: `BoardWorkspace.jsx`, `PcbCanvas.jsx`,
`viewportTools.js`, `http.mjs` modified; `boardSource.js`,
`usePlacementEditor.js`, `PlacementEditBar.jsx`, `boardSourceWrite.test.mjs`
untracked). `npm --prefix viewer test` at the time of writing: **674 pass, 0
fail**.

---

## The decision: a drag ends as one changed numeric literal in `boards/<stem>.tsx`

Nothing else. No overlay file, no sidecar, no database, no in-memory placement
model. A drag on the PCB canvas produces a byte-range replacement of one
`pcbX`/`pcbY` literal in the board's own source, and the canvas then re-reads
that file rather than trusting its own prediction of what it wrote.

The rule that makes this checkable is one sentence:

> **An edit is durable if and only if it changes `board_source_hash`.**

That is not a new invariant. It is the function the build already uses to decide
whether it can skip work (`generation.py:851` computes it, `generation.py:866`
`_unchanged_prior_result()` short-circuits on it, `source_hash.py:83`
`board_source_hash()` walks the entry TSX plus `product.json`, `parts.json` and
every local import breadth-first).

### The overlay lost on a measurement, not an argument

The rival design — a `boards/<stem>.overlay.json` holding `{refdes, dx, dy}`
patches applied inside the build — is invisible to the cache. Measured, on a
copy of `examples/terminal-keyboard`:

| what changed | `board_source_hash` digest | changed? |
| --- | --- | --- |
| nothing (baseline) | `663c510c5d799fce…f487a24e` | — |
| `pcbX={-45.8}` → `pcbX={-43}` | `3844d57105fc48e1…d1492368` | **yes** |
| a `locked:` comment inserted above the tag | `7977f03fecfe2623…4fe15840` | **yes** |
| a new `boards/main.overlay.json` written | `663c510c5d799fce…f487a24e` | **no** |

Command: `PYTHONPATH=packages/circuitpy/src python3.12` calling
`source_hash.board_source_hash(entry, root)` on a `shutil.copytree` of the
example, once per row.

An overlay file leaves the digest byte-identical, so `_unchanged_prior_result`
returns the prior sidecar and the human's edit produces **nothing at all**. That
is fixable with a second hash key, but the fix is the tell: the overlay is a
foreign body in this pipeline. Two further costs settle it — the agent reads and
writes `boards/main.tsx` and has no reason to open a second file, so the two
diverge silently; and a delta applied after the compile keeps applying after the
agent re-places the part, which is a permanent invisible geometric bias on a
board headed to a fab. A clobber is visible in a diff. A silent offset is not.

So: **one artifact, one author-visible number, no merge protocol.** The human's
`-43` and the agent's `-43` are the same kind of thing in the same file.

---

## Only the number moves, and only after the file on disk proves it has not moved

The write is a compare-and-swap, because the agent editing this same file is the
whole point of the app.

`board_source_write` (`viewer/src/server/circuit/http.mjs:554`) is the only
command in this app that changes a file the user owns, and it is deliberately
the narrowest one. `planSourceWrite` (`http.mjs:191`, exported so `node:test`
drives every refusal without an HTTP server) enforces:

- **The whole file length must match** what the client read (`http.mjs:199`) —
  else `SOURCE_CHANGED`, HTTP 409.
- **Every edit carries the text it expects to replace** (`http.mjs:227`
  `text.slice(start, end) !== edit.expected` → `SOURCE_CHANGED`). A stale offset
  would otherwise land a coordinate in the middle of a comment.
- **At most 8 edits per write, 200 characters each** (`http.mjs:173-175`). A
  drag makes two; the lock comment is the long one.
- **No control characters** except tab, newline, carriage return
  (`http.mjs:220`).
- **No overlapping edits**, and two insertions at one point are refused because
  their order would be undefined (`http.mjs:224`).
- **Only `boards/<stem>.tsx`** — `boardSourceRelPath(file)` gates the shape, the
  resolved path must stay under the project root, and an `lstat` that is not a
  regular file is refused so a symlink cannot walk out (`http.mjs:566-582`).
- **Never while the board is building.** `chat.turnInProgress(projectId) ||
  pipelineRunning(projectId)` → `BUILD_RUNNING`, HTTP 409 (`http.mjs:556`).

The file is written to a sibling temp file and `rename`d (`http.mjs:591-594`):
the catalog watcher can start a build the instant it notices the change, and a
half-written board source is one the next build cannot compile.

The client half is pure text with no DOM and no fetch, so `node:test` covers the
parser, the matcher and the splicer directly (`boardSource.js:25-26`).
`applyEdits` (`boardSource.js:369`) sorts and applies right-to-left so earlier
offsets stay valid, and **throws** on overlap rather than producing a
plausible-looking wrong file.

**Formatting is never touched.** `formatMm` (`boardSource.js:344`) rounds to
three decimals — a micron, two orders below anything a fab holds — and strips
trailing zeros and `-0`. Nothing else in the line is rewritten. Our board files
carry the engineering record in their comments (`terminal-keyboard/boards/main.tsx:268-271`
explains why the status LED sits at `x=-45.8` and what `-48` cost), and a
reformat that ate those would cost more than the drag is worth.

---

## A placement binds to geometry by anchor arithmetic, or it does not bind at all

Nothing in the compiled artifact remembers which line of source it came from —
group names come out as `unnamed_group7`, and `circuit.json` has no file or line
field anywhere. So the link is arithmetic, and it has to be exact.

`bindPlacements` (`boardSource.js:502`) matches a parsed placement's
`(pcbX, pcbY)` against two candidate sets keyed to 0.1 µm
(`KEY`, `boardSource.js:436`, rounds to 4 decimal places):

- **`pcb_group.anchor_position`** for a block instance or a `<group>` — a tag
  written `pcbX={14} pcbY={-18}` compiles to a group anchored at exactly
  `(14, -18)`.
- **`pcb_component.center`** for a part the board file placed itself.

Both are filtered to direct children of the root group (`boardSource.js:523`,
`:529`), matching the parser, which only reads placements at `depth === 0`
inside `<board>` (`boardSource.js:296`).

A third set covers geometry a board line places with no component behind it — a
`<MountingHole>`'s drill, a `<silkscreentext>` label (`LOOSE_TYPES`,
`boardSource.js:445`). These never decide *which* placement owns a point; they
are only how a placement finds its drawing.

**Copper is deliberately absent from every candidate set** (`boardSource.js:442`):
a trace or a via is something the router made, never something a line of the
board file put at that point.

Three ways to refuse, each with a reason the UI shows verbatim
(`boardSource.js:556`, `:561`, `:604`):

- two placements in the file on the same exact spot → *"two things in the board
  file sit on this exact spot"*
- one placement matching two anchors → *"this spot matches more than one thing
  on the board"*
- no anchor, or an anchor that draws nothing → *"nothing on the built board sits
  where this line says"*

**Match, never guess. A drag that writes to the wrong element is worse than a
drag that refuses.**

### On our three real boards the binder refuses nothing

Ran the shipped `parseBoardSource` + `bindPlacements` against each example's
real `main.tsx` and `main.circuit.json`:

| board | placements parsed | bound | unmatched | skipped |
| --- | --- | --- | --- | --- |
| terminal-keyboard | 12 | **12** | 0 | 0 |
| harness-puck | 17 | **17** | 0 | 0 |
| hydrate-coaster | 28 | **28** | 0 | 0 |

Zero ambiguity on any board we ship. The anchor is not a heuristic that mostly
works; on our corpus it is exact.

---

## The geometry a placement is bound to comes from the last build, and is carried by id

This is the subtlety that will bite anyone re-deriving it.

After a drag, the file says `-43` and the built board still says `-45.8`. That
disagreement is the honest state until a rebuild. So geometry is captured once
per **build** and carried forward by placement id through every edit after it
(`usePlacementEditor.js:124-148`), never re-matched by coordinate.

The key is `buildKey` — the `circuit.json` URL, which carries
`?v=<mtime>-<size>` — and explicitly **not** the `index` object's identity.
Writing the source bumps the catalog revision, which makes the workspace refetch
`circuit.json` and hand back a fresh `index` object with identical contents;
keying on identity re-derived geometry by coordinate right after a drag and
unbound the part that had just been moved. One move per part, and no undo. The
comment at `usePlacementEditor.js:132-137` records that this was caught by
dragging a real board twice and was invisible to every unit test.

---

## An edit survives a rebuild, and moves exactly the frame it names

Measured, not asserted. Two copies of `examples/terminal-keyboard`, identical
but for one literal and one lock comment, each compiled placement-only:

```
tscircuit-cli build boards/main.tsx --routing-disabled --disable-parts-engine
  base: exit=0 wall=20.9s  137 pcb_components
  edit: exit=0 wall=19.7s  137 pcb_components

keys equal: True
MOVED 2 of 137 components
  pcb_component_132 = LED1: (-45.8, -32)   -> (-43, -32)     delta=(2.8, 0)
  pcb_component_133 = R20:  (-45.8, -29.5) -> (-43, -29.5)   delta=(2.8, 0.0)
```

The edit was `<StatusLed … pcbX={-45.8}>` → `pcbX={-43}`, plus the lock comment
inserted on the line above. Exactly the two parts that block owns moved, by
exactly the distance asked for, and the other 135 did not move at all.

Placement is stable under rebuild because no repair pass writes a
`pcb_component` center — the five passes at `generation.py:980-1044`
(`circuit_normalize`, `router_bridge`, `diffpair`, `powerwidth`,
`pour_clearance`) rewrite copper only. **Placement is stable; copper is not.**
That is exactly the line this contract refuses along.

### The same run proves the lock comment is inert to the compiler

The edited board carried
`{/* locked: placed by hand - do not move this without asking */}` above the tag
and still compiled to 137 components. Re-parsing the edited file and binding it
against its own fresh `circuit.json` returns:

```
placements 12  bound 12  unmatched 0
StatusLed: {"x":-43,"y":-32,"locked":true,"label":"LED1 +1","refdes":["LED1","R20"]}
```

The loop closes: write → compile → re-bind at the new position, with the lock
read back off disk.

### The same run also proves "saved" is not "legal"

The base build produced **no** `*_error` elements. The edited build produced
**six**:

```
pcb_footprint_overlap_error:  pcb_smtpad C2.pin1 overlaps with pcb_smtpad LED1.pin1
pcb_footprint_overlap_error:  pcb_smtpad C2.pin1 overlaps with pcb_smtpad LED1.pin2
pcb_pad_pad_clearance_error:  Pads .C2>.pin1 and .LED1>.pin1 too close (0mm, min 0.1mm)
pcb_pad_pad_clearance_error:  Pads .C2>.pin1 and .LED1>.pin2 too close (0mm, min 0.1mm)
pcb_courtyard_overlap_error:  Courtyard of U2 overlaps with courtyard of R20
pcb_courtyard_overlap_error:  Courtyard of C2 overlaps with courtyard of LED1
```

Both builds exited **0**. This is the house rule in its natural habitat: *never
trust an exit code* (CLAUDE.md — `tscircuit-cli build` exits 0 with real errors;
every gate parses produced artifacts). A confirm loop that diffs component
centres and stops there would have called this drag a success.

The board's own source predicted the collision. `main.tsx:268-271` records that
`x=-45.8` "still leaves 0.44mm of courtyard between R20 and the LDO". Dragging
2.8 mm east spends that 0.44 mm and puts LED1 on top of the LDO's input cap.

**Therefore three words, never interchangeable:**

- **saved** — the literal is in the file. Immediate, and all a write can claim.
- **legal** — a build produced no `*_error` elements. Placement-only confirm is
  **~20 s** (measured above, 137 parts, the largest example).
- **orderable** — the full gauntlet ran: gerbers, BOM gate, KiCad DRC. Minutes,
  and the only claim that may be attached to a fab packet.

If the UI ever spends one word for two of these, the feature lies.

---

## The merge rule: a comment in the file, and it is a convention, not a lock

`LOCK_COMMENT` (`boardSource.js:34`) is written on the line above the element:

```jsx
{/* locked: placed by hand - do not move this without asking */}
<StatusLed rail="V3_3" led="LED1" r="R20" pcbX={-43} pcbY={-32} … />
```

A lock has to survive into the board file or it is not a lock — the next agent
reads `boards/main.tsx`, not this app's memory. So it is visible in the file,
visible in the diff, visible to the model, and inert to the compiler (proven
above). `lockEdits` (`boardSource.js:415`) inserts and removes it; `readLock`
(`boardSource.js:331`) reads it back with `LOCK_LINE_RE`.

The rebuild request names the convention (`BoardWorkspace.jsx:512-514`):

> "Keep every placement I set — do not re-place parts to make routing easier,
> and never move a placement that carries a `locked:` comment above it. If a
> placement I chose makes the board unroutable, say so and show me the evidence
> rather than moving it back."

**Say plainly what this is.** It is a prompt-level convention. It is not a lock,
not a file permission, and not a merge algorithm. Nothing prevents the agent
from re-placing a hand-placed part; the comment only makes the override obvious
in the diff afterwards, and requires the agent to delete a line that says a
human chose this.

Three things make that honest rather than wishful, and **only the first two are
built**:

1. **Built.** The write refuses while a turn is in progress (`http.mjs:556`), so
   a human and the agent cannot write the file in the same instant.
2. **Built.** The compare-and-swap means an agent edit that lands first turns the
   human's next drag into a `SOURCE_CHANGED` refusal and a reload, not a silent
   clobber of the agent's work.
3. **NOT BUILT — the gap that must close before this ships.** The lock
   convention exists only in the per-turn rebuild string at
   `BoardWorkspace.jsx:512`. `grep -n "locked:" skills/circuitcode/SKILL.md`
   returns **nothing** (the only hit for "locked" in that 493-line file is
   `parts.json` at line 64, unrelated). An agent invoked any other way — a
   direct chat message, a review round, a fix turn — has never been told the
   convention exists. Until `SKILL.md` carries it, the merge rule holds only on
   the one code path that happens to mention it.

A further known cost, measured above: **inserting a lock comment changes
`board_source_hash`** (`663c510c…` → `7977f03f…`). A lock is a no-op for
geometry but invalidates the build cache. `usePlacementEditor.js:163-166` is
already honest about the geometry half — a lock counts `delta = 0` toward
"changes waiting on a rebuild", because offering to spend minutes rebuilding for
a comment would be a lie about what changed. It does not change the fact that
the next build the user *does* ask for cannot be short-circuited.

---

## What a user can and cannot edit on terminal-keyboard today

The largest example: 100 mm board, 137 parts, 5,463 elements in
`boards/main.circuit.json`. Counts below come from running the shipped
`parseBoardSource` + `bindPlacements` against the real files, not from grep.

### The twelve things that are draggable

| line | tag | position | binds | parts moved | drawables moved |
| --- | --- | --- | --- | --- | --- |
| 252 | `<Rp2040Core>` | (0, −25) | group | 22 | 296 |
| 260 | `<UsbCData>` | (−23, −40.5) | group | 7 | 86 |
| 264 | `<Ldo3v3>` | (−40, −27) | group | 3 | 25 |
| 272 | `<StatusLed>` | (−45.8, −32) | group | 2 | 15 |
| 275 | `<DebugPort>` | (38, −33) | group | 3 | 9 |
| 314–319 | `<MountingHole>` H1–H6 | ±47/0, ±42 | group | 0 | 1 each |
| 324 | `<silkscreentext>` | (24, −42.6) | loose | 0 | 1 |
| | | | | **37** | **438** |

**12 draggable handles. 0 unmatched. 0 skipped.**

### The honest coverage number is 27%

**37 of 137 components (27.0%)** can be moved by dragging something. Of the 2,014
drawable PCB objects, **438 move with a drag and 1,576 do not.**

The 1,576 split cleanly in two:

- **465 are copper** — 252 `pcb_trace` + 213 `pcb_via`. Refused by design; see
  below.
- **1,111 belong to the 100-key field** — 300 `pcb_smtpad`, 308
  `pcb_solder_paste`, 250 `pcb_silkscreen_path`, 153 `pcb_silkscreen_text`, 100
  `pcb_courtyard_rect`. These are the 50 diodes and 50 switches emitted by
  `keyCells()` (`main.tsx:99-163`) with `pcbX={x - 1.4}` and `pcbX={x}` where
  `x = colX(c)`. There is no literal for one key, so there is nothing to write.

**`137 − 37 = 100`.** Every unreachable component on this board is a generated
key. Nothing else is missing.

### Not a single-part edit exists on this board

Every bound placement with parts in it holds 2 or more (22, 7, 3, 3, 2). Drag
`<StatusLed>` and both LED1 and R20 move, because the board file's only handle is
the block's frame anchor. The UI must say so **before** the drag commits — the
existing label already does this: `placementLabel` renders `"LED1 +1"`
(`boardSource.js:691`, verified in the run above).

### The other two boards are much better, and that is the point

| board | components reachable | draggable handles | single-part handles |
| --- | --- | --- | --- |
| terminal-keyboard | 37/137 (**27.0%**) | 12 | 0 |
| harness-puck | 44/61 (**72.1%**) | 17 | 4 |
| hydrate-coaster | 44/44 (**100%**) | 28 | 8 |

On hydrate-coaster the only drawn objects that do not move with some drag are
113 traces, 108 vias and 8 stray solder-paste shapes — i.e. copper and nothing
else. Coverage is not a property of the mechanism; it is a property of how the
board was written. A board composed of literal-placed blocks is fully editable.
A board with a 50-iteration placement loop is editable everywhere except the
loop.

---

## What this refuses, and why each refusal is structural

These are not policy choices to be relaxed later. In each case there is no
number in the board file to write.

**Traces and vias.** 465 objects on terminal-keyboard, 280 on harness-puck, 221
on hydrate-coaster. Our board sources contain no primitive that expresses route
geometry — `<trace from= to=>` is a netlist edge, not a path — and every id is
ordinal (`pcb_via_0`), so inserting one part upstream renumbers everything after
it. Five passes rewrite all copper on every build
(`generation.py:980-1044`). A dragged trace would be a drawing, not an edit.
`LOOSE_TYPES` (`boardSource.js:445`) excludes copper for exactly this reason.

**Pads inside a footprint.** 300 of terminal-keyboard's 454 `pcb_smtpad` belong
to components the board file cannot address. Moving one pad of a QFN is a
footprint change, not a board change, and the footprint is named by a string
from an imported land pattern, not authored in the project.

**Anything inside a golden block.** Moving C2 inside `<Ldo3v3>` means editing
`blocks/ldo-3v3/ldo-3v3.tsx`, which is byte-identical across all three example
projects. That edit silently forks a graded, testbenched shared part and, via
`source_hash.py`'s import walk, invalidates every board in the project. The
parser's `depth === 0` guard (`boardSource.js:296`) makes this structurally
impossible rather than merely discouraged: the draggable unit is a direct child
of `<board>`.

**Loop- and trig-placed parts.** The 100 keys above; harness-puck's 16 ring
pixels from `RING_R * Math.cos(rad(theta))`. The nearest literal has a blast
radius measured by scout:source at 100 of 137 parts and 16 new blocking errors
for a single `PITCH` change from 10 to 10.5. Refusing is correct.

**Copper pours, at any distance.** Terminal-keyboard has 31, all on `bottom`,
with 56 inner cutouts carved for the pre-move geometry. scout:server proved
nothing in the pipeline measures anything against a pour: a via moved 1 mm into
solid GND copper — verified inside the outer ring and inside no cutout by
point-in-polygon — was reported clean by the fast Python checks, the 2.3 s node
checks, the 2.4 s `pour_clearance` pass **and** the 13.5 s KiCad DRC leg (646
findings, 0 error, identical to the untouched board). **We must not offer an
edit whose defects no gate can see.** This is a hole in the check set that a
full build does not close either, and it deserves its own fix independent of
this work.

**Rotation, for now.** The write path implements move and lock only
(`usePlacementEditor.js:269-271` exports `move`, `setLock`, `undo`). The
rotation design is specified separately in `ide-altium-parity.md` ("Spacebar
rotates the part"); it lands as a third edit kind on the same mechanism —
`pcbRotation` is a literal in the same tag — and nothing in this contract needs
to change to accept it. Arbitrary angles will still be refused: 439 of 454 pads
are `shape:"rect"` with no rotation field and `pcb_courtyard_rect` carries only
centre/width/height, so 90° steps are the only honest offer.

**Any edit while a historic revision is on screen.** `canEdit = editing &&
!viewing` (`BoardWorkspace.jsx:461`) and the editor is gated on it
(`:467`). Without this a user would drag a part on a build from twenty minutes
ago and write that coordinate into current source.

---

## The exact list of supported edit kinds

Everything the contract accepts, and nothing else:

1. **Move a block instance by its frame anchor.** `<Rp2040Core>`, `<UsbCData>`,
   `<Ldo3v3>`, `<StatusLed>`, `<DebugPort>` and every other direct child of
   `<board>` carrying literal `pcbX`/`pcbY`. Moves 2–22 parts as a unit; the
   count is shown in the label before the drag commits. 11 of terminal-keyboard's
   12 handles, and the majority of real placement decisions on all three boards.
2. **Move a part the board file placed itself**, where the tag is a direct child
   of `<board>` with literal coordinates and its compiled `pcb_component.center`
   matches. 0 on terminal-keyboard, 2 on harness-puck, 7 on hydrate-coaster.
3. **Move a mounting hole.** All 6 of terminal-keyboard's; 13 across the three
   boards. Binds through `LOOSE_TYPES` as a group owning no parts.
4. **Move authored silkscreen text.** 1 on terminal-keyboard, 4 on harness-puck,
   11 on hydrate-coaster. The safest edit on the board — it touches no copper.
5. **Lock or unlock a placement.** Writes/removes `LOCK_COMMENT` above the tag.
   Counts 0 toward pending-rebuild changes.
6. **Undo, up to 50 deep, per board file.** `usePlacementEditor.js:231-246`;
   history is dropped when the selected file changes (`:113-117`) rather than
   offering an undo that would land on a different file.

Snapping is on the **delta**, not the absolute coordinate (`snapDelta`,
`boardSource.js:358`) over `SNAP_STEPS = [1, 0.5, 0.25, 0.1]` mm with
`FINE_STEP_MM = 0.01`. Absolute snapping is what Altium does and it is wrong for
our boards: a 2.54 mm header nudged onto a 0.5 mm grid lands at 2.5 and quietly
loses its pitch.

---

## What is not built

Stated plainly so nobody reports a half-built thing as done.

- **`SKILL.md` does not know about `locked:`.** Measured above. This is the one
  item that must land before the feature can claim agent/human composition at
  all.
- ~~**There is no confirm loop.**~~ **Landed 2026-08-16.** `board_edit_apply`
  (`http.mjs`) takes a semantic edit, writes it, and returns a verdict from
  `circuitpy.fastcheck` — the Python leg plus the node subset minus
  `checkTracesAreContiguous`, with `checks.trace_anchor_warnings` standing in
  for it. Measured on terminal-keyboard through the HTTP command: **1,222 ms**
  for save + verdict, and the 2.8 mm `<StatusLed>` drag reports all six of the
  blocking errors the 20 s A/B compile produced, plus four
  `trace_left_its_pad` the compile does not report at all. It still cannot see
  the pour, and says so in `check.notChecked` on every response. It grades
  **predicted** geometry (`check.geometry === "predicted"`), so it may reach
  *legal* and may never say *orderable*; the ~20 s placement-only compile is
  still the only thing that grades what the compiler actually produces, and
  nothing runs it automatically.
- **No rotation.** Specified in `ide-altium-parity.md`, not implemented here.
- **No two-human and no two-session story.** The compare-and-swap makes a
  conflict *visible* (`SOURCE_CHANGED` → reload); it does not merge anything, and
  the project workspace under `~/.autonomous-circuit/projects/<uuid>` is not a
  git repo, so there is no undo underneath the app's own 50-entry history.
- **I did not run a full build in this session.** Every timing here is the
  placement-only compile (`--routing-disabled --disable-parts-engine`), measured
  twice at 20.9 s and 19.7 s on the 137-part board. The 15–45 minute full-build
  figure is carried from the brief, not re-measured.
