# harness-puck — build loop and design-review panel

Board: `boards/main.tsx` · 70mm round · 2 layers · 1.6mm · JLCPCB economy PCBA.
Toolchain: tscircuit 0.0.2279 · @tscircuit/checks 0.0.152 · kicad-cli 10.0.5.
All builds run `CIRCUIT_PARTS_ENGINE=off` (offline, parts resolve from the
blocks' pinned LCSC numbers).

---

## Part 1 — circuitcode repair loop (before the panel earns its time)

The panel does not review a board that fails the gauntlet. These are the
gauntlet rounds.

### Build round 1 — the ring landed on top of the MCU

`ok: true`, **213 error**, 258 warning, 440 info.

Headline: `pcb_packing_error` — "PackSolver2 ran out of iterations" — plus 26
courtyard overlaps between ring pixels (D10–D15) and U2/U3/U4/SW1/SW2, and 15
pad-pad clearance errors. Every one of them was one root cause: the ring helper
returned a `<group>` with **no `pcbX/pcbY`**, and an unpositioned group hands
the whole subtree to auto-layout, which repacked the ring into the middle of
the board. `ws2812-chain.tsx` warns about exactly this in a code comment; I
read it and still did it.

**Fix:** explicit `pcbX/pcbY/schX/schY` on the ring's outer group, and the
per-pixel inner groups replaced with a flat `flatMap` of keyed elements so no
unpositioned group exists anywhere in the tree.

### Build round 2 — the crystal net could not be routed

`ok: true`, **135 error**, 258 warning, 418 info. Placement now holds; routing
was skipped entirely.

Two real findings:

1. `pcb_autorouting_error` — *"the 10mm maximum length for `.Y1 > .pin1` to
   `.U3 > .XIN` cannot be satisfied: its endpoints are 11.78mm apart."* This is
   a **golden-block defect, not a board defect**: `rp2040-core` places Y1 at
   `pcbX={-11}`, which puts Y1.pin1 at (−14.10, 10.15) while U3.XIN is at
   (−2.60, 7.57) — 11.78mm, past the router's hard ceiling on the crystal net.
   Any board built from this block is unroutable out of the box. Routing is
   skipped wholesale when this trips, which is what produced the other 133
   errors (39 `pcb_trace_missing`, 38 `pcb_port_not_connected`, and the
   `unconnected_items` DRC cascade).
   **Fix, and its cost:** patched the *project's* frozen copy of the block —
   Y1 −11→−8, C15 (−11, 3.5)→(−8, 2.5), C16 −11→−8, R11 −7.5→−5.5. Placement
   only; no net, value, footprint or pinout changed, and the deviation is
   recorded in the block's own header comment. The upstream fix belongs in
   `packages/golden-blocks/blocks/rp2040-core/`.
2. `pcb_courtyard_overlap_error` C3/C23 — my ring-bulk cap sat 0mm from the
   logic LDO's output cap. Moved C22/C23 out to y = −19.

### Build round 3 — routed, 31 errors left

`ok: true`, **31 error**, 264 warning, 416 info. First build that actually
routed.

What was left: 5 `pcb_via_trace_clearance_error` (all inside the RP2040 escape),
20 KiCad `[clearance]` violations at 0.05–0.098mm against a 0.1mm rule (U3, U4,
Y1, DVDD), one `[hole_clearance]` where a GND track ran 0.133mm from a mounting
hole, and — the one that mattered — `Via [BTN_MODE] … [shorting_items] Items
shorting two nets (nets BTN_MODE and GND)`. A real short, produced by the
router, in a congested channel.

Diagnosis: this is a *width* problem before it is a placement problem. The board
declared `minTraceWidth="0.2mm"` and the RP2040 is a 0.4mm-pitch QFN-56. A 0.2mm
trace between two 0.2mm-wide pads on a 0.4mm pitch leaves nothing; the router
then squeezed vias into whatever gaps were left and the DRC found them.

**Fixes:**

- `minTraceWidth` 0.2mm → **0.15mm**. Still above `tables.WARN_TRACE_WIDTH_MM`
  and well above the 0.127mm blocking floor, and still enough copper for the
  loads on this board: `trace_width_for(current_a=0.48)` = 0.127mm for the
  worst-case pixel rail, `0.1A` = 0.127mm for logic. 0.15mm is not a compromise
  on ampacity, it is room for the escape.
- **SW1 moved to the board centre** — (0, −11) → (−2, −3). It fits in the 9 × 7mm
  gap between the RP2040's own BOOTSEL (SW2) and RESET (SW3), which makes the
  delegate key 3.6mm off the true centre of a 70mm puck instead of 11mm off.
  Better product, and it opens the middle of the board as a routing channel.
- SW4 (mode) (20, −1) → (17, 4); U2 (10, −10) → (11, −11); U5 (−10, −10) →
  (−12, −11); H2/H3 out to radius 22.8mm; ring-bulk caps to (±18.5, −19.5).
- Schematic re-laid-out: everything was scattered across an 83 × 58 unit sheet
  and rendered unreadably small. Compacted to ~67 × 34, power left → brain
  centre → I/O right, ring along the bottom.

### Build round 4 — one courtyard, and routing skipped again

`ok: true`, **135 error**. Exactly one placement error —
`Courtyard of U4 overlaps with courtyard of SW4` — and the router refuses to run
with any placement error outstanding, so 134 cascaded behind it.

My mistake, and worth writing down: I sized U4 from its footprint *name*
(`soic8_pillpads_w9.3102mm…`) and assumed 9.3mm across x. The real component
bbox from `main.circuit.json` is **4.44 × 9.31mm** — the long axis is y. Since
then I read component extents out of `main.circuit.json` (`pcb_component`
`center/width/height`) instead of estimating them from footprint strings.

**Fix:** SW4 (17, 4) → (18.5, 2).

### Build round 5 — routed clean apart from four

`ok: true`, **4 error**, 259 warning, 430 info.

- 3 × `[hole_clearance]` — GND tracks 0.115–0.172mm from a drilled hole against
  a 0.2mm rule.
- 1 × `[clearance]` at Y1, 0.085mm against 0.09mm.

**Fixes attempted:** mounting holes Ø3.2 (M3) → **Ø2.2 (M2)**, buying 0.5mm of
radial room around each; and the crystal cluster nudged another 0.5mm out to
open up Y1.

### Build round 6 — the crystal nudge was a regression

`ok: true`, **133 error**. `pcb_autorouting_error`: *"the 10mm maximum length
for `.C15 > .pin1` to `.U3 > .XIN` cannot be satisfied: its endpoints are
10.29mm apart."*

The binding constraint on the crystal cluster is **C15, not Y1**. At the round-2
position C15.pin1 sits 9.89mm from XIN — 0.11mm inside the ceiling. Reverted the
0.5mm nudge; kept the M2 holes. Recorded in the block's header so the next
person does not repeat it.

### Build round 7 — two left, and they are not mine

`ok: true`, **2 error**, 262 warning, 430 info.

Both are `[hole_clearance]`, and localising them (script over
`main.circuit.json`: every trace segment against every hole) puts them at
**(±2.9, −28.09)** — the two NPTH alignment holes inside `usb-c-power`'s
imported TYPE-C footprint. The mounting holes were innocent: their closest
copper edge is 0.335mm, 2.40mm and 2.14mm.

The geometry, measured off the footprint: the NPTH edge sits at x = 3.20 and the
pin1 shell plated-hole's outer edge at x = 3.725. That is a **0.525mm channel**.
At a 0.2mm rule the widest legal track through it is 0.125mm — **below the
0.127mm fab floor**. No track may legally pass, and the router put two GND
segments through it anyway (x = 3.41 and x = −3.46) on their way from the
connector's shell tabs to its shell plated holes. The router does not have to
use that channel — x = ±4.33 straight down is clear — it simply chose it.

This is a **block + router defect, not a board defect**, and the board file
cannot close it: the geometry is inside the block's committed footprint and the
path is the autorouter's choice.

### Build rounds 8–11 — the router will not converge, and why

Three attempts to close the last two errors, each rebuilt and measured:

| # | Change | Result |
|---|---|---|
| 8 | USB block 0.5mm further out (receptacle flush with the rim) | **136 error** — C1 (the block's VBUS bulk) courtyard-collides with ring pixel D17. The bottom of the ring and the connector's support parts are 0.05mm apart at pcbY = −29; there is no room to move J1 outward. Reverted. |
| 9 | Two `<keepout shape="circle" radius="0.6mm">` over the NPTHs at (±2.9, −28.094) | **4 error** — the keepouts *work*: both `[hole_clearance]` violations are gone. But the reroute they force comes back worse: a **via inside U2.VOUT1's pad**, plus **V5 and LED_DATA left unconnected**. r = 0.6mm was picked so a 0.15mm centreline stays 0.225mm clear of the hole while missing J1's own copper (nearest pad is 0.618mm from the hole centre). |
| 10 | Keepouts + U2 nudged 0.5mm, C23 nudged 0.5mm | **7 error** — a different handful: R11/R12 `[clearance]` down to 1e-06mm, C9/R12 `[hole_clearance]`, V5 and U3 unconnected. |
| 11 | Reverted to the round-7 geometry (keepouts removed, documented in the source) | **2 error** — reproduced exactly. This is the shipped state. |

The pattern across 8–11 is the finding: **at 182 source traces, 159 routed traces
and 127 vias on two layers with no ground plane, this board sits past what the
local autorouter does reliably.** Every reroute returns a different handful of
sub-0.1mm clearance flukes; nudging parts moves the flukes around rather than
removing them. `references/pcb-layout-craft.md` says the router "degrades past
roughly 50 traces" and that is what this is.

The two fixes a competent EE would reach for are both unavailable here:

- **A ground plane.** `<copperpour layer="bottom" connectsTo="net.GND" />`
  compiles, but the fill stops 0.200mm from the board edge while the exported
  KiCad rule demands 0.290mm, so every pour is a blocking
  `[copper_edge_clearance]`. Six candidate board props were tried
  (`minBoardEdgeClearance`, `boardEdgeClearance`, `minEdgeClearance`,
  `minCopperToEdge`, `minTraceToBoardEdgeClearance`,
  `minBoardOutlineClearance`) — all silently ignored. A GND plane would remove
  roughly 40% of the routed nets and most of this class of error.
- **Copper on the regulator tab.** `<copperpour>` is board-wide per layer, so it
  cannot be given to one net's tab.

## Final gauntlet state (build round 11)

| | |
|---|---|
| `ok` | true |
| Warnings | **2 error · 262 warning · 430 info** |
| The 2 errors | both `drc_violation [hole_clearance]`: GND tracks at 0.1396mm and 0.1848mm from the TYPE-C footprint's NPTH alignment holes, against a 0.2mm rule |
| `fab.ready` | **false** — a packet is not fab-ready with any `error`-severity warning outstanding. Gerbers were exported by **kicad-cli 10.0.5** (not the tscircuit fallback), so `main_fab/gerbers.zip`, `bom.csv` and `cpl.csv` are real, but `ORDER.md` is not written and the packet must not be called orderable |
| Board | 70.0 × 70.0mm, rounded to a 70mm circle, 2 layers, 1.6mm |
| BOM | 58 placements, 58 orderable, 18 unique parts, 6 extended |
| Geometry | 221 SMD pads · 4 plated holes · 5 drilled holes · 159 routed traces · 127 vias |

The 262 warnings are dominated by KiCad schematic-parity noise from the
tscircuit→KiCad conversion, not by the design: 57
`[footprint_symbol_field_mismatch]` (a `Description` field the converter leaves
empty), 74 `[net_conflict]` (the converter does not carry net names onto pads),
50 `[footprint_symbol_mismatch]` (tscircuit footprints have no KiCad symbol
library behind them). Three are real and design-owned:
`pcb_trace_too_long_warning` at 14.12mm, 10.10mm and 16.70mm — the USB pair and
the LED_DATA run, against tscircuit's generic 10mm ceiling.

---

## Part 2 — the seven-lens panel

Evidence read for this round: `boards/main_review/_pcb.png`,
`boards/main_review/_schematic.png`, `boards/main.board.json`, `product.json`,
`boards/main.tsx`, and every `BLOCK.md` in `blocks/`. There is no `parts.json`;
`parts-book` has not been run on this project.

```design-review
{
  "board": "boards/main.tsx",
  "round": 1,
  "verdict": "iterate",
  "ready_to_make": false,
  "lenses": [
    {"lens": "power", "score": 6, "notes": [
      {"severity": "must-fix", "target": "U5",
       "detail": "U5 burns (5 - 3.3) x 0.48 = 0.82W at the ws2812-chain block's own worst case of 60mA/pixel x 8, in a SOT-223 whose only copper is its footprint. ldo-3v3's BLOCK.md budgets <=500mA continuous *with a poured tab*; this board has no pour, so junction temperature at worst case is not defensible.",
       "fix": "pour the tab net, or clamp aggregate ring duty in firmware and say so in the datasheet, or replace both LDOs with one buck (no block exists)"},
      {"severity": "should-fix", "target": "net.V5",
       "detail": "VBUS carries 30uF of bulk (C1 10uF from usb-c-data plus C2 and C20, the two LDO input caps at 10uF each). USB 2.0 limits VBUS bulk to 10uF for inrush; a fussy host port can trip on hot-plug.",
       "fix": "drop one LDO input cap - needs a prop on ldo-3v3, which has none"},
      {"severity": "should-fix", "target": "board",
       "detail": "no ground plane. Eight WS2812 drivers switch three constant-current channels each with fast edges and every return is a trace on a 2-layer board.",
       "fix": "bottom-layer GND pour - blocked today by [copper_edge_clearance], see build rounds 8-11"},
      {"severity": "consider", "target": "C4-C11",
       "detail": "the RP2040's eight 100nF sit 2.2mm from the nearest pad edge (rp2040-core's internal placement), not the 1mm a QFN wants.",
       "fix": "block-level; leave it"}
    ]},
    {"lens": "manufacturability", "score": 7, "notes": [
      {"severity": "should-fix", "target": "D10-D17",
       "detail": "the WS2812B 5050 land pattern is from the datasheet, never reflowed, and 5050 pixels are the classic rotation defect. Eight of them at eight different rotations (22.5 deg steps) is eight chances to get pin 1 wrong.",
       "fix": "check the JLC placement preview against _pcb.png before paying, pixel by pixel"},
      {"severity": "should-fix", "target": "J1",
       "detail": "TYPE-C-31-M-12 is an SMD+through-hole hybrid; JLC's rotation convention for it differs from tscircuit's and the placement preview is the only safety net.",
       "fix": "same preview check"},
      {"severity": "consider", "target": "silkscreen",
       "detail": "'AUTONOMOUS HARNESS' at (0, 19.2) crowds the C4-C8 refdes row. Legible, ugly.",
       "fix": "move to (0, -10.5) on the next reroute-tolerant change"}
    ]},
    {"lens": "layout", "score": 6, "notes": [
      {"severity": "must-fix", "target": "J1 NPTH (+/-2.9, -28.094)",
       "detail": "two GND tracks run 0.1396mm and 0.1848mm from the connector's alignment holes against a 0.2mm rule. The channel they use is 0.525mm wide and cannot legally carry any track above the 0.127mm fab floor.",
       "fix": "keepouts fix it and break other things (round 9); the real fix is a keepout in the block's own footprint plus a router that honours NPTH clearance"},
      {"severity": "should-fix", "target": "net.USB_DP / net.USB_DM",
       "detail": "the pair runs ~40mm from J1 at the back rim to U3 in the upper half, unmatched, no controlled impedance, and the toolchain flags 14.12mm and 16.70mm segments. Full-speed USB tolerates this; nothing here proves it.",
       "fix": "none available - U3 cannot move nearer the rim without displacing the ring"},
      {"severity": "consider", "target": "H1/H2/H3",
       "detail": "measured closest copper edge is 0.335mm, 2.395mm and 2.138mm - the mounting holes are clean.",
       "fix": "none"}
    ]},
    {"lens": "testability", "score": 6, "notes": [
      {"severity": "should-fix", "target": "net.SWCLK / net.SWD",
       "detail": "rp2040-core brings SWD out as nets and nothing on this board lands them anywhere. There is no way to attach a debugger. Recovery still works over BOOTSEL+UF2, so it is not fatal.",
       "fix": "a 3-pad SWD footprint - blocked: <testpoint> emits a BOM row with no LCSC number, which the DFM gate raises as an error-severity part_not_orderable"},
      {"severity": "should-fix", "target": "net.V3_3_LED",
       "detail": "LED1 proves the logic rail only. If U5 fails, the single symptom is a dark ring, which is also the symptom of a dead first pixel, a bad GPIO16 and wrong firmware.",
       "fix": "a second status-led instance on V3_3_LED (LED2/R21) - one block instance, ~10 more traces"},
      {"severity": "consider", "target": "C1/C3/C21",
       "detail": "rails are probed at 0805 bulk-cap pads, silkscreened '5V', '3V3', 'LED3V3'. Big enough for a probe tip; not as good as real test points.",
       "fix": "none available"}
    ]},
    {"lens": "cost", "score": 7, "notes": [
      {"severity": "should-fix", "target": "board",
       "detail": "parts-book has never run on this project; there is no parts.json, so no stock or price has been re-checked since the blocks were authored on 2026-08-10. Do not order against a BOM nobody re-priced.",
       "fix": "run parts-book --lookup before ordering"},
      {"severity": "consider", "target": "R3/R4 and D10-D17",
       "detail": "$18 of a ~$57 order is extended-part loading fees on 6 lines. A Basic 27R 0402 in usb-c-data and a Basic WS2812 equivalent would take two of them out, ~$6.",
       "fix": "parts-book, block edit, layout survives (same footprints)"}
    ]},
    {"lens": "safety", "score": 8, "notes": [
      {"severity": "consider", "target": "J1 CC1/CC2",
       "detail": "usb-c-data spends both USBLC6 channels on D+/D-, so the CC pins reach the 5.1k pulldowns with no ESD device. They are exposed contacts on a desk object people will touch.",
       "fix": "block-level; a second ESD array or a 4-channel part"},
      {"severity": "consider", "target": "U5",
       "detail": "at the worst case above, U5 runs hot inside a sealed printed shell. The AMS1117 thermally shuts down rather than failing dangerously, and 0.82W cannot make the shell hot enough to burn, so this is a reliability item, not a hazard.",
       "fix": "vent the shell over U5, or the power must-fix above"}
    ]},
    {"lens": "product-fit", "score": 7, "notes": [
      {"severity": "should-fix", "target": "SW1",
       "detail": "the delegate key sits at (-2, -3), 3.6mm off the centre of a 70mm puck, because the RP2040 owns the middle. A cap centred on the shell will rock unless the shell gives the plunger its own guide bore.",
       "fix": "enclosure requirement - state it on the drawing; it is not a board change"},
      {"severity": "should-fix", "target": "J1",
       "detail": "the receptacle's mating face sits 0.35mm inside the board edge. A thick shell wall will stop a plug's overmould from seating.",
       "fix": "move J1 out - attempted in round 8 and it collides C1 with D17; needs C1 moved inside usb-c-data"},
      {"severity": "consider", "target": "the ring",
       "detail": "8 pixels, evenly spaced, with the break at the back where the cable leaves. One pixel per agent slot reads exactly like the product. This part works.",
       "fix": "none"}
    ]}
  ],
  "must_fix_count": 2,
  "bring_up": "plug USB-C, expect LED1 lit and 3.30V +/-3% on both C3 and C21; then hold SW2 while tapping SW3 and the puck enumerates as RPI-RP2.",
  "blocking_warnings": 2
}
```

**Verdict: iterate — and the two must-fixes cannot be closed from the board
file.** Scores, lowest first: power 6, layout 6, testability 6, manufacturability
7, cost 7, product-fit 7, safety 8. The bar wants every lens ≥ 7, power and
safety ≥ 8, zero must-fix and zero blocking warnings; this board misses on four
counts.

Both must-fixes were attempted and measured rather than argued:

1. **U5's 0.82W with no tab copper.** The fix is copper on the tab net.
   `<copperpour>` only takes a whole layer, so it cannot serve one net's tab, and
   a whole-layer pour is itself blocked (build rounds 8–11). The remaining
   options are a firmware clamp on aggregate ring duty — a constraint the
   hardware does not enforce — or a buck block that does not exist. **Open.**
2. **The two `[hole_clearance]` violations at J1.** Keepouts close them and the
   router then breaks something worse (round 9). **Open.**

Round 2 of the panel would route notes that circuitcode has no lever to act on,
so it is not run. The honest hand-off, in order of leverage:

- **Give the pipeline a per-net pour, or make `<copperpour>` respect a settable
  board-edge clearance.** One change fixes the regulator thermals, the return
  paths, and most of the router's clearance flukes at once. Highest leverage by
  a distance.
- **Add a keepout around the NPTH alignment holes inside `usb-c-power`'s
  footprint** so no board inherits the 0.525mm channel.
- **Move Y1/C15/C16/R11 in `rp2040-core` upstream** (this project has a local
  patch); C15 is the binding constraint at 9.89mm to XIN.
- **Let `<testpoint>` out of the BOM**, or give the DFM gate a do-not-place
  concept, so a board can carry rail and SWD probe pads without a blocking
  `part_not_orderable`.

## What the panel could not check

Thermal behaviour, EMI, signal integrity and real-world part fit are outside
every deterministic tool in this pipeline. The 0.82W figure is arithmetic; the
junction temperature behind it is not, because θja for this exact copper is
unknown. The USB pair's 40mm run is judged by rule of thumb, not simulation. The
WS2812 land pattern, the TS-1187A pad pairing and the crystal load are all
datasheet readings that have never met a reflow oven. Every one of those is
judgement, and it is labelled as judgement.
