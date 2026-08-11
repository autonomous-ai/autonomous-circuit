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

---

## Open — detected, but a user can still hit them

These are the ones that matter now. Each is currently check-only.

| # | Defect | Found on | Where the fix belongs |
|---|---|---|---|
| 8 | **The debug interface (SWCLK, SWD) reaches no connector and no test point — the board cannot be programmed once assembled.** | all three | **Planner.** A board with an MCU and no route to its debug pins should be impossible to plan, not detectable afterwards. Every block is individually fine and the board is still useless, which is exactly the signature of a planner-level gap. |
| 9 | Silkscreen plotted at 0.033mm strokes; JLCPCB holds 0.15mm and thinner ink prints broken or not at all. | all three | **Skeleton or exporter default.** If it comes from one default, one change fixes the whole catalog. |
| 10 | Mask openings separated by less than the 0.2mm sliver minimum; narrowest 0.114mm. Those pads bridge in the oven. | all three | **Block / footprint**, most likely — a pad pitch that produces a sliver will produce it in every board using that part. |
| 11 | A via placed inside an SMD pad (`C8.pin2`). | hydrate-coaster | **Pipeline or planner.** If the router does this unprompted it will do it on user boards. |
| 12 | USB pair skew 18.88mm against a 3.8mm budget. | terminal-keyboard | **`circuitlib`** — a length-matching constraint the planner applies to any differential pair, not advice. |
| 13 | Copper sized for 604mA carrying 649mA, every segment one width. | harness-puck | **`circuitlib`** — per-net width from the current it actually carries, applied at plan time. |
| 14 | An LDO reaching ~96 °C junction at the downstream chain's datasheet peak. | harness-puck | **Planner.** A power budget that permits a part past its thermal rating should fail the plan, before anything is built. |
| 15 | The pour-margin fix from #2 is structural, but `hydrate-coaster` still hardcodes `cutoutMargin="0.25mm"` on two pours instead of using the helper, and two `BLOCK.md` files still give it as advice to remember. | hydrate-coaster | **Delete the advice.** A number written down in three places will disagree in two of them. One owner, then a check that nothing hardcodes it. |

---

## The rule

When a board fails and an agent fixes it, the question is never "is the board
fixed". It is **"why did the first composition get this wrong, and what stops
the next one"**. The fix belongs in the planner, the block, the skeleton or
`circuitlib` — never in advice a user has to remember, and never only in the
example that happened to catch it.

A block that needs correct handling to be safe is a block that will be handled
wrong.
