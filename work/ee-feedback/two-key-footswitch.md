# two-key-footswitch — EE feedback

## What I built

USB-C RP2040 footswitch: two keys (push-to-talk on GPIO2, mute on GPIO3), one
status LED, four M3 mounting holes (a footswitch gets stepped on — two
opposite corners, the fleet default, leaves the other diagonal unsupported).
Blocks: `usb-c-data`, `rp2040-core`, `ldo-3v3`, `sw-tact` x2, `status-led`,
`glue.tsx`. Board: **47.5 x 68.9mm**, 2 layers — smaller footprint than every
other built board in the fleet at time of writing, as intended for the
cheap/small end of the range.

## Verdict

`fab.ready: false`, **3 blocking findings**, all `part: "U3"`, all
geometrically inside `rp2040-core`'s own footprint (a via ~2.7mm off the
chip's own origin, near its C4-C8 decoupling row) — on nets this board never
routes anywhere near. Reproduced twice at `autorouterEffortLevel="10x"`,
byte-identical error text both times. BOM: 19 lines, 16 orderable. Gerbers are
kicad-cli verified (not the unverified tscircuit fallback).

## Friction, ranked by time cost

1. **A block-internal routing defect that effort escalation doesn't fix
   (biggest cost: ~30 minutes).** SKILL.md documents this exact failure mode
   for `rp2040-core` (desk-air-monitor's DVDD via) and says 5x/10x clears it.
   Here it didn't: 10x reproduces the same 3 errors deterministically, and a
   `100x` attempt (worth trying — this board has so few parts) ran 28 minutes
   with **no verdict at all** before I killed it — no partial result, nothing
   to poll but "still running." The documented "one escalation, bounded"
   ladder only fires for `pcb_autorouting_error`/`pcb_trace_error` kinds;
   `dfm_hole_clearance` and `drc_violation` (what I got) never trigger it, so
   this failure mode gets zero automatic help. It's a golden-block defect,
   not a placement one — I never touched rp2040-core's internals — so no
   board-level fix exists; it belongs in `rp2040-core`'s own routing.

2. **`board_fast_check` under-counts blocking findings by 2/3 on this exact
   board (~10 minutes of confusion avoided only because I happened to diff
   it against the full build).** Fast check reported `counts.error: 1`
   because it doesn't run kicad-cli DRC/ERC — and 2 of my 3 real blocking
   errors are `drc_violation` kind, invisible to it. The tool is honest about
   this (`notChecked` names KiCad explicitly), but "1 blocking finding" reads
   as "almost done" when the true number is 3. A small board is exactly where
   this bites hardest: with so little else on the board, the gap between fast
   check and full build is the whole story, not a rounding error.

3. **A hard, missing block dependency the brief didn't name (~5 minutes to
   catch, would have been a wasted full build otherwise).** `rp2040-core`
   requires `V3_3`; only `ldo-3v3` provides it; `usb-c-data` only ever gives
   `V5`. `circuitlib.helpers.board_plan()` confirmed the auto-pull, so this
   cost me one Python call instead of a failed compile, but a builder who
   composed literally from the brief's block list would burn a full 20-40
   minute build to learn it.

4. **No block for a real foot switch.** `sw-tact` is a 5.1mm SMD tactile
   switch, finger-rated. Built strictly from the golden library, this is a
   control PCB for two foot-actuated keys, not two SMD buttons a boot presses
   — the enclosure carries the mechanical load down to tiny pads. Not a bug,
   just a gap: no panel-mount switch or jack block exists yet.

5. **`board_net_widths` matched intuition and the code's own worked
   example.** V5 ceiling 1.1mm (real headroom over the 0.5mm power floor);
   GND ceiling 0.4mm at `U3.TESTEN` — capped by the RP2040's own 0.4mm pad
   pitch, below the floor, un-fixable by placement. Same phenomenon the tool's
   own comments describe for V3_3 on a different board. `board_source_write`
   + drifted-verdict detection also behaved exactly as documented: a
   no-`moves` re-check after the write correctly refused to grade stale
   geometry instead of silently answering wrong.
