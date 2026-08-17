# Lens 4 — Verdict, round 4, 2026-08-17

Scope: `board_fast_check`'s new `last_build` (blocking/invisible_here) feature,
plus the rest of the lens (latency on the biggest board, `notChecked` honesty,
never-built / timeout / no-python paths). App live on `:4179`, driven by
`curl` against the real server and by direct `python -m circuitpy.fastcheck`
calls. No application code touched. No project state changed — every call was
either a read or a `board_fast_check` (read-only; does not write).

**Score: 7/10** (round 3 implicit baseline: 8, for the wiring; this round finds
the headline number is now right but uncovers a second, larger honesty gap the
fix didn't touch).

## The three named boards — driven live

`POST /api/board_fast_check` with `moves: []`, ids resolved from
`~/.autonomous-circuit/projects/*/product.json`:

```
two-key-footswitch (63e23034…) → counts.error=1, lastBuild={blocking:3, invisibleHere:2, invisibleKinds:['drc_violation'], fabReady:false}
desk-air-monitor  (17775e74…) → counts.error=0, lastBuild={blocking:0, invisibleHere:0, invisibleKinds:[],              fabReady:true}
```

Both match the brief exactly (3/2 and 0/0), and match a hand-computed
severity-filter over each board's own `main.board.json` run independently in
Python (not through `fastcheck.py`, to cross-check it rather than trust it).

**pixel-badge could not be driven live**: the installed project at
`~/.autonomous-circuit/projects/a10a67ef-76c4-4c07-b779-cac1b7ab147c` has never
been built — `boards/` holds only `main.tsx`, no `main.circuit.json`, no
sidecar. `board_fast_check` against it correctly returns
`status: "unavailable", reason: "this board has not been built yet, so there
is nothing to check against"` (verified by curl, quoted below) — never a false
clean. I could not verify 10/7 against the *installed* project because it
doesn't have a build to verify against. I verified 10/7 instead against the
**checked-in** `products/pixel-badge/boards/main.board.json` in the repo,
running the identical severity-filter/kind-set logic used by
`last_build_verdict`: `blocking: 10, invisible_here: 7`, kinds
`{drc_violation: 7, pcb_trace_error: 1, pcb_pad_trace_clearance_error: 1,
dfm_hole_clearance: 1}` — matches the brief. The number is right; the
*installed example* just isn't in a state to prove it live, which is itself
worth fixing before the next round (an example project that has never been
built is a bad first impression on its own, independent of this lens).

Never-built board, quoted:
```
$ curl -s -X POST :4179/api/board_fast_check -d '{"id":"a10a67ef-…","file":"boards/main.tsx","moves":[]}'
{"ok": false, "status": "unavailable",
 "reason": "this board has not been built yet, so there is nothing to check against",
 "counts": {"error": 0, "warning": 0, "info": 0}, "lastBuild": null, "elapsedMs": 0}
```
The chip (`PlacementEditBar.jsx:49-53`) reads `verdict.status === "unavailable"`
before anything else, so this can never render green. Correct.

## `FULL_BUILD_ONLY_KINDS` — checked against what the pipeline actually emits

`fastcheck.py:467-479` lists 11 kinds. I grepped every `"kind": "..."` literal
in `packages/circuitpy` and `packages/verify` (the only two places a sidecar
warning is constructed) and checked each entry's real name and its *possible*
severity, because `last_build_verdict` only ever inspects `severity == "error"`
(`fastcheck.py:505-507`):

| entry | real kind exists? | can it ever be `error`? |
|---|---|---|
| `drc_violation` | yes (`generation.py:1576`) | yes — live |
| `gerber_pad_missing` | yes (`gerber_truth.py:426`) | yes (`"error"` literal) |
| `gerber_drill_missing` | yes (`gerber_truth.py:298`) | yes (`"error"` literal) |
| `part_not_orderable` | yes (`checks.py:1764`) | yes, on assembly packets |
| `erc_violation` | yes, but forced | **no** — `checks.py:423` `_ADVISORY_KINDS = {"erc_violation"}`, `_kicad_severity` downgrades it to `"info"` unconditionally |
| `unverified_gerbers` | yes (`generation.py:1607`) | **no** — `"severity": "warning"` is a literal, never varies |
| `gerber_drill_extra` | yes (`gerber_truth.py:356`) | **no** — literal `"warning"` |
| `gerber_mask_sliver` | yes (`gerber_truth.py:622`) | **no** — literal `"warning"` |
| `gerber_silk_on_pad` | **no such kind anywhere** | — the real kind is `"gerber_silk_over_pad"` (`gerber_truth.py:676`), literal `"warning"` |
| `bom_line_unorderable` | **not emitted anywhere in the repo** | — |
| `bom_no_supplier_part` | **not emitted anywhere in the repo** | — |

**The typo is the concrete, wrong-in-one-direction bug the brief asked for**:
`fastcheck.py:474` has `"gerber_silk_on_pad"`; the pipeline emits
`"gerber_silk_over_pad"` (confirmed live — it's in every one of the four
sidecars I checked, e.g. two-key-footswitch's warnings). Today it is harmless
only because that check happens to always grade `"warning"`, never `"error"`
— if a future DFM tightening ever promotes silk-over-pad to `error` (round 2
already flagged this as a real physical risk — "fabs clip silk over copper in
CAM prep"), the typo means it would **never** be flagged invisible, and
`counts.error` would read low again with nothing in `invisible_kinds` to
explain the gap. That is exactly the two-key-footswitch bug this feature was
built to close, waiting to reopen on one string.

Net: **7 of the 11 entries can never contribute to `invisible_here` today**
(dead: `erc_violation`, `unverified_gerbers`, `gerber_drill_extra`,
`gerber_mask_sliver`, the typo'd silk entry, and two kinds that don't exist).
Only 4 do real work (`drc_violation`, `gerber_pad_missing`,
`gerber_drill_missing`, `part_not_orderable`). None of this changes today's
counts — it's dead weight, not miscounting — but it shows the set was written
from names, not verified against emitters, and `test_fastcheck.py`'s own
guard (`test_the_kinds_it_cannot_see_are_the_ones_a_rebuild_produces`) only
asserts membership for two kinds, not that the other nine match real strings
or real severities.

## The bigger gap: the whole system is blind to warning-severity findings

`last_build_verdict` filters `validation.warnings` to `severity == "error"`
*before* anything else happens (`fastcheck.py:504-508`). That is also the
only place `FULL_BUILD_ONLY_KINDS` gets applied. Consequence: any full-build
finding of a full-build-only kind that lands as `warning` (which is most of
them — see table above, `drc_violation` itself is usually `warning`, only
sometimes `error`) is invisible to `counts`, invisible to `invisible_here`,
and mentioned nowhere with a number. I measured this on all four boards with
a sidecar and pending changes:

```
two-key-footswitch: sidecar warnings=159 (145 drc_violation) — live counts.warning=18  → 141 hidden, 0 disclosed
terminal-keyboard:  sidecar warnings=417 (377 drc_violation) — live counts.warning=87  → 330 hidden, 0 disclosed
macropad-6:         sidecar warnings=169 (156 drc_violation) — live counts.warning=11  → 158 hidden, 0 disclosed
hydrate-coaster:    sidecar warnings=161 (148 drc_violation) — live counts.warning=12  → 149 hidden, 0 disclosed
```

The chip's collapsed state never shows this. The expanded panel names the
category ("Not checked: ... KiCad's own ERC and DRC ...") but gives no count
for it — only the error-tier "N unseen" got a number. An engineer who opens
the panel on a `0 blocking / 0 unseen` board (all four above read exactly
that) sees "legal," full stop, while a rebuild would surface 150–380 more
findings the gate structurally cannot predict. That is not a lie in the literal
sense — "legal" is scoped to blocking severity, and the category-level
disclosure exists — but it is the same shape of gap the whole feature was
built to close, one severity tier down, and it is bigger by two orders of
magnitude than the bug that motivated the fix.

## Latency, measured, on the biggest board

`terminal-keyboard` (137 parts, 5,463 elements) is the largest of the twelve
installed boards (`macropad-6` 1,937 · `harness-puck` 2,442 · `terminal-keyboard`
5,463 — checked by element count across all installed projects).

```
$ time curl -s -X POST :4179/api/board_fast_check -d '{"id":"b6a59eab-…",...}'
  → elapsedMs (server-reported): 1042, 1103, 1124   (3 runs)
  → wall clock (curl, incl. HTTP): 1.08s, 1.14s, 1.16s

$ python -m circuitpy.fastcheck <terminal-keyboard project>   (no HTTP, no Node)
  → elapsed_ms: 866.6, 846.3, 838.7   (3 runs)
```

The module's own docstring quotes 753ms warm for this board (2026-08-16); a
day later, on the same board, through the path an engineer actually hits
(the HTTP endpoint, not the bare CLI), it is consistently **over one second**
(1.04–1.16s). The Python-only number (840–870ms) is itself already closer to
the ceiling than the docstring's benchmark suggests. Not a correctness bug —
"sub-second" is now optimistic for the one board it matters most on.

## What's clean

- **Never-built board**: honest `unavailable`, quoted above. Never renders green.
- **No Python / timeout** (read, not driven — `fastCheck.mjs:110-124` for the
  missing-interpreter path, `:157-185` for the 12s `execFile` timeout): both
  fall through to the same `unavailable(reason, startedAt)` shape with no
  `ok: true` anywhere on the path. A `SIGTERM`'d process with no valid stdout
  hits `!parsed` and reports `"the fast check did not run: <stderr/error>"`.
  No path in this file can produce a false pass.
- **Stale-relative-to-the-TSX** (the "50 edits since compile" question, for
  the *live* counts and warnings, as opposed to the `last_build` sidecar):
  caught this in the wild, unprompted — `two-key-footswitch`'s installed
  project already had one part dragged and never rebuilt. Calling
  `board_fast_check` with `moves: []` (what a read-only glance does) correctly
  added `notChecked: [{"what": "1 part the board file has moved since this
  build", "why": "this answer grades the board that was built. Pass \`moves\`
  ... or rebuild"}]` and `"drifted": 1` (`http.mjs:836-859`, `sourceDrift` in
  `boardEdit.mjs:220-231`). The `lastBuild` sentence itself ("The last full
  build found N blocking findings") is grammatically retrospective and stays
  true regardless of drift — it doesn't claim to be about the current file.
  What it does **not** do: attach any age or edit-count to `lastBuild` itself
  — `atEpochS` is parsed into the JS object (`fastCheck.mjs:203`) and never
  displayed anywhere in `PlacementEditBar.jsx`. A build from 5 minutes ago and
  one from 5 hours ago read identically on screen.

## Must-fix, ranked

1. **Warning-severity full-build-only findings are completely unquantified.**
   `last_build_verdict` (`fastcheck.py:504-508`) filters to `error` before
   `FULL_BUILD_ONLY_KINDS` is even consulted. Extend the sidecar read to
   report a `warning`-tier count too (or fold both into one "structurally
   invisible" number) so "0 unseen" is never shown on a board carrying
   300+ un-rebuilt DRC findings. This is the same failure mode the round
   fixed, one severity down, and bigger.
2. **Fix the typo**: `fastcheck.py:474` `"gerber_silk_on_pad"` →
   `"gerber_silk_over_pad"`. Currently inert (that kind is warning-only) but
   wrong, and it would silently reopen the undercount if severity policy
   ever changes. Delete or replace the two kinds that don't exist anywhere
   in the repo (`bom_line_unorderable`, `bom_no_supplier_part`) and add a
   test that walks the real emitters rather than hand-asserting two strings.
3. **Show `lastBuild` age on the chip.** `atEpochS` is already parsed and
   thrown away. One relative-time string ("last full build 6h ago, 12 edits
   back") turns a frozen number into an honestly-scoped one.
4. **pixel-badge, as installed, has never been built.** Not this lens's bug
   directly, but it means one of the three boards this exact brief asks
   about cannot be driven live to prove the number — only reproduced against
   the repo's checked-in copy. Worth a rebuild before the next round so all
   three examples are drivable the same way.
5. **"Sub-second" is now marginal-to-broken on the biggest board via the real
   path** (1.04–1.16s over HTTP). Not urgent, but the docstring's own
   benchmark table is a day stale on the number that matters most.

## The one sentence

After an edit, this tool does not let an engineer believe the board is fine
when it has a *blocking* problem it should have flagged — the headline
undercount is fixed and verified 3/2, 10/7, 0/0 on the three named boards —
but on every board I checked it shows "0 unseen" while 150–380 real
KiCad-only warnings sit un-rebuilt and un-counted, which is silence dressed
as a clean answer, just one severity tier below where anyone is looking.
