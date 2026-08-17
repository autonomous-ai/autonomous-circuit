# Lens 5 — Integrity, round 4

Against `4eb9d28` (commit `bf92bac` is the fix under test). Question: can the
tool corrupt or lose work? Every claim below is driven live against
`:4179` or run through the real headless render harness — commands and
output are quoted, not paraphrased.

**Score: 8/10** (round 3 held this at 6/10, unscored gaps closed).

## Re-driving round 3's four claimed fixes

### 1. Coordinate bound scans every `pcbX`/`pcbY` literal, mask-aware, nested groups included — HOLDS

harness-puck's `R30` resistor sits inside a `<group>` (`boards/main.tsx:74`),
exactly the case round 2 proved got through. Drove a byte-range write setting
its `pcbX` to `1e30`:

```
$ curl -X POST /api/board_source_write ... {"start":3528,"end":3531,"text":"1e30","expected":"-17", ...}
{"code":"INVALID_ARGUMENT","message":"line 74: X is 1e+30mm, which is off any board — positions must be within ±1000mm"}
```

Refused, names the line, file untouched (`diff` clean against a pre-test
backup). Then drove the mask-aware half: inserted `{/* pcbX={99999} ... */}`
as a JSX comment right before `</board>`. It went straight through —
`sourceLength: 11827`, no refusal — confirming a coordinate-shaped number
inside a comment is correctly *not* treated as a coordinate.

### 2. A write that breaks the file is refused with esbuild's own message; an already-broken file is left alone — HOLDS

Deleted the file's own `</board>` tag via a byte-range edit:

```
{"code":"INVALID_ARGUMENT","message":"this edit leaves the board file unable to compile — Unexpected end of file before a closing \"board\" tag (line 286). The file it replaced compiled, so the edit is what broke it."}
```

File on disk unchanged. Then corrupted the file directly on disk (bypassing
the API, simulating "it was already broken before this request"), and sent
an unrelated one-byte insert far from the break: it was accepted
(`sourceLength: 11777`, no error) and the file is still broken — matching the
documented "a file that was already broken is left alone" behavior rather
than trapping whoever has to fix it.

### 3. `board_source_write` always leaves a history row, synthesising a summary when none is given — HOLDS

Wrote with no `summary` field at all. Last line of
`.circuit/revisions.jsonl`:

```
{"summary":"edited boards/main.tsx (+1 bytes)", ...}
```

### 4. `editor.nudgeBy` applies the delta inside the queue — HOLDS

Ran the real suite (not just read it):

```
$ node --test ... BoardWorkspace.keys.render.test.js
✔ Ctrl+arrow nudges the selected part one step, and repeats while held
✔ a held nudge does not eat the keystrokes that land mid-write
ℹ pass 14  ℹ fail 0
```

The second test fires four un-awaited `Ctrl+ArrowRight` presses (the shape of
a held key) and asserts four writes landing four 0.5mm steps from -2 to 0,
not a collapsed no-op. It does.

## The fifth item: drift under-reports an identity swap — CONFIRMED, still real

`bindPlacements` (`viewer/src/client/components/board/boardSource.js:941`)
matches a source placement to built geometry by `(x, y)` coordinate alone.
Drove it on hydrate-coaster: swapped R30 and R31's `pcbX` literals
(`-2,-6` ↔ `2,-6`, a genuine part-swap edit an EE could make by accident),
then asked for a verdict with no moves:

```
$ curl board_fast_check ... (after the swap)
{"ok":true,"status":"legal", ... }   # no "drifted" field, no notChecked entry about it
```

`sourceDrift` (`viewer/src/server/circuit/boardEdit.mjs:220`) reports
`drifted: 0` because both placements still bind to *some* anchor — just the
wrong one. This is worse in practice than "under-reported": it is **fully
silent**. Every other drift case at least gets a `notChecked` line
("N parts moved since this build"); an identity swap gets nothing, and the
canvas binding (`usePlacementEditor.js:237`, used to attribute which
placement a build-time warning highlights) is now silently wrong until the
next rebuild. Still an honest, disclosed limitation rather than a hidden one
— the commit message says so — but it deserves to surface *something*, not
zero.

## Past the list

**Concurrent writers, driven for real.** `planSourceWrite`
(`http.mjs:221`) is a compare-and-swap on the whole file length plus a
per-edit `expected` substring check, executed inside a per-project promise
queue (`createEditQueue`, `boardEdit.mjs:70`).

- Two non-conflicting concurrent writers (different byte ranges,
  same-length replacements) both fired with `curl … & curl … & wait`: both
  succeeded, both edits landed on disk (verified by `grep` after), 3ms apart
  per the history timestamps. No loss.
- Two *conflicting* writers — same byte range, same base `sourceLength`,
  different replacement text, fired truly concurrently: one came back `200`,
  the other came back a clean `409 SOURCE_CHANGED — "the board file changed
  since it was read — reopen the board and try again"`. No silent
  overwrite, no corrupted splice, no data loss. This is the collision this
  product is built around, and it resolves exactly right.

**Undo across every edit shape that adds or replays bytes.**
`node --test boardSourceRotate.test.js` (35/35 pass) round-trips *every
placement on every shipped example board* through rotate→undo, byte-identical
to source, covering both the prop-edit path and the structural `<group>`
wrap path (a wrap rewrites four lines; its undo replays recorded inverse
bytes, not a recomputed value). Separately drove `widthEdits` +
`invertEdits` on the exact "net wired inside a block" case from
`netWidth.test.js` (a width edit that inserts a whole new `<trace>` line):
Set adds 2 lines, Undo returns `back === BOARD` — `true`, byte-identical.
`BoardWorkspace.edit.render.test.js`'s "undo refuses to overwrite somebody
else's edit" (an agent writes over a human's move mid-session) still passes
live — undo detects the semantic drift and refuses with a named reason
rather than clobbering the agent's work.

**A check that comes back `unavailable` does not block the write.** Asked
`board_fast_check` on a never-built project:
`{"ok":false,"status":"unavailable","reason":"this board has not been built
yet, so there is nothing to check against"}` — no fake verdict. Then wrote to
that same unbuilt board's source: it landed (`200`, history row written)
regardless. Read `usePlacementEditor.js:371-439`: `write()` and `checkNow()`
are decoupled — a save is never gated on a verdict, so a slow or failing gate
can never eat an edit.

**A file edited on disk outside the app (agent, external editor, `git
checkout`) is not invisible to an open session.** Opened an SSE connection
to `/api/events?projectId=…`, then rewrote the board file directly with
Python (bypassing every API), and captured:

```
event: catalog_changed
data: {"revision":2}
```

within ~3s, off `fs.watch(..., {recursive:true})` (`catalog.mjs:237`) — the
mechanism `manifestRevision` rides to make an open editor refetch. This is
the server half of "did my work survive"; the client half (refetch on
`manifestRevision` bump, `usePlacementEditor.js:161-198`) is exercised by the
existing agent-collision test above.

**Server restart cannot half-write a file.** Read, not driven (restarting
the shared dev server would have disrupted the other judges' concurrent
sessions running against it): `writeAtomic` (`boardEdit.mjs:318-322`) writes
to a sibling `.tmp` file and `renameSync`s over the target — POSIX rename is
atomic, so a crash mid-write leaves either the old file or the new one, never
a half one. The edit queue (`createEditQueue`, `boardEdit.mjs:70-84`) is
explicitly in-memory only and explained why: it protects against two
requests to *this* process interleaving, not against a second process (the
agent) or a restart — that job belongs to the compare-and-swap, which is
durable because it re-reads disk on every turn.

## Housekeeping

Every probe above ran against a backed-up copy of the target project file
(`cp` before, `diff` after) and was restored to the original bytes; the
pixel-badge project I wrote a no-op probe edit into had no prior `.circuit/`
directory, so it was `rm -rf`'d back to pristine rather than left with a
synthetic history row. One unrelated concurrent-session edit landed on
`examples/harness-puck/boards/main.tsx` (an `NC_PX` floating-net fix,
`git diff` confirms it postdates my restore and touches a different net) —
left alone, since overwriting another session's real work is the exact
failure this lens polices.

## Must-fix, ranked

1. **Identity-swap drift should surface *something*, not nothing.** Two
   placements trading coordinates produces `drifted: 0` and no `notChecked`
   line at all — the one drift shape that gets zero signal instead of
   reduced signal. A cheap partial fix: when `bindPlacements` matches N
   placements to N anchors but the *set of matched (placement, anchor) pairs*
   differs from the previous binding at the same anchors, that is itself
   detectable without an id map and worth a generic "binding changed since
   last build" notice.
2. **`SOURCE_CHANGED`'s message doesn't say what changed.** Every refusal
   from a real race gets the same "the board file changed since it was read
   — reopen the board and try again," with no hint of which byte range or
   placement collided. Not a correctness bug — nothing is lost — but it
   turns a routine agent/human interleave into a full reload instead of a
   targeted retry.

No must-fix rises to blocking. Everything adversarially driven this round —
the CAS under real concurrency, every undo shape, the unavailable-check path,
the external-edit reconciliation path, atomic writes — held.

## The sentence

Could this tool lose an engineer's work? Not under anything I could drive at
it this round — writes are atomic, undo refuses rather than guesses when
someone else has been in the file, concurrent writers get a clean loser
message instead of a silent stomp, and a slow or broken verdict never blocks
a save; the one live gap is a mislabeled highlight after a rare identity-swap
gesture, not a lost byte.
