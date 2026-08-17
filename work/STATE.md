# Master state — the IDE for the last 1%

**Goal (reset by Dee 2026-08-16, supersedes the fab-readiness goal below):**
Autonomous Circuit is **Cursor for electronics**. Agents already do ~99% of a
board. The 1% left is the hard part — routing is NP-hard, and taste is human —
so the app needs a board IDE an electrical engineer can drop into. Quality bar:
**familiar to an EE**, i.e. Altium/KiCad muscle memory. Not full-featured;
enough to edit.

Standing instructions that came with the goal:

1. Keep building and testing IDE features.
2. The three example boards must come out of the app good to go, needing only a
   couple of small human edits.
3. **Commit and push frequently.**
4. Run a standing board of judge/reviewers who *use every feature* and score it.
   Keep iterating to raise the score.

The metric is the panel score: `docs/reviews/ide-panel-rubric.md` — seven
lenses, 70 points, ship bar written into that file. It replaces `fab.ready` as
*the* number this loop moves, without replacing `fab.ready` as a floor.

## Where things stand (2026-08-16, evening)

**The IDE surface is committed and green.** `6a209dc` landed the editor: edit
engine with a byte-exact inverse, semantic server edit + fast verdict
(`board_edit_apply`, `board_fast_check`), keymap arbiter, pointer arbiter,
right-click menu, shortcut sheet derived from the resolvers, Properties
editing, rotate. `ec67807` before it gave the suite a real DOM, which is the
only reason the wiring bugs of the last three days are now catchable.

- viewer: **958 pass, 0 fail, 9.4s**
- circuitpy: **391 pass + 58 subtests**
- All three example boards: `fab.ready: true`, 0 blocking, in the repo **and**
  in the app's own project workspaces.

**The app workspaces were stale and are now synced.** The app reads
`~/.autonomous-circuit/projects/<uuid>`, not the repo. harness-puck and
terminal-keyboard were Aug 15 builds; hydrate-coaster was **Aug 11 with 3
blocking errors**. All three replaced with today's verified builds (old copies
moved, not deleted, to the session scratchpad backup).

**One real defect found and fixed doing that**: `board_fast_check` reported
`blocked` on all three fab-ready boards. Two legs report the same finding —
`harvest_circuit_json` grades a `pcb_trace_too_long_warning` element `warning`
from its suffix, `run_tscircuit_checks` grades everything `@tscircuit/checks`
returns `error` — and the build ends with `checks.dedupe` (first wins → the
warning copy) while the fast gate did not. Now it does. All three read `legal`.

## The fleet (2026-08-17)

Six products, built with the app by AI electrical engineers who had never seen
this codebase. `scripts/fleet-status` regenerates the table in
`products/README.md` from each board's own sidecar, so it cannot flatter us.

**1 of 4 built boards is fab-ready**; the other three are at 1, 3 and 10
blocking findings and their engineers are iterating. Every builder also writes
`work/ee-feedback/<slug>.md`, and that file is the input to the next round of
IDE work — three findings from the first one are already fixed:

- the planner silently dropped the second of any repeated block (two status
  LEDs came back as one, and every check agreed because none of them saw it);
- `board_fast_check` answered `legal` about the built board after a raw
  `board_source_write`, with no hint it was grading different geometry;
- `dfm_power_trace_width` was unsatisfiable by construction (ledger #47).

## The score

**Round 1: 40/70. Round 2: 51/70**, same day, same rubric
(`docs/reviews/ide-panel-rubric.md`, write-up in
`docs/reviews/ide-panel-2026-08-16.md`, judges' own reports under
`work/ide-panel/round<N>/`). Verdict went 2 → 8 once the gate the app had never
called was called. Nothing is at 9 or 10 yet and nothing should be.

## Open, ranked — this is the next round's queue

1. **Integrity is the lowest lens (6).** Its two must-fixes were fixed in
   `62c4255`; the score cannot move until a judge re-drives them. Everything a
   fix claims has to be verified by someone who did not write it.
2. **Editing (7) needs two things.** `board_edit_apply` still has no client
   caller — the drag path writes byte ranges and asks for a verdict separately,
   which works but leaves a whole tested endpoint unused. And placement ids are
   positional (`tag[ordinal]`), so an agent inserting an earlier element of the
   same tag silently renames every later one.
3. **Keyboard precision (Familiarity, 8).** No arrow-key nudge, no `Tab`
   mid-drag properties, `F11` unbound, double-click fits instead of opening
   Properties. `SNAP_STEPS` and the edit queue already exist, so nudge is
   wiring rather than new arithmetic.
4. **Every board's third human edit is our defect, and it is the same one on
   all three: the USB pair** (ledger #12). Two boards have no corridor for it,
   the third has the connector-side leg unrouted. `reserve.py` is built and
   tested (48 tests) and **not wired into `build_board`**, because on both
   refusing boards the reserved rebuild currently comes back *worse*
   (`147b958`) — the corridor has to get cheaper first.
5. **No parts lock on two boards** (ledger #46): writing one fills the BOM's
   Footprint column and takes `fab.ready` from true to false, because the USB-C
   receptacle's hybrid SMD/through-hole footprint loses its plated holes in the
   plot. Platform defect, understood, unfixed.
6. Router track (separate agents): composition beats the relay, the incumbent
   still wins on all three boards, `CIRCUIT_ROUTER` stays off by default.

## The loop

`work/LOOP.md` still describes the fab-readiness machine and its invariants —
one build at a time, gate on parsed artifacts, never commit a board whose
sidecar is not `fab.ready: true`, `fab.ready` is a floor. **Those invariants
still hold.** What changed is the top of the loop: pick the next action from
the panel's ranked must-fix list, build it, test it, commit it, push it, and
re-run the panel to see whether the number moved.

## Log

- 2026-08-16 evening — Goal reset to the IDE. Suite floor re-established
  (a hung `ShortcutSheet.render.test.js` was making the viewer suite take 615s
  and report a failure; it pressed Escape on `window`, and Radix listens for it
  in the capture phase on `document`). Five commits landed and pushed: test
  harness, rail-width study, reserve+diffpair, fastcheck, the IDE surface.
  App workspaces synced. Panel round 1 dispatched.
- Earlier history (three boards to fab-ready, the panel that kept dying on
  context, the via-in-pad phantom): `docs/night-log-2026-08-16.md` and
  `docs/lessons.md`.
