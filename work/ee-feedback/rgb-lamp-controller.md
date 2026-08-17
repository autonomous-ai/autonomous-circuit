# rgb-lamp-controller — EE feedback

Built `products/rgb-lamp-controller`: USB-C desk lamp brain, RP2040 driving 8
on-board WS2812 pixels in a line, mode + brightness buttons, a status LED, and
a 3-pad header (DATA/5V/GND) to continue the strip off-board. Composed from
`usb-c-data`, `ldo-3v3`, `rp2040-core`, `ws2812-chain` (count=8), `sw-tact` x2,
`status-led`, plus `glue.tsx` (MountingHole, GndPour, DebugPort). No
`testpoint` golden block exists, so the off-board header is hand-rolled JSX
copying `DebugPort`'s pattern — glue, not an invented circuit. One documented
spec gap: the header's 5V pad feeds raw VBUS to a chain whose data stays
3.3V logic (no level-shifter block exists); `ws2812-chain/BLOCK.md` calls
that combination "unsupported" and I left it that way, not invented a fix.

**Verdict:** `fab.ready: true` on build #1 (5x, the documented floor, was
enough — no escalation needed). 84.9 x 68.6mm, 2 layers, 0 blocking, 194
advisory, 394 info. BOM 24 lines, 18 orderable. `gerberSource: kicad-cli`
(verified, not the tscircuit fallback).

## Friction, ranked by time cost

**1. `circuitlib.layout`'s planner can't size a parametric block or hold two
instances of one block id — cost ~35-40 min.** `place_row`/`place_board`/
`board_fits`/`overlap_warnings` all key `placements` by `block_id` in a plain
dict, and all call `box(block_id)` with no `count`. Two failures from one root
cause: a second `sw-tact` silently overwrote the first, and `ws2812-chain`
sized itself off the *default 4-pixel* box even though I asked for count=8.
Calling `place_board()` straight with my real block set returned a nonsense
149.8 x 51.4mm single-row board. I had to monkeypatch
`layout.BLOCK_BOX_MM["ws2812-chain"]` to the real 8-pixel box at runtime, add
a synthetic `"sw-tact-2"` key, and hand-run `place_row()` per row (two rows,
not the planner's one), verifying with `board_fits()`/`overlap_warnings()`
myself. This is the same bug `desk-air-monitor`'s build flagged for duplicate
`status-led`s — now confirmed to also break the one parametric block in the
catalog. One fix (thread an optional `count` and stop keying by bare
`block_id`) closes both reports.

**2. No `testpoint` glue helper, despite the pattern already existing inside
`DebugPort`.** Any board that wants "a few labeled pads on named nets" — this
one's off-board header — has no reusable component and has to copy
`DebugPort`'s internals by hand. A parametrized `TestPads(nets, labels, ...)`
next to `DebugPort` in `glue.tsx` would turn every future header into one
import.

**3. `scripts/check`'s doc promise doesn't match its output.** SKILL.md
describes it as "no kicad, no fab export... artifacts discarded" — cheap and
structural. What came back was full KiCad-grade DRC (`footprint_symbol_
mismatch`, `net_conflict`, `holes_co_located`, `duplicate_footprints`) at the
same volume as the real build's sidecar (194 warnings / 394 info either way,
nearly identical set). It's hard to use for its stated purpose — a free gate
before paying for the real thing — when it returns the real thing's noise.

**4. Real, but the gauntlet doesn't check it.** `helpers.board_plan().
regulator` correctly flagged the AMS1117 as thermally "marginal" at this
board's worst case (8 pixels full white + RP2040 peak, ~597mA through the
LDO — 110°C junction, 15°C headroom) — but nothing in the build gauntlet
surfaces this; it's a plan-time-only helper I had to remember to call myself.

**5. `board_fast_check` + `board_net_widths` told the truth — and a
previously-reported bug looks fixed.** After moving the pixel chain 3mm with
`board_source_write`, `board_fast_check` (no `moves` passed) correctly
reported `drifted: 1` with a specific explanation of what a stale verdict
would miss, rather than the silent stale "legal" `desk-air-monitor`'s builder
hit today. `board_net_widths` gave real, board-derived numbers matching the
build's own `dfm_power_trace_width` warnings: V5 ceiling 1.1mm (limited by
`U1.VBUS`), GND ceiling 0.4mm (limited by `U3.TESTEN`, the RP2040's own
0.4mm QFN pitch), both currently routed at 0.2mm. Verified true against the
sidecar, not just plausible.
