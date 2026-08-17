# env-logger-usb — EE feedback

Built `products/env-logger-usb`: USB-C environment logger streaming
temperature/humidity/pressure over USB serial. Composed entirely from golden
blocks (`usb-c-data`, `ldo-3v3`, `rp2040-core`, `sensor-bme280`, `i2c-bus`,
`status-led` x3, `sw-tact`) plus `glue.tsx` (`PadHeader`, `MountingHole`,
`GndPour`). No invented circuits.

**Verdict:** `fab.ready: true`, 0 blocking, 172 advisory (`warning`), 355
info. 99.6 x 64mm, 2 layers. BOM 23 lines, 18 orderable. Fab packet complete.
One full build cleared it — `autorouterEffortLevel="5x"` was enough;
`build.attempts: 1` and `autorouterEffort: "5x"` in the sidecar, so the new
floor-escalation to `10x` never triggered on this board (neither of the two
outcomes the brief warned about happened — it just wasn't needed).

## Friction, ranked by time cost

**1. `circuitpy.preflight` timed out at its own 300s ceiling three times in
a row, on a board an out-of-band identical reproduction compiled in 15s —
cost ~20 minutes.** Same project, same `routingDisabled` patch, same
`toolchain` env: run through the module it hit `TimeoutError: node timed out
after 300s` three separate times; run by hand in a scratch mirror (same
patch, same `NODE_PATH`/`PATH`) it finished in 15.1s, matching the tool's own
"~17s" claim. `ps` showed the module's own node process alive and burning
100%+ CPU mid-run, not hung — reads as contention on a shared machine
outrunning a hard-coded 300s ceiling, not a board bug. A promised-fast gate
can silently eat 5 minutes three times before an engineer thinks to
reproduce it by hand, and there's no `--timeout` to raise or contention
signal in the JSON to explain the miss. Worked around it by calling
`circuitpy.fastcheck.fast_check()` directly on a manually-built
`circuit.json` — same grading, no subprocess ceiling.

**2. My own silkscreen labels collided with a block's own auto-placed
refdes text — caught by the grading, cost ~10 minutes to trace and fix.**
`"USB-C 5V"`, `"PWR"`, `"LOG"`, `"ERR"` landed within ~1mm of `usb-c-data`'s
R3/R4 and each `StatusLed`'s own resistor refdes silkscreen, all flagged
`silk_text_overlap`. Nothing in `glue.tsx` or the block READMEs says where a
block's own part-name silkscreen sits, so a board author placing a caption
near a block has to find out by building. Moving the labels 2-7mm further
from the block origin cleared it.

**3. `place_board()` sizes the board from content with no "reserve N mm of
open space here" knob — cost ~15 minutes of hand arithmetic to land
`PadHeader`.** The plan for this block set came back a clean 99.6 x 51.4mm,
but that left only 2.06mm between `rp2040-core`'s crystal/BOOTSEL cluster and
`usb-c-data`'s inward edge — not enough for the breakout header glue.tsx says
must sit in open board space, clear of the MCU's own box. Growing the board
meant re-deriving `place_board`'s internal edge-placement formula by hand
(`box_center_y = -height/2 + EDGE_MARGIN_MM + edge_h/2`, minus the origin
offset) to re-seat `usb-c-data` and the mounting holes at the new height —
the same shape of manual fix `i2c-sensor-hub`'s feedback already reported.
`place_board` taking an optional `extra_band_mm` (or a furniture list with
its own measured box, the way `PadHeader` deserves one) would make this a
one-line change instead of hand math.

**Positive.** `PadHeader` did what the brief wanted — one header,
`nets=["SDA","SCL","SWCLK","SWD","GND"]`, no floating nets
(`checks.floating_net_warnings()` returned empty). App-side: `board_fast_check`
without moves agreed exactly with the full build's sidecar
(`lastBuild.blocking: 0`, `invisibleHere: 0`) — no discrepancy to report.
Moving `SW1` 2mm via `board_source_write` and re-checking *without* `moves`
correctly refused to grade the new geometry (`drifted: 1`, told me to pass
`moves` or rebuild) rather than silently answering about stale copper;
passing `moves` correctly predicted the induced courtyard/pad-clearance
overlap with LED3, then cleared once restored. `project_create`'s
`{"req":{...}}` quirk is already fixed — both the wrapped and flat body
shapes created a project correctly.
