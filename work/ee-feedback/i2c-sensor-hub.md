# i2c-sensor-hub — EE feedback

Built `products/i2c-sensor-hub`: USB-C board that reads a BME280 and breaks
its I2C bus out to a bare-pad header for other sensors. Composed entirely
from golden blocks (`usb-c-data`, `ldo-3v3`, `rp2040-core`, `i2c-bus`,
`sensor-bme280`, `status-led` x2, `sw-tact`) plus `glue.tsx` (mounting holes,
ground pour, debug port). No pin-header block exists in the library, so the
SDA/SCL/3V3/GND breakout is four bare `testpoint` pads, same pattern
`examples/hydrate-coaster` uses for its own debug port — a library gap, not
an invented circuit.

**Verdict:** `fab.ready: true`, verified via `kicad-cli` (`gerberSource:
"kicad-cli"`). 95 x 67mm, 2 layers, 0 blocking, 173 advisory, 364 info. BOM
25 lines, 18 orderable (7 are bare-copper test points, correctly excused).
Fab packet complete (gerbers, BOM, CPL, board.glb, ORDER.md). Took 3 full
pipeline runs (1 `check` + 2 `circuit`) to get there.

## Friction, ranked by time cost

**1. `scripts/check` does not match its own documentation — cost ~20
minutes on a step that's supposed to be cheap.** SKILL.md: "Cheap structural
check — compile + circuit-json scan + checks library only. No kicad, no fab
export, artifacts discarded." My run returned `drc_violation` entries
referencing KiCad-specific structures (`board-F_Silkscreen.gto`, mm
clearances) and `gerber_drill_extra`/`gerber_silk_over_pad` findings — output
that only exists after a KiCad conversion and a gerber export. It took as
long as a full `circuit` build (~20 min) and found the same class of error a
full build would. Run before a full build to save time, it cost the same
time twice.

**2. Even the documented escalation (`10x`) still hit a blocking DRC error
on build #1 — needed a placement nudge, not just an effort bump.** I started
at `10x` up front (per the hydrate-coaster precedent for
`usb-c-data`+`rp2040-core`), and still got one blocking finding: a DVDD-net
via (RP2040's internal 1.1V core rail, entirely inside `rp2040-core`'s own
footprint) at 0.079mm clearance against a 0.09mm rule. Fix was widening the
gap between `rp2040-core` and its nearest neighbor (`i2c-bus`) from 2mm to
5mm — a board-level placement change for a violation inside a block's own
net. Corroborates `work/ee-feedback/desk-air-monitor.md`'s finding that `5x`
isn't reliably enough for `rp2040-core` boards; mine shows `10x` isn't a
guarantee either. Cost one full build (~9 min, cache-warm).

**3. The BOM gate's "this is bare copper, don't ask for an LCSC number"
carve-out only recognizes refdes prefixes `TP`/`FID`/`MH`/`H` with a
trailing digit — undocumented in `glue.tsx` or SKILL.md.** I named my four
breakout pads `PAD_SDA`/`PAD_SCL`/`PAD_3V3`/`PAD_GND` for readability; each
came back as a blocking `part_not_orderable` error ("BOM row has no LCSC
part number"). Renaming to `TP4`-`TP7` (DebugPort already owns `TP1`-`3`)
cleared it instantly. The rule lives in
`circuitpy/checks.py:_UNSOURCED_PREFIXES` with no pointer to it from
anywhere a board author would look before naming a testpoint.

**4. `circuitlib.layout.place_board()`/`place_row()` silently drop a
second instance of the same block — confirmed, not new.** Same finding as
`desk-air-monitor`: asking for two `status-led`s returns one placement, no
warning. I hand-placed the second LED and the debug port/breakout band in
the open space above the auto-placed row, verified with `board_fits()` /
`overlap_warnings()` before building.

**5. Positive — the app's move-prediction is honest.** After installing the
board as a project, `board_fast_check` (no moves) reported `legal, 0 errors`
matching the as-built sidecar. Moving `SensorBme280` 3mm via
`board_edit_apply` correctly flipped the verdict to `blocked, 40 errors`
(mostly `trace_left_its_pad` — "the part moved and its route did not
follow"), with `geometry: "predicted"` labeled honestly as a prediction, not
a rebuild. What the app told me was true both times.
