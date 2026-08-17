# macropad-6 — EE feedback

Built `products/macropad-6`: USB-C six-key macropad, 3x2 grid of tactile
switches on 19.05mm keycap pitch, direct-GPIO (no matrix, no diodes), one
status LED, RP2040 brain. Composed from `usb-c-data`, `rp2040-core`,
`ldo-3v3`, `sw-tact` x6, `status-led`, plus `glue.tsx` (4x mounting holes,
debug port, ground pour). `ldo-3v3` was not in the brief's block list but
was added anyway — `rp2040-core` requires `net.V3_3` and nothing else
provides it. Nothing hand-rolled outside glue.

**Verdict: `fab.ready: false`.** 66 x 84mm, 2 layers (well inside the
100x100mm/$2-for-5 tier). BOM 19 lines, 16 orderable. 14 blocking errors,
169 warnings, 317 info, after 3 full builds and 2 fast checks (~70 min
wall clock). Reported honestly, not chased further.

## Friction, ranked by time cost

**1. `rp2040-core`'s SWD/RUN corner is fragile and non-deterministic — the
dominant cost, ~45 of ~70 minutes.** Build #1 (2mm gap key field to
`rp2040-core`, `5x` effort): 11 blocking errors, all but 3 (switch shorts,
#2) inside `rp2040-core`'s own U3/U4 QSPI cluster — a DVDD via clearance
violation and a hole clearance violation, nothing my board touches. Trying
`10x` (SKILL.md: "go to 10x while the router is missing its own
clearances") made it *worse*: 64 errors, same cluster, plus a new
USB_DP/USB_DM via short absent at `5x`. Reverted to `5x`, re-ran the
*identical* file: 64 errors again, not 11 — unstable run to run,
independent of effort. Widening the key-field gap 2mm→6mm (board
80mm→84mm tall) cut it to 14, concentrated on one thing: `DebugPort`'s SWD
trace crosses `rp2040-core`'s RUN/reset corner (R12, SW3, RUN's own via)
to reach U3.SWD, dense enough that the router placed a via *inside* the
RUN pad. `BLOCK_GAP_MM=2mm` guarantees courtyards don't touch; it says
nothing about routing headroom for traces a board adds on top — six GPIO
escapes plus an SWD pair here. No board-level fix found for the RUN-corner
congestion itself; reads as inherent to the block.

**2. `sw-tact` leaves pins 2/3 unrouted — shorts against a board-level
`GndPour`.** The block only traces pin1 (signal) and pin4 (GND); pins 2/3
are the same physical nodes (`internallyConnectedPins: [[1,2],[3,4]]`) but
carry no net. With `GndPour`, the pour flooded up to the floating pin-3
pads and 3 of 6 switches (not all — depends on local pour geometry) came
back `shorting_items` against GND. Fixed by tracing pin3 to `net.GND` at
board level (electrically a no-op) — but this is exactly the class of
defect golden blocks exist to prevent; every board pairing `sw-tact` with
a ground pour hits it.

**3. `scripts/check` is documented as cheap and isn't.** SKILL.md: "cheap
structural check... no kicad, no fab export." It actually ran the full
kicad ERC/DRC/DFM gauntlet — same error classes, ~7-25 min wall clock like
`scripts/circuit` — then discarded the artifacts. Two `check` runs cost as
much as a full build each, for a verdict that turned out non-deterministic
(#1). The doc should say what it actually does.

**4. The placement editor cannot move a key.** `board_edit_apply` requires
a placement's `pcbX`/`pcbY` to be a literal number in the source; my key
field uses named constants (`x = (c - 1) * PITCH`) for the same
traceability reason the file's other coordinates are documented. Result:
`SwTact[1]` and `StatusLed[1]` are absent from the placement index
entirely (`PLACEMENT_NOT_FOUND`); only the mounting holes (bare number
literals) were movable. Nudging `MountingHole[2]` by 0.5mm was genuinely
pleasant — sub-second, a plain-English summary (`moved MountingHole H2
from (22, -36) to (22, -35.5)`), inline verdict in the same response.
Nudging a key — the one interaction a human opens this board to do — was
not possible through the API a real drag would use.
