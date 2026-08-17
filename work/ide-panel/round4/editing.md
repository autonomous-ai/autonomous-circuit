# Lens 3 — Editing, round 4, 2026-08-17

**Score: 6/10** (round 2: 7, round 3: 8 — down this round)

Round 3 left two named gaps and one carried must-fix. This round verifies all
three live, plus two of round 3's own must-fixes:

| # | Claim from round 3 | Status | Evidence |
|---|---|---|---|
| A | net-width edit has no undo entry | **Fixed** | `30b1ad7`. `usePlacementEditor.js:658` now `pushHistory({kind:"width",...})`. Re-ran `round3/probes/lens3-round3.probe.test.js`: the assertion that Undo *stays disabled* after a Set now fails (7/8 pass, the 1 fail is the old bug's own assertion, inverted). |
| B | Set not gated at the measured ceiling | **Still true** | `NetWidthRow.jsx:109-117`: `disabled={!draft.trim() \|\| busy}`, no ceiling check. `netWidth.js` `widthEdits()` (server-side plan) has no ceiling clamp either — grepped every `ok: false` branch, none mention a ceiling. Re-ran the round-3 probe's "Set is NOT disabled over ceiling" case: **passes**, i.e. the gap is confirmed open, unchanged since round 3. |
| C | `board_edit_apply` has no client caller | **Declared, but the declaration is not honest** | see below |
| D | placement ids are positional | **Still true, and now shown to be worse than "still true"** | see below |

## C — `board_edit_apply`'s AGENT_ONLY entry is a rationalisation

`commandsAreWired.test.mjs:45-51` now lists it:

> "the agent-facing edit primitive: whole-placement moves by name, used by
> the circuitcode skill and by scripts."

That sentence is checked for length (`>40` chars) only, not for truth. It
isn't true. `grep -rln "board_edit_apply" skills/ packages/ scripts/` —
**zero hits**. `skills/circuitcode` has no reference to `4179`, `VIEWER_PORT`,
or any `/api/` route at all — the skill edits `boards/<stem>.tsx` directly
with its own file tools and calls `circuitpy.build_board()` in-process; it has
no HTTP client to the viewer server, so it structurally cannot be "using"
`board_edit_apply` today. The endpoint's own test file agrees with me, not
with the allowlist comment: `boardEdit.test.mjs:559` — *"`board_edit_apply` —
the endpoint no client uses."* `docs/architecture/ide-edit-contract.md:483`
marks it landed as an agent+verdict primitive but never claims the skill
calls it.

Round 3 asked whether this is "an honest resolution or a rationalisation."
It's the second one. The test now proves someone *wrote a sentence*, not that
the sentence is correct — which is precisely the failure mode
`commandsAreWired.test.mjs`'s own doc comment (`app_set_effort`, `PadHeader`,
`editEngine.js`) says it exists to catch. It just moved one level up: instead
of a silent join, there's now a documented join with a false justification.

## D — positional ids: built the case, and it is a real silent-write bug, not a label glitch

hydrate-coaster: four resistors, file order R30/R31/R32/R33 → ids
`resistor[1..4]`. Scenario: human clicks R32 (`resistor[3]`); an agent, in the
same session, inserts a new resistor ahead of R30 and writes the file — no
rebuild in between, so the geometry snapshot (`usePlacementEditor.js:219-238`,
keyed by id, refreshed only on a new `buildKey`) is now stale. New file order:
R99(new)/R30/R31/R32/R33 → ids `resistor[1..5]`. `rebindPlacements`
(`boardSource.js:1129`) matches each fresh id against the stale snapshot, so
every resistor but the last inherits its **neighbour's** built geometry.

Drove it with the real harness (`viewer/src/client/components/board/__tests__/boardWorkspace.test-helper.js`,
`server.agentWrites()` + `w.ui.set({manifestRevision: 99})`, the same
mechanism the shipped "undo refuses to overwrite an agent's edit" test uses).
New probe: `work/ide-panel/round4/probes/lens3-positional-ids.probe.test.js`,
2/2 pass, real output quoted:

```
BEFORE insert, selected: R32 · moves 1 part
AFTER insert, Properties panel now shows: R31 · moves 1 part      # label already wrong, no warning anywhere on screen

# Mouse drag on what still LOOKS like R32 on the canvas — self-corrects:
last write edit: {"edits":[{"start":5423,...,"expected":"-8"},{"start":5433,...,"expected":"2"}], "summary":"R32 moved 5, 5 mm"}
# because PcbCanvas re-hit-tests the compiled board by componentKey at the new
# pointer-down (BoardWorkspace.jsx:1074, onPlacementSelect), which is real,
# immutable geometry — the stale id map never gets asked.

# Ctrl+ArrowRight on the SAME stale selection — does not self-correct:
Ctrl+ArrowRight after the insert:
  R31 now at: 2.5 -6   (was 2, moved)
  R32 now at: -8 2     (unchanged — the part the human actually clicked)
last write: {"edits":[{"start":5272,"end":5273,"text":"2.5","expected":"2"}], "summary":"R31 moved 0.5, 0 mm"}
```

The nudge path (`BoardWorkspace.jsx:1006-1013`, `selectedPlacement =
editor.placements.byId.get(activePlacementId)`) never re-hit-tests — it trusts
the id captured at the original click. `PropertiesPanel`'s typed-X/Y commit
and the rotate CW/CCW toolbar buttons take the identical `placement`/
`activePlacementId` prop (`BoardWorkspace.jsx:1036`, `:1437`), so the same
misdirection applies to them by construction, not just to the arrow key.

So: a human selects a real part, an agent inserts one earlier element of the
same tag anywhere in the file, and with **zero refusal, zero warning banner,
and a Properties panel that already silently relabelled itself**, the next
Ctrl+arrow, typed coordinate, or rotate click edits a *different, real
component* than the one on screen under the selection box — while the part
the human actually meant to touch sits untouched. This is a data-integrity
bug with an editing-lens trigger, not a cosmetic label mismatch: the file
changes, correctly formed, at the wrong element. It's also inconsistent by
input method — mouse drag is accidentally safe, keyboard/typed edits are not
— which makes it worse to reason about, not better.

## What else was driven and held up

- Click-select on the PCB canvas: correct, `data-slot="property-placement-summary"` matches every time it wasn't racing the bug above.
- Rotation still writes `pcbRotation={270}` for one CW turn and the button
  explains it inline (`PlacementEditBar.jsx:244-257`) — round 1's fix holds.
- A drag that leaves the canvas cancels rather than commits:
  `PcbCanvas.jsx:684-690` `onPointerLeave` clears `dragRef`/`move` with no
  write. Read, not driven this round — round 1/2 already drove the in-bounds
  drag/undo paths and nothing here changed them.
- Typed X/Y is always mm regardless of the mil toggle, but says so inline
  (`PropertiesPanel.jsx:216-219`): "millimetres — the board file is mm
  whatever the units button says." Carried from round 3 as a real trap for
  anyone who trusts the toggle over the fine print, but it is honestly
  labelled, not silent — downgraded to should-fix.
- Held Ctrl+arrow no longer eats keystrokes (`bf92bac`, integrity's find,
  editing-relevant): 4 unawaited presses → 4 writes, 4 steps. Re-verified
  live inside my own probe run (separate from `bf92bac`'s own test).
- A part placed by code, not a literal (`terminal-keyboard`'s D1), still
  cannot be dragged and Properties still says which part and why — unchanged
  from round 2/3, not re-driven this round (no code touched that path).

## Must-fix, ranked

1. **Positional ids silently misdirect a keyboard nudge, a typed coordinate,
   or a rotate-button click onto a neighbouring part** after any concurrent
   insert of an earlier same-tag element — no refusal, no warning, and the
   mouse-drag path (which self-corrects) hides how real the gap is. Fix needs
   a stable id (content hash or agent-assigned key) or, cheaper, invalidating
   the geometry snapshot whenever the placement *count* changes rather than
   only on a rebuild.
2. **`board_edit_apply`'s AGENT_ONLY reason is false as written** — nothing
   in `skills/`, `packages/`, or `scripts/` calls it. Either wire a real
   caller or rewrite the reason to say what's actually true ("built for a
   future agent-verdict loop, not yet wired to any client").
3. **Trace-width Set writes over the measured ceiling with no gate**, just a
   co-displayed warning — carried unfixed from round 3.
4. mm/mil: typed coordinates ignore the units toggle. Honestly hinted, so
   downgraded from round 3's must-fix, but still worth a should-fix at minimum.

## Should-fix

- No multi-select / align / distribute (unchanged since round 1).
- The positional-id snapshot-staleness class above should get a regression
  test in the shipped suite, not just this round's probe file.

## Evidence inventory

- `work/ide-panel/round3/probes/lens3-round3.probe.test.js` re-run:
  `cd viewer && node --test --experimental-strip-types --no-warnings=ExperimentalWarning --import ./scripts/testHooks.mjs ../work/ide-panel/round3/probes/lens3-round3.probe.test.js`
  → 7 pass, 1 fail (the fail is the fixed bug's own assertion, inverted — good news).
- New probe, this round: `work/ide-panel/round4/probes/lens3-positional-ids.probe.test.js`
  → 2/2 pass, output quoted above.
- `git show 30b1ad7 --stat`, `git show bf92bac --stat` — the two fixes credited above.
- No application code touched. No example board or project workspace touched
  — every probe ran against the in-memory `fakeServer` the harness provides
  (`boardWorkspace.test-helper.js`); `examples/hydrate-coaster/boards/main.tsx`
  on disk is unmodified (`git status` clean for that path).

**Could an EE make the five edits a real board needs without opening a text
editor?** Move, rotate, nudge, type-a-coordinate and set-a-trace-width all
land in the file today — but only as long as nothing else touches the file in
between, and this app's whole premise is that something else (an agent) does.
The moment it does, editing goes from "byte-exact" to "silently edits your
neighbour," and nothing on screen says so.
