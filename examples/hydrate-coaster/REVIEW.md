# Board sign-off — hydrate-coaster

**Not orderable — 6 blocking findings.**

- `pcb_placement_error` — C8.pin2: Via at (-26.55mm, -28.14mm) is inside SMD pad C8.pin2 at (-26.51mm, -28.00mm)
- `dfm_hole_clearance` — board: a track passes 0.228mm from a plated hole at (4.33, -32.59); the fab needs 0.28mm — route around it, the drill's own tolerance can cut a track this close
- `review_debug_unreachable` — board: the debug interface (SWCLK, SWD) reaches no connector or test point, so the board cannot be programmed or halted once it is assembled
- `drc_violation` — V5: [unconnected_items] Missing connection between items
- `gerber_silk_line_width` — board-F_Silkscreen.gto: board-F_Silkscreen.gto plots 0.033mm silkscreen strokes; JLCPCB holds 0.15mm and thinner ink prints broken or not at all
- `gerber_mask_sliver` — board-F_Mask.gts: 2 pair(s) of mask openings on mask top are separated by less than 0.2mm; the narrowest is 0.114mm near (103.16, -131.73) in plot coordinates. A web that thin burns off and the two pads bridge

We already know about these, so there is nothing to approve here yet. What we want from you is whether the blocking list is **complete** — a defect we are missing is worth far more to us than one we have already found.

## What is in this folder

| File | What it is |
|---|---|
| [`main_fab/gerbers.zip`](boards/main_fab/gerbers.zip) | what the fab actually builds |
| [`main_fab/bom.csv`](boards/main_fab/bom.csv) | every line, with LCSC part numbers |
| [`main_fab/cpl.csv`](boards/main_fab/cpl.csv) | placement and rotation for assembly |
| [`main_fab/kicad-project.zip`](boards/main_fab/kicad-project.zip) | open it in KiCad and run your own DRC |
| [`main_fab/board.glb`](boards/main_fab/board.glb) | 3D, for enclosure fit |
| [`main_review/_schematic.png`](boards/main_review/_schematic.png) | the schematic |
| [`main_review/_pcb.png`](boards/main_review/_pcb.png) | the layout |

Also here: [`boards/main.tsx`](boards/main.tsx) (the board, as code) and
[`findings.json`](findings.json) (every finding, in full).

**Start with [`kicad-project.zip`](boards/main_fab/kicad-project.zip).** Open it in KiCad, run DRC with your own
rules, and look at the board in a tool we did not write. If our checks and
yours disagree, that disagreement is the most valuable output of this review.

## All findings by kind

`fab.ready = False` · 537 findings total, 6 blocking.

| Kind | Count |
|---|---|
| `drc_violation` | 315 |
| `erc_violation` | 207 |
| `review_decoupling_distant` | 2 |
| `pcb_trace_too_long_warning` | 1 |
| `pcb_placement_error` | 1 |
| `dfm_hole_clearance` | 1 |
| `dfa_edge_clearance` | 1 |
| `dfa_pin_pitch` | 1 |

Non-blocking findings are in `findings.json` here, in full. Some are noise from
our own converter and are labelled as such — **please challenge that labelling**
if any of it looks like a real defect we have talked ourselves out of.

## Questions

| # | Question | Verdict |
|---|---|---|
| 1 | Would you let a **non-engineer** order this unsupervised? | yes / **no** |
| 2 | When it arrives, will it **power up**? Rails, sequencing, brown-out, boot straps. | yes / **no** |
| 3 | Can it be **brought up and debugged** — test points, reset access, an LED that proves power? | yes / **no** |
| 4 | Will the **assembly house** build it right first time — rotations, polarity marks, part availability? | yes / **no** |
| 5 | Does the **layout** hold up — return paths, decoupling placement, trace widths, thermals? | yes / **no** |
| 6 | Does it **fit the product** — connector positions, mounting, enclosure interface? | yes / **no** |
| 7 | What did our checks **miss**? | notes |

Question 7 is the one we most want answered.

## Verdict

- [ ] **Approved to manufacture**
- [ ] **Approved after listed changes**
- [ ] **Rejected**

Reviewer: ______________________  Date: ____________
