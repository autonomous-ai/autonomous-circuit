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

## The fleet (2026-08-17, midday)

Twelve products, built with the app by AI electrical engineers who had never
seen this codebase. `scripts/fleet-status --write` regenerates the table in
`products/README.md` from each board's own sidecar, so it cannot flatter us.

**6 of 9 built boards are fab-ready.** Two more (`bench-i2c-scanner`,
`dual-rail-psu`) and one rebuild (`env-logger-usb`) are in flight. Every
builder also writes `work/ee-feedback/<slug>.md`, and that file is the input to
the next round of platform work — which is where nearly everything below came
from.

### What the fleet has cost us in defects, and what it bought

Seven fixes today, every one traceable to a board somebody was trying to
finish:

- **The router escalation was switched off for every board in the fleet.**
  `_set_autorouter_effort` refused any source that already declared
  `autorouterEffortLevel`, and SKILL.md tells every engineer to declare `"5x"`.
  Three engineers hand-rebuilt at 10x, one at a time, guessing, while the
  mechanism built to do exactly that sat disabled by the instruction meant to
  make boards route. A declared effort is now a **floor** (ledger #51).
- **The planner's block gap was one number and `rp2040-core` needs another.**
  Measured A/B on an identical composition: 2mm → **13 blocking**, 5mm → **1**
  (ledger #52). Also measured, so nobody spends another build on it: **10x does
  not move this class** — the escalation fires now and comes back 9 against 9.
- **A wire nobody drew leaves no element to be wrong about.**
  `checks.floating_net_warnings` found dead wiring on four boards, three of
  them `fab.ready: true`: `sensor-node-mini`'s USB pair terminates in air (it
  composes `usb-c-power`, which has no data pins), `desk-air-monitor`'s button
  is wired to itself, and two pixel chains never get their data in.
- **`board_fast_check` said "1 blocking" on a board whose real number was 3.**
  It cannot run KiCad — that needs a compile — so `drc_violation` is invisible
  to it. It now reads the sidecar and reports `lastBuild.invisibleHere`; the
  IDE chip says "1 blocking · 2 unseen", and a *clean* answer on a board with
  standing KiCad blockers is amber rather than green.
- **The effort pill never sent the effort.** `app_set_effort` landed
  server-side with settings, driver flag and review loop behind it, and no
  client code named the command: the pill read "Max" while every turn ran at
  the CLI's default.
- **`project_create` ignored a flat body** and answered 200 with a project
  called "New project" — two engineers named a board and got that instead.
- **Products' `blocks/` were gitignored** while the README inside them promised
  "a frozen snapshot so gerbers stay reproducible". A fresh clone had no
  snapshot at all, and a block one engineer patched so a board's two sensors
  could take different I2C addresses would have vanished silently, leaving both
  at 0x76 with no error anywhere. Now tracked, the way `examples/` always did.

### The pattern under five of those

Code that compiles, passes its own tests, is documented as landed, and is
**called by nothing**: `board_fast_check`, `onPlacementRotate`, `PadHeader`,
`editEngine.js`, `app_set_effort`. Four in one day. A unit test proves a piece
works; nothing proved the app reached it. `commandsAreWired.test.mjs` now reads
the server's command table and fails if a command has no caller in shipping
client code, or no written reason in `AGENT_ONLY`. Lesson H in
`docs/lessons.md`; the same question is still open one layer out, for a block
nothing composes and a check no gate calls.

## The score


**Round 1: 40/70. Round 2: 51/70**, same day, same rubric
(`docs/reviews/ide-panel-rubric.md`, write-up in
`docs/reviews/ide-panel-2026-08-16.md`, judges' own reports under
`work/ide-panel/round<N>/`). Verdict went 2 → 8 once the gate the app had never
called was called. Nothing is at 9 or 10 yet and nothing should be.

## Open, ranked — this is the next round's queue

1. **Three boards need re-placing at the new block gap, and four need their
   dead wiring fixed.** Both are dispatched and in flight. The gap is measured
   (13 → 1 blocking) but no *product* board has been carried onto it yet,
   because every placement in a shipped board is a frozen literal — the planner
   fix does nothing for an existing board until somebody re-derives it.
2. **Promote `net_reaches_one_part` from `warning` to `error`** once those four
   boards are clean. It is graded low today only because flipping three shipped
   boards from ready to blocked is Dee's call, not a commit's.
3. **Integrity is the lowest lens (6).** Four of round 3's five findings were
   fixed in `bf92bac`; the score cannot move until a judge re-drives them.
   Everything a fix claims has to be verified by someone who did not write it.
4. **Editing (7).** `board_edit_apply` still has no client caller — now
   *declared* as agent-only with a written reason in `commandsAreWired.test.mjs`
   rather than silently unused, but the decision stands either way. Placement
   ids are still positional (`tag[ordinal]`), so an agent inserting an earlier
   element of the same tag silently renames every later one.
5. **Ledger #51(b), and measured as low value**: the escalation decides off the
   circuit.json scan, so KiCad's `drc_violation` — the kind that actually
   blocks the RP2040 boards — does not exist yet when the retry is chosen.
   Fixing the timing means either a cheap DRC probe before the decision or a
   retry that wraps more of the pipeline. **10x does not move this class**
   (ledger #52's A/B), so this buys less than it costs today.
6. **Every board's third human edit is our defect, and it is the same one on
   all three: the USB pair** (ledger #12). Two boards have no corridor for it,
   the third has the connector-side leg unrouted. `reserve.py` is built and
   tested (48 tests) and **not wired into `build_board`**, because on both
   refusing boards the reserved rebuild currently comes back *worse*
   (`147b958`) — the corridor has to get cheaper first.
7. **No parts lock on two boards** (ledger #46): writing one fills the BOM's
   Footprint column and takes `fab.ready` from true to false, because the USB-C
   receptacle's hybrid SMD/through-hole footprint loses its plated holes in the
   plot. Platform defect, understood, unfixed.
8. Router track (separate agents): composition beats the relay, the incumbent
   still wins on all three boards, `CIRCUIT_ROUTER` stays off by default.

## The loop

`work/LOOP.md` still describes the fab-readiness machine and its invariants —
one build at a time, gate on parsed artifacts, never commit a board whose
sidecar is not `fab.ready: true`, `fab.ready` is a floor. **Those invariants
still hold.** What changed is the top of the loop: pick the next action from
the panel's ranked must-fix list, build it, test it, commit it, push it, and
re-run the panel to see whether the number moved.

## Log

- 2026-08-17 midday — Fleet to twelve products, 6 of 9 built boards green.
  Seven platform fixes, all traced to a board somebody was trying to finish
  (see the fleet section above). Ledger gained #51, #52, #53 and lesson H.
  Suites: viewer **1009 pass**, circuitpy **424 pass + 58 subtests**. Five
  agents in flight: two new products, one rebuild, one re-placement of the
  three RP2040-blocked boards, one repair of the four boards with dead wiring.
- 2026-08-16 evening — Goal reset to the IDE. Suite floor re-established
  (a hung `ShortcutSheet.render.test.js` was making the viewer suite take 615s
  and report a failure; it pressed Escape on `window`, and Radix listens for it
  in the capture phase on `document`). Five commits landed and pushed: test
  harness, rail-width study, reserve+diffpair, fastcheck, the IDE surface.
  App workspaces synced. Panel round 1 dispatched.
- Earlier history (three boards to fab-ready, the panel that kept dying on
  context, the via-in-pad phantom): `docs/night-log-2026-08-16.md` and
  `docs/lessons.md`.
