# Board sign-off — terminal-keyboard

**The tool says this board is orderable:** zero error-severity findings, and gerbers independently produced by `kicad-cli` from the same file KiCad ran DRC against.

That is our claim. This review is where it gets tested by someone who did not build it.

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

`fab.ready = True` · 1053 findings total, 0 blocking.

| Kind | Count |
|---|---|
| `drc_violation` | 642 |
| `erc_violation` | 394 |
| `review_decoupling_distant` | 3 |
| `dfa_edge_clearance` | 2 |
| `pcb_trace_too_long_warning` | 1 |
| `dfa_off_board` | 1 |
| `dfa_pin_pitch` | 1 |
| `dfa_rotation_watchlist` | 1 |

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
