# Board sign-off — harness-puck

**Not orderable — 2 blocking findings.**

- `dfm_hole_clearance` — board: a track passes 0.006mm from a plated hole at (-4.33, -27.59); the fab needs 0.28mm — route around it, the drill's own tolerance can cut a track this close
- `drc_violation` — R11: [clearance] Clearance violation ( clearance 0.0900 mm; actual 0.0850 mm)

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

`fab.ready = False` · 694 findings total, 2 blocking.

| Kind | Count |
|---|---|
| `drc_violation` | 437 |
| `erc_violation` | 254 |
| `pcb_trace_too_long_warning` | 2 |
| `dfm_hole_clearance` | 1 |

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
