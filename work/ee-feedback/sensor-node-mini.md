# EE feedback — sensor-node-mini

Built with `usb-c-power` + `rp2040-core` + `ldo-3v3` + `sensor-bme280` +
`i2c-bus` + `status-led` + `glue.tsx`. Result: `fab.ready: true`, 0 blocking,
80.9 × 45.9mm, first build, `autorouterEffortLevel="5x"` (never needed 10x).
Friction below, ranked by time it actually cost.

## 1. `DebugPort` placement is still hand math (~20 min, the biggest cost)

`place_board()` sized and placed all six *measured* blocks with zero
warnings — genuinely good, and a first for me (no hand-computed rows like
`desk-air-monitor`/`rgb-lamp-controller` needed). But `DebugPort` isn't in
`BLOCK_BOX_MM` — it's "board furniture," not a measured block — so the
planner can't place it. I had to read `place_board`'s internals by hand
(`box()`, the row math, `HOLE_STRIP_MM`, `keepoutRadiusForHole`) to find the
one open strip on the board (row's right edge to the H2 keepout, ~5.4mm
wide) and land three test points there by hand-picked coordinate. This is
exactly the class of bug the planner fix just closed for measured blocks —
`DebugPort` (and presumably any other glue element) is the same problem one
level down. Every `usb-c-power` board will need this exact placement, so a
`place_board(..., debug_port=True)` option that reserves a slot the way it
already reserves the mounting-hole strip would pay for itself on build one.

## 2. `review_floating_pin`'s wording overstates the risk on USB_DP/USB_DM (~5 min)

With no USB data pair, `board_fast_check` correctly flags U3's USB_DP/USB_DM
as floating — true, they're unterminated. But the message reads "a strap pin
left floating boots at random," which is TESTEN/boot-strap language, not a
USB data pair's — a floating D+/D- doesn't put the chip in a random boot
state, it's a deliberate no-connect on any power-only USB board. Cost a few
minutes confirming it wasn't a real defect. `UsbCConnector` has an `ncPins`
prop for exactly this ("intentionally left unconnected") — `rp2040-core` has
no equivalent, so the warning can't be silenced or reworded even when correct.

## 3. `circuitlib.layout` isn't discoverable from `products/README.md` (~5 min)

The README says to use `place_board()` but doesn't say where it lives;
found it by grepping the repo (`skills/circuitcode/circuitlib/layout.py`).
Minor, but every builder pays this tax once.

## What worked, and was true when checked

- **The debug-interface gate is real.** `fab.py` escalates
  `review_debug_unreachable` to blocking — a `usb-c-power` board that forgot
  `DebugPort` would fail the build, not ship silently unflashable. Good
  guardrail for exactly the mistake this product could have made.
- **`board_fast_check`'s drift detection is honest.** After
  `board_source_write` moved `status-led` 2mm, an unqualified re-check
  correctly reported `"drifted": 1` and refused to claim the old verdict
  still applied; passing `moves` then predicted 7 real errors (traces
  stranded off their moved pads) — a true, useful answer, not a guess.
- **`board_net_widths` matched the file's own documented case.** V5 ceiling
  1.1mm, V3_3 ceiling 0.4mm (capped by the RP2040's own QFN pad pitch) —
  both currently routed at 0.2mm, both below the 0.5mm power floor warning
  already surfaced by `board_fast_check`. Numbers were internally consistent
  everywhere I checked them.

## The `usb-c-power` finding the brief asked for

The board **cannot** be flashed over USB — `usb-c-power` exposes no D+/D-,
and its own `BLOCK.md` says so plainly. The correct fix was not switching to
`usb-c-data` (this node never talks USB in the field) but adding
`DebugPort` test points wired to `rp2040-core`'s exposed `SWCLK`/`SWD` nets,
programmed once over SWD before it ships. The block library already has the
right piece for this (`DebugPort` exists for exactly this reason); the gap
is that placing it is still manual (see #1) and the library has no
first-class way to say "USB data is absent on purpose" (see #2).
