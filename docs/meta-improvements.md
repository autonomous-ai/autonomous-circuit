# The meta list — mechanisms, not features

**The bar (Dee, 2026-08-10): "everything gotta be gold standard. just like claude
code is the gold standard of coding."**

So the useful question is not "what feature next" but *what makes Claude Code
gold standard, and what is the equivalent here.* Eight things, and each one
generates work:

| What makes Claude Code good | The equivalent for boards |
|---|---|
| It closes the loop itself — writes, runs, reads the error, fixes | build → verify → review → fix, plus an expert panel before anyone pays a fab |
| It uses real tools, not simulations of them | the real toolchain, real fab formats, real orderable parts |
| It tells you the truth about what it did | honest gates: `fab.ready` is earned, "not verified" is said out loud |
| It is fast enough to stay in flow | build time is a feature; caching and incrementality are quality |
| It does the whole job, not the fun part | a board alone is not a product — enclosure, firmware, assembly, bring-up |
| It learns your codebase | the project owns its blocks, its parts lock, its product definition |
| It is extensible | golden blocks are the extension point |
| It never silently corrupts your work | sidecar ordering, generated files never hand-edited, nothing overwritten |

This file is the running list. Status: **done** · **building** · **next** ·
**later**. Newest thinking at the bottom of each section.

---

## 1. Feedback loops — the thing that actually lifts quality

1. **done — build → verify → review loop.** Write TSX, run the gauntlet, read
   the JSON verdict, Read both rendered images, fix, repeat. The images are
   load-bearing: `ok: true` says the pipeline ran, only the pictures say the
   board is right.
2. **done — the expert design-review panel.** Seven independent lenses (power,
   manufacturability, layout, testability, cost, safety, product fit), scored
   separately, must-fix notes routed back, ship bar that has to be earned. One
   reviewer looking for everything finds the first thing and stops; seven
   narrow reviewers do not.
3. **done — golden set with sentinels.** Known-good asks must pass, deliberately
   broken asks must be refused *for the right reason*. If the sentinel ever
   passes, the eval has gone blind — and a blind eval is worse than none.
4. **next — the failure corpus.** Every real defect we hit becomes a permanent
   fixture and a test. Tonight alone produced four worth keeping: the PTH
   annular rule misapplied to vias, KiCad grading boards against its own stock
   defaults, the router's 0.05mm annular rings, the unpositioned group that
   stacks its children. The gauntlet should get monotonically harder to fool.
5. **next — agent-level eval.** Everything above measures the *pipeline*. The
   product metric is the *agent*: hand it N briefs cold, let it design, and
   score first-pass yield — how many boards reach the ship bar with no human
   turn. That number is the honest answer to "does this work", and we do not
   have it yet.
6. **later — the physical first-article loop.** Boards arrive; a written bench
   protocol says what to measure; results promote a block's `BLOCK.md` status
   from compile-verified to hardware-verified. Until that loop runs, every
   block carries an honest "not yet hardware-verified".
7. **next — the toolchain canary.** tscircuit ships roughly seven releases a
   day with no changelog and no semver. A scheduled job that rebuilds every
   golden block against the newest version and diffs the circuit JSON turns
   "upstream broke us" from a surprise into a report.

## 2. Assets that compound

8. **building — the golden block library** (9 blocks). Values, polarities,
   pinouts and land patterns frozen in something a human checked once. This is
   the only defence against the failure class no deterministic check can see.
9. **next — the parts catalog** (Dee asked, 2026-08-10: *"how do we have a list
   of most popular electronic components so we don't have to reinvent the
   wheels"*). JLCPCB's **Basic + Preferred** library is exactly that list — it
   is already filtered by popularity and stocked availability, and Basic parts
   carry no feeder fee. Mirror it locally (the jlcparts nightly-rebuild
   pattern), because the live search takes 47–90 seconds cold and can never sit
   in the design loop.
10. **later — the reference gallery.** Every board that reaches the ship bar
    becomes a recipe in the skill. Worked examples are the highest-density
    training material the next design gets.
11. **building — provenance on every artifact.** Which block versions, which
    toolchain version, which parts snapshot produced this packet. Reproducing a
    board six months from now should be a fact, not an archaeology project.

## 3. Blind spots to close

The honest gap list says no deterministic check we own knows Ohm's law, catches
a mirrored pinout, or predicts heat. Each of these attacks one of those.

12. **next — a SPICE smoke test.** `circuit-json-to-spice` exists; ngspice is
    free. Even a crude pass — rails are not shorted, the LED current is sane,
    the divider lands where it should — attacks the single biggest blind spot
    directly. A board passes every gate today with a 10Ω resistor where 10kΩ
    belongs.
13. **next — a thermal estimate for regulators.** `(Vin − Vout) × I` against the
    package's thermal resistance is arithmetic, not simulation, and it catches
    the classic cooking-LDO mistake that no DRC will ever mention.
14. **next — bring-up firmware and self-test.** The July proposal called this
    "the board debugs itself": generated test firmware that reports which rails
    are up and which peripherals answer, over serial. It matters more here than
    anywhere because a user cannot scope a board — the conversation has to
    replace the oscilloscope.
15. **later — netlist diff between substrates.** Our second-substrate check
    currently runs on a converted board, so a same-org converter sits inside
    the trust path. A netlist diff audits the conversion itself.
16. **unblocked 2026-08-10, now worth doing — test points on every rail.**
    `<testpoint>` used to fail the BOM gate as an unorderable part (it has no
    LCSC number because it is copper, not a part), so all three example boards
    shipped without one and every testability lens scored 5–7. The gate now
    exempts copper features, and a board carrying a test point reaches
    `fab.ready: true` — verified end to end. Adding them to the blocks and to
    the skill's defaults is the cheap next step.
17. **later — panelization.** More boards per order at the same setup fee.

## 4. The whole job

A board in a bag is not a product. Every item here is a piece of the thing the
user actually wanted.

18. **next — the enclosure handoff.** Board outline, hole pattern and pitch,
    connector positions and overhangs, tallest component — emitted as a
    machine-readable file that Vibe consumes to model the printed body. This is
    where the two products stop being neighbours and start being one loop.
19. **later — assembly instructions** with populated-board renders, plus an
    interactive HTML BOM for hand assembly (both proven open patterns).
20. **later — a firmware skeleton per board**, generated from the same pin map
    the board was built from, so the code and the copper cannot silently
    disagree.
21. **later — "explain this board"**: a generated design-rationale document —
    why these parts, what the rails are, how to bring it up. Shipped beside the
    packet.
22. **blocked — order status.** JLCPCB has no assembly-order endpoint and gates
    API access on order history. PCBWay's partner API is the realistic first
    integration when volume justifies asking.

## 5. The tool itself

23. **building — the Altium-grade viewer.** Prompt on the left, board on the
    right. Cross-probing, net masking, layer control, a violations panel that
    zooms to the offender — the interactions an EE already has in their hands.
24. **partial — incremental builds.** The export cache exists; whole-stage
    skipping and parallel stages do not. Build time is a quality attribute
    because it sets how many review rounds a design can afford.
25. **later — a parts-drift watcher.** Stock and Basic/extended status move.
    A scheduled re-check of every pinned part turns a dead BOM into a
    notification instead of a failed order.
26. **later — cost regression.** A board that quietly got three times more
    expensive is a design smell worth a warning.
27. **later — a block-authoring skill.** Blocks are the extension point, so the
    path from "we need a buck converter" to "there is a verified buck block"
    should itself be a well-worn, testable workflow.

---

## Known defects the first real boards surfaced (2026-08-10)

Building three real products found more than any amount of reasoning did.
Fixed tonight: the plan loop had no approve button; the safety gate refused a
board for saying "no mains, ever"; the pipeline reported COMPILE_ERROR for
boards that compiled; test points were treated as unorderable parts; vias were
judged by the through-hole annular rule; KiCad graded boards against its own
defaults. Still open, in leverage order:

1. **`usb-c-power`'s alignment holes are unroutable-through, and the router
   routes through them anyway.** The gap between an NPTH edge (x=3.20) and the
   pin-1 shell hole (x=3.725) is 0.525mm, so the widest legal track is 0.125mm
   — under the 0.127mm floor. **This is the sole thing blocking all three
   example boards**, and it is the footprint's fault, not theirs.

   Investigated 2026-08-10, so the next attempt starts further along: the
   element is `<keepout>` (not `pcbkeepout`), and inside a footprint it accepts
   only `shape="circle"` with a `radius` — a rect keepout is rejected at
   build time. A 0.65mm-radius pair does build and the router does see them,
   but the result is *worse*: 2 hole-clearance errors become 3 keepout
   violations, because the traces crossing that region are J1's own
   shell-to-GND and VBUS1-to-VBUS2 ties, which have to get across the body
   somehow. Fencing the area without giving those ties a sanctioned path just
   moves the complaint.

   So the fix is one of: route the connector's internal ties explicitly in the
   block instead of leaving them to the autorouter; or tie the shells at a
   single point away from the holes; or get the router to honour hole
   clearance upstream. The first is most likely to work and is a block-local
   change.
2. ~~No ground plane is possible.~~ **Fixed 2026-08-10.** `<copperpour>` fills
   to exactly 0.200mm of the board edge and cannot be told otherwise
   (`minBoardEdgeClearance` is silently ignored — verified). But 0.2mm *is*
   JLC's routed-outline floor; 0.3mm is the V-cut figure, and the condensed
   research table had dropped that parenthetical. So the gate was blocking a
   legal geometry — the same preference-as-floor mistake as the via rule. Now
   blocks at 0.2 and warns at 0.3, and a board with a ground plane builds.
3. **`rp2040-core` places its crystal 11.78mm from XIN**, past the router's
   10mm ceiling, so every board built from that block is unroutable until the
   placement is tightened. C15 is the real binding constraint at 9.89mm — a
   0.11mm margin, which is not a margin.
4. **`ws2812-chain`, the TS-1187A pad pairing, and the ABM8 load capacitance
   are unverified against hardware.** If the TS-1187A pairing is 1+3/2+4
   rather than 1+2/3+4, every switch on every board is a short. That one is
   worth checking before anything is ordered.

## Standing principles this list keeps rediscovering

- **A gate set to a preference instead of a floor is noise, and noise trains
  everyone to ignore the gate.** Block at the fab's real limit; warn at what we
  would rather see. Learned three times tonight in one evening.
- **Measure the noise floor before trusting a new check.** KiCad ERC looked like
  a free second opinion until a correct board produced 152 findings, every one
  an artifact of the conversion.
- **Absence of screening is not safety.** A gate that answers "fine" when it did
  not look is worse than no gate.
- **The pictures are part of the contract.** Anything an agent must judge by eye
  needs a rendered artifact it is required to look at.
