# usb-c-breakout — EE feedback

Built `products/usb-c-breakout`: USB-C receptacle broken out to six labelled
bench pads (5V, GND, CC1, CC2, D+, D-) with a status LED. Composed from
`usb-c-data`, `status-led`, `glue.tsx` (MountingHole, GndPour), and six
`<testpoint footprintVariant="pad">` glue pads. No MCU — the first no-MCU
board in the fleet.

**Verdict:** `fab.ready: true` on build #1 (5x, the floor), 36 x 40.5mm, 2
layers, 0 blocking, 49 advisory, 124 info. BOM 7 orderable lines (the six
testpoints are bare copper, not parts, so they inflate "13 lines" without
being a real shortfall). `gerberSource: kicad-cli`. One follow-up build fixed
a silkscreen collision (below); size and warning counts held.

## Friction, ranked by time cost

**1. Silkscreen collision has no static check, only a buried post-hoc
warning — cost one full rebuild.** `layout` checks footprint overlap, hole
clearance and board-fit before a build; nothing checks silkscreen text width
against its neighbours. My first `_pcb.png` showed "VBUS" and "GND" fused
into "VBUSGND" — unreadable on the one board whose entire job is being read
off the silk by a human with a probe. The only trace of it in the verdict was
one `gerber_silk_over_pad` warning at position 46 of 173, next to 68
boilerplate `drc_violation` entries — nothing said "text collision" in
words. Caught only because the rule says look at the PNG. Uncached, that's a
20-40 min round trip for a string-length bug a width estimate could catch
pre-build.

**2. `place_board()` doesn't know glue exists, so every testpoint band is
hand math.** `BLOCK_BOX_MM` covers golden blocks only; a testpoint row has no
box, so `place_board(["usb-c-data","status-led"])`'s clean 36 x 30.5mm plan
had no room for the six pads that are this board's whole point. I hand-added
an 8mm band, re-derived every coordinate, then called `board_fits()`,
`overlap_warnings()` and the private `_hole_clearance_warnings()` myself to
re-verify before building — maybe 10 minutes, clean only because the
arithmetic was careful. A `place_board()` that accepted a plain
`{"height_mm": 8}` spacer entry would have done this in one call.

**3. `project_create`'s request shape doesn't match its siblings — cost one
wasted call.** `POST /api/project_create` reads `req?.name` from the body, so
`{"name": "..."}` silently creates "New project"; every other command
(`project_rename`, `board_fast_check`, ...) reads its fields off the body
directly. Had to read `http.mjs` to find the mismatch, then `project_rename`
to fix it.

**4. `scripts/check`'s advertised "cheap" tier is not cheap.** SKILL.md
describes it as "no KiCad, no fab export"; its own docstring admits circuitpy
exposes no stages-limited entry point, so it runs the *identical* full build
into a tempdir and strips two warning kinds after the fact. Didn't matter on
this 8-part board, but the promise of a fast pre-flight gate is false on
every board in the fleet.

## What the app told me that was true

`board_net_widths` on V5/GND: ceiling **1.1mm** on both, set by the ESD
chip's own SMT pad pitch (`U1.VBUS`/`U1.GND`), narrowest routed point **0.2mm**
(a via drill). `helpers.trace_width_for()`: USB-C default 0.9A needs 0.26mm —
already above the board's narrowest point. 1.5A needs 0.525mm (roughly the
DFM power floor); 3A needs 1.367mm, which **the ESD chip's footprint alone
makes unroutable on two layers regardless of router effort** — a real,
board-specific ceiling, not a router shortfall. The `dfm_power_trace_width`
warning that reports this is `warning`, not `error`, on the one board type
where VBUS width is a functional question, not a habit one. `board_fast_check`
with `moves` correctly predicted a 0.65mm pad nudge as `blocked`
(`trace_left_its_pad`) though the same nudge read "legal" (with a drift
note) when checked without `moves` — both answers were honest about what they
did and didn't grade.
