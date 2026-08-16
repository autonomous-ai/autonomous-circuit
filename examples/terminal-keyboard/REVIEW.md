# Board sign-off — terminal-keyboard

**Not orderable — 1 blocking finding.**

- `dfm_hole_clearance` — U4: a pad passes 0.130mm from a via at (10.12, -20.90); the fab needs 0.2mm — route around it, the drill's own tolerance can cut a track this close

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

`fab.ready = False` · 1177 findings total, 1 blocking.

| Kind | Count |
|---|---|
| `drc_violation` | 654 |
| `erc_violation` | 387 |
| `supplier_footprint_mismatch_warning` | 47 |
| `source_part_not_found_warning` | 44 |
| `dfa_edge_clearance` | 17 |
| `dfm_power_trace_width` | 3 |
| `schematic_symbol_short` | 2 |
| `netclass_pair_skew` | 2 |

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
