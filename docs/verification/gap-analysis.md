# The verification we do not have

**2026-08-11.** An honest grade of our pre-fabrication verification against what
professional EDA tools, the fab itself, and a real EE design review actually
check. Written to be acted on: every gap carries a rating for *how likely it is
to cost a fab cycle* and *what it costs to close*.

## The exchange rate this document is priced against

> **Dee, 2026-08-11:** *"each time we send to JLCPCB it takes weeks to get back.
> so spending more time and effort to make it right is worth it."* … *"even if
> you send 1-day, 2-day or even 3-day to get the build right and verify
> everything, that's still better than waiting 2 weeks from JLCPCB."*

A missed defect costs **two weeks plus ~$85, serially**. A check costs compute.
The two are not comparable, so the ranking below uses two axes and only two:

- **Fab-cycle risk** — how often would this defect actually reach the fab, and
  would it be fatal (unusable board / rejected order) or cosmetic.
- **Wall-clock added** — *not* compute. Compute is free; a check that runs an
  hour beside thirty others is cheap. A check that adds ten serial minutes to
  every build is expensive and belongs off the critical path.

## TL;DR — the grade

We are strong on **copper geometry** (four independent sources agree on it) and
close to blind everywhere else. Specifically:

| Domain | Our coverage | Verdict |
|---|---|---|
| Copper DRC (clearance, width, drill, annular, edge) | 4 sources: tscircuit, `@tscircuit/checks`, KiCad DRC w/ fab rules, our DFM gate | **Good** |
| Netlist / connectivity | tscircuit + KiCad ERC (ERC leg is advisory — measured noise) | **Adequate** |
| Orderability of parts | BOM gate + parts lock | **Good** |
| **Assembly (DFA)** | *nothing* — we check copper to edge, never a component body | **Blind** |
| **Net classes / current capacity** | a helper exists, nothing measures the routed board | **Blind** |
| **DC operating point** | nothing; the SPICE path is a documented dead end | **Blind** |
| **Tolerance / corners** | nothing — every number is nominal | **Blind** |
| **The gerbers themselves** | *nothing reads the files we actually ship* | **Blind** |
| **Solder mask / paste / silk** | nothing (KiCad's silk rules are pinned to info) | **Blind** |
| Thermal | one LDO helper, called only when the source declares it | **Thin** |
| Signal integrity, impedance, PDN | nothing | **Blind — and mostly fine**, see below |

The single sharpest sentence: **our packet is what the fab actually builds, and
no check we own has ever opened it.**

---

## 1. What real verification covers

### 1.1 Altium's rule taxonomy — the reference list

Altium groups design rules into ten categories, and the names are a useful
checklist of what "verified" means to a professional tool
([design rule types](https://www.altium.com/documentation/altium-designer/pcb/design-rule-types)):

- **Electrical** — clearance, short-circuit, un-routed net, un-connected pin,
  **creepage distance** (surface path across board edge/cutouts), modified
  polygon ([electrical rules](https://www.altium.com/documentation/altium-designer/pcb/design-rule-types/electrical))
- **Routing** — widths, via styles, routing layers, fanout, differential pairs
- **SMT** — pad-to-via, silk-to-mask overlap
- **Mask** — solder mask and paste expansion
- **Plane** — power-plane connect style, polygon connect style
- **Testpoint** — style and usage
- **Manufacturing** — silk-to-silk, hole size, board-outline clearance,
  minimum annular ring, acute angle
- **High Speed** — parallel segment (crosstalk), length, **matched lengths**,
  daisy-chain stub length, vias under SMD, **maximum via count**, max via stub
  length (back-drilling), **return path**
  ([high speed rules](https://www.altium.com/documentation/altium-designer/pcb/design-rule-types/high-speed))
- **Placement** — room definition, **component clearance** (incl. 3D model),
  permitted layers, **height**
  ([placement rules](https://www.altium.com/documentation/altium-designer/pcb/design-rule-types/placement))
- **Signal Integrity** — thresholds for the SI analyzer

Beyond rules, Altium ships two *solvers*: the **Signal Integrity Analyzer**
(2D field solver for characteristic impedance from real geometry, then
reflection/crosstalk/overshoot against IBIS-style buffer models
([SI analyzer](https://www.altium.com/documentation/altium-designer/analyzing-pcb/si-analysis/si-analyzer-altium)))
and the **PDN Analyzer** (IR drop and current density across every copper layer
from source to load
([PDN analyzer](https://www.altium.com/capabilities/design/power-analyzer))).

### 1.2 KiCad's DRC surface

KiCad 9/10's violation set is broader than the geometry we currently exercise
([`DRC_ITEM` reference](https://docs.kicad.org/doxygen/classDRC__ITEM.html)):
isolated copper, starved thermal, dangling via/track, hole-to-hole, zones
intersect, track angle, minimum segment length, padstack, microvia drill range,
courtyards overlap / missing / malformed, PTH-inside-courtyard, duplicate and
missing footprints, **soldermask bridge**, silk/mask clearance, text height and
thickness, and a family of budget checks — `length out of range`, `stub too
long`, `return-path break`, `skew out of range`, `via count out of range`,
`diff-pair gap out of range`, `diff-pair uncoupled length too long`.

Two things matter about that list. First, the budget checks are **geometric
comparisons against numbers you supply**, not simulation — which means they are
reimplementable by us without a field solver. Second, KiCad expresses them
through **custom rules** with a small constraint language
(`(rule "x" (condition "A.NetClass == 'Power'") (constraint track_width (min 0.4mm)))`,
constraint types including `track_width`, `clearance`, `diff_pair_gap`, `skew`,
`length`, `via_count`, `hole_clearance`, `edge_clearance`, `assertion`
([KiCad DRC rules](https://www.protoexpress.com/blog/how-to-set-up-design-rules-kicad/))),
so the mechanism for wiring them up already exists in a tool we already run.

Note the trap: KiCad net classes act as **router defaults, not enforced
minimums** — widening or narrowing a track by hand does not itself violate
anything unless a rule constraint also exists
([pcbnew docs](https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html)). Writing a
netclass into our `.kicad_pro` therefore buys nothing on its own.

### 1.3 What the fab and the assembler check

JLCPCB's own DFM tool runs a 30+ point checklist over traces, mask, drilling,
silkscreen and assembly, with the flagged geometry localised visually
([jlcdfm.com](https://jlcdfm.com/)). The numbers that matter to us, read from
their published capability tables:

**Fab (2-layer)** ([PCB capabilities](https://jlcpcb.com/capabilities/pcb-capabilities)):
track/space 0.10/0.10 mm at 1 oz; PTH annular ring ≥0.18 mm absolute, 0.25 mm
recommended; min via 0.15 mm hole / 0.25 mm pad; via-to-via 0.2 mm; pad hole-to-
hole 0.45 mm; copper to routed edge ≥0.2 mm; board ≥3×3 mm; **solder-mask
opening spacing ≥0.10 mm (0.13 mm for black/white)**, **mask sliver ≥0.2 mm**;
**silkscreen line ≥0.15 mm, text height ≥1.0 mm**.

**Assembly** ([PCBA capabilities](https://jlcpcb.com/capabilities/pcb-assembly-capabilities)):

| Rule | Economic PCBA | Why it bites |
|---|---|---|
| **Component body to board edge** | **≥2.5 mm** | conveyor rails and depanel routing hit the part |
| **SMD to SMD clearance** | **≥0.3 mm** | placement head cannot reach |
| Min IC pin pitch | 0.4 mm (0.35 Standard) | below it the line refuses |
| Smallest package | 01005 | |
| Board size | 10×10 mm min | below it, no assembly |
| SMT sides | single side only | a bottom-side part silently is not placed |
| Fiducials / rails | not required on Economic | required on Standard |

**Panelisation for assembly** (Standard tier): 5 mm process edges on all sides,
1 mm fiducials placed ≥3.85 mm from the panel edge, three or more per panel with
one corner offset ≥5 mm so the panel cannot be loaded 180° out, 2 mm tooling
holes ([process edges spec](https://jlcpcb.com/help/article/specifications-for-adding-process-edges-and-positioning-holes)).

**Gerbers**: RS-274X recommended (X2 accepted), copper + mask + silk + **explicit
board outline** + Excellon NC drill in the same archive
([gerber prep](https://jlcpcb.com/blog/prepare-perfect-gerber-files)).

### 1.4 Standards worth encoding

**IPC-2221B** current capacity: `A(mil²) = (I / (k·ΔT^0.44))^(1/0.725)`, with
**k = 0.048 external, 0.024 internal** — an internal trace needs roughly twice
the copper of an external one for the same rise
([trace width vs current](https://www.wevolver.com/article/trace-width-vs-current-in-pcb-design)).
Worked: 1 A at ΔT = 10 °C on 1 oz external ≈ 0.30 mm. Our router's default is
**0.15 mm**, good for about 0.5 A. IPC-2152 supersedes it and accounts for
adjacent planes, so IPC-2221 is the conservative direction to be wrong in
([IPC-2152](https://www.protoexpress.com/blog/how-to-optimize-your-pcb-trace-using-ipc-2152-standard/)).

**IPC-2221B spacing** is a table keyed by category — B1 internal, B2 external
uncoated ≤3050 m, B3 external uncoated >3050 m, B4 external polymer-coated, A5–A7
for coated leads ([spacing table](https://www.smpspowersupply.com/ipc2221pcbclearance.html)).
Our envelope caps at 24 V, where every category is looser than the fab's own
0.1 mm floor, so this one is already covered by accident — worth saying out loud
rather than claiming as coverage.

**IPC-7351B courtyards**: three density levels with courtyard excess of roughly
0.10 mm (Least/C), 0.25 mm (Nominal/B), 0.50 mm (Most/A) added around the land
pattern; the courtyard is the pick-and-place and rework keep-out, i.e. an
*assembly* rule, not an electrical one
([IPC-7351 overview](https://www.protoexpress.com/blog/features-of-ipc-7351-standards-to-design-pcb-component-footprint/)).
The exact per-package table is paywalled; we should treat the three numbers as
approximate and never claim IPC-7351B conformance from them.

**IPC-A-610** governs the acceptability of a *soldered* assembly — fillets,
wetting, voids, bridging, hole fill. **None of it is checkable from CAD**
([IPC-A-610 overview](https://www.wevolver.com/article/ipc-a-610-acceptability-of-electronic-assemblies)).
The only pre-fab action it implies is design-for-inspectability. We should stop
treating it as a target.

### 1.5 What a real design review covers

Published EE review checklists
([pcb-review.com](https://www.pcb-review.com/blog/pcb-design-review-checklist.html),
[Altium](https://resources.altium.com/p/pcb-design-and-review-checklist))
are overwhelmingly *electrical and architectural*, not geometric:

decoupling cap per IC supply pin placed close · bulk cap per rail · pull-up or
pull-down on every reset pin · every BOOT/strap pin tied to a defined level ·
crystal load caps computed from C_L minus stray, not guessed · every unused pin
explicitly tied per datasheet · ESD/TVS on every off-board signal · reverse
polarity protection on user-accessible power · test point on every rail plus a
ground reference · programming header present and reachable after assembly ·
power sequencing on multi-rail parts · continuous ground reference under every
fast signal, no signal crossing a plane split.

Our seven-lens design-review panel asks these questions of a model. Nothing
*measures* them. Most of them are computable from `circuit.json` connectivity.

### 1.6 Open-source tooling actually available

`kicad-cli` is much larger than the two commands we use: `pcb export`
{gerbers, drill, pos, step, svg, pdf, **ipc2581**, **odb**, gencad, ijb},
`pcb drc` (`--all-track-errors`, `--schematic-parity`, `--severity-all`,
`--format json`, `--exit-code-violations`), `sch erc`, **`sch export netlist
--format {kicad,orcadpcb2,cadstar,spice,spicemodel}`**, `pcb render`, and
`jobset run` ([KiCad 9 CLI](https://docs.kicad.org/9.0/en/cli/cli.html)).
Two of those are directly useful to us today: `--all-track-errors` (we currently
get one violation per track, not all of them) and `pcb render` (a second,
independent picture of the board for the image-review leg).

**KiBot** (GPL/AGPL — licence text disagrees with its PyPI classifier, check
before depending) orchestrates DRC/ERC/BOM/gerber/render and has a `diff`
output that renders two board revisions against each other
([KiBot](https://github.com/INTI-CMNB/KiBot)). **KiKit** (MIT) panelises and
exports a JLCPCB fab set, and explicitly *does not* DRC as part of `kikit fab`
([KiKit](https://yaqwsx.github.io/KiKit/latest/fabrication/jlcpcb/)).
**InteractiveHtmlBom** (MIT) is a human review aid, not a machine check.

For gerbers: **gerbv** (GPLv2, `--export=png --dpi=600` headless),
**tracespace** (MIT, gerber→SVG), **gerbonara** (Apache-2.0, the maintained
Python successor to the archived `pcb-tools`). The important finding is
negative: **none of them rule-check gerbers.** They render. There is no
off-the-shelf "DRC the gerbers" tool, which is exactly why the gap below is
still open industry-wide and why closing it is ours to do.

`kicad-happy` (MIT, ~929★) is not a rule engine — it is a prompt pack for LLM
agents ([repo](https://github.com/aklofas/kicad-happy)). Interesting as a source
of review questions; not a gate.

---

## 2. What we cover today

Four independent detection sources over the same design:

1. **tscircuit compiler findings** — `*_error` / `*_warning` elements in
   `circuit.json` (stage 1).
2. **`@tscircuit/checks`** `runAllChecks` — ~23 checks, an independent codepath
   over the same JSON (stage 2).
3. **KiCad ERC/DRC** on converted files, with the fab's real rules supplied via
   a generated `.kicad_pro` — without which KiCad grades against its own stock
   defaults, 207 findings vs 50 (stage 3).
4. **Our DFM gate** — trace width, via/PTH drill and annular, copper-to-edge,
   copper-to-hole (two rules: 0.20 mm NPTH, 0.28 mm PTH), board size, thickness,
   envelope, footprint IoU bands, BOM orderability and parts-lock drift
   (stage 4).

Plus: a seven-lens design-review panel, golden blocks with full-gauntlet tests,
a hard safety envelope, and three Ohm's-law spot checks in
`skills/circuitcode/circuitlib/helpers.py` (`led_current`, `pullup_warnings`,
`regulator_thermal`) which fire **only when the board source declares the
values to them** — they do not read the built board.

Honest note on the ERC leg: every ERC type is pinned to `info` because a
*correct* board produced 152 findings, all artifacts of the schematic converter.
That is the right call and it is documented with its measurement. But it means
we have **three** geometry sources and roughly **zero** independent connectivity
opinions.

---

## 3. The gaps, ranked

Rating key — **Risk**: how likely this costs a fab cycle. **Wall-clock**: added
time on the critical path (compute cost is deliberately ignored).

### G1 · Assembly / DFA rules — nothing checks a component body
**Risk: high · Wall-clock: seconds · Status: BUILT (`packages/verify`, `assembly.py`)**

We check *copper* to board edge at 0.2 mm. JLCPCB's assembly line needs the
**component body 2.5 mm from the edge** and **0.3 mm between SMD parts**. A
board can be perfectly DRC-clean, perfectly orderable, and still come back
mis-assembled or be rejected at review. Measured on our own examples before
building anything: `terminal-keyboard` has a courtyard **1.80 mm** from the
board edge, `hydrate-coaster` 3.55 mm, `harness-puck` 4.32 mm. One of three
already violates a published assembly rule that no gate we own can see.

Also in this class and equally invisible today: parts on the bottom side when
Economic PCBA is single-side SMT (they simply do not get placed), IC pin pitch
below 0.4 mm, and a board under 10×10 mm submitted for assembly.

### G2 · The gerbers themselves are never inspected
**Risk: high · Wall-clock: seconds (parallel) · Status: BUILT (`gerber.py`, `gerber_truth.py`)**

The zip is the only artifact the fab consumes, and every check we own runs
*upstream* of it. An export bug — a missing layer, a dropped drill, an outline
in the wrong file, a units/format slip — is invisible to all four sources and
fatal at the fab. No open-source tool does this (see §1.6), so it has to be
written: parse RS-274X and Excellon independently, extract geometry, and
cross-check the result against `circuit.json`.

The independence is the whole value. Re-deriving the answer through the same
library that produced it proves nothing.

### G3 · Net classes — every net is routed at the same width
**Risk: high · Wall-clock: seconds · Status: BUILT (`netclass.py`)**

The router uses 0.15 mm everywhere. By IPC-2221 that carries ~0.5 A at ΔT =
10 °C. A USB-powered board with an eight-LED WS2812 chain draws ~0.5 A on its
own; a rail at 1 A wants 0.30 mm. Nothing in the pipeline compares a net's
*measured routed width* to the *current it must carry*. This is the failure that
does not show up as a defect at all — the board works on the bench and runs hot
forever.

`circuitlib.helpers.trace_width_for()` already computes the right number. It has
never been pointed at a built board.

### G4 · No DC operating point — nothing knows a rail's voltage
**Risk: medium-high · Wall-clock: seconds · Status: BUILT (`dc.py`), on a new path**

The recorded dead end is still real, verified today against
`circuit-json-to-spice` **0.0.45**: it emits 42 lines for a 2321-element board,
models nothing without a SPICE model, has **no voltage source at all** (power
arrives through a connector), and **anonymises every node to `N1..N36`** so no
rail can be identified. `tsci simulate analog` runs the same conversion and
fails with `singular matrix: check node n3`. Wiring either in would add a check
that always finds nothing — worse than no check, because it implies coverage.
**Rejected, and it should stay rejected until the converter names its nodes.**

But the blocker is the *converter*, not the idea. `circuit.json` carries the
whole netlist with real names: `source_net` gives `is_power` / `is_ground` and
names like `V5` and `V3_3`, `subcircuit_connectivity_map_key` gives exact
connectivity groups, and every passive carries its value. Building the nodal
system ourselves keeps the names. That is the path taken.

### G5 · Every number is nominal — no tolerance or corner analysis
**Risk: medium · Wall-clock: seconds (thousands of solves are milliseconds) · Status: BUILT (`corners.py`)**

Every resistor is ±1%, every ceramic ±10% before DC-bias derating, every rail
±5%, and ambient is not 25 °C. A divider that lands at 3.28 V nominal may land
outside its threshold at the corner. Nothing asks. Once a DC solver exists,
Monte-Carlo over tolerances is nearly free and answers the question directly.

### G6 · Solder mask, paste and silkscreen are unchecked
**Risk: medium · Wall-clock: seconds · Status: PARTIAL (gerber-side mask/silk checks in `gerber_truth.py`)**

KiCad's `soldermask_bridge`, `silk_over_copper` and `text_height` findings are
all pinned to `info` in our noise floor — correctly, because the converted
footprints produce noise. But that leaves the real versions unchecked: a mask
sliver under 0.2 mm burns off and bridges two pads; silkscreen printed over a
pad ends up as ink on a solderable surface; text under 1.0 mm is unreadable and
JLC may drop it.

### G7 · Thermal only covers a declared LDO
**Risk: medium · Wall-clock: seconds · Status: NEXT**

`regulator_thermal()` is good arithmetic and is only ever called with values the
board source hands it. Any part with a thermal pad — a motor driver, a buck, a
hot LED — has copper-area-versus-dissipation requirements nothing measures.

### G8 · Design-review checklist items are asked, never measured
**Risk: medium · Wall-clock: seconds · Status: NEXT**

Decoupling per IC power pin, bulk per rail, floating strap pins, unused pins,
ESD on off-board connectors, a test point per rail, a reachable programming
header — all computable from `circuit.json` connectivity, all currently left to
a model's judgement in the review panel. Turning the seven lenses' recurring
questions into deterministic checks is the highest-leverage remaining work after
the gaps above.

### G9 · Exhaustive composition, not sampled
**Risk: medium · Wall-clock: none (off critical path) · Status: owned by another agent**

`evals/composition.py` builds the pair matrix. With compute effectively free the
right target is every *reachable* combination, not every pair. Flagged here for
completeness; not mine to build.

### G10 · Signal integrity, impedance, PDN, back-drilling, creepage
**Risk: low **for what we build** · Wall-clock: high · Status: REJECTED, with reasons**

Altium's SI and PDN analyzers are real capability we do not have. They are also
mostly irrelevant to a 2-layer, ≤24 V, ≤480 Mbps, block-composed board:

- **Impedance control** needs a stackup we do not specify and a 4-layer board to
  hit sanely. USB 2.0 full-speed on a 2-layer board is routine without it.
- **Creepage** is a mains and high-voltage concern; the safety envelope caps at
  24 V, where IPC-2221's tables are looser than the fab's own floor.
- **Back-drilling** applies to boards with more layers than ours.
- **PDN IR-drop** matters at plane currents far above ours; the trace-width
  check in G3 covers the honest version of the same question.

What *is* worth taking from that list is cheap and geometric: **USB
differential-pair intra-pair skew** (budget ≈150 ps ≈ 25 mm on FR-4, or ±3.8 mm
in length terms
([USB constraint management](https://www.allpcb.com/blog/pcb-design/pcb-constraint-management-for-usb-designs-ensuring-signal-integrity-and-compliance.html)))
and **matched-pair gap consistency**. Both are length arithmetic on
`pcb_trace` routes, not simulation. Included in G3's module.

### G11 · Panelisation and fiducials
**Risk: low for Economic PCBA · Wall-clock: n/a · Status: REJECTED for now**

Economic PCBA needs neither rails nor fiducials, and that is the tier we ship
on. It becomes real the day we move to Standard PCBA or panelise for volume —
at which point the 5 mm rails, 1 mm fiducials at ≥3.85 mm, and the ≥5 mm corner
offset all become blocking. Recorded so it is a decision, not an oversight.

### G12 · IPC-A-610 acceptability
**Risk: n/a · Status: REJECTED — not checkable pre-fab**

Governs a soldered board under inspection. Nothing in CAD can pass or fail it.
Listing it as a target would be dishonest.

---

## 4. What this implies for the pipeline

Three structural notes, independent of any single check:

1. **Verification should fan out, not queue up.** Every gap above is
   independent of every other. Ten checks running concurrently cost ten times
   the compute and no extra wall-clock. The critical path is the build, not the
   verification.
2. **Independence is the property that matters.** Four sources agreeing on
   copper geometry is worth less than one source looking at something none of
   the others can see. Rank future work by *what it can see that nothing else
   can*, not by how thorough it sounds.
3. **A check that cannot see something must say so.** `coverage` belongs in
   every check's output next to `findings`. Silence must never read as a pass —
   that is the exact reason the SPICE leg stays out.
