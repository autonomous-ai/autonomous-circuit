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

## #28 · CI has never been green. Not once. — found 2026-08-21

Checking PR #13's checks turned up something bigger than the PR. **`main` fails
four of six jobs, and the last 100 runs contain zero successes** — there is no
commit in this repo's recorded history where CI passed. `viewer` and `skills`
are green; `pipeline`, `routing contract`, `golden blocks` and `structural
evals` are red on `main` at `0edb454` and have been every day back through
2026-08-17. PR #13 fails the same four with the same causes and adds none.

Two causes, both concrete, neither a mystery:

- **numpy is never installed.** Every job runs `pip install pytest` and stops
  there; `reserve.py`, `diffpair.py` and `router/algorithms/metaheuristic.py`
  all import numpy. `pipeline` loses **60 tests** (`ReserveResult(status=
  'refused', reason_code='no_numpy')` — the code degrades correctly, the tests
  assert the happy path) and `routing contract` cannot even collect. Same
  breakage locally: a bare venv fails 59, `pip install numpy` takes it to 530
  passed. **Cheap: one word in the workflow, or the skip discipline the repo
  already applies to kicad.**
- **kicad-cli is never installed either.** `structural evals` tries, and the
  log says `E: Unable to locate package kicad-cli` — the Ubuntu package is
  `kicad`; `kicad-cli` is the binary inside it. `golden blocks` and `pipeline`
  do not try at all. So gerbers fall back to the tscircuit exporter and the
  gate reports what that exporter actually produces: **missing pad flashes on
  J1/U4/U5, missing drill hits, 0.050mm silkscreen against a 0.15mm floor.**
  Those findings are true — the fallback packet is unshippable, which is what
  `unverified_gerbers` has been saying all along. The question is whether CI
  should install kicad and grade the real path, or stop grading gerbers when
  the source is not kicad-cli. **That is a real call, not a config typo.**

**Why this is the same disease.** Every green local run has been read as "the
change is safe" while the shared gate was red the entire time — the seventh
entry in *the machine measures correctly and the reading is wrong*, and the
most expensive, because it is the ruler every other reading was checked against.


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
| **G** | U4 flash footprint IoU 0.6347 — **RESOLVED 2026-08-20**: fetched C97521's land pattern and ours is identical to the digit. The metric tops out near 0.75, so the score was the ruler; see #11 | 18/18 | no |

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

## Done 2026-08-19 — the three P0s

Branch `feat/see-the-pour-properly`, on top of `6c3f6cd` (PR #5 merged).

### #18 · `isolated_copper` is re-measured, not suppressed

`circuit-json-to-kicad` writes a pour as a **triangle mesh** — one 3-vertex
`filled_polygon` per triangle — and KiCad's connectivity treats each as its own
island. That is the right question asked of the wrong object, and it is the
largest warning-severity category in the corpus: **2394 instances over 17
boards**, every one of them describing the mesh rather than the copper.

`checks._remeasure_isolated_copper` unions the mesh across shared edges and
judges the rule on the answer. On weather-badge-15: 2623 triangles across 2
zones union into **one connected region**, so the rule drops to `info` carrying
the count that proves it. A pour that really is in pieces keeps the severity
KiCad gave it, and a pour that is not a mesh is left to KiCad entirely — on
that shape KiCad's island analysis is the right one.

One trap caught on review before it shipped: `verify_bridge._load()` imports
every check inside **one** `try`, and `_ensure_path()` prefers a *vendored*
verifylib beside the skill runtime. A runtime vendored before `pour` existed
does not carry it — so adding `pour` to that block would have made the one
missing name take all nine others down, and the verdict would have come back
as a single `verify_unavailable` line reading like a missing toolchain rather
than the regression it was. Optional checks now import one at a time. Proven by
simulating the absence: nine modules survive, `pour` is skipped. Exactly the
trap `1e7ac6a` already paid for once.

It was tempting to put `isolated_copper` in `KICAD_NOISE_FLOOR` and be done. A
floor pins a rule *unconditionally*, which would have traded 2394 false alarms
for one silent real one the first time a pour genuinely fragmented. Measuring
it costs a union and keeps the finding.

7 tests, including the one that matters: **a pour that really is in pieces must
keep its severity.** If that ever goes green while demoting, this is a filter.

### #14 · Something finally asks whether there is a ground plane

New check `verifylib/pour.py`, wired into `CIRCUIT_JSON_CHECKS` and the CLI.
Grepped first to be sure the hole was real: `netclass._ground_shapes` reads
pours when they exist and **nothing anywhere required one**. Run over the
corpus it produces exactly the shape the measurement predicted:

```
ground_pour_missing    warning    4 boards   weather-badge-5 (fab.ready=True),
                                             harness-puck, terminal-keyboard,
                                             hydrate-coaster
ground_pour_one_sided  info      17 boards   poured on bottom, never on top
ground_pour_partial    warning    0 boards
```

`hydrate-coaster` is the case a naive check would pass: it *has* two copper
pours, on `top`, and **neither is on the ground net**. Counting pours instead
of ground pours calls that board fine.

**It does not block, on purpose.** The bar is "block only what makes the
delivered board unusable or the order refused", and a plane-less board is
neither — every ready board has `unconnected_items = 0`, so no pad depends on
pour copper to reach its net. The line lives on the fab profile where an EE
moves it in one place. 10 tests.

### #21 · How close is a passing board to not passing — measured

`scripts/board-table.py --margin`. `fab.ready = True` says a board cleared the
gate and says nothing about by how much, and after the decoupling experiment
those are visibly different facts.

```
10 of 18 ready boards sit on the last rung of the effort ladder
   having needed the repair pass to get there.
```

Three more needed rescuing with one rung still unused; only two boards reached
the top rung routed clean. On the ten, `check_failed` has already said in
plain words that no retry was attempted because there is nothing harder to try.
So the answer to "is every board one perturbation from losing its verdict" is
**not all, but most of the ones at 10x** — and weather-badge-15, which lost
`fab.ready` to a 7µm clearance 11.5mm from the change, is one of the ten.

This is the number that governs #16, #15 and #20: every one of them moves
copper, and on ten boards there is nothing left to absorb the move.

### Confirmed end to end, not just in tests

Rebuilt weather-badge-15 through the real pipeline on the new code, 265.3s,
against the app's own sidecar as the control:

```
fab.ready                     True -> True          unchanged
error                            0 -> 0             unchanged
warning                         28 -> 26
info                            59 -> 32
isolated_copper            warning -> info          re-measured, count attached
ground_pour_one_sided        absent -> info         the new check ran
verify_unavailable                    absent        nothing lost
```

Every delta accounts for: **-30** `supplier_footprint_mismatch` (29 info + 1
warning) that `CIRCUIT_PARTS_ENGINE=off` cannot produce, **-1 warning / +1
info** for `isolated_copper` moving, **+1 info** for `ground_pour_one_sided`.
28-1-1 = 26 and 59-29+1+1 = 32. Nothing else moved.

And the check that mattered most after the import split: all ten other verify
families are still present at their original counts — `crystal_net_routed_long`
2, `netclass_pair_*` 4, `review_decoupling_distant` 5, `thermal_regulator` 1,
`diffpair_not_routed` 1, `dfa_off_board` 1, `dfm_power_trace_width` 2,
`review_esd_unprotected` 1 — and `verify_unavailable` never appears.

### Not done here

`packages/circuitpy` arrives with **59 pre-existing test failures** on
`tests/test_reserve.py` and friends — verified by stashing this branch and
re-running: identical count, so they are on `main` and not from this work. The
`verify` suite is green: **184 passed** (174 before this branch, +10 for
`pour`). `circuitpy/tests/test_checks.py` + `test_fab.py`: 128 passed.

## weather-badge-16, 2026-08-19 — the 22nd board, built to be measured

Built as a **one-variable experiment against weather-badge-15**, not as a copy
for the count. Identical source but for one line: a second `<GndPour />` on the
top layer. That single line is the whole of #15, and #15 is the item every
board in the corpus carries. Criteria were written before the build ran
(`wb16-compare.py`).

### The routing answer is yes, and it is emphatic

```
                         wb-15    wb-16
fab.ready                 True     True
error                        0        0
effort rung                10x      10x
blockingByAttempt          [2]      [2]
ground_pour_one_sided     info    GONE
netclass_pair_reference      1 ->    0
```

The router took the top pour at **no cost in headroom** — same rung, same
blocking count — and the diff pair's return path went from a warning to
nothing, which is precisely what a top pour is for. #21's worry did not land
here: adding copper is not the same kind of perturbation as moving a part.

### And the gate lied about it

`drc_violation` fell **16 -> 0** and warnings **27 -> 11**. None of that was an
improvement:

```
check_failed: kicad DRC failed: kicad-cli failed (exit -11): no output
```

`-11` is SIGSEGV. Reproduced by hand, deterministically, `exit=139`. Two
triangulated pours instead of one takes the board from **2639 to 5800** filled
polygons and 0.99MB to 1.67MB, and kicad-cli dies on it.

So the sixteen DRC findings were not fixed, they went **missing** — and the
board came out reading *cleaner than the control*, still `fab.ready = True`.

**A gate that crashed looked exactly like a gate that passed, only better.**
That is the one failure shape this pipeline must never have, and the asymmetry
that allowed it was already in the code: kicad-cli *absent* produces a blocking
`unverified_gerbers`, because gerbers nobody could check must not ship;
kicad-cli *invoked and dead* produced a `check_failed` warning and shipped. A
gate that was present and died is strictly worse evidence than one that was
never there, so it cannot be the softer verdict.

Fixed: new `checks.gate_did_not_run`, error severity, so `fab_ready` stops the
board. Kept distinct from `check_failed`, which is advisory and also carries
notes that are not failures at all — the effort ladder says "no retry was
attempted" through it, and blanket-blocking that kind would stop boards over a
note. The ERC leg deliberately stays advisory: every ERC kind is pinned to
`info` by `KICAD_NOISE_FLOOR`, so an ERC leg that dies loses nothing that could
ever have blocked.

### Correction, same day: the wb-16 verdict was read mid-flight

Everything above about the crash is sound. **What was reported as the board's
final state was not.**

`weather-badge-16` lives under `~/.autonomous-circuit/projects`, the workspace
the app owns, and the Vite dev server was running the whole time. Its silent
review loop saw a board that had lost its verdict, **removed the top pour**,
rewrote the comment around it in its own words, and rebuilt. `main.tsx` mtime
`11:29:42`; the comment it left reads:

> *Bottom ONLY on build #1, deliberately. A previous revision of this file also
> poured `top` as an explicit unmeasured experiment and that build never
> reached a fab.ready verdict, so the experiment produced no evidence and cost
> the board its first shot.*

That is the review loop doing exactly its job on a workspace it owns, and it is
fatal to an experiment. So:

```
wb-15 vs wb-16, as they now stand
  error 0 -> 0    warning 27 -> 27    info 30 -> 30
  per-kind delta: NONE — the two boards produce identical findings
  pour 2423 triangles, one piece, 3040mm2 — identical
  netlist 173 pads compared, 0 split / 0 merge; boot chain 8/8
  rung 10x, blockingByAttempt [2] — identical
```

**weather-badge-16 is currently a twin of weather-badge-15.** The one line that
made it an experiment is gone.

This board's own rule, broken by the person writing it down: *never report
mid-flight state as final*. `fab.ready: False` with `gate_did_not_run` was
observed and reported while another agent was concurrently editing the source.
It was true when read and it was not the end of the story.

**What survives the correction, and what does not:**

| claim | status |
|---|---|
| kicad-cli segfaults on the two-pour board | **holds** — 4/4 by hand on the kept artifact, `exit=139`, 5800 polygons / 1.67MB |
| a crashed gate left the board `fab.ready` and looking cleaner | **holds** — that build is on disk |
| `gate_did_not_run` blocks it | **holds** — unit-tested; the live observation was mid-flight |
| the router takes a top pour at no cost in headroom | **holds** — re-measured in isolation, same rung, same `[2]` |
| wb-16 is a board with a top pour | **false now** |

The re-run lives in the scratchpad, outside the app's workspace, because an
experiment cannot share a directory with an agent whose job is to repair it.
Its result is the section below, and it confirms every surviving claim.

### Re-run in isolation — every claim now rests on an uncontaminated build

Same source, built in the scratchpad where no review loop can reach it. The top
pour survived into the artifact this time (`{'bottom': 18, 'top': 28}`).

```
                                  wb-15   top-pour (isolated)
fab.ready                          True                 False
error                                 0                     1
warning                              27                    10
info                                 30                    27
rung / blocking                     10x  [2]          10x  [2]

gate_did_not_run                      0 ->    1     <- the fix fires, cleanly
netclass_pair_reference               1 ->    0     <- the top pour did its job
drc_violation (warning)              16 ->    0     <- absent, not resolved
drc_violation (info)                  3 ->    0     <- same
```

And the cause, measured on all three artifacts side by side:

```
board                  bytes  polygons   kicad-cli pcb drc
wb-15                 988003      2639   exit=0
wb-16 (as it stands)  988003      2639   exit=0
top-pour (isolated)  1668979      5800   exit=139
```

**Everything now holds on evidence that no other agent touched:**

- The router takes a top pour at **no cost in headroom** — same rung, same
  `blockingByAttempt [2]`.
- `netclass_pair_reference` **clears**, which is the electrical point of it.
- kicad-cli **segfaults deterministically** on the doubled triangle mesh.
- `gate_did_not_run` **stops the board**, on a real build rather than a unit
  test — `fab.ready = False`, and the sixteen missing DRC findings are now
  named as missing instead of read as absent.

That last line is the whole fix working end to end: the same board that
yesterday would have shipped `fab.ready = True` looking cleaner than its
control now refuses, and says why.

### What this makes of #15

**Not a design limit — a converter limit, and the same one as #18.** The router
routes it, the electrical result improves, and the thing that breaks is
kicad-cli choking on a pour written as thousands of triangles instead of a
polygon outline. #18 was that defect making noise; #15 is that defect blocking
a real improvement. Fix the converter's pour emission and both close.

Until then #15 is blocked on the toolchain, and wb-16 is the board that proves
it rather than the note that says nobody tried.

## Done 2026-08-19 — #22, the pour ships as an outline

`kicad_normalize` now unions the converter's triangle mesh into its boundary
rings and re-spells each zone as one `filled_polygon` — the shape KiCad's own
filler writes, because the format has no way to say "hole". Holes are cut in
with zero-width slits, right to left.

Measured over **all 12 boards in `products/`** — a different fleet from the 19
app workspaces the rest of this board measures, so none of these numbers
compare against anything above:

| | |
|---|---|
| Zones outlined | **12/12 boards**, every mesh zone on each |
| B.Cu polygons | e.g. `2325 -> 4`, `2232 -> 1`, `709 -> 1` |
| Plotted copper area | **identical on B.Cu and F.Cu, every board** — the gerbers are re-plotted and the shoelace area compared |
| kicad-cli DRC | loads and completes on all 12, before and after |
| Every DRC rule except `isolated_copper` | **zero delta on every board** |

**The `isolated_copper` numbers cannot be stated as a delta, and this is not a
hedge.** kicad-cli 10.0.5 caps its DRC report at **199 violations per rule** —
proven, not assumed: all 12 boards report exactly 199 before, while `clearance`
on the same reports varies 0–4. So every before-figure is a floor. After
outlining the count is 0–23 and below the cap, so the *after* side is real and
the *before* side is only known to be ≥199. The honest measure of fragmentation
is the gerber region count, which is uncapped and is what the table reports.

### Two bugs, four boards

The first cut of this pass declined 4 of the 12 outright, and it was two
causes, not four boards:

- **`_fracture` gave up after 32 candidate anchors.** On `i2c-sensor-hub` the
  outer boundary is a bare four-corner rectangle, so once two holes are merged
  in, every ring vertex within 14mm of the third hole belongs to one of them
  and is either already carrying a slit or screened off by one. The anchor
  that works is the **124th** nearest (59th and 65th on the other two). The cap
  existed to stop a pathological pour becoming a geometry benchmark — but the
  sweep rebuilt the ring's edge list *per candidate*, which is what made a
  bounded sweep look necessary. Hoisted, it is one pass per candidate and can
  afford the whole ring.
- **Three collinear points cost `rgb-lamp-controller` its entire pour.** The
  converter emits a few collapsed triangles per board — 3 of 2325 here. They
  enclose no copper. They are now dropped, not declined.

### What holds it

`tests/test_pour_outline.py`, 13 tests, including the real `i2c-sensor-hub`
pour as a fixture. **5 of them fail on the code as it stood** — checked by
reverting both fixes in place and re-running, because a test that cannot fail
on the defect is not evidence about it. `packages/circuitpy`: 472 passed.
`skills/circuitcode`: 133 passed.

### What this does NOT show

**Nothing here touches #15.** `fcu_regions` is `0->0` on all 12 — not one board
in `products/` pours on top, so this run demonstrates the #18 half only.

## Measured 2026-08-19 — #15 is NOT the mesh, and the mesh theory is refuted

The claim above — *"fix the converter's pour emission and both close"* — is
**wrong**, and now measured rather than argued. Run wb-16's top-pour board
(`tp/main.kicad_pcb`, 5800 fills) through the outlining pass and DRC it:

```
main.kicad_pcb      5800 fills   exit=139
outlined            5757 -> 15   exit=139     (0.43s to outline)
```

Same segfault. Delta-debugging the outlined board, one zone at a time:

- **B.Cu's 18 zones alone: exit 0.** **F.Cu's 28 zones alone: exit 139.** The
  crash lives entirely on the top pour.
- Shrunk to a **single sufficient zone: #40, F.Cu, net GND, one fill, 288
  vertices.** That zone alone on an otherwise empty board segfaults kicad-cli.
- That fill **visits one vertex — `(92.738553, 96.8101)` — four times.**
  Exactly the trigger the via pass already had written into `_fracture` as the
  reason it will not anchor twice on one vertex. **The converter writes such a
  zone by itself**: zone 40 is byte-identical before and after the outlining
  pass, which never touched it (it was already one polygon).
- Split that ring into simple loops at its own repeats, area preserved exactly,
  change nothing else → **exit 0.** The 4-visit vertex is the crash, proven by
  removal.

**So #15 was never blocked on the triangle mesh.** It is blocked on the
converter emitting a pour outline that touches itself at a point, and
kicad-cli dying on it.

### And that is not the whole of it either

Splitting zone 40 and re-running the *full* board still segfaults. Shrinking
again lands on **three F.Cu GND zones together (#29, #33, #40)**, none of which
repeats a vertex at all. So there is a second cause that only appears when
several top-layer zones coexist, and it is not the 4-visit rule.

**The split used in that experiment is also not a usable repair.** Decomposing
zone 40's ring produced four loops, one of them with **negative area** — a
hole. Emitted as its own `filled_polygon` that hole becomes solid copper, so
the arithmetic held while the copper did not. Any real fix has to keep holes as
holes; that is what `_fracture` exists to do and the repair belongs there.

### Fixed. A self-touching outline is re-spelled as the outlines it is made of

A zone carries **as many `(polygon)` blocks as its shape needs** — the main
ground pour on every board in `products/` has between 8 and 22 — and their
union is the zone. So the crashing outline never needed the zone broken up: it
needed replacing, in place, by the several simple outlines it decomposes into.
The holes are the part that has to be right: a hole that meets its region at a
point is **walked out and back from that point** rather than bridged to,
because a bridge spends the shared vertex twice more and rebuilds the crash.

Measured end to end on wb-16's top-pour board, through `normalize_for_fab`:

```
kicad-cli DRC   exit 139  ->  exit 0
F.Cu            3161 regions, area 2935.3198  ->  42 regions, SAME area
B.Cu            2639 regions, area 3224.2189  ->  18 regions, SAME area
```

An earlier cut of the split copied a fill into more than one zone and put
**+98.7mm² of copper on F.Cu**. Nothing in the source said so; the gerber area
did. It is a test now.

## weather-badge-17, 2026-08-19 — the top pour, measured against its own control

The experiment wb-16 could not finish, run properly this time: the same board
built twice, differing in one line. Both builds outside
`~/.autonomous-circuit/projects`, so nothing could edit them mid-flight.

| | bottom only | both layers |
|---|---|---|
| **`fab.ready`** | **True** | **False** |
| `netclass_pair_reference` | 1 | **0** |
| `ground_pour_one_sided` | 1 | **0** |
| `clearance` (error) | 0 | **41** |
| `hole_clearance` (error) | 0 | **5** |
| `solder_mask_bridge` (error) | 0 | **4** |
| `zones_intersect` (error) | 0 | **3** |
| `copper_sliver` | 1 | 8 |
| `isolated_copper` | 16 | 43 |
| **every other finding class — 29 of them** | **identical** | **identical** |

**The routing claim holds and the electrical claim holds.** The router takes
the top pour at the same effort and the same `blockingByAttempt [2]`, and
`netclass_pair_reference` — the finding that says 30% of USB_DP has no ground
under it — **clears**.

**The board still does not ship, and now it says so.** 53 error-severity DRC
instances the pour brings with it, led by 41 clearance violations between the
F.Cu zone and the tracks it was poured over: 0.0465mm against a 0.15mm floor.
That is the pour being laid without the clearance the tracks need, and it is a
real defect in the board, not in the gate.

**That is the whole difference from wb-16.** Same experiment, and last time it
came back `fab.ready = True` looking cleaner than its control because sixteen
DRC findings had been eaten by a segfault. This time DRC ran, found 53 reasons,
and the board refused. A gate that can see is worth more than a gate that
passes.

**#15's toolchain blocker is closed. #15's engineering question is now open and
answerable**: the top pour needs clearance to the copper already routed under
it. That is a pour-generation question — `GndPour`'s clearance against existing
tracks — not a converter one.

## Measured 2026-08-20 — #12's only lever is measured, and it is shut

weather-badge-19's crystal net is a real defect, not a measurement artifact:

```
U3.XIN  @ (-19.60, -5.21)        pad to pad: 7.94mm — well inside the 10mm ceiling
Y1.pin1 @ (-20.10, -13.13)
copper the router laid:          26.21mm + 2 vias = 29.41mm   (a 3.3x detour)
U3.XOUT -> R11.pin1:             17.17mm + 2 vias
whole crystal net:               62.91mm of copper, 4 vias
```

The parts are close. The copper is not. That is exactly what the check says,
and #12's note — *"route hints, still the only untried lever"* — is now tried.

**Our own router cannot take the job, and the reason is one line.**
`CIRCUIT_ROUTER=portfolio` came back `router_declined`:

> *board carries 17 copper pour(s) generated around the incumbent's traces;
> re-pouring after our route is not written yet*
> — `router_bridge.py:180`, gated behind `CIRCUIT_ROUTER_ALLOW_POURS`

**Every board we build is poured**, so routerlib has never routed one in the
normal path. That is a bigger fact than #12 and it belongs to #9.

Opening the gate and measuring anyway, twice:

| | nets connected | crystal net | verdict |
|---|---|---|---|
| tscircuit (incumbent) | **33/33** | 62.91mm, 4 vias | `fab.ready`, 0 errors |
| ours, `portfolio` | **29/33** | — incumbent kept | `fab.ready`, 0 errors |
| ours, `portfolio-force` | 29/33 | **0.00mm — not routed at all** | **285 errors**, 4 nets open |

The portfolio gate did its job: ours is worse on completeness and was refused.
And the answer to "would our router route the crystal shorter" is **no — it
does not route that net at all**, so finishing routerlib is not a shortcut to
#12 either.

**What is left for #12**, in the order they cost:

1. A tscircuit-side route constraint or hint for the net, if one exists — the
   cheapest lever and the only one not yet looked for.
2. Placement: move Y1 so no detour is available, rather than asking the router
   not to take one.
3. Finish routerlib (4 nets, and re-pour after routing) — which #9 wants
   anyway, and which this measurement now prices.

**Not attempted here, deliberately.** Generalising the corridor reservation
(`reserve.py`) from a differential pair to a single net is real work with its
own verification burden, and it would be built on a router that cannot route
this board.

## Measured 2026-08-21 — #19b answered: our number is the wrong one

Two measurements disagreed on the same file: `normalize_schematic_truth` said
**0 pins disagree** (188 compared, 84 wrong before, 0 after) and KiCad's
schematic parity said **3 pads disagree**. Settled by exporting KiCad's own
netlist from the shipped `.kicad_sch` and reading it against the design.

The shipped schematic, weather-badge-24:

```
BOOTSEL_SW -> SW2.1                design: SW2.1 AND SW2.2 are BOOTSEL_SW
GND        -> SW2.2   wrong                SW2.3 AND SW2.4 are GND
GND        -> SW3.2   wrong
GND        -> Y1.1    wrong        design: Y1.1 is U3.XIN
TR_R11_Y1  -> Y1.4    wrong        design: Y1.4 is GND
SW2.3, SW2.4, SW3.3, SW3.4 — absent from the netlist entirely
```

**Four pins on the wrong net and four pins missing**, not three — and one of
them is `Y1.1`, the crystal's XIN, drawn to ground. This is the file a human
opens out of `kicad-project.zip`.

**Why our pass reports zero.** The design netlist unions
`internally_connected_source_port_ids` — SW2 `{1,2}` becomes one node, which is
correct. The comparison then satisfies that node from **any** member: pin 1 is
on BOOTSEL_SW, so the group matches, and pin 2 sitting on GND never registers.
Same for Y1 `{4,2}`: pin 2 is on GND, so pin 4 on `TR_R11_Y1` is invisible. A
group check that passes on one member cannot see the member wired elsewhere —
precisely the defect shape this module was written for, the 50
terminal-keyboard keys that read as shorted.

**So neither check is lying, and the useful one is KiCad's.** Ours answers "is
each electrical node represented somewhere in the drawing", which is a weaker
question than the one its summary implies. `0 after` is true of the question it
asks and false of the question it appears to answer.

**Not fixed here.** The repair is in the comparison, not the labels: every pin
in a group has to be on the group's net, not just one of them. That changes
what the pass stamps and has to be re-measured across the fleet, so it is filed
as #19c rather than rushed — the diagnosis is the deliverable.

## Reviewed 2026-08-21 — a hardware engineer read the board, and found three

First outside review of weather-badge-23, from the PCB image alone, without the
schematic. All three land, and all three were already in our own data.

### 1. The tactile switch is shorted by construction — every board with a button

> *"Nút nhấn sai footprint, đang bị nối 2 tiếp điểm với nhau nên luôn bị nhấn."*
> (The button footprint is wrong — the two terminals are tied together, so it
> reads as permanently pressed.)

Confirmed against `C318884`'s datasheet and our own pad coordinates:

```
pin1 top-left  ──6.00mm──  pin4 top-right      the real terminal
   |3.70mm                    |3.70mm
pin2 bot-left  ──6.00mm──  pin3 bot-right      the other real terminal

blocks/sw-tact declares  [["pin1","pin2"], ["pin3","pin4"]]   left column / right column
```

The datasheet ties the pads across the **5.90mm** span; the block ties them
across the **3.70mm** span. Perpendicular. So `pin1`+`pin2` carry the signal
while belonging to *different* terminals, `pin3`+`pin4` carry ground the same
way, and the signal is tied to ground through the switch's own body. The button
can never do anything.

**This is a board-killing defect, not a quality one**, and it is in a golden
block, so it is on every board that places a button — SW1, SW2 and SW3 on the
weather-badge line alone.

**And it is the other half of #19b.** Yesterday the schematic was measured
against this pairing and the *schematic* was called wrong. The pairing was the
wrong one to measure against. `internallyConnectedPins` was written from
somebody's reading of the part, never from the datasheet, and every check
downstream inherited it — including ours.

### 2. The power widening does nothing, and we printed the proof

> *"Dây nguồn 5V và 3.3V có đi dây lớn nhưng đi được 1 khúc thì chuyển lại dây
> nhỏ thì cũng như không."*

Our own sidecar, weather-badge-23:

```
V3_3: 156 of 340 segments widened — narrowest point 0.2mm -> 0.2mm
V5:    21 of 93 segments widened — narrowest point 0.25mm -> 0.25mm
```

156 segments widened and the narrowest point did not move. Current is set by
the narrowest point, so the pass costs copper and buys nothing. We measured it
exactly and never read it as "this does not work" — see #6.

### 3. The routing is too tight for the fab to build

> *"Đi dây vẫn rối bị quá sát với chân linh kiện, JLC sẽ ko làm đc vì dễ chạm."*

We report `clearance` and `copper_sliver` and mostly file them below blocking.
The reviewer's reading is stronger than ours: not "marginal" but "they will
refuse it".

## Open

- **#14 · Gate on the pour.** **DONE** — `verifylib/pour.py` measures and
  reports it. Whether it should *block* is now a one-line decision on the fab
  profile, deliberately left to an EE.
- **#15 · Pour the top layer too.** **TOOLCHAIN BLOCKER CLOSED.** It was never
  the mesh: one F.Cu zone whose *outline* visits a vertex four times, written
  that way by the converter. `kicad_normalize` separates such a zone into one
  zone per region and wb-16's top-pour board goes exit 139 -> exit 0 with the
  plotted copper identical on both layers. Re-run as **weather-badge-17**
  against its own bottom-only control: `netclass_pair_reference` clears, and
  the board is `fab.ready = False` on **53 error-severity DRC instances the
  pour brings** — 41 of them clearance, 0.0465mm against a 0.15mm floor.
  **Now an engineering question, not a toolchain one**: the pour needs
  clearance to the tracks already routed under it. See the wb-17 table above.
- **#22 · Make the converter emit a pour as an outline, not a mesh.** **DONE,
  both halves.** 12/12 boards in `products/` outline with identical plotted
  copper and no other rule moving; and the self-touching zone that actually
  blocked #15 is separated, measured on wb-16 and wb-17.
- **#16 · Move U2's input cap.** Cause named to the line, fix measured twice,
  **blocked**: the cap does not fit inside the block's box and the board has no
  routing margin to absorb it moving out. Needs a decision — grow the box and
  re-lay the boards, or a smaller package (BOM change, `parts-book`). See the
  measurement above.
- **#21 · The boards pass with no headroom.** **MEASURED** — 10 of 18 ready
  boards sit on the last rung having needed the repair pass. Still open as a
  *decision*: the ladder needs a rung above 10x, or the boards need to route
  clean at a lower one. Until then #16, #15 and #20 are all gambling.
- **#17 · Derive the USB skew budget from the interface speed.** **DONE.**
  Read off the controller's LCSC number; unknown controllers keep the strict
  High Speed budget. Measured across all 24 boards on disk: 24/24 recognised as
  Full Speed, `netclass_pair_skew` warnings **28 -> 0**, replaced by 28 `info`
  lines that still carry the millimetres.
- **#18 · Stop `isolated_copper` from firing on a triangulated pour.** **DONE**
  — re-measured rather than suppressed; a genuinely fragmented pour still
  warns.
- **#19 · Fix the `.kicad_sch` export.** **RE-MEASURED, AND SPLIT IN TWO.**
  On weather-badge-21 it is 68 `net_conflict`, not 2246, and **65 of the 68 are
  a spelling difference between two tools** — one side names a net and the
  other invents `.Y1 > .pin1 to .U3 > .XIN` or `Net-(LED1-Pad1)` for it. Those
  now report at `info`. **The other 3 are the drawing and the board
  contradicting each other** and now report separately, with the pad:

  ```
  SW2 pad 2   board BOOTSEL_SW   schematic GND
  SW3 pad 2   board RUN_SW       schematic GND
  Y1  pad 4   board GND          schematic TR_R11_Y1
  ```

  All three are a 4-pad part folded onto a 2-pin symbol — the same shape as the
  50 keys that started `kicad_schematic`. **The board is the one that is
  right**: its netlist was compared pad for pad against `circuit.json` with 0
  splits and 0 merges. So the drawing is wrong on 3 pins, and
  `normalize_schematic_truth` reports *"0 pins disagree"* after its own pass.
  **Two of our own measurements contradict each other, and that is the next
  thing to look at** — not the naming.
- **#19b · Reconcile the schematic normaliser with KiCad's parity.**
  **ANSWERED — ours is the wrong number.** 4 pins on the wrong net
  (`SW2.2`, `SW3.2`, `Y1.1`, `Y1.4`) and 4 more missing entirely, one of them
  the crystal's XIN drawn to ground, while our pass reports `0 after`. Cause:
  the comparison satisfies an `internallyConnectedPins` group from **any**
  member, so a pin wired elsewhere inside a satisfied group is invisible. See
  the measurement above.
- **#26 · The tactile switch's internal pairing is wrong.** **FIXED IN BOTH
  BLOCKS.** It was in `rp2040-core` too — SW2 and SW3, the BOOTSEL and RESET
  buttons, which are the two the reviewer was looking at; `sw-tact` only places
  SW1. Both now pair by row, `{pin1,pin4}` and `{pin2,pin3}`, and wire both
  pads of each terminal. Rebuilt: `fab.ready` holds, the crystal net drops
  **29.41mm -> 23.81mm**, and `net_conflict_disagreement` goes **3 -> 1** —
  two of the three places the drawing appeared to contradict the board were
  this pairing, which is the other half of #19b. Cost: `hole_to_hole` 0 -> 9
  and `isolated_copper` 14 -> 16, both at warning. **Every board on disk still
  carries the old block and needs re-syncing before it means anything.**
  Original diagnosis: `blocks/sw-tact` declared
  `[["pin1","pin2"],["pin3","pin4"]]` — the left and right columns — while
  `C318884` ties its pads across the long span, top pair and bottom pair. Each
  declared group therefore holds one pad of *each* terminal, so signal and
  ground meet inside the switch and the button reads permanently pressed. Fix
  the block, then re-check every board that places one. **Read the datasheet
  before writing the pairing** — the current one was somebody's reading of the
  part and every check downstream trusted it.
- **#27 · Decide whether tight routing blocks.** **HALF DONE — the number now
  exists; the decision is still the EE's.** A hardware reviewer read our
  verdict as "the fab will refuse this" and we filed it below blocking. It
  turns out neither reading was wrong about the board: **the gate never saw
  the copper at all.** DRC runs at `min_clearance_mm - drc_tolerance_mm` =
  0.09mm, and on weather-badge-23 it reports **zero** clearance findings.
  Re-run the same file with the floor moved to `warn_clearance_mm` (0.127mm —
  declared in the profile since forever, read by the router's cost model and
  by no check) and the same kicad-cli finds **399**, two of them under JLC's
  own 0.10mm floor, the narrowest at 0.0908mm — clearing our gate by 800
  nanometres. Shipped in PR #13: a second 3.6s DRC pass at the margin floor,
  `clearance_under_fab_floor` (warning) + `clearance_no_margin` (info).
  **Neither blocks on purpose** — that call went out in the review packet and
  is still open. When the answer comes back it is one severity string here.
  Sixth entry in *the machine measures correctly and the reading is wrong*,
  and the first where the machine was never asked. Checked against the review
  loop before shipping: phase 1 gates on `severity === "error"` and phase 2 on
  a closed kind allowlist, so a `warning` here informs and does not recruit
  review rounds nobody can close by editing TSX.
- **#19c · Make the group check require every member.** The repair for #19b.
  Changes what the pass stamps, so it needs re-measuring across the fleet —
  filed rather than rushed.
- **#12 · Route hints for the crystal net.** **LEVER MEASURED, AND SHUT.**
  weather-badge-19 routes a 7.94mm hop as 26.21mm of copper through 2 vias.
  Our own router declines every poured board, and forced it does not route the
  crystal net at all (285 errors, 4 nets open). Three remaining levers are
  named in the measurement above.
- **#12b · Decide the 10mm crystal gate, in writing.** Unchanged.
- **#11 · U4 footprint.** **DONE — the fetch was made and the footprint is
  right.** C97521's own land pattern, 2026-08-20: 8 oval pads, 1.2700mm pitch,
  0.63 x 2.25mm each, rows 7.0602mm apart, 9.3102mm outer span. Our footprint's
  own name carries the same five numbers
  (`soic8_pillpads_w9.3102mm_pw0.63mm_pl2.25mm`). **Identical, and it still
  scores 0.6347** — under the 0.65 warning band, while a *correct* 0402 scores
  0.7249. The IoU tops out near 0.75 for a correct part, so the score was the
  metric and the pill pads cost it again for not being rectangles. Recorded in
  `VERIFIED_SUPPLIER_FOOTPRINTS`: still measured, still reported, no longer
  graded as a defect. **0.6347 meant "different"; it now means nothing at
  all.**
- **#9 · read `packages/router/`.** **NOW PRICED.** routerlib refuses every
  poured board outright (`router_bridge.py:180`) — which is every board we
  build — and with the gate opened it connects 29/33 nets on
  weather-badge-19. Two named gaps: re-pour after routing, and 4 nets.
- **#8 · Wire the safety gate to the ask.** **DONE.** `preflight_safety()`
  scanned the board's source graph and nothing read `product.json`'s
  description — the natural-language request the whole project was built from.
  A dangerous intent that compiles to innocent-looking source walked straight
  through. It is screened first now, with the same patterns, so the envelope
  cannot say two different things depending on which half caught you. Prose
  needs a negation rule that source does not (`no mains anywhere` passes,
  `no problem, switches mains` does not). All 25 boards on disk still pass.
  Known hole, pinned by a test rather than left to be rediscovered: a bare cell
  format (`an 18650 charger board`) is in neither pattern table.
- **#6 · `V3_3` width.** **CONFIRMED FROM OUTSIDE.** A hardware reviewer read
  the same defect off the board image: widened for a stretch, narrow again, so
  it buys nothing. Our sidecar had the number all along — 156 of 340 segments
  widened, narrowest point 0.2mm before and after.
- **#20 · Route the USB pair as a pair.** `diffpair_not_routed` on 5 boards,
  coupling down to 2%, 30% of the run with no reference. Separate from D and
  not dismissed by it.
- **#13 · `scripts/board-table.py`.** **DONE, MERGED** — PR
  [#5](https://github.com/autonomous-ai/autonomous-circuit/pull/5) merged
  2026-08-19; `feat/board-table-instrument` is on `upstream/main`.
- **#18 / #14 · the pour work.** **DONE, MERGED** — PR
  [#6](https://github.com/autonomous-ai/autonomous-circuit/pull/6)
  (`feat/see-the-pour-properly`) merged 2026-08-19, `4839f0c` on
  `upstream/main`.
- **#22 and #15's toolchain half · the outlined and untangled pour** are on PR
  [#7](https://github.com/autonomous-ai/autonomous-circuit/pull/7)
  (`feat/pour-as-outline`), **open, awaiting review**.

**Remotes, because this cost a moment:** `upstream` is
`github.com/autonomous-ai/autonomous-circuit` and is where every PR above
lives. `origin` is a stale GitLab fork (`triluongmh/autonomous-tscircuit`, one
commit); pushing there sends the work nowhere anyone reads.

## Lessons paid for

- **A tool's own report has a ceiling, and a ceiling looks like a measurement.**
  Every board read `isolated_copper = 199` before outlining — twelve boards,
  polygon counts from 313 to 2325, the same number every time. That is
  kicad-cli's per-rule report cap, not a count, and `clearance` varying 0–4 on
  the same reports is what proves it. A before/after delta built on a capped
  number is not a delta. Find the uncapped measure — here, the gerber region
  count — before writing anything down.
- **A repair can invent the finding it is judged by.** The first cut split a
  self-touching zone into three zones, which still touched at the point they
  were parted at — and KiCad calls touching zones intersecting, at error
  severity. Three of wb-17's eight `zones_intersect` were the pass arguing with
  itself. Always ask what a repair *adds* to the report, not only what it
  removes.
- **Measure the pass, not the function.** Two corpus sweeps called
  `_outline_pours` directly and were written up as "all 12 boards re-measured
  after every change". The fourth pass was never in that loop and had run on no
  board but wb-16 and wb-17. Pointing the same sweep at `normalize_for_fab`
  cost one edit and turned a sentence that was not true into one that is: the
  pass fires **0 times** on all 12.
- **A skip with no note is a check that was never asked for.** The first cut
  examined a zone's outline only when the zone had exactly one `(polygon)` and
  said nothing about the rest — which is the main pour zone on every board
  there is, 14 zones across the corpus, and the ones most likely to
  self-touch. Every give-up in this pass records a reason now.
- **Two things that both look broken are not therefore the same break.** #18
  and #15 were filed as one converter defect with two symptoms, and the
  sentence "fix the pour emission and both close" sat on this board as if it
  had been measured. It had not. #18 closed and #15 did not move a single exit
  code. A shared cause is a hypothesis until one fix is shown to close both.
- **Area arithmetic is not copper.** Splitting a self-touching ring into loops
  kept the signed-area sum exact and still would have filled a hole with
  copper, because one loop came out negative and a `filled_polygon` has no
  winding. An invariant that holds on the numbers can still be false about the
  board.
- **A bounded search reports as a clean decline.** `_fracture` stopped after 32
  candidate anchors and said "this pour is not something I understand", which
  read as a property of four boards. It was a property of the cap: the anchor
  that works was the 124th. Whenever code gives up after N tries, N is a claim
  about the data — measure it or the give-up message is a lie with a reason
  attached.
- **Count instances, not rows.** `_collapse_kicad_repeats` folds repeats into
  one row carrying `xN`. `isolated_copper` is 18 rows and 2394 instances; the
  row count says nothing about how much of a board is affected.
- **A tool's output format is not the thing it describes.** 2423
  `filled_polygon` entries looked like 2423 fragments of copper and were one
  triangulated plane. KiCad made the same mistake. Union before judging.
- **An experiment cannot live in a workspace an agent owns.** wb-16's top pour
  was reverted by the app's silent review loop between two of this session's
  own builds — correct behaviour for that loop, and it deleted the variable
  under test. Build experiments outside `~/.autonomous-circuit/projects`.
- **This board's oldest lesson caught its own author.** `fab.ready: False` was
  read, reported as confirmation, and was mid-flight — another agent was
  editing the source at that moment. Before calling a verdict final, check
  nothing else is writing to the workspace.
- **A gate that crashed reads as a gate that passed, only better.** wb-16 lost
  sixteen DRC findings to a segfault and came out looking cleaner than its
  control, still `fab.ready`. Whenever a check can fail, ask what its silence
  looks like — and make "did not run" louder than "ran and found nothing", not
  quieter.
- **One `try` around ten imports is nine hostages.** Adding a check to a
  shared import block means a runtime that predates the check loses every
  check. The failure even disguises itself as a missing toolchain. Import
  what is optional one at a time.
- **A floor silences a rule; a measurement keeps it.** `isolated_copper` could
  have gone into `KICAD_NOISE_FLOOR` in one line. That pins a rule
  unconditionally, and would have traded 2394 false alarms for one silent real
  one the first time a pour genuinely fragmented. Re-measure, then judge.
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

- **A declared property is a claim, and a claim needs a source.**
  `internallyConnectedPins` on the tactile switch was written from somebody's
  reading of the part, not from its datasheet, and it was wrong by ninety
  degrees. Every check downstream — including the schematic pass that reported
  zero — measured against it faithfully and inherited the error. A number
  nobody sourced is a number nobody checked.
- **A group that passes on one member cannot see the others.** The schematic
  pass unions internally-connected pins into one node — correct — then
  satisfied that node from whichever member matched first. Four pins wired to
  the wrong net sat inside satisfied groups and were reported as zero. When a
  check folds several things into one, ask what it does with the ones it
  folded.
