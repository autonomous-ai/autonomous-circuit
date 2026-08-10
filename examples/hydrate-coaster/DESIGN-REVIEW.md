# hydrate-coaster — design review

Two logs. First the **build loop** (`circuitcode`: does the pipeline accept it), then the
**panel** (`design-review`: would a competent team sign it off). Newest last in both.

---

## Build loop

| # | Change | Errors out | Notes |
|---|---|---:|---|
| 1 | First cut: USB-C + LDO + RP2040 + 2 LEDs + button + two `copperpour` electrodes + a bottom-layer GND pour. 80×80mm, `minTraceWidth="0.2mm"`. | **122** | The autorouter never ran. `pcb_autorouting_error` on Y1: tscircuit applies `DEFAULT_CRYSTAL_MAX_TRACE_LENGTH_MM = 10` to every crystal net and `Y1.pin1`→`U3.XIN` is **11.78mm** in the shipped `rp2040-core` block. Reproduced with the block alone on an empty board — **the block cannot route on any board as shipped.** |
| 2 | Project-local placement fix in `blocks/rp2040-core`: Y1/C15/C16/R11 moved into the empty slot under the QFN, beside XIN/XOUT. Longest crystal hop 8.44mm. | **66** | Routing now runs. The damage moves to the QFN fanout: `shorting_items` on DVDD/QSPI, `pcb_trace_error`, 17 × `hole_clearance`. Root cause read off `_pcb.png`: USB_DP/DM sit on the chip's *top* side while the USB connector is at the board's *bottom* edge, so the pair crossed the whole 0.4mm-pitch chip. Also: the bottom-layer GND pour came back `isolated_copper` — every pad on this board is top-side, so a bottom pour has nothing to bond to. |
| 3 | `<group pcbRotation={180}>` around `Rp2040Core` (USB/QSPI side now faces the connector). `minTraceWidth` 0.2 → 0.15mm — 0.2mm is wider than the 0.4mm-pitch QFN escape can take. Bottom GND pour deleted. | **3** | Left: 2 × `hole_clearance` and one `clearance` at Y1, all vias squeezed into the 2.8mm gaps beside the crystal by the cup-sense nets, which were on GPIO12–14 (chip top side). |
| 4 | Cup-sense nets moved to GPIO2/3/4 and LED/button to GPIO0/1 — all on the chip's **right** side, exiting into the empty x = −8…9 corridor. Chip's top side left to the crystal and RUN/SWD. | **2** | Both `hole_clearance`, tracks threading the 0.72mm channel between the USB-C receptacle's alignment hole and its mounting pill. New warning: `Zone [CAP_A] isolated_copper` — moving the pour anchor R32 to x=−8 let its exit trace slice a corner off the plate. |
| 5 | Anchors R32/R33 back to (±16, 2). Explicit routing tolerances on the board: `minTraceToPadEdgeClearance="0.25mm"`, `minViaEdgeToPadEdgeClearance="0.25mm"`, `minViaHoleEdgeToViaHoleEdgeClearance="0.25mm"`, `minPlatedHoleDrillEdgeToDrillEdgeClearance="0.3mm"`. Schematic spacing tightened. | **220** | **Regression — the wrong lever.** `minTraceToPadEdgeClearance` is applied as a general clearance, so tscircuit's own checker started reporting every trace pair under 0.25mm: `too close to trace (gap: 0.238mm)`, ×200. Every one of those gaps is *above* JLC's 0.127mm floor; the board did not get worse, the bar did. Reverted. |
| 6 | Back to round-4 tolerances, keeping only the two hole-to-hole ones (they cannot produce trace-to-trace findings). LDO moved from (14, −18) to (16, −13) so the V5 run leaves the receptacle **upward** through the corridor instead of hugging its hole row; USB block nudged to y = −34.8 to close the routing channel under it. | **2** | Same two `hole_clearance` findings plus a via landing tangent to a VBUS pad. |
| 7–15 | Nine more runs chasing those last two errors. Measured, each a full rebuild: pour anchors to the middle of the plates (x = ±16) → **28**; LDO tucked beside C1 → **123** (C1/C2 courtyard overlap) then **5** once separated; LEDs moved right of the connector → **4**; **4 layers** → **2 errors but zero BOM rows and no gerbers** (the pipeline's exporters are 2-layer only); `parts.json` removed → no change (ruled out as a variable). | **2** | Reverted to the best measured configuration: anchors at x = ±8, LDO at (14, −18), LEDs left of the USB. The build is fully deterministic — three runs of identical source gave byte-identical error lists — so this is a floor, not a dice roll. |

Build cost, for anyone repeating this: **10–20 minutes per run** on this machine with the
local router working ~90 nets, and the three reference boards building at once.

---

**Final build: `ok: true`, 2 error / 186 warning / 334 info, `fab.ready: false`.**
182 of the 186 warnings are tscircuit→KiCad converter artifacts (`net_conflict` ×94,
`footprint_symbol_mismatch` ×42, `footprint_symbol_field_mismatch` ×40, plus 6 missing/
extra/duplicate-footprint notes). They fire on a four-component probe board too, so they
carry no information about this design. The four that do: `holes_co_located` ×2,
`isolated_copper` ×1, `pcb_trace_too_long_warning` ×1.

---

## Panel — round 1

Evidence read: `boards/main_review/_pcb.png`, `boards/main_review/_schematic.png`,
`boards/main.board.json`, `product.json`, `parts.json`, `boards/main.tsx`.

```design-review
{
  "board": "boards/main.tsx",
  "round": 1,
  "verdict": "iterate",
  "ready_to_make": false,
  "lenses": [
    {"lens": "power", "score": 8, "notes": [
      {"severity": "should-fix", "target": "C17",
       "detail": "C17 is the 10uF bulk for the 3V3 rail and sits ~10mm from U3 (block-placed at rp2040-core local (9,6), which the 180deg rotation puts at board (-29,-28)). At that distance it is bulk for the board, not for the chip; the 8x100nF do the local work alone.",
       "fix": "add a board-level 10uF 0805 (C15850) within 3mm of U3 on V3_3, or move C17 inside the block"},
      {"severity": "consider", "target": "U2",
       "detail": "AMS1117 drops 1.7V at the modelled 105mA = 0.18W in SOT-223, a third of the 0.51W the block calls comfortable, so no copper pour on the VOUT tab. If the firmware ever drives the electrodes hard or a second LED lands, re-check.",
       "fix": "none now; revisit if the 3V3 load passes 250mA"}
    ]},
    {"lens": "manufacturability", "score": 6, "notes": [
      {"severity": "must-fix", "target": "J1",
       "detail": "Two hole_clearance errors at the USB-C receptacle: a GND track 0.115mm and a V5 conductor 0.133mm from a drill, against KiCad's 0.2mm default. The C165948 land pattern packs 12 signal pads, 4 mounting pill drills and 2 alignment NPTH into ~10x8mm, and everything leaving the connector runs that gauntlet. Not orderable open.",
       "fix": "out of reach from the board file - see 'why there is no round 2'"},
      {"severity": "consider", "target": "J1, LED1, LED2",
       "detail": "Rotation-prone parts: the hybrid USB-C receptacle and both polarised LEDs. JLC's rotation convention differs from ours and ORDER.md's placement-preview warning is the safety net - but ORDER.md is not written while fab.ready is false.",
       "fix": "check the JLC placement preview against _pcb.png before paying"}
    ]},
    {"lens": "layout", "score": 6, "notes": [
      {"severity": "should-fix", "target": "net.CAP_A / copperpour EA",
       "detail": "isolated_copper on zone CAP_A. R32 sits 4mm inside the plate's inner edge and its CAP_A_SENSE exit trace slices a corner off the plate, orphaning a sliver of floating copper inside the sense electrode. Small, but it is loose metal under a wet mug and it shrinks the active area.",
       "fix": "cut a notch in the EA outline so R32 sits on a peninsula, or move R32 to the plate's bottom edge so its exit path leaves in under 1mm"},
      {"severity": "should-fix", "target": "Y1",
       "detail": "The crystal net's routed length is 12.81mm against tscircuit's 10mm crystal rule (straight-line is 8.44mm after the block fix - the router detours). Harmless at 12MHz in our judgement, but it is judgement, not measurement.",
       "fix": "tighten rp2040-core's crystal cluster upstream, or accept and check the first article's frequency"},
      {"severity": "should-fix", "target": "vias on net.V3_3 and net.GND",
       "detail": "Two holes_co_located findings: two drills at the same coordinate. The fab will drill twice into one hole.",
       "fix": "router-side; no board-file lever found"},
      {"severity": "consider", "target": "net.CAP_A_SENSE / net.CAP_B_SENSE",
       "detail": "Both sense nets run 20-30mm up the same corridor, roughly parallel. Mutual coupling between them works directly against the A-minus-B differential the firmware wants.",
       "fix": "on a 4-layer revision, run them on opposite sides of a ground layer"},
      {"severity": "consider", "target": "board",
       "detail": "No ground plane. Two layers, every pad on top, bottom layer carries routing; a bottom pour came back isolated because nothing on that layer is a GND pad. Return paths are individual traces.",
       "fix": "4 layers - blocked today, the pipeline's exporters produce no BOM and no gerbers at layers=4"}
    ]},
    {"lens": "testability", "score": 7, "notes": [
      {"severity": "consider", "target": "SW2, SW3",
       "detail": "BOOTSEL and RESET are interior parts (enclosure.json flags them 'reachable only with the case open'). Reflashing a sealed unit means opening it.",
       "fix": "two 3mm access holes in the base under SW2/SW3, or accept that reflash is a service operation"},
      {"severity": "consider", "target": "board",
       "detail": "Probe points are real pads but they are component pads: 3V3 at C3 pin1, 5V at C2 pin1, GND at C3 pin2, silkscreened. Good enough for a meter, awkward for a scope ground spring.",
       "fix": "none - a dedicated testpoint element is currently unusable, see the pipeline note in README"}
    ]},
    {"lens": "cost", "score": 7, "notes": [
      {"severity": "should-fix", "target": "R3, R4 (C25100, 27R 0402)",
       "detail": "1,738 pieces in stock and an Extended part. The thinnest line in the BOM is a sub-cent resistor that also drags a $3 loading fee. It comes in through usb-c-data, so replacing it is a block edit, not a board edit.",
       "fix": "parts-book --lookup for a Basic 27R 0402, then swap it inside blocks/usb-c-data"},
      {"severity": "consider", "target": "board",
       "detail": "5 extended lines = $15, which is 30% of the $49.60 run. Three of them (RP2040, crystal, USB-C receptacle) are unavoidable for this design.",
       "fix": "none; the number is the number"}
    ]},
    {"lens": "safety", "score": 8, "notes": [
      {"severity": "should-fix", "target": "J1.CC1, J1.CC2",
       "detail": "usb-c-data moves both USBLC6 channels onto D+/D-, which leaves CC1/CC2 with no ESD protection - and the CC pins are exposed inside the receptacle on a desk object people will plug and unplug daily. usb-c-power protects CC; usb-c-data does not protect both.",
       "fix": "block-level: a second ESD array, or move one channel back to CC"},
      {"severity": "consider", "target": "copperpour EA, EB",
       "detail": "Electrodes are covered with solder mask and sealed inside the printed body, and each GPIO reaches them through 1k. The only user-touchable metal is the grounded USB shell. safety_gate() returns pass: USB-C 5V only, no mains, no battery, no radio.",
       "fix": "none"}
    ]},
    {"lens": "product-fit", "score": 7, "notes": [
      {"severity": "should-fix", "target": "enclosure",
       "detail": "The mug wants to be centred at board (0, +13), not at the board centre - the electronics band owns the bottom third. The printed body has to sit the board off-centre or the plates end up under the mug's rim instead of its base.",
       "fix": "state (0,+13) as the cup-ring centre in the enclosure brief; it is already in README and enclosure.json"},
      {"severity": "consider", "target": "SW1",
       "detail": "MUTE at (29,-24) is an interior part 7.5mm from the right edge, so the lid needs a plunger over it. Fine for a coaster you press from above, but it is a moulding job, not a hole.",
       "fix": "plunger in the lid, or move SW1 to the board edge and accept a side button"},
      {"severity": "consider", "target": "copperpour EA, EB",
       "detail": "Nobody has measured that two 25x30mm plates under a 2mm lid can separate a full mug from an empty one. The topology is standard and the geometry is sane; the sensitivity is not knowledge we have. This is the product's real risk and building the board is how you retire it.",
       "fix": "first-article measurement before any tooling spend"}
    ]}
  ],
  "must_fix_count": 1,
  "bring_up": "plug USB-C, expect LED1 (PWR) lit and 3.30V +/-3% between C3 pin1 and C3 pin2; hold SW2 and tap SW3 and the RPI-RP2 drive appears",
  "blocking_warnings": 2
}
```

**Verdict: iterate, and the iteration is not a board-file edit.** The board is one
must-fix away from orderable and that must-fix is 0.07mm of copper next to a drill in an
imported connector footprint. Everything else scores at or above the bar except layout
and manufacturability, which are held down by that same finding and by the 2-layer
stackup underneath it.

### Why there is no round 2

The panel routes must-fix notes back to `circuitcode`. That already happened — nine
times, before the panel formally sat, because the same two errors were the last thing
standing in the build loop. Every measured attempt is in the table above: moving the
regulator, moving the LEDs, moving the pour anchors, tightening the router's clearance
tolerances, and going to four layers. **The best result any of them produced was worse
than doing nothing.** The build is deterministic — three runs of identical source gave
byte-identical error lists — so this is a floor, not variance to be re-rolled.

The three fixes that would actually clear it are all outside the board file:

1. **Widen the escape around `J1`** — a change to the C165948 land pattern inside
   `blocks/usb-c-power`, which is a block-authoring job with a testbench.
2. **Four layers**, so GND and 3V3 leave the surface entirely — blocked today: at
   `layers: 4` the pipeline produced 0 BOM rows and no gerbers.
3. **A hand-routed escape** for the connector — the pipeline has no hand-routing seam.

Per the design-review skill's cap, that is the point at which the panel stops iterating
and hands the trade-off over. **Do not order these boards.** Two conductors sit within
0.13mm of a drill at the USB connector; on a $10 board that is a coin-flip on a short,
and the whole point of the review is not to pay for it twice.
