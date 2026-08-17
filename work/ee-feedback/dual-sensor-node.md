# dual-sensor-node — EE feedback

Built `products/dual-sensor-node`: USB-C RP2040 node, two BME280s on one I2C
bus (indoor on-board, outdoor via a pigtail header), two status LEDs, one
button. Composed from `usb-c-data`, `ldo-3v3`, `rp2040-core`, `i2c-bus`,
`sensor-bme280` x2, `status-led` x2, `sw-tact`, `glue.tsx` (`MountingHole`,
`GndPour`, `DebugPort`, `PadHeader`).

**Verdict:** `fab.ready: true`, first build, no retry. 95.3 x 68mm, 2 layers,
0 blocking, 178 warning, 389 info — in line with the fleet's existing
warning/info baseline (i2c-sensor-hub: 173/364). BOM 25 lines, 18 orderable.

## The two-instance question — this is what I was sent to check

Both fixes landed today held up completely. `place_board()` on a 9-entry list
with two `sensor-bme280` and two `status-led` returned all 9 placements
(`sensor-bme280#2`, `status-led#2`), zero overlap/fit warnings, and that
carried end to end: BOM lists `BME280,"U5,U6"` on one line, `cpl.csv` has both
at distinct coordinates, both PNGs show the two sensors cleanly apart
(labelled INDOOR/OUTDOOR). Then, in the app, I moved only U6 via
`board_source_write` (byte-range edit) and re-checked: `board_fast_check`
with no `moves` correctly reported `drifted: 1` instead of a stale "legal"
(also a same-day fix, also held), and with the matching `moves` entry the
predicted-geometry errors named exactly `U6`/`C20`/`C21` — never `U5`/`C18`/
`C19`. No confusion between the two instances anywhere in plan, write, check,
BOM, or gerbers.

## Friction, ranked by time cost

**1. `sensor-bme280`'s own docs promise a fix that was never shipped (~20
min).** BLOCK.md and REVIEW.md both say two BME280s on one bus need "the
second one's SDO at VDDIO (0x77), which is a block variant, not a
board-level trace" — but the block hardwires `SDO -> GND` with no prop to
move it, and no such variant exists anywhere in `packages/golden-blocks` or
any product's copied `blocks/` (checked before writing). Without it, two
on-board BME280s on one bus is a straight I2C address collision — this
board's whole point can't be built. Fixed it the way the docs already
prescribe: added one prop, `addr1`, to this project's local copy of
`sensor-bme280.tsx` (commented as a deviation). This belongs upstream in
`packages/golden-blocks` — every future two-sensor board hits this exact
wall otherwise.

**2. `PadHeader`'s `prefix` has no way to avoid a refdes collision with
`DebugPort` (~5 min).** The BOM gate only excuses bare-pad parts from needing
an LCSC number when the refdes's alpha part is exactly `TP`/`FID`/`MH`/`H`
(`packages/circuitpy/checks.py::_UNSOURCED_PREFIXES`). `DebugPort` is
`PadHeader` with the default `prefix="TP"`, claiming `TP1`-`TP3`. A second
`PadHeader` on the same board (any bus breakout, which is exactly what
`PadHeader`'s own docstring shows) needs its own prefix, but the prop is
just a string glued to `${i+1}` — no start-index. Landed on `prefix="TP4"`
(`TP41`-`TP44`, alpha-prefix still `TP`) rather than something readable like
`TP4`-`TP7`. A `startIndex` prop would fix this for every board that wants a
debug port and a breakout header, which is most RP2040 boards with a bus.

**3. Growing the board past `place_board()`'s output still needs a hand
reseat (~10 min).** Same finding i2c-sensor-hub already recorded: adding
furniture `place_board()` doesn't plan for (here, `DebugPort` + `PadHeader`)
means growing the outline by hand and re-deriving the edge-block's formula
at the new height so the USB-C mouth stays on the new edge instead of
floating in the extra headroom. Correct, but it's the same derivation twice
now across two boards — `place_board(..., extra_headroom_mm=N)` would remove
it.

**4. Confirmed, not new: `V3_3`'s ceiling sits below the app's own power
floor.** `board_net_widths` gives `V3_3` a 0.4mm ceiling here (RP2040 pad
pitch), below the 0.5mm `powerFloorMm` the DFM warning asks for — same gap
desk-air-monitor and terminal-keyboard already hit. Any RP2040 board trips
this; worth fixing the advisory once rather than per board.
