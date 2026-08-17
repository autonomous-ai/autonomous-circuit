# dual-sensor-node — EE feedback

Built `products/dual-sensor-node`: USB-C RP2040 node, two BME280s on one I2C
bus (indoor on-board, outdoor via pigtail), two status LEDs, one button.
Composed from `usb-c-data`, `ldo-3v3`, `rp2040-core`, `i2c-bus`,
`sensor-bme280` x2, `status-led` x2, `sw-tact`, `glue.tsx` (`MountingHole`,
`GndPour`, `DebugPort`, `PadHeader`).

**Verdict:** `fab.ready: true`, first build, no retry. 95.3 x 68mm, 2 layers,
0 blocking, 178 warning, 389 info — in line with the fleet's warning/info
baseline (i2c-sensor-hub: 173/364). BOM 25 lines, 18 orderable.

## The two-instance question — this is what I was sent to check

Both fixes landed today held up completely. `place_board()` on a 9-entry list
with two `sensor-bme280` and two `status-led` returned all 9 placements
(`sensor-bme280#2`, `status-led#2`), zero overlap/fit warnings, carried end
to end: BOM lists `BME280,"U5,U6"` on one line, `cpl.csv` has both at
distinct coordinates, both PNGs show them cleanly apart (labelled
INDOOR/OUTDOOR). In the app, I then moved only U6 via `board_source_write`
(byte-range edit) and re-checked: `board_fast_check` with no `moves`
correctly reported `drifted: 1` instead of a stale "legal" (also a same-day
fix, also held), and with the matching `moves` entry the predicted-geometry
errors named exactly `U6`/`C20`/`C21` — never `U5`/`C18`/`C19`. No confusion
anywhere in plan, write, check, BOM, or gerbers. Straight answer: nothing
mishandled the second instance.

## Friction, ranked by time cost

**1. `sensor-bme280`'s own docs promise a fix that was never shipped, and the
fix lives somewhere git throws away (~25 min).** BLOCK.md/REVIEW.md both say
two BME280s on one bus need "the second one's SDO at VDDIO (0x77), which is
a block variant, not a board-level trace" — but the block hardwires
`SDO -> GND` with no prop to move it, and no such variant exists anywhere in
`packages/golden-blocks` or any product's `blocks/`. Without it this board's
point — two sensors, one bus — is an address collision. Fixed exactly as
the docs prescribe: one prop, `addr1`, patched into this project's local
`sensor-bme280.tsx`. Sharper problem: every product's
`blocks/` is `*`-gitignored by design (a "frozen snapshot," reproduced by
re-copying from the skill, never from git history). My patch lives inside
that ignored tree — anyone reproducing this product the documented way gets
the *unpatched* block back, both sensors silently at 0x76, no compile error
(an unread JSX prop is dropped). I force-added the one patched file
(`git add -f`) so this board reproduces from git; a workaround, not a fix.
The real fix is landing the SDO-select variant upstream.

**2. `PadHeader`'s `prefix` has no way to avoid a refdes collision with
`DebugPort` (~5 min).** The BOM gate only excuses bare-pad parts from
needing an LCSC number when the refdes's alpha part is exactly
`TP`/`FID`/`MH`/`H` (`checks.py::_UNSOURCED_PREFIXES`). `DebugPort` is
`PadHeader` at its default `prefix="TP"`, claiming `TP1`-`TP3`. A second
`PadHeader` for a bus breakout — exactly what the docstring shows — needs
its own prefix, but the prop is a bare string glued to `${i+1}`, no start
index. Landed on `prefix="TP4"` (`TP41`-`TP44`) instead of a readable
`TP4`-`TP7`. A `startIndex` prop fixes this for every board wanting both.

**3. Growing the board past `place_board()`'s output still needs a hand
reseat (~10 min).** Same finding i2c-sensor-hub recorded: furniture
`place_board()` doesn't plan for (`DebugPort` + `PadHeader` here) means
growing the outline by hand and re-deriving the edge-block formula at the
new height. Correct, but it's the same derivation twice now —
`place_board(..., extra_headroom_mm=N)` would remove it.

**4. Confirmed, not new: `V3_3`'s ceiling sits below the app's own power
floor.** `board_net_widths` gives `V3_3` a 0.4mm ceiling (RP2040 pad pitch),
below the 0.5mm `powerFloorMm` the DFM warning asks for — same gap
desk-air-monitor already hit. Any RP2040 board trips this; worth fixing the
advisory once rather than per board.
