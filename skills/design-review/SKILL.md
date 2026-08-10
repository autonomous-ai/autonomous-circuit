---
name: design-review
description: Run a finished board past a panel of expert reviewers before anyone pays a fab. Use after circuitcode reports a clean build and before ordering — "is this ready to make", "review the board", "design review", "can we order these", "check it before I spend money" — or whenever a board is about to be handed to a human as orderable. Scores seven engineering lenses, routes must-fix notes back to circuitcode, and holds the ship bar until the board earns it.
---

# design-review — the panel that stands between a board and a fab bill

## Purpose

`circuitcode` closes the *pipeline* loop: does it compile, does it pass the
gauntlet, do the pictures look right. This skill closes the *engineering* loop:
**would a competent team sign this off?**

The difference matters because the gauntlet is blind to whole classes of
mistake — it cannot tell you the regulator will cook, that there is nowhere to
put a scope probe when the board arrives dead, that the one part you cannot
buy is the one holding the design together, or that the connector is on the
wrong edge for the enclosure. Those are review findings, not check failures.

A wrong board costs about $85 and two weeks. A review round costs minutes. The
arithmetic is not close.

## When to use

After `circuitcode` reports `ok: true`, and **always** before telling a user a
packet is orderable. Also whenever a board changed materially — new block, new
MCU, a size change, a part swap.

Do not use it to fix a build that is still failing; that is circuitcode's loop.
The panel reviews boards that already pass, because a board that fails the
gauntlet has not earned the panel's time.

## The panel

Seven lenses. Each is a separate pass with a separate question — **do not merge
them**, because a single reviewer looking for everything finds the first thing
and stops. Each returns a score 1–10 and a list of notes.

| # | Lens | The question it alone asks |
|---|---|---|
| 1 | **Power integrity** | Do the rails hold up? Budget vs source, regulator dropout and heat, decoupling placement and count, bulk capacitance, inrush, brown-out on the MCU. |

| 2 | **Manufacturability** | Will the fab build this right the first time? DFM margins, part availability and Basic/extended mix, rotation-prone packages, assembly side, panelization, silkscreen legibility. |
| 3 | **Layout & signal** | Is the physical design sound? Placement logic, return paths and ground, trace widths for current, connector access, antenna and mounting keep-outs, thermal spreading. |
| 4 | **Testability & bring-up** | When it arrives dead, how do we find out why? Test points on every rail, boot/reset access, an LED that proves power, probe-able signals, a written bring-up order. |
| 5 | **Cost & sourcing** | What does it really cost, and what breaks the BOM? Unit cost at 5 and at 100, extended-part fees, single-source risk, stock depth, named alternates. |
| 6 | **Safety** | Envelope compliance (no mains, sealed battery blocks, certified radio only), ESD on exposed connectors, thermal limits, sharp mechanical edges, failure modes. |
| 7 | **Product fit** | Does this board make the *product* work? Enclosure interface, connector positions vs the case, user-facing indicators, the actual job the object does. |

## The loop

```
build clean → bundle evidence → 7 lenses score independently
     ↑                                      ↓
     └── circuitcode fixes ← route must-fix notes ← panel verdict
```

Repeat until the bar is met. Cap at **4 panel rounds** — past that the design
has a problem the panel cannot fix by iterating, and the honest move is to take
the disagreement to the user with options.

### 1. Bundle the evidence

Before scoring, gather what the panel judges:

```bash
python ~/.claude/skills/circuitcode/scripts/review /abs/project
```

Then `Read` **both** `_review/_schematic.png` and `_review/_pcb.png`, and read
`<stem>.board.json` (warnings, BOM summary, fab state), `product.json`,
`parts.json`, and the board source. A reviewer who has not looked at the
pictures is guessing.

### 1b. Do the arithmetic — do not eyeball it

Three lenses have real maths available. Run it; a number beats an impression,
and these are exactly the failure classes the gauntlet is blind to:

```python
from circuitlib.helpers import (
    regulator_thermal, led_current, pullup_warnings,
    power_budget, trace_width_for,
)
from circuitlib.parts import cheaper_basic_part

# power lens — will the regulator cook?
regulator_thermal(vin=5.0, vout=3.3, current_a=0.18, package="SOT-223")

# power lens — is every indicator's series resistor sane?
led_current(rail_v=3.3, resistance_ohms=1000)

# layout lens — is the power trace wide enough for the current it carries?
trace_width_for(current_a=0.5)

# cost lens — is an extended part costing ~$3/line for nothing?
cheaper_basic_part("C25100")
```

A board passes every structural check with a 10-ohm LED resistor or an LDO
dissipating a watt in a SOT-23. If you score the power lens without running
these, you have scored a guess.

### 2. Score each lens

For each of the seven, in order, produce: a score 1–10, and notes. Every note
carries a **severity** and a **target**:

- `must-fix` — the board should not be made like this.
- `should-fix` — real, worth a round, not fatal.
- `consider` — a judgement call, state the trade-off and move on.

A note without a specific location (`U2`, `net.V3_3`, "the USB connector",
"top-left corner") is not a note; it is an opinion. Make it specific or drop it.

### 3. The ship bar

A board is **ready to make** when all of:

- zero `error`-severity warnings in the sidecar, and `fab.ready` is `true`;
- **every lens scores ≥ 7**;
- **zero `must-fix` notes** open;
- power integrity and safety score **≥ 8** (these two get a higher bar — the
  failure modes are fire and a dead board, not disappointment);
- someone can state, in one sentence, how to bring the board up when it arrives.

Miss any of those and the verdict is `iterate`, not `ready`.

### 4. Route the notes back

Hand `must-fix` and `should-fix` notes to `circuitcode` as concrete edits —
"move C2 within 2mm of U2 pin 3", not "improve decoupling". Then rebuild and
re-run the panel. Only the lenses whose inputs changed need a full re-score;
say which ones you re-ran.

## Output format

Exactly one fenced ```design-review JSON block, then a 2–3 sentence summary in
plain words:

```json
{
  "board": "boards/main.tsx",
  "round": 1,
  "verdict": "iterate",
  "ready_to_make": false,
  "lenses": [
    {"lens": "power", "score": 6, "notes": [
      {"severity": "must-fix", "target": "U2",
       "detail": "AMS1117 drops 1.7V at 180mA = 0.31W in SOT-223 with no copper pour; add a 10x10mm pour on the tab or move to a buck.",
       "fix": "add pour under U2 tab"}
    ]},
    {"lens": "manufacturability", "score": 8, "notes": []},
    {"lens": "layout", "score": 7, "notes": []},
    {"lens": "testability", "score": 4, "notes": [
      {"severity": "must-fix", "target": "board",
       "detail": "no test point on V3_3 — if the board arrives dead there is nothing to measure.",
       "fix": "add TP on V3_3 and GND"}
    ]},
    {"lens": "cost", "score": 8, "notes": []},
    {"lens": "safety", "score": 9, "notes": []},
    {"lens": "product-fit", "score": 7, "notes": []}
  ],
  "must_fix_count": 2,
  "bring_up": "plug USB-C, expect the power LED lit and 3.30V +/-3% on TP1",
  "blocking_warnings": 0
}
```

## Non-negotiables

1. **Never score a lens you did not actually examine.** A 10 you did not earn is
   worse than a 5 you did — it launders an unchecked board into a signed-off one.
2. **Never declare `ready_to_make` with `fab.ready: false`.** Unverified gerbers
   are not a shippable packet, whatever the board looks like.
3. **Read the images every round.** The layout changes between rounds; a score
   carried over from the previous picture is fiction.
4. **Be specific or say nothing.** Every note names a part, a net, or a place.
5. **Do not iterate past 4 rounds.** Take it to the user with the trade-off.
6. **Say what you could not check.** Thermal behaviour, EMI, signal integrity,
   and real-world part fit are outside every deterministic tool we have — the
   panel's judgement on them is judgement, and should be labelled as such.
7. **The panel does not edit the board.** It reviews and routes. circuitcode
   makes changes; keeping those roles apart is what keeps the review honest.

## Required final response

1. **Verdict in one line** — ready to make, or iterating and why.
2. **The scores** — seven numbers, lowest first.
3. **What changed this round** — if this is round 2+.
4. **What you could not check** — the honest limits.
5. **The bring-up sentence** — how the user knows it works when it arrives.
