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
| 9 | Silkscreen plotted at 0.033mm strokes; JLCPCB holds 0.15mm and thinner ink prints broken or not at all. **The real defect was worse than the symptom: all 54 silkscreen texts on the converted board are under JLCPCB's 1.0mm minimum height, 39 of them at 0.267mm, and none carries an explicit stroke — so KiCad derives one and plots it at a fifth of the floor. Every board this tool has ever made arrives with no readable reference designator.** | all three | **Pipeline (stage 3a)** — `kicad_normalize.normalize_for_fab` holds the converted board to the fab profile's silkscreen floors between the converter we do not own and the plotter. Idempotent, counted, reported. Measured after: 1864 of 1865 sub-floor strokes → 0 | `normalize_for_fab` in `circuitpy/kicad_normalize.py` |
| 8 | **The debug interface (SWCLK, SWD) reaches no connector and no test point — the board cannot be programmed once assembled.** | all three | **Planner** — `rp2040-core` declares `exposes=("SWCLK","SWD")`, `unexposed_nets()` reports what nothing brings out, and `validate_board_law` makes it an **error**. Satisfiable today with a bare `<testpoint>`: copper, exempt from the BOM gate, reaches `fab.ready` | `unexposed_nets` in `circuitlib/blocks.py`, `debug_unreachable` in `circuitlib/helpers.py` |
| 13 | Copper sized for 604mA carrying 649mA, every segment one width. | harness-puck | **`circuitlib`** — `Block.peak_draw_ma` / `peak_per_unit_ma` give the planner a worst case (a WS2812 idles at 4mA and pulls 60mA), `board_plan` turns it into `power_trace_width_mm` by IPC-2221, and declaring less is an error | `power_trace_width` in `circuitlib/helpers.py` |
| 14 | An LDO reaching ~96 degC junction at the downstream chain's datasheet peak. | harness-puck | **Planner** — the same peak drives `regulator_thermal` at a 45 degC ambient, and a plan that would cook the part has `buildable = False`. Twenty WS2812s behind an AMS1117 is 1.3A and 183 degC: that board can no longer be planned | `overheats` in `circuitlib/helpers.py` |
| 10 | Mask openings separated by less than the 0.2mm sliver minimum; narrowest 0.114mm. | all three | **Not a defect — the check was wrong.** All ten sub-0.2mm webs sit inside *one part's own land pattern*: 0.114 and 0.157mm inside the USB-C receptacle's footprint, 0.1985mm inside each of eight 0402s, which is simply what a 0402 land pattern is. Those dams are specified by the package and JLCPCB builds them daily. The check is now scoped to webs between *different* parts — where nobody qualified the geometry — and that version fires on none of the three boards. Escalating the original would have made every board permanently un-orderable over a standard passive | `_mask_slivers` scoping in `verifylib/gerber_truth.py` |

---

## Open — detected, but a user can still hit them

These are the ones that matter now. Each is currently check-only.

| # | Defect | Found on | Where the fix belongs |
|---|---|---|---|
| 11 | A via placed inside an SMD pad (`C8.pin2`). | hydrate-coaster | **Pipeline or planner.** If the router does this unprompted it will do it on user boards. |
| 12 | USB pair skew 18.88mm against a 3.8mm budget. | terminal-keyboard | **`circuitlib`** — a length-matching constraint the planner applies to any differential pair, not advice. |
| 16 | Raising silkscreen to the fab's 1.0mm floor (#9) made more of it land on pads: 70 strokes inside a mask opening became 99. Ink on a solderable surface stops the joint wetting, and JLCPCB clips it. | all three | **Block / layout.** The floor is not negotiable, so the text has to move: reference designators want a placement clear of the part's own pads, which is a footprint and `circuitlib.layout` question, not an exporter one. Created by a fix, and recorded rather than absorbed. |
| 15 | The pour-margin fix from #2 is structural, but `hydrate-coaster` still hardcodes `cutoutMargin="0.25mm"` on two pours instead of using the helper, and two `BLOCK.md` files still give it as advice to remember. | hydrate-coaster | **Delete the advice.** A number written down in three places will disagree in two of them. One owner, then a check that nothing hardcodes it. |

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
