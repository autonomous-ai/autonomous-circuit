# desk-air-monitor — EE feedback

Built `products/desk-air-monitor`: USB-C desk device, temperature/humidity/
pressure on I2C (BME280), two status LEDs, one button, RP2040 brain. Composed
entirely from golden blocks (`usb-c-data`, `rp2040-core`, `ldo-3v3`,
`sensor-bme280`, `i2c-bus`, `status-led` x2, `sw-tact`) plus `glue.tsx`
(mounting holes, ground pour, debug port). Nothing hand-rolled.

**Verdict:** `fab.ready: true`. 61.0 x 64.8mm, 2 layers, 0 blocking, 170
advisory, 328 info. BOM 21 lines, 18 orderable. Took 2 full builds: build #1 at
the documented floor effort (`5x`) came back `fab.ready: false` with exactly
one blocking error; build #2 at `10x` cleared it with nothing else changed.

## Friction, ranked by time it cost or would cost the next engineer

**1. `5x` is called "the floor" but wasn't enough here — cost a full second
build (~12 min).** SKILL.md states `autorouterEffortLevel="5x"` is the floor
every board declares. On this 7-block board, `5x` produced one blocking KiCad
DRC error: a DVDD via clearance violation (0.0900mm required, 0.0722mm
actual) — entirely inside `rp2040-core`'s own footprint, on a net this board
never touches. `10x` (also documented, as an escalation path) cleared it with
zero other changes. If `5x` is not reliably sufficient for any board that
includes `rp2040-core` plus a few more blocks, the skill should say so and
recommend `10x` as the real floor for MCU boards, saving every future
RP2040-based board one full build.

**2. `circuitlib.layout` silently drops duplicate blocks — fix this one
first.** `board_plan()`, `place_board()`, and `place_row()` all key their
`placements` output by `block_id` in a plain dict. Asking for two
`status-led` instances (this board needs two) doesn't error or warn — it
just returns one entry, and the second LED vanishes from the plan with no
signal. Nothing told me; I only caught it by comparing dict length against my
block list. A less careful build would have silently shipped with one LED
missing from the layout math while the board file still had two
`<StatusLed>` tags — a mismatch the planner itself can't see. I hand-rolled
the two-row layout math instead (verified with `overlap_warnings()` /
`board_fits()` / hole-clearance calls before ever building) — the
responsible workaround, but it shouldn't take that for "two LEDs."

**3. `board_fast_check` after a raw `board_source_write` reports stale,
misleading results.** I moved `SwTact` 3mm right via `board_source_write`
(the byte-range API every real drag in the app uses), then called
`board_fast_check` with no `moves` — it came back `status: "legal", geometry:
"as_built", 0 errors`. That is the *old* position's verdict, not the one I
just wrote. Passing the matching `moves` array to the same endpoint returned
the truth: `status: "blocked", geometry: "predicted", 2 errors` (a trace that
left its pad, a copper overlap). `board_source_write` doesn't compute or
attach `moves` itself, so a caller who trusts the plain "legal" after a write
is trusting a check that never looked at the write. This is a real footgun:
the tool told me something false when I asked it the natural way.

**4. `board_plan(capabilities=[...])` capability names aren't discoverable
from the error.** I guessed `"usb"` and `"sensing"`; both came back in
`unavailable` with no hint of the real names (`usb-data`,
`sensor-environment`) — I had to read `circuitlib.blocks.CAPABILITY_INDEX`
source to find them. A near-miss suggestion in the `unavailable` list would
save a source dive every time.

**5. Confirmed, not new: the power-trace-width warning asks for something
impossible on this MCU.** `board_edit_apply`'s DFM check told me to widen
`V3_3` to the 0.5mm power floor; `board_net_widths` says `V3_3`'s ceiling
here is 0.4mm, capped by `U3`'s (RP2040) own pad pitch. Matches the
documented terminal-keyboard finding — worth fixing the advisory itself
rather than leaving every RP2040 board to trip over it.
