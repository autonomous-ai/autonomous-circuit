# BOARD — autonomous-circuit

**Updated 2026-08-19. `upstream/main` is `6040efb` (PRs #1, #2, #3 merged).
Everything below the SUPERSEDED line is history — keep it, do not trust its
SHAs.**

**Scope: board quality only — will a board that comes back from the fab and
gets flashed actually work, and is it good rather than merely working.**

**Deploy is not ours and is not tracked here.** Everything about deployment,
metering, turn locks and concurrency — PR #4, `docs/deployment.md`,
`docs/pre-deploy.md`, and every section below the SUPERSEDED line mentioning
them — is out of scope. Do not reopen it, do not add "ready for deploy" or
"deployed" states to this board. If a board task turns out to need one of
those documents, that is a signal the task is not a board task.

---

# CURRENT

## Measured 2026-08-19 — will a fabbed, flashed board actually run?

One command now answers this: `scripts/board-table.py` (`--rules --netconflict
--errors --netlist --pour --boot`). 21 projects, 19 with full artifacts.

### The copper is right. Three things prove it.

**1. The netlist the fab receives is the netlist the source asked for.**
`--netlist` compares **pad for pad**: `pcb_smtpad.port_hints[0]` gives the pad
number, `pcb_port` gives the pin, and the map from source net class to copper
net must be a bijection. **3286 of 3817 pads compared across 19 boards — 86.1%
— 0 splits, 0 merges.** Gerbers are plotted from that board, so this is the
whole question of electrical faithfulness and it passes.

The 531 uncompared pads are, every one of them, **pads KiCad carries with no
net at all** — unused RP2040 GPIO, J1's SBU and shield pins, the redundant
terminal of each 4-pin tactile. There is nothing on the copper side to compare
them against. No pad on a rail is left out: the first version of this check
missed 30% of pads, U3's thermal pad and every J1 shell pin among them, purely
because circuit.json spells a pad `pin13` / `thermalpad` where KiCad spells it
`13` / `57`. That is a key mismatch in the *check*, and it was hiding exactly
the pads whose net matters most.

A first version of this check compared *refdes* groups and returned 19/19
IDENTICAL — but it could not have seen a transposition: swap `U3.XIN` with
`U3.XOUT` and every refdes set is unchanged while the oscillator is dead. Pad
level catches it, because `Y1.pin1` and `C15.pin1` stay on the original net and
the class splits. Cross-checked independently: the 253 nets whose KiCad names
are pin descriptors (`.Y1 > .pin1 to .U3 > .XIN`) were resolved through pin
aliases and **all 253 agree**. The swap is refuted, not assumed.

**2. `unconnected_items` is 0 on every `ready` board.** KiCad's ratsnest is
satisfied: every pad reaches every other pad on its net through real copper.
The one violation in the corpus is on `hydrate-coaster`, which is not ready.

**3. The ground pour is one piece.** `--pour`: **one** connected region,
**100%** of the pour area, on every board that has a zone. See the correction
below — this took two passes to get right.

**4. The RP2040 boot chain holds on every board, asserted not eyeballed.**
`--boot` checks eight things per board — VREG_IN on a rail with VREG_VOUT on its
own DVDD class; XIN and XOUT on different nets with XIN reaching the crystal;
all six QSPI pins landing on U4; a pull-up on QSPI_SS; a pull-up on RUN; a
series resistor on each of USB_DP/DM; QSPI_SS reachable from a button; SWCLK
reaching something other than the chip. **21 of 21 boards pass all eight**,
except SWD-unreachable on `hydrate-coaster` and `terminal-keyboard` — both
already not ready, and hydrate-coaster reports it at error severity, which is
the gate behaving correctly.

Read out in full on weather-badge-15: XIN—Y1.pin1—C15 and XOUT—R11—Y1.pin3—C16
is the series-resistor topology from the hardware design guide; BOOTSEL is SW2
pulling QSPI_SS to GND through R13; USB through 27R R3/R4; CC1/CC2 on 5k1
R1/R2; TESTEN to GND.

**So: these boards power up, and USB enumerates in BOOTSEL.** What is left is
not "does it run" — it is margin, and one gate that lets a plane-less board
through.

### Corrected: `isolated_copper` is a false positive, all 2394 of them

First read was "the ground pour is shattered into 2423 fragments, the largest
holding 5%". That was wrong and the correction matters.

`circuit-json-to-kicad` writes a pour as a **triangle mesh** — every
`filled_polygon` is exactly 3 vertices, min = median = max = 3, 2423 of them
on weather-badge-15. Counting those entries measures the mesh, not the copper.
KiCad's connectivity counts them the same wrong way and calls 199 of them
isolated. Union the triangles across shared edges: **one component, 3040mm2,
100%**. The copper is continuous; the format is what is fragmented.

This is 2394 instances across 17 boards — the single largest warning-severity
category in the corpus — and it is entirely noise. It also inflates the B.Cu
gerber to 457KB / 2639 G36 regions against 64KB on F.Cu, which is a real CAM
risk even though the copper is fine.

### What actually still needs fixing

| # | Finding | Spread | Blocks? |
|---|---|---|---|
| **A** | **Nothing in the gate requires a ground pour to exist.** `weather-badge-5` is `fab.ready=True` with zero zones. Grepped: no check reads for a missing pour; `netclass.py:478` reads pours but never requires one | 1 ready board (+2 non-ready, +1 top-only) | no |
| **B** | **No board ever pours on `top`** — every pour is `bottom` only, 98% coverage | 17/17 | no |
| **C** | U2.VIN decoupling **exactly 9.7mm** on all 17 boards — identical, so it is in the block, not the routing | 17/17, 88 findings, median 5.8mm vs a 5mm budget | no |
| **D** | `netclass_pair_skew` grades every board against **150ps / 3.8mm — the USB 2.0 *High Speed* number** (`verifylib/netclass.py:61`), applied unconditionally: grepped, **no interface speed is declared anywhere in the pipeline**. RP2040's controller is Full-Speed-only (12Mbps, 83ns bit time), so the worst measured skew of 17.13mm ≈ 114ps is ~1/700 of a bit. *Assumption flagged: FS-only is from the RP2040 datasheet, not from anything this repo declares — which is itself the bug* | 16 findings, 14 boards | no |
| **D2** | **The diffpair findings that are not about timing**: `diffpair_not_routed` on 5 boards ("no corridor wide enough for the pair exists"), coupling as low as **2% of the run**, and `netclass_pair_reference` — 30% of USB_DP_CONN has no ground under it. These are impedance and return path, and unlike D they are real | 5–6 boards | no |
| **E** | Crystal net over the 10mm ceiling, 2 vias each — see #12, unchanged | 16 findings, 12 boards | no |
| **F** | `holes_co_located` (101), `copper_sliver` (46), `hole_to_hole` 0.10mm vs 0.1995mm min (8) | 20 / 16 / 2 boards | no |
| **G** | U4 flash footprint IoU 0.6347 — **still unresolved**, needs one network fetch for C97521's land pattern; see #11 | 18/18 | no |

**A and B are the two that decide whether a board is good rather than merely
working.** A plane-less board passing the gate is the same class of hole this
repo keeps recording: the check was never asked. B means every return current
on the component side has to find a via.

**Caveat on A, recorded rather than hidden:** the four boards without a bottom
pour (`weather-badge-5` 08-14, `harness-puck` 08-12, `terminal-keyboard` 08-11,
`hydrate-coaster` 08-11 top-only) are the four oldest-but-one artifacts, which
looks like pipeline drift — except `macropad-3x3` and `macropad-6`, both built
**08-12**, do have a bottom pour. So mtime does not establish a regime and
"wb-5's build path is stale" is *unproven*. What is proven by grep is that
nothing checks, which is why #14 stands either way. Rebuilding wb-5 would
settle it.

**D is a gate that is wrong, not a board that is wrong.** The budget should come
from the declared interface speed; hardcoding the HS number makes 14 of 17
boards look bad for a reason that does not apply to any of them.

### All 12 error-severity findings land on non-ready boards

`--errors`: hydrate-coaster 6, harness-puck 5, terminal-keyboard 1. Nothing at
error severity rides on a board marked ready. The gate is honest about hard
failures; the gap is entirely in what it declines to call hard.

### `net_conflict` — 2246 instances, zero of them copper

`--netconflict` splits them by what disagrees. Three regimes, and the regime is
**per board**, not per net — so this is an exporter version difference, not a
board defect:

```
   938  floating  41.8%   schematic has no net where the PCB pad has one
   655  unnamed   29.2%   schematic net is Net-(REF-PIN), same copper, no label
   421  renamed   18.7%   TR_Y1_xin vs `.Y1 > .pin1 to .U3 > .XIN`
   232  unparsed  10.3%   "No corresponding pin found in schematic"
```

Of the 421 renamed, **406 name the same two endpoints** by a different scheme
(verified by matching every name token against the four endpoint words). The
6 residual are one crystal-net name collision. All of it is `--schematic-parity`
output: re-running DRC without that flag produces none of it. **The exported
`.kicad_sch` is the broken artifact, not the board** — and it ships in
`kicad-project.zip`, which is what a human opens.

## Settled by measurement, 2026-08-19 — #16 is real, and it does not fit

`ldo-3v3` puts `Cin` at `(-2, -6)` while the AMS1117's `VIN` pad is at
`(+2.93, +2.30)`. Centre-to-pin that is **9.65mm**, which is the 9.7mm the
check reports, on all 17 boards, identically. The cause is named and it is one
line of one block.

Moving it does not work, and the reason is worth more than the fix.

**Run 1 — `Cin` to `(6.4, 2.3)`.** `fab.ready` True -> False, **66 errors**.
One cause, cleanly: U2's courtyard overlaps C2's by **0.292mm**, which is a
placement error, and *"Autorouting was skipped because 1 PCB placement error
was found"* — so all 30 traces went missing as a cascade. The 0.292mm is also
the correction: C2's courtyard half-extent is 1.68mm, U2's courtyard ends at
x=5.012, so the first legal centre is x=6.692.

**Run 2 — `Cin` to `(7.0, 2.3)`**, 0.308mm of courtyard clear. One variable
against a base rebuilt from the same source. Both 10x, both seeded, base
reproduces the app's own build exactly bar the 30 `supplier_footprint_mismatch`
rows that `CIRCUIT_PARTS_ENGINE=off` cannot produce. 279.9s vs 279.3s.

```
                                   base    moved
fab.ready                          True    False
error                                 0        1
warning                              27       28
info                                 30       29
DRC/ERC instances                  1060     1061
blockingByAttempt                   [2]      [3]

review_decoupling_distant             5 ->    4   the U2 finding cleared
crystal_net_routed_long               2 ->    1   better
pcb_trace_too_long_warning            2 ->    1   better
pcb_pad_trace_clearance_error         0 ->    1   BLOCKING
dfm_hole_clearance                    0 ->    1
drc_violation                        16 ->   17
```

**The fix worked and the board still failed.** The blocking error is
`Pad .R1 > .pin1 and trace [.J1 > port.GND1, .R1 > port.cathode] are too close
(clearance 0.093mm, minimum 0.1mm)` — **7 micrometres short**, on R1 at
`(-1.51, -12.83)`, about **11.5mm from anything that moved**. Nothing collided.
The router re-solved the whole board and one net landed 7µm inside the limit.

### The finding under the finding: there is no routing headroom

`check_failed` says it in the sidecar's own words:

> the board declares `autorouterEffortLevel="10x"`, which is the hardest
> routing pass this pipeline will run, so no retry was attempted. 2
> routing-class finding(s) survived

Base passes with `blockingByAttempt [2]` — two blocking findings that the
repair pass cleans up — at the **top rung of the effort ladder**, with nothing
above it to escalate to. So a board that reads `fab.ready = True` is not a
board that passed comfortably; it is a board that passed with two findings of
margin and no next gear. Any placement change is a coin flip, and this one
landed 7µm on the wrong side.

That is why **#16 cannot ship as a cap move**. There is no room inside the
block's own box either: the box is `(-4.18, -6.7, 6.42, 2.85)`, `max_y` **is**
VIN's pad edge, and the first legal centre east of U2 is x=6.692 — already
outside. The cap has to leave the box, the box has to grow ~2mm, and every
board using `ldo-3v3` has to be re-laid out with the room. Options, none free:
grow the box and re-run `evals/measure_block_boxes.py --write`; or fit a
smaller package near VIN (an 0603 courtyard is ~1.1mm half-width against
0805's 1.68, which would put the box growth near 0.6mm) — but that is a BOM
change and `parts-book` owns it.

**Nothing was shipped.** The repo's blocks are untouched; both runs live in a
scratchpad copy.

## Open

- **#14 · Gate on the pour.** A board with no zone must not reach `fab.ready`.
  `weather-badge-5` did.
- **#15 · Pour the top layer too**, or say in writing why bottom-only is the
  design.
- **#16 · Move U2's input cap.** Cause named to the line, fix measured twice,
  **blocked**: the cap does not fit inside the block's box and the board has no
  routing margin to absorb it moving out. Needs a decision — grow the box and
  re-lay the boards, or a smaller package (BOM change, `parts-book`). See the
  measurement above.
- **#21 · The boards pass with no headroom.** `10x` is the top of the ladder and
  wb-15 arrives there with `blockingByAttempt [2]` and nothing to escalate to.
  Worth measuring across the corpus: how many boards are one perturbation away
  from losing `fab.ready`, and is the answer "all of them".
- **#17 · Derive the USB skew budget from the interface speed** rather than
  hardcoding the High Speed figure (`verifylib/netclass.py:61`).
- **#18 · Stop `isolated_copper` from firing on a triangulated pour** — union
  the mesh before judging it, the way `--pour` does. 2394 instances of noise
  is what buried A and B.
- **#19 · Fix the `.kicad_sch` export.** 2246 parity findings and the file a
  human opens is wrong.
- **#12 · Route hints for the crystal net.** Unchanged, still the only untried
  lever.
- **#12b · Decide the 10mm crystal gate, in writing.** Unchanged.
- **#11 · U4 footprint.** Unchanged, still needs the network fetch.
- **#6 · `V3_3` width.** **#8 · wire `safety_gate()`.** **#9 · read
  `packages/router/`.** Unchanged.
- **#20 · Route the USB pair as a pair.** `diffpair_not_routed` on 5 boards,
  coupling down to 2%, 30% of the run with no reference. Separate from D and
  not dismissed by it.
- **#13 · `scripts/board-table.py`.** **DONE, in review** — PR
  [#5](https://github.com/autonomous-ai/autonomous-circuit/pull/5), branch
  `feat/board-table-instrument`, 3 commits on `upstream/main`.

## Lessons paid for

- **Count instances, not rows.** `_collapse_kicad_repeats` folds repeats into
  one row carrying `xN`. `isolated_copper` is 18 rows and 2394 instances; the
  row count says nothing about how much of a board is affected.
- **A tool's output format is not the thing it describes.** 2423
  `filled_polygon` entries looked like 2423 fragments of copper and were one
  triangulated plane. KiCad made the same mistake. Union before judging.
- **A correct fix can still fail, and the failure can be 7µm away from
  anything you touched.** Moving a decoupling cap 9.65mm -> 4.07mm cleared its
  finding, improved two others, and lost `fab.ready` to a clearance violation
  11.5mm from the change. Judge a block edit on the whole warning delta, never
  on the finding it was aimed at.
- **Ask what a passing check cannot see.** The refdes-level netlist comparison
  returned 19/19 IDENTICAL and was blind to a pin transposition on the one net
  that starts the chip. A check that cannot fail on the defect you care about
  is not evidence about that defect.
- **Prose in SKILL.md is advice a model weighs.** Only the loop makes it so.
- **A caveat is not a measurement.**
- **Never report mid-flight state as final.**
- **Comparing different boards concludes nothing.**
- **The recurring defect is not four separate bugs** — the machine measured
  correctly and said too little for anyone to act on.

---

# SUPERSEDED 2026-08-18 (kept for its measurements)

## The 21-board corpus

```
             boards   KiCad lines   blocking   fab.ready
patched        13        21–24          0        13/13
unpatched       8      476–1246        12         5/8
```

Nothing sits between 24 and 476. That is a switch, not a trend.

## What the gate does not catch

Every patched board passes. All of these are advisory and none of them block:

| Finding | Spread | Task |
|---|---|---|
| **Crystal net over the 10mm ceiling** — and **16 of 17 carry exactly 2 vias** (3.20mm, a third of the budget) | **16 of 17 traces, 11/11 boards** | #12 |
| U4 flash footprint below the warning band (IoU 0.6347) | 18/18 boards | #11 |
| `V3_3` routed 0.15mm against a measured 0.40mm ceiling | every board | #6 |
| `safety_gate()` never called by `build_board()` | always | #8 |

A board that passes the gate is not a board that powers up.

## Settled by measurement, 2026-08-18

**Crystal rotation 270° is refuted.** One-variable comparison on a copy of
weather-badge-15, exactly one line different:

```
no rotation   ready ✓   0 errors   11.26mm (2 vias) + 14.28mm (2 vias)
rotation      ready ✗   4 errors   20.72mm (4 vias)
```

Longer copper, twice the vias, and it loses `fab.ready`. The revert in PR #1
was correct. Six earlier data points across *different* boards had suggested
the opposite — comparing different boards concludes nothing.

**Levers that do not work on the crystal net**, all measured: moving parts
(16/17 still 2 vias), rotation (above), raising effort (1x = 0 blocking,
5x = 16 blocking), `minTraceWidth`/`clearance` (they gate the checker, not the
router — the agent worked that out on its own).

The lever nobody has tried: `routeHintPointProps`, which takes `via` and
`toLayer` and can hold a net on one layer. Named in the rotation commit's own
closing paragraph and never used.

## Open

- **#12 · Route hints for the crystal net.** 94% of all measurements, the only
  untried lever. Fix once in `rp2040-core`, every RP2040 board inherits it.
- **#12b · Decide the gate, in writing.** 94% of boards ship with an
  out-of-spec oscillator net and nothing blocks. Either the 10mm ceiling is
  wrong for a real 2-layer board — then say what the real number is and why —
  or `crystal_net_routed_long` has to block. Measuring it, printing it and
  ignoring it is the worst of the three.
- **#11 · U4 footprint.** Needs one network fetch for `C97521`'s real land
  pattern; without it 0.6347 means "different", not "wrong". Ours, measured:
  8 pill pads, pitch 1.2700mm (correct), 0.63 × 2.25mm, rows 7.0602mm apart,
  outer span 9.3102mm. The profile's own comment says a *correct* 0402 scores
  0.73–0.77, so this ruler is loose.
- **#6 · `V3_3` width.** `power_width_widened` widens 31% of the run; the
  narrowest point stays 0.2mm, held by `pcb_via_36.drill`. The culprit is named.
- **#8 · Wire `safety_gate()` into `build_board()`.**
- **#9 · Read `packages/router/`** (2487 nodes, 26.6% of the graph, off by default).
- **#13 · `scripts/board-table.py`.** Every finding today came from measuring
  across all 21 boards at once, and each time it was hand-rolled Python. The
  corpus is the instrument; it should be one command.

## Lessons paid for

- **Prose in SKILL.md is advice a model weighs.** Only the loop makes it so.
- **A caveat is not a measurement.** The crystal check's blind spot was
  documented, and a violating board passed anyway.
- **Never report mid-flight state as final.** wb5, wb8, wb9, wb12 all healed
  after the number that looked final.
- **Comparing different boards concludes nothing.** Six data points said the
  rotation helped; one controlled run said the opposite.
- **The recurring defect is not four separate bugs.** The machine measured
  correctly and said too little for anyone to act on: "a long trace" → which
  trace, which pins, by how much; "too close to something" → to C40, at these
  coordinates.

---

# HISTORY (stale SHAs below this line)

## What happened while we were not looking

- **PR #1 merged.** `97112a3 Merge pull request #1 from autonomous-ai/feat/port-our-fixes`
  *is* `upstream/main`. `65c815a` is an ancestor of it — every ported patch is
  upstream now. `git log upstream/main..HEAD` is **empty**.
- **Two commits landed on the branch after `65c815a`, before the merge:**
  - `3cd53e3` — **Revert "Turn the crystal so its signal pin faces the chip"**
    (our patch #5, `a5b3a57`). One file, `blocks/rp2040-core/rp2040-core.tsx`,
    −28/+2. ⚠️ **The revert message records no reason** — it is the bare
    `git revert` boilerplate. The ledger row for #5 said the 270° figure came
    from a different board and had to be re-measured; somebody evidently
    re-measured, and *that measurement is now lost*. This repo's own lesson
    applies to the revert as much as to the patch: **a caveat is not a
    measurement.** Worth reconstructing before anyone re-lands a rotation.
  - `1e7ac6a` — **"A missing tool has to leave a None behind, not a hole"**, not
    ours and a genuinely good catch: `build_board()` bound `kicad_sch`/`kicad_pcb`
    inside `if toolchain.kicad_cli_exe() is not None:` and read `kicad_pcb`
    unconditionally ~240 lines later when zipping the KiCad project. Without
    kicad-cli the name was never bound → `UnboundLocalError` → **no verdict at
    all**, rather than the honest degradation the contract promises. The
    `kicad_unavailable` info and blocking `unverified_gerbers` warning were both
    being produced and both thrown away by a crash further down. Commit says
    this is what had been red in CI: **1 failed / 6 passed / 24 errors → 1 failed
    / 30 passed / 0 errors.** Exactly the shape of trap this board keeps
    recording — a degradation path that was written and could never be reached.
- **Our checks survived the revert.** `3cd53e3` touched only the block TSX, so
  `crystal.py`, the `fab.py` rows and the verify suite are intact upstream.

## Blocker #1 is clearing itself — `weather-badge-11` PASSED ✅

First board to run end to end on the ported code. Project `eb9231c2`, sidecar
`boards/main.board.json`:

```
fab.ready = True   attempts 1   autorouterEffort 10x
warnings:  0 error · 24 warning · 61 info
```

Artifacts all present (`main.board.json`, `main_review/`, `main_fab/`). Note the
effort: **10x**, the top rung of upstream's own ladder (`default → 1x → 2x → 5x
→ 10x`) — not the `5x` our dropped floor patch argued for.

> ⚠️ **A turn is still live** (`claude -p` PID 37298, started 12:13) — the build
> turn ended and the silent review loop is running its rounds. Treat the numbers
> above as **mid-flight**, exactly as this board's own lesson says: wb5 "41
> errors", wb8 "89 errors", wb9 "6 errors" were all intermediate and all healed.
> Re-read the sidecar once the process is gone.

> ⛔ **Do not touch `viewer/src/**` while a turn is live.** `viewer/vite.config.mjs:13`
> imports `src/server/circuit/http.mjs`, so that whole module graph is a Vite
> *config dependency* — editing any file in it **restarts the dev server**, which
> fires `httpServer.once("close", () => circuit.close())` → `driver.mjs::close()`
> → `controller.abort()` on every entry in `turns` → the running `claude` child
> is killed. Same for `git checkout` of another branch (rewrites `viewer/` on
> disk) and for `scripts/dev.sh` / `build-skill-runtimes.sh` (re-vendors the
> skill mid-build, so the next generator call inside the same turn runs
> half-new code). Safe meanwhile: `.claude/BOARD.md`, `packages/**` sources,
> reading, and new files nothing imports yet.

## Untracked on disk, never committed

`docs/pre-deploy.md` (26538 B, written today 11:59) and `docs/deployment.md`
(18602 B, 11:22) are **untracked** — `git log -- docs/pre-deploy.md` is empty and
neither is in `git ls-files`. So patch #11 is *still* not in git, but a file now
exists and is substantially larger than `df883a1`'s 225 lines. Somebody rewrote
it today. Decide: commit, or delete and stop referencing it.

---

## Landed on `feat/port-our-fixes` (historical — all of this is upstream now)

```
7c4029e  #1  collapse repeated KiCad rules          clean pick
bf77127  #2  ink off the pads                       1 conflict, resolved
b41089e  #3  crystal net length check               1 conflict, resolved
c082aee  #3  record the gap it closes
3e79997  #4  a via is copper too
87774ef  NEW margin applied to copper, not only placement
a01342e  #7  panel is the driver's job              clean pick
adc17cd  #8  say which review phase is running      clean pick
3a37240  #9  undo a review round that breaks a board  clean pick
a5b3a57  #5  turn the crystal 270deg                clean pick
a5a3835  NEW check_failed defined twice
65c815a  NEW keep the repo in one language (driver.test.mjs)
```

Both conflicts were the same hunk of `packages/circuitpy/tests/test_verify_policy.py`:
each commit carried tests belonging to a patch not yet ported.

## Patch ledger

| # | Patch | Source | Upstream | Evidence |
|---|---|---|---|---|
| 1 | Collapse repeated KiCad rules (`_collapse_kicad_repeats`) | `0bf4e24` | absent | **measured on wb9's real `main.board.json`: 498 → 24 lines**, whole board 552 → 78. Severity preserved: 165 warnings → 15 groups, 333 info → 9. Nothing blocking dissolved into advice. |
| 2 | Ink off the pads: `--subtract-soldermask` (generation.py), read `%LPC` (gerber.py), **`gerber_silk_over_pad` into `VERIFY_ESCALATED_KINDS` (fab.py)** | `a3b4833` | absent | wb9 shipped `ready=true` with 10 silk strokes inside mask openings. Flag verified live at `generation.py:1600`, inside `build_board()` itself — escalation has a remedy behind it. |
| 3 | Crystal net length check (`crystal.py`, `rules.py`, `cli.py`, `verify_bridge.py`, **`crystal_net_too_long` into `VERIFY_BLOCKING_KINDS`**) | `b0c4c0d` `1c1dcb2` | absent | wb9 passes, with 0.54mm of slack against a 10mm ceiling. |
| 4 | A via is copper too (`copper_length`, `via_count`) | `5ab1675` | absent | the routed half of #3. |
| 5 | Turn the crystal 270° | `211e655` | absent | upstream already carries the *placement* fix (`pcbX={0} pcbY={-10.5}`), not the rotation. Recorded measurement is from another board — **re-measure**. |
| 6 | ~~Pin QFN escape traces at 0.15mm~~ | `baab857` | **DROPPED** | see below |
| 7 | Panel as a driver phase | `b7e2a73` | absent | 5 of 8 boards skipped it while it lived only in SKILL.md prose. |
| 8 | Name the running review phase | `2ee74b4` | absent | silence reads as a hang. |
| 9 | Undo a review round that breaks an orderable board | `a62bfa0` | absent | |
| 10 | Effort ladder | `75ba721` | **upstream has its own** | theirs is `default → 1x → 2x → 5x → 10x`; 100x dropped with a measurement on two-key-footswitch, 2026-08-17: 28 minutes, no verdict, killed. Same conclusion we reached independently. |
| — | 5x effort floor | `9009e21` → `4f250a7` | — | self-cancelling: a full grid gave 1x = 0 blocking, 5x = 16 blocking. |
| 11 | `docs/pre-deploy.md` | `df883a1` | absent | **PORTED 2026-08-18**, revised against `65c815a` (225 → 403 lines). Documentation. |

### Why #6 was dropped

Upstream ships `netwidth.py`, which **measures** the escape ceiling per pad over
180 bearings instead of hardcoding one. Run against wb9:

```
V3_3   ceiling 0.40mm  limited at U3.IOVDD6   routed 0.15mm   declared: none
V5     ceiling 1.10mm  limited at U1.VBUS     routed 0.25mm   declared 0.50mm
```

Our patch would pin `IOVDD1-6`, `DVDD1-2`, `VREG_IN/VOUT`, `USB_VDD`, `ADC_AVDD`
and `GND` at 0.15mm. Note what that does and does not do: the router **already**
picks 0.15mm on `V3_3`, so the patch would not narrow anything — it would
*freeze* the rail thin by declaring the router's own choice, and stop upstream's
`power_width_widened` from ever raising it. A measurement beats a guess, and the
guess was ours.

Worth separating out as its own task: **0.15mm routed against a 0.40mm measured
ceiling is a real gap on `V3_3`, and upstream is not currently closing it.**
Dropping #6 does not fix that; it only stops us cementing it.

## Verification

No test failure on this branch is ours.

| Suite | Clean `upstream/main` | This branch |
|---|---|---|
| circuitpy | 99 failing | **59 failing** (strict subset) |
| verify | 144 passing | **174 passing** |
| viewer | 13 files failing | **13 files failing**, same set |
| `driver.test.mjs` | — | **49/49 passing** (re-run at `65c815a`, the commit that edits it) |
| `npm run build` | — | passes |

`comm -13` over both failure lists is empty in both directions of interest. The
59 circuitpy failures are upstream's own — `test_reserve.py` is most of them —
and reproduce on a clean `upstream/main` worktree with the same toolchain.

`skills/` is untouched by this branch: the SKILL.md edits belonged to the effort
commits (#10), which upstream solved its own way.

## Next

1. **Run one real board on this branch.** Eleven commits and no board has
   exercised them end to end. Highest uncertainty: `crystal_net_routed_tight`
   (new) and the 270° rotation, whose numbers came from a different board.
   Restart `dev.sh` first.
2. Close the `V3_3` gap above, or record why 0.15mm is right.
3. Read `packages/router/` (2487 nodes, 26.6% of the graph, `CIRCUIT_ROUTER=off`).
   Not a blocker for these commits: our checks measure whatever copper exists,
   whoever laid it.
4. ~~Decide patch #11 (`docs/pre-deploy.md`)~~ **DONE 2026-08-18 — ported and revised.**
   The file is now in the tree at 403 lines (was 225). Every claim re-read against
   `65c815a`; the stale ones carry a `REVISED 2026-08-18` block saying what changed.
   The Pre-deploy checklist below now cites a document this branch actually carries.

~~5. Push, open the PR.~~ **Done.** PR #1 OPEN and `MERGEABLE`; local `HEAD`,
`upstream/feat/port-our-fixes` and `origin/feat/port-our-fixes` all at `65c815a`.

## "What if this becomes a service?" — `docs/pre-deploy.md` ✅ DONE 2026-08-18

Asked right after the deployment runbook. Answered by **porting patch #11 and bringing it
current** rather than writing a third doc — P1.1/P1.3 already were this answer, they had
just gone eighteen commits stale. Closes Next #4.

**The substantive change, not a refresh:** the 2026-08-12 draft framed multi-user as
*path* isolation (a `CIRCUIT_HOME`/`CLAUDE_CONFIG_DIR` pair per user) and put it **last,
deliberately**. Wrong altitude for a service. Those vars isolate where files live, not
who can read them — every turn is `bypassPermissions` under one OS user, so tenant A's
build can `cat` tenant B's tree with no exploit at all. **Execution isolation moves from
last to prerequisite**, ahead of the worker split, and the env pair demotes to bookkeeping
inside it. Doc now opens with the fork that sets the whole size: **internal-trusted vs
public-untrusted** — the second is not a bigger version of the first, it needs a sandbox
story this repo has no step toward.

Claims corrected against code, each verified not inferred:
- **P1.3's "multiple users under one OS user share that directory" is WRONG.**
  `sessionJsonlPath` (`projects.mjs:74`) is
  `<CLAUDE_CONFIG_DIR>/projects/<encodeCwd(realpath(workspace))>/<sessionId>.jsonl`, and
  workspace is `$CIRCUIT_HOME/projects/<crypto.randomUUID()>`. Distinct projects already
  give distinct encoded dirs. Collision needs a shared `CIRCUIT_HOME` **and** a shared
  project id — one project with two users, not two users colliding. Stage 2 is more
  plumbing and less hard than the doc budgeted.
- **P1.3's other blocker is also gone** — `circuitHome`/`claudeConfigDir` are per-call,
  not read at boot.
- **P0.4's "do the log and the metering falls out for free" is FALSE as written** — the
  `result` line is free, but review children are never parsed (`stdout.resume()`), and
  review is up to 7 more full `claude` turns. Now written as an underestimate, with the
  "last line wins" fix.
- **P1.2 retitled per-*build* → per-*turn***: the 2700s backstop exists, the turn
  deadline does not.
- **The safety bullet was wrong** — `preflight_safety()` IS called unconditionally at
  `generation.py:921`; the gap is `safety_gate()` on the NL ask.
- **P0.1 expanded**: auth is three jobs, and `project.json` has no owner field at all, so
  identify-without-authorize hands every user every board.
- **P0.2 half done** (board-source writes guarded, `chat_start_turn` not); **Node floor
  22.12 not 20+**; image table gained the two gitignored/manual steps
  (skill install, vendored runtime) the original never listed.

Priority order revised: decide the fork → logging → auth → lock + cap → **isolation** →
per-turn deadline → worker split → sticky routing → per-user path plumbing.

**Step 4 sized, 2026-08-18** (asked as a follow-up; now its own section in the doc).
The seam is right: `spawnClaude` (`driver.mjs:1021`) is 11 lines with exactly two call
sites — `:1110` main turn, `:1274` review loop — so swapping one function covers all 7
review children. Code cost is small in both columns; almost none of the estimate is code.

| | Estimate | Confidence |
|---|---|---|
| Internal — OS user per tenant | **1–2 days ops, 0 code** | high — `circuitHome(env)`/`claudeConfigDir(env)` are per-call, host/port are env, so a systemd template unit + shared read-only toolchain is the whole job |
| Public — swap `spawnClaude` to a container runner | 2–3 days | high, clean seam |
| Public — build the image (`kicad-cli` on Linux) | 2–5 days | **genuinely unknown** |
| Public — quota + egress + abuse | own scope | not estimated |

Three findings that are the actual cost of the public column:
- **Mounts must be at identical absolute paths or `--resume` breaks.** The *parent*
  computes `encodeCwd(realpath(workspace))` from the *host* path, and
  `claudeSessionExists` is what picks `--resume` vs `--session-id`. Mount elsewhere and
  chat history silently detaches. Solvable; the kind of thing that eats a day by surprise.
- **A bind-mounted `CLAUDE_CONFIG_DIR` defeats the sandbox.** Creds live there,
  `--add-dir …/skills` puts it in scope, and the CLI must *write* session JSONL into it
  so it cannot be read-only. Containerise the FS, hand over the credential. Public needs
  creds arriving another way — a separate change from swapping `spawnClaude`.
- **Do not quote 4.8 GB for KiCad.** That is the full macOS bundle (3D models + symbol
  and footprint libraries). Linux `kicad-cli` for DRC + gerber export is a different
  artifact and is **unmeasured here**. `toolchain/node_modules` at 544 MB *is* measured.

And the sufficiency point, which is the sentence that should land hardest:
container-per-turn is **necessary, not sufficient**. Still missing afterwards — per-turn
quota (2700s is per-*build*), egress policy (model has network; parts engine touches it
once per build), abuse handling. Public-untrusted is a different product, not a bigger
estimate.

No implementation. Nothing committed.

---

## Deployment runbook — `docs/deployment.md` ✅ DONE 2026-08-18

Asked for: a single MD to hand to devops answering *"what do we do when we deploy?"*.
Written at `docs/deployment.md` (354 lines), English, uncommitted.

Covers, in order: the security posture first (bypassPermissions + no auth + loopback =
single-tenant only), host prereqs with the measured 1.0 GB / 84% CPU / 45-min-per-build
sizing, the four build steps, the **manual skill install** (no installer exists — the
driver just passes `--add-dir ~/.claude/skills`), Claude credentialing incl. the
`keys.env` 4-key allowlist footgun and JLC creds at `~/.config/autonomous-circuit/jlcpcb.env`
(a path `CIRCUIT_HOME` does **not** move), run + full env table, verification, network
posture, state/backup/upgrade/rollback, known gaps, triage table.

Verified live while writing, not inferred:
- `node viewer/src/server/server.mjs` boots standalone with `CIRCUIT_HOME`/`VIEWER_PORT`
  overrides — smoke-tested on :4188 against a throwaway state dir.
- **Healthcheck is `POST /api/app_prereq_check`** (returns claudeCli/node/toolchain/python/
  kicadCli with `healthy` flags); `POST /api/app_info` for liveness. `GET /api/events` is
  SSE and must never be probed.
- `VIEWER_SERVER_LIFETIME_MS` unset → no auto-shutdown; set → the process self-exits,
  which under `Restart=always` is a restart loop on a timer. Doc says leave it unset.

Corrected during review: the install list is **five** skills, not four —
`design-review` ships in `skills/` and was missing from the first draft. Its absence is
the quiet kind: builds still succeed, the pre-order ship gate just never runs. The doc now
derives the list from `skills/*/` instead of naming them, and warns that every `~` means
the *service user's* home (§5 `User=`), not the deployer's — install as yourself, run as
`circuit`, and you get a green `app_prereq_check` on a non-functional app.

Deliberately **not** written: Dockerfile, systemd unit file, `start` script. A fenced
sketch lives inside the MD; new deploy artifacts are a separate decision.

Open follow-up: this doc references `docs/pre-deploy.md` via `git show df883a1:` because
patch #11 is unported — same decision as Next #4 below. If #11 lands, the two docs should
cross-link properly instead.

---

## Pre-deploy (`docs/pre-deploy.md`)

> **The doc is now in this tree** (patch #11 ported and revised 2026-08-18, 403 lines).
> Every line below was re-verified against the code on `65c815a`, 2026-08-17 — and those
> verifications are what the revision folded into the doc itself, so the doc and this
> checklist no longer disagree. This section stays as the running checklist; the doc
> carries the reasoning and the staged plan.

- [ ] **P0.1 auth — open, unchanged.** Still nothing: no cookie, no token, no
      middleware. `server.mjs:29` still binds `VIEWER_HOST || "127.0.0.1"` and
      that default is still the entire security model. The only `Authorization`
      in the server is `jlcpcb.mjs` signing its own *outbound* calls — not
      inbound auth, and easy to mistake for it when grepping.
- [ ] **P0.2 turn lock — half done, and "exists, unused" is now the wrong
      words.** `turnInProgress` **is** called: `http.mjs:577` wraps it in
      `refuseIfBuilding()`, used at `:668` and `:739` to 409 (`BUILD_RUNNING`)
      any *board-source write* while a build owns the file. That is a real
      guard, but it is not P0.2's. The doc's fix — gate `chat_start_turn` — is
      **not done**: `http.mjs:969` calls `chat.startTurn()` with no check, and
      `startTurn()` in `driver.mjs` does not self-guard either. Two turns on
      one project still collide on `claude --resume`.
- [ ] **P0.3 concurrency cap — open, unchanged.** `driver.mjs:1860`
      `const turns = new Map()`, unbounded. No semaphore, queue or cap anywhere
      in `viewer/src`.
- [ ] **P0.4 logging — open; the count is right but reads worse than it is.**
      Still literally 5 `console.*` in the server. One of them is the `log()`
      helper (`driver.mjs:30`) behind **12 call sites**, so turn start/end and
      every review round already reach the server log. Still absent: the
      structured per-turn line (turnId · projectId · model · effort ·
      elapsed · tokens · cost · exit) and any request/response log at the
      `POST /api/<command>` boundary — `http.mjs:1056` logs only on throw, so
      every successful command is invisible. No cost meter follows until this
      lands. (Not this: patch #8's `emitPhaseNote` is user-facing SSE
      `text_delta`, not logging. The 12 `log()` sites predate our port —
      `44a1f82`.)
- [ ] **P1.2 timeout/quota — open as written, but narrower than the doc reads.**
      The doc's "unset by default" is about the *env var*, not the budget: the
      code falls back to **2700s** (`runner.py::_default_wall_clock_s`, raised
      from 300s on 2026-08-11 — two days *before* `df883a1`, so the doc knew),
      plus `CPU_TIMEOUT_S = 2700` and a 512 MiB output cap, enforced through
      `_enforce_rlimits()`. So: **a per-*build* backstop exists; a per-*turn*
      deadline does not**, and one turn chains many builds — which is how the
      doc's board ran about an hour without contradicting a 45-minute budget.
      No quota of any kind.
- [ ] **P1.1 worker split — open, untouched.** Confirmed by absence: zero hits
      for redis/amqp/bullmq/pubsub/postgres/kafka/sqlite across `viewer/src`
      and `viewer/package.json`.
- [ ] **P1.3 users/folders — open, but one stated blocker is already gone.**
      The doc says `CIRCUIT_HOME` and `CLAUDE_CONFIG_DIR` are "read once at
      boot, so it is one-process-per-user unless that changes." Not true today:
      `circuitHome(env = process.env)` and `claudeConfigDir(env = process.env)`
      (`projects.mjs:44`, `:56`) are pure per-call functions of their argument,
      resolved fresh at all 5 call sites. The seam is already threadable —
      no caller threads a per-user env through it yet. That is a smaller job
      than the doc budgets for.
- [ ] **`keys.env` footgun — open, unchanged.** `PIPELINE_ENV_VARS` is still
      exactly the 4 pipeline keys, and `readKeysEnv` (`driver.mjs:388`) drops
      anything else **silently** — no warning, no log. Someone will still put
      `ANTHROPIC_API_KEY` in a file called `keys.env` and lose an afternoon.
- [ ] **NEW · Per-user prompt + outcome log, for improving the product.**
      Asked for 2026-08-17. Separate by user, record what people asked and what
      the pipeline produced, so the gap is measurable instead of anecdotal.
      This is the reason to do **P0.4 with a `userId` column from day one**
      rather than retrofitting one, and the reason P1.3 stops being "last".

      *Pair the prompt with two outputs, not one — both are already in hand:*
      - **the plan** (`proposedPlan`, already returned by `runTurn`,
        `driver.mjs:1252`) — what the user approved. A bad board usually starts
        as a bad plan, so this is where you look when the score is bad.
      - **the board verdict** — the `.board.json` sidecar, which is already a
        finished outcome record: `fab.ready`, `bom.orderable`/`lines`/
        `basicParts`, `build.attempts`, `build.autorouterEffort`,
        `build.blockingByAttempt`, `validation.warnings`, `toolchain.*`,
        `source.hash`. This is the score.

      *Constraints found while checking, each one a real trap:*
      - **Do not copy the transcript.** Every prompt and every response already
        sits at full fidelity in `~/.claude/projects/<encoded>/<sessionId>.jsonl`,
        which the driver already reads (`sessionState`,
        `recoverPlanFromTranscript`). What is missing is an *index* over it plus
        the outcome join — not a second copy, which only doubles the PII surface.
      - **Snapshot the sidecar's values; never store a path to it.**
        `main.board.json` is overwritten every build (`generation.py:1928`), so
        a row pointing at it loses its verdict on the next build.
      - **The join is 1:n, not 1:1.** A plan turn produces zero sidecars; a
        build turn can touch several boards. Model it as turn → 0..n board
        outcomes keyed on `source.hash`, or the first multi-board project
        breaks the schema.
      - **The meter IS free at `fromResult`, exactly where `pre-deploy.md` says.**
        Settled by capturing a real `claude -p --output-format stream-json` run
        on 2026-08-17 (an earlier note here said otherwise, reasoning from the
        repo having no fixtures — wrong; absence of a fixture is not absence of
        a field). The `result` line carries, verbatim:
        `total_cost_usd` · `usage{input_tokens, output_tokens,
        cache_creation_input_tokens, cache_read_input_tokens,
        output_tokens_details.thinking_tokens, server_tool_use}` ·
        `modelUsage{<model>: {costUSD, contextWindow, maxOutputTokens, …}}` ·
        `duration_ms` · `duration_api_ms` · `ttft_ms` · `time_to_request_ms` ·
        `num_turns` · `is_error` · `subtype` · `stop_reason` ·
        `terminal_reason` · `api_error_status` · `permission_denials` ·
        `session_id`. `fromResult` (`driver.mjs`) currently reads `obj.result`
        (the text) and discards **all** of it. One turn row's meter *and* its
        error status come from that one line.

      *Tap point — decided 2026-08-17.* Log **what the user sees**, not what the
      model did. There is exactly one funnel: `broadcastChatEvent`
      (`http.mjs:485`), wired in as `emit:` on `createChatService`. Every SSE
      byte a client receives passes through it. This is strictly better than the
      transcript for product questions, because it captures the **silences** —
      the review loop drains its child's stdout without parsing, by design, so a
      ninety-minute quiet stretch is invisible in the transcript and obvious as a
      timestamp gap in an event log. That gap is what patch #8 was written for.

      *Caveat, and a finding in its own right:* a tap there records
      **intent-to-display, not receipt**. `clients` is a live Set and there is
      **no SSE replay** — `writeSse` emits `event:`/`data:` with **no `id:`**,
      and there is no Last-Event-ID handling or backlog anywhere in `http.mjs`.
      A broadcast with nobody connected goes nowhere and nothing persists it, so
      a user who reloads during a long build loses the stream and can only
      reconstruct from `sessionState`. **The fix and this log are the same
      work:** a durable per-turn event log *is* the backlog, so adding `id:` +
      Last-Event-ID replay on top of it is nearly free. Logging pays twice.

      *Sinks — three, not two, because granularity differs:*
      - **raw append-only file, verbatim** — every event exactly as broadcast.
        This is the "clone" instinct and it is right: you cannot reconstruct
        what you did not keep, and this sink needs no schema.
      - **Postgres, coalesced** — turn row + message rows + 0..n board-outcome
        rows. **Not** per-event: `text_delta` (`driver.mjs:612`) is a streaming
        *token* delta, so one row per delta is write amplification for no
        analytical value. Concatenate to one message per assistant turn.
      - **stdout, milestones only** — turn start/end, phase transitions,
        verdict. Per-token to a terminal is an unreadable log.

      *Harvest — the confound to control for.* "Collect the prompts that made
      good boards" selects on an outcome this repo has already measured as
      unstable: `autorouterEffortLevel` moves the verdict hard, and **not in a
      consistent direction** — one rp2040-core board went 5 blocking → 0
      blocking when raised, while the effort-floor grid in the ledger above
      recorded 1x = 0 blocking against 5x = 16. Different boards, and `default`
      and `1x` are separate rungs on upstream's ladder, so these are not one
      contradiction — but together they say the thing that matters: **effort
      moves the score and the movement does not generalise.** Harvest on raw
      outcome and you harvest router luck wearing a prompt's clothes.
      Pin the confounds instead — the sidecar already records
      `build.autorouterEffort`, `build.attempts` and `toolchain.*`, so this is a
      WHERE clause, not new work. Highest-signal cases are **bad boards from
      reasonable prompts** and **good boards with `build.attempts > 1`**: both
      show where the agent had to flail.

      *Terminate the harvest in an eval case, not a table.* `evals/run.py`
      already scores `fab.ready` + blocking and `composition.py` /
      `composition-matrix.json` sit beside it. A harvested prompt becomes a case
      there or the whole exercise is a lake nobody queries.

      *Decision (2026-08-18) — store the token **breakdown**, USD optional.* Asked
      to log total tokens and skip USD ("they can multiply it themselves"). Fine —
      but only if the four counters are stored **separately**, because they are
      priced very differently and one summed total cannot be un-mixed:
      `cache_read` ≈ **0.1×** base input, `cache_creation` **1.25×** (5-min TTL) or
      **2×** (1-hour TTL), and output is **5×** input on Opus 5 ($5 / $25 per MTok).
      That is a 50× spread between the cheapest and dearest token in one turn — a
      single `total_tokens` column makes the multiply impossible, not merely
      lossy. So: `input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
      `cache_read_input_tokens`, plus the model id (a turn can span models —
      `modelUsage` is keyed by model).

      Keeping `total_cost_usd` is still worth one column even so: it is already
      computed on the `result` line (zero work), and it is a **price snapshot**.
      Recomputing an old turn later with today's rate gives a wrong historical
      number. Their call — the breakdown is the part that is not optional.

      *Requirement 1 (2026-08-17) — grain is project → turn → events; log model,
      user_id, prompt, tokens.* This is the app's **native** grain, not a schema
      imposed on it: `broadcastChatEvent(projectId, event)` already carries
      `projectId`, every ChatEvent already carries `turnId`, and one project =
      one session (`uuidv5`) = one turn at a time. Both keys are at the funnel
      for free. `model` and `effort` are already threaded parameters
      (`runTurn` → `buildCommandArgs`, `driver.mjs:1101`). `user_id` ships as a
      **nullable column from day one** and gets filled when P0.1 lands — do not
      wait for auth to start logging, the backfill gap is the cheap part.

      *Requirement 2 (2026-08-17) — log errors. There are FOUR families; keep
      them apart.*
      1. **Turn errors** — 4 emit sites (`driver.mjs:1083` spawn/startup,
         `:1219` no-response with stderr + exit code, `:1248` cancelled,
         `:1901` driver failure). All four already go through `onEvent` →
         `broadcastChatEvent`, so the one tap catches them. `stderrBuf` (8 KiB
         cap) is already buffered and today only feeds one message string at
         `:1219` — keep it on the error row, it is the diagnostic payload.
         Turn-level status also comes free off the `result` line above
         (`is_error`, `subtype`, `api_error_status`, `terminal_reason`).
      2. **API/command errors** — `http.mjs:1056`, `ipcError` carrying
         `code`/`statusCode`/`detail`. These never touch the chat funnel, so
         req 2 needs a **second tap at the command boundary**. Today it logs
         only on throw, so every *successful* command is invisible — the other
         half of P0.4.
      3. **Board findings** — `validation.warnings[].severity === "error"`.
         **Not a system error.** This is the product working correctly and
         reporting a bad design. Merge it into the same table and it swamps
         everything by volume (the measurement table below shows 500–1200
         warnings on a single board) and your error rate stops meaning
         anything. Keep "is our app broken?" answerable separately from "did
         this board need work?".
      4. **Swallowed review failures** — `driver.mjs:1345` "could not snapshot
         the workspace", `:1375` "could not undo the round". These go to
         `log()` only: never to the user, never failing the build
         ("Best-effort throughout — never fails the build turn", `:1414`). For
         a log whose job is answering *is our app broken*, these are the
         failures that are **invisible by construction** — the same shape as
         this repo's standing trap, a review that cannot run reading exactly
         like a clean one.

      *The blind spot that costs the most — review children are unmetered and
      unwatched.* `runReviewRound` (`driver.mjs:1264`) does
      `child.stdout.resume()` — a pure drain, nothing kept — plus
      `child.stderr.resume()`, so unlike the main turn it does not even buffer
      stderr; `spawnClaude` throwing returns `false` silently; the exit code is
      never read. Consequences: **no `result` line is parsed for any review
      round**, so a `fromResult`-based meter counts the main turn only, while
      review is up to **2 + 3 + 2 = 7 additional full `claude` turns**, each
      rebuilding the board and `Read`ing two PNGs — plausibly the *majority* of
      spend. A meter that omits it is not a billing meter, it is an
      underestimate. Same hole swallows req 2: a review child that dies leaves
      no trace anywhere. **Fix is small and idiomatic:** keep the last stdout
      line and parse it — the repo's own "one JSON line, last line wins"
      convention — and buffer stderr the way the main turn already does.

      *And refine the claim above about silences:* an event log shows **that**
      there was a ninety-minute gap, not what happened inside it, because the
      review child's output is discarded. Diagnosis needs the driver's existing
      `log()` sites, which already print phase + round + finding counts
      (`:1505`, `:1531`, `:1557`, `:1582`). Carry review round counts and
      outcomes onto the turn row, or the log answers *slow* without ever
      answering *why*.

      *Store:* **Postgres**, since P1.1 Stage 1 wants a durable queue anyway and
      one dependency beats two. But the ordering matters: the **turn-row shape**
      is the hard part and the store is swappable, so write the structured line
      to an append-only file first and load it in later. Designing the schema
      before one real turn line exists is designing it blind.

      *Contract:* server-side logging in `driver.mjs` touches nothing frozen.
      If any of it becomes client-visible, the ChatEvent union is name-coupled
      to the client (`docs/circuit-interfaces.md` §3) and needs a
      `circuit-interfaces-CHANGES.md` entry — patch #8 dodged exactly this by
      reusing `text_delta` instead of adding an event kind.

      *Open question for a human:* prompts here are product ideas people may
      consider confidential. Retention window and who can read the table are
      not engineering calls.

- [ ] **Safety in the build path — the old line was wrong and is now
      re-scoped.** `safety_gate()` is indeed never called from `build_board()`
      (only `evals/run.py`, `golden.py::run_golden_set`, tests, SKILL.md
      prose). **But the envelope is not unenforced:** `build_board()` calls
      `spec_mod.preflight_safety()` unconditionally at `generation.py:921` —
      inside `build_board` (def at `:879`), no env flag, no swallowing
      try/except — and it raises `SpecValidationError` on mains patterns, raw
      RF, raw battery ICs outside `blocks/`, and voltage literals. The real
      remaining gap is narrower and worth stating properly: **`preflight_safety`
      screens the generated source; `safety_gate()` screens the natural-language
      ask.** A dangerous *intent* that compiles to innocent-looking source still
      walks through.

## Board measurements (16 fab packets on this machine)

`kicad` = `drc_violation` + `erc_violation`. The column splits cleanly in two
with nothing in between.

```
                          ready  err  warn  kicad   min
terminal-keyboard         no      1   1247   1246
hydrate-coaster           yes     0    745    682
harness-puck              no      5    724    656
night-light               yes     0    578    530    230
weather-badge-3 baseline  yes     0    557    517    160   <- control, collapse OFF
weather-badge-9           yes     0    546    490     85   <- upstream code (*)
hydrate-coaster (2nd)     no      6    537    522
desk-air-monitor          yes     0    521    476    180
------------------------------------------------------------
weather-badge-8           yes     0     74     23     97   <- collapse ON
weather-badge-2           yes     0     71     24     79
weather-badge-4           yes     0     70     23     47
weather-badge-6           yes     0     69     23     43
weather-badge             yes     0     69     23     54
status-ring               yes     0     68     23     64
weather-badge-7           yes     0     66     23     75
weather-badge-5           yes     0     65     21    100
```

(*) wb9's numbers come from the 15:47 fab packet, which is complete and
self-consistent. `main.tsx` was edited at 15:49 and a build started at 15:49:56
(trying `vinThickness="0.5mm"` on Ldo3v3) never landed. Source is ahead of the
packet.

wb9 is 1.7x the median area (52.9 x 87.9 = 4650 mm²) at a non-round size, so
upstream appears to size the outline itself rather than take the author's
figure. Feature or retreat is unmeasured. Build time (43–100 min on patched
boards) shows no trend; one run proves nothing.

## Lessons already paid for

- **Prose in SKILL.md is advice a model weighs, not a guarantee.** Only the loop
  makes it so.
- **A caveat is not a measurement.** The crystal check's blind spot was
  documented and a violating board was passed anyway.
- **Never report mid-flight state as final.** wb5 "41 errors", wb8 "89 errors",
  wb9 "6 errors" — all three intermediate, all three healed.
