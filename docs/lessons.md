# The defect ledger

Every blocking defect we have hit on a real board, and **where the fix landed**.

The three example boards are the first three boards, not the deliverable. What
they are actually for is finding defect classes before a user does. So a defect
is not closed when the example stops failing — it is closed when a *future*
board cannot hit it.

Four places a fix can land, in descending order of value:

| Where | What it buys |
|---|---|
| **Planner** | the board can't be designed wrong in the first place |
| **Block / skeleton** | every board inheriting it is correct by construction |
| **`circuitlib`** | the number is right everywhere it is used, one owner |
| **Check only** | we find out afterwards, every time, forever |

**Check-only is not a fix.** It is a smoke alarm. A defect that is only detected
is still shipped by every user who ignores a warning, and still costs a build
cycle for every user who doesn't. It stays **open** in this ledger until it
moves up the table.

`scripts/shift-left-check` verifies that every fix claimed below actually exists
in the tree, and fails if one has been reverted or was never landed.

---

## Closed — a future board cannot hit these

| # | Defect | Found on | Fix landed in | Proof |
|---|---|---|---|---|
| 1 | Autorouter ran a GND track 0.115mm from the USB-C alignment drills; the fab needs 0.20mm. The router has no hole-clearance model at all. | all three | **Block** — a keepout sized to the receptacle's belly, inside the footprint so it travels with `pcbX`/`pcbY`/`pcbRotation` | `<keepout` in `usb-c-power.tsx` |
| 2 | A copper pour cuts a 32-sided polygon around every hole, so a nominal 0.2mm margin measures 0.1976mm at the chord midpoints — under the fab floor. Any board with any hole and a pour. | hydrate-coaster | **Glue block** — `POUR_CUTOUT_MARGIN_MM` derives the safe margin from the segment count, and the pour helper applies it | `POUR_CUTOUT_MARGIN_MM` in `blocks/glue.tsx` |
| 3 | `circuitlib.layout` stored each block's *size* and assumed its geometry was centred on the origin. It is not — `usb-c-data`'s copper sits 6.04mm above. Every board built on our own placement advice was wrong by millimetres before anyone wrote a line of TSX. | composition matrix (6/42 clean) | **`circuitlib`** — measured boxes relative to the origin, produced from real builds, plus `place_board()` which plans the whole board and returns its own warnings | `place_board` in `circuitlib/layout.py` |
| 4 | `autorouterEffortLevel` is a board prop with five settings and no CLI flag. We had never set it, so every board this project ever built routed at the default. At `"5x"`: terminal-keyboard 46 → 18 blocking, harness-puck 5 → 1, no design change. | all three | **Pipeline** — stage 0b retries at `5x` on a routing-class finding and keeps the retry only when it has strictly fewer blocking warnings | stage `0b` in `generation.py` |
| 5 | The planner could emit two USB-C entries on one board — `usb-c-data` is a superset of `usb-c-power` and reuses the same refdes block. | composition matrix | **Planner** — `board_plan` never picks both | the `usb-c-power` notes in `circuitlib/blocks.py` |
| 6 | `rp2040-core`'s crystal sat 11.78mm from XIN, past the 10mm limit, and the router silently skipped **the whole board**. | rp2040 boards | **Block** — placement corrected upstream | crystal placement in `rp2040-core.tsx` |
| 7 | A bare `<hole>` carries no keepout, so the router treats a mounting hole as free space. | all three | **Glue block + skeleton** — `MountingHole` ships the keepout, and a new project's first board already uses it | `MountingHole` in the project skeleton |
| 21 | The router's default effort produces boards that are not fab-ready, and **the escalation that exists to fix that cannot see the evidence**: stage 0b reads circuit.json, while the findings — `[clearance]`, `[shorting_items]`, `[hole_clearance]` — only exist after the KiCad cross-check three stages later. rp2040-core + `DebugPort`: `fab.ready` false with 5 blocking KiCad findings at default, true with 0 at `5x`, same design, same hour. | rp2040-core bench; all three boards | **Skeleton + skill + gauntlet** — every board declares `autorouterEffortLevel="5x"`, so the effort a board needs is a property of the board rather than a rescue the pipeline may not get to attempt. The escalation stays as the net for boards that do not. | `autorouterEffortLevel` in the project skeleton |
| 22 | A via's *drill* was invisible to the hole-clearance gate — it only looked at component holes — so KiCad reported two violations at 0.132mm and 0.148mm on a board our own stage-4 gate called clean. Worse than a missed finding: a defect only KiCad can see arrives after the escalation gate, so the route is never retried. | rp2040-core bench | **`circuitlib`/profile + check** — `min_via_to_copper_mm = 0.20` (JLC "Via hole to Track"), and the gate now measures vias, SMD pads and tracks against every drill. Fires on the defect at stage 4a, in time to escalate; fires on none of the three boards. | `min_via_to_copper_mm` in `fab.py` |
| 23 | The hole-clearance gate modelled every drill as a circle of `hole_width/2`. The USB-C shell drills are 0.8 x 1.6mm **slots**, so copper off the end of one measured 0.4mm further away than it was. | reading the rule against the footprint | **Check** — drills, pads and slots are stadiums now, measured segment-to-segment | `_stadium` in `checks.py` |
| 24 | **The hole rule was applied to the hole's own pad**, so J1's shell-to-GND tie read as 0.006mm from J1's own GND drill and blocked two boards. That number is the annular ring. JLCPCB publishes "PTH annular ring >= 0.20mm" beside "PTH to Track 0.28mm"; both can be true only if the track figure measures copper arriving from outside. Measurement settles it: 28 segments across the three boards came within 0.28mm of a shell drill from outside its pad, and 25 of them measured 0.2000mm to four decimals — that is not geometry, it is where a connection crosses its own pad boundary, counted 25 times. A net-blind rule makes every plated hole unconnectable. KiCad's own hole-clearance DRC, on at 0.2mm and firing on NPTH holes on the same board, reported nothing at these pads. | harness-puck, hydrate-coaster | **Check** — same-net copper is exempt; with no net known either side, copper inside the pad's own outline is exempt; everything else still blocks. Both directions are pinned in the failure corpus. | `hole-rule-applied-to-the-hole-s-own-pad` in the failure corpus |
| 25 | The router halo (#21's sibling fix) was applied to **all four sides**, which put a USB-C receptacle's lowest copper 4.00mm inside the outline — a socket no cable reaches. The halo's evidence is about interior blocks; there is nothing to route in front of a connector. | `place_board` regression | **`circuitlib`** — the connector's face keeps `EDGE_MARGIN_MM`, the other three sides keep the halo | `face = EDGE_MARGIN_MM if edge` in `layout.py` |
| 9 | Silkscreen plotted at 0.033mm strokes; JLCPCB holds 0.15mm and thinner ink prints broken or not at all. **The real defect was worse than the symptom: all 54 silkscreen texts on the converted board are under JLCPCB's 1.0mm minimum height, 39 of them at 0.267mm, and none carries an explicit stroke — so KiCad derives one and plots it at a fifth of the floor. Every board this tool has ever made arrives with no readable reference designator.** | all three | **Pipeline (stage 3a)** — `kicad_normalize.normalize_for_fab` holds the converted board to the fab profile's silkscreen floors between the converter we do not own and the plotter. Idempotent, counted, reported. Measured after: 1864 of 1865 sub-floor strokes → 0 | `normalize_for_fab` in `circuitpy/kicad_normalize.py` |
| 8 | **The debug interface (SWCLK, SWD) reached no connector and no test point — the assembled board could not be halted, single-stepped or recovered from a bad image.** | all three | **Planner + glue block + skeleton.** The planner refuses the plan: `rp2040-core` declares `exposes=("SWCLK","SWD")`, `unexposed_nets()` reports what nothing brings out, `validate_board_law` makes it an **error**. `DebugPort` is what satisfies it — three 2.54mm pads, SWCLK/SWD/GND, beside `MountingHole` in the glue block. It is board furniture and **not** part of the MCU block, on measurement both ways: three pads inside `rp2040-core`'s own box send the debug pair through the crystal cluster and the router comes back with a via shorted into the QFN pad field; outboard, in open board space, the same design is `fab.ready` | `unexposed_nets` in `circuitlib/blocks.py`; `DebugPort` in `blocks/glue.tsx` |
| 13 | Copper sized for 604mA carrying 649mA, every segment one width. | harness-puck | **`circuitlib`** — `Block.peak_draw_ma` / `peak_per_unit_ma` give the planner a worst case (a WS2812 idles at 4mA and pulls 60mA), `board_plan` turns it into `power_trace_width_mm` by IPC-2221, and declaring less is an error | `power_trace_width` in `circuitlib/helpers.py` |
| 14 | An LDO reaching ~96 degC junction at the downstream chain's datasheet peak. | harness-puck | **Planner** — the same peak drives `regulator_thermal` at a 45 degC ambient, and a plan that would cook the part has `buildable = False`. Twenty WS2812s behind an AMS1117 is 1.3A and 183 degC: that board can no longer be planned | `overheats` in `circuitlib/helpers.py` |
| 15 | The pour-margin fix from #2 was structural but the number was still written down in three places: `hydrate-coaster` hardcoded `cutoutMargin="0.25mm"` on two pours, and two `BLOCK.md` files gave it as advice to remember. A number written down three times will disagree in two of them. | hydrate-coaster | **Delete the copies** — the pours pass `POUR_CUTOUT_MARGIN_MM`, and both BLOCK.md files now point at the constant instead of quoting it. `scripts/shift-left-check` fails if a literal comes back. | no `cutoutMargin="0.25mm"` literal outside the constant |
| 26 | **The supplier-footprint IoU band called eight identical capacitors a footprint mismatch because they were rotated.** Same part C1525, same `footprint="0402"`: thirteen at rotation 0 scored 0.7249, eight around an LED ring at multiples of 22.5 degrees scored 0.4739-0.4916, under the 0.5 error floor. The kind blocks, so **any board with an off-axis passive could never be fab-ready** — every circular layout, angled connector and diagonal-edge part, permanently un-orderable, and it looks like a real DFM problem every time. A bench of one part at six angles settles what the boards suggested: 0.7249 at 0, 90, 180 and 270; 0.4739 at 22.5; 0.4215 at 45. The metric is compared axis-by-axis, so it survives a quarter turn and collapses off-axis. | harness-puck | **Check** — orthogonal parts are graded in full; an off-axis part keeps its measurement at `info` with the angle and the bench numbers attached, reported rather than dropped. Lowering the floor past 0.47 was refused: it would keep the metric wrong and widen the hole for genuinely mismatched parts. Both directions are in the failure corpus, which now runs the IoU bander at all — it did not, which is how eight capacitors got there unopposed. | `footprint-iou-graded-through-a-rotation` in the failure corpus |
| 10 | Mask openings separated by less than the 0.2mm sliver minimum; narrowest 0.114mm. | all three | **Not a defect — the check was wrong.** All ten sub-0.2mm webs sit inside *one part's own land pattern*: 0.114 and 0.157mm inside the USB-C receptacle's footprint, 0.1985mm inside each of eight 0402s, which is simply what a 0402 land pattern is. Those dams are specified by the package and JLCPCB builds them daily. The check is now scoped to webs between *different* parts — where nobody qualified the geometry — and that version fires on none of the three boards. Escalating the original would have made every board permanently un-orderable over a standard passive | `_mask_slivers` scoping in `verifylib/gerber_truth.py` |

---

## Open — detected, but a user can still hit them

These are the ones that matter now. Each is currently check-only.

| # | Defect | Found on | Where the fix belongs |
|---|---|---|---|
| 11 | A via placed inside an SMD pad (`C8.pin2`). | hydrate-coaster | **Pipeline or planner.** If the router does this unprompted it will do it on user boards. |
| 27 | **The router-effort escalation cannot see the evidence it exists for.** Stage 0b decides from circuit.json, but the findings that say "route it differently" — `[clearance]`, `[shorting_items]`, `[hole_clearance]` — are KiCad's, three stages later. An rp2040-core board came back with five blocking findings, all five of them KiCad's, and the retry never fired. Every board now declares its own effort (#21), which is a floor a human chose rather than a rescue the pipeline performs. | rp2040-core bench; all three boards | **Pipeline.** Either run the KiCad cross-check before the escalation gate (it costs seconds, the retry costs minutes, and the fab costs two weeks) or move enough of its verdict into stage 4a that circuit.json alone can say "the route is wrong". #22 moved one — via drill clearance — and it now fires in time; `[clearance]` and `[shorting_items]` are still only KiCad's to see. |
| 12 | USB pair skew 18.88mm against a 3.8mm budget. | terminal-keyboard | **`circuitlib`** — a length-matching constraint the planner applies to any differential pair, not advice. |
| 16 | Raising silkscreen to the fab's 1.0mm floor (#9) made more of it land on pads: 70 strokes inside a mask opening became 99. Ink on a solderable surface stops the joint wetting, and JLCPCB clips it. | all three | **Block / layout.** The floor is not negotiable, so the text has to move: reference designators want a placement clear of the part's own pads, which is a footprint and `circuitlib.layout` question, not an exporter one. Created by a fix, and recorded rather than absorbed. |

---

## What these three boards taught about checks themselves

Not defects in a board — defects in the *way we look at boards*. Both were
caught before shipping, and both would have been invisible afterwards.

| # | Lesson | How it surfaced | What changed |
|---|---|---|---|
| A | **A check that cannot parse a shape silently passes it.** Our gerber reader could not evaluate parameterised aperture macros (`RotRect`, `RoundRect` — defined once, given their real numbers at each `%ADD%`), so it assigned them zero size and dropped them. **56 of 230 mask openings on harness-puck — a quarter of the layer, and every fine-pitch QFN pad — were never examined.** The sliver count went from "2" to the true 10 once they were resolved. | measuring a fix's before/after and finding the two numbers incomparable | macro parameters are substituted and rotation applied; nothing is dropped for being unparseable |
| B | **A fix can blind its own smoke alarm.** Setting `solder_mask_min_width` made KiCad plot the mask as 223 filled regions instead of 174 flashed apertures. The check only understood flashes, so it would have reported a clean mask on every board where the fix had been applied — quiet, not clean. | re-running the check on the fixed packet before believing the fix | the reader handles both representations, and the setting itself was **retracted**: measured against the same yardstick it removes no web at all, and costs an 8x slowdown. A change that alters the representation and not the board is a placebo |

## The rule

When a board fails and an agent fixes it, the question is never "is the board
fixed". It is **"why did the first composition get this wrong, and what stops
the next one"**. The fix belongs in the planner, the block, the skeleton or
`circuitlib` — never in advice a user has to remember, and never only in the
example that happened to catch it.

A block that needs correct handling to be safe is a block that will be handled
wrong.
