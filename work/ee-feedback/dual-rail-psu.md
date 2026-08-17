# dual-rail-psu — EE feedback

`fab.ready: true`, 0 blocking, 41.1 x 51mm. Three builds: baseline (1 blocking),
one placement fix (clean), one to declare rail width (clean, narrower gone).
Ranked by time cost.

## 1. A router-placed via landed 0.036mm inside its own component's clearance
   — cost: one full build cycle (~15 min wall clock + investigation)

First build blocked on one `dfm_hole_clearance`: a via at (-3.94, -2.20) sat
0.164mm from U2's (the LDO's) own GND pad against a 0.2mm floor. The pipeline's
own effort escalation (5x -> 10x) reported the *identical* finding both times —
so this was placement tightness, not router quality, exactly as the SKILL.md
note predicts ("do not hand-rebuild at a higher effort").
`evals/composition-matrix.json` shows `ldo-3v3 + status-led + usb-c-power`
clean at default effort on a compact 36.3 x 31.0mm board; mine is the same
three blocks plus a second LED, two pad headers, and 20mm of open board above
for the headers. My read: more free routing space let the router pick a via
next to the LDO's own pad that a tighter board never offered it room to place.
Fix was a 3mm nudge of the LED row away from the LDO (verified with
`layout.board_fits()`/`overlap_warnings()`/`_hole_clearance_warnings()` first,
all `[]`) — cheap once diagnosed, but diagnosing it meant reading circuit.json
by hand to find which pad and via were 0.036mm apart, because the sidecar names
the *component* (U2) and a coordinate, not "your LED row is too close." That
gap between "here's a coordinate" and "here's what to move" is the finding.

## 2. `PadHeader` had no way to ask for a wider trace on the pads that carry a
   rail — cost: ~15 min to find the gap and extend the block

This board's whole point is that `V5`/`V3_3` carry real current to a header a
person plugs a breadboard into, and `PadHeader`'s own trace is the *only*
copper between the rail and the outside world — there's no MCU downstream to
own the width question. `docs/architecture/rail-width.md` already worked out
that `<trace thickness="…">` is the live mechanism and that it must be
measured against the placement first (`circuitpy.netwidth`) or it can scrap a
board. But `glue.tsx`'s `PadHeader` had no `thickness` prop at all — the primitive
that's the single point where a header-only board's rail width has to be
declared didn't expose the thing `rail-width.md` says to declare. Added a
`thickness?: (string | undefined)[]` prop (per-pad, same order as `nets`) to
the project's own copy of `glue.tsx`. This should live in
`packages/golden-blocks` so every board with a pad-header rail gets it, not
just this one.

## 3. `place_board` doesn't know glue exists, so a header-only board still
   needs hand math for the height — cost: ~10 min, second time this has
   happened

`place_board(["usb-c-power", "ldo-3v3", "status-led", "status-led"])` returned
a clean 41.1 x 31.0mm plan — correct for the four blocks, but blind to the two
`PadHeader` rows the board actually needed room for, because `PadHeader` isn't
in `BLOCK_BOX_MM` (nothing glue-level is). Grew the board by 20mm and shifted
every planned block and both mounting holes down by half that, by hand, to
keep the connector on the true bottom edge and put all the new room above.
`usb-c-breakout`'s header comment describes doing the exact same hand
arithmetic for its testpoint row. Two boards independently doing the same
reserved-band math is the planner missing a primitive: something like
`place_board(blocks, reserve=[{"height_mm": 20, "position": "top"}])` would
turn this from arithmetic into a parameter.

## Consistency check the task asked for

`circuitpy.netwidth` (CLI) and the app's `board_net_widths` returned
*identical* numbers for `V5`/`V3_3` (ceiling 1.1/1.3mm, narrowest 0.5/0.5mm,
declared 0.5/0.5mm) after installing the project and running
`board_fast_check` + `board_net_widths` through the HTTP API. `board_fast_check`
also agreed with the CLI build's verdict (0 error / 0 warning / 8 info). No
CLI/app divergence found on this board — worth recording as a negative result,
since the task flagged disagreement as the most important possible finding.
