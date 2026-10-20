---
name: kicad
description: The tool card for a KiCad harness (KiCad-native) workspace — every kicadpy command, its request shape, the publisher, the verdict, and how to find KiCad's CLI and Python on this machine. Read before the first board and whenever a command's shape is in doubt.
---

# KiCad harness tool card

Every command runs through the host Python Harness gave you: `"$KICAD_HARNESS_PYTHON"`. It already has
`kicadpy` and `circuitpy` on its path and `CIRCUIT_TOOLCHAIN` set. Each tool prints exactly one
JSON line, `{ok: true, result}` or `{ok: false, error, kind}`; parse the last line of stdout.

## Discover KiCad first

When the tile ships its own KiCad (the store package does), Harness sets `$KICADPY_CLI`,
`$KICADPY_PYTHON` and `$KICAD_HARNESS_SHARE` (the symbol/footprint libraries) — use those and do not
look in `/Applications`. Otherwise:

```bash
echo "${KICADPY_CLI:-unset} ${KICADPY_PYTHON:-unset} ${KICAD_HARNESS_SHARE:-unset}"
ls /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli || command -v kicad-cli
ls /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
"$KICAD_HARNESS_PYTHON" -c 'from kicadpy import toolchain; print(toolchain.executable("cli")); print(toolchain.executable("python"))'
```

`kicadpy` finds the macOS bundle by itself; on another layout set `KICADPY_CLI` and
`KICADPY_PYTHON`. Use the absolute paths you discovered. The bundled Python is the one that can
`import pcbnew` — author and inspect PCB objects with it (`tools/*.py`, run with that
interpreter). The host Python cannot import `pcbnew`, and KiCad's Python cannot import `kicadpy`.

## Never let kicad-cli run in the background

Run `kicad-cli` in the **foreground**, wrapped: `timeout 180 kicad-cli pcb drc …`. Measured
2026-09-21 on a Codex run: five `kicad-cli pcb drc` processes launched from background terminals
hung in kernel state `UE` for hours, none wrote its output, and the agent waited on them for 30+
minutes each. The publisher and `kicadpy check` run it with a 120 s bound — prefer them. If a
`timeout` fires, the run is wrong, not slow: read the command, do not retry it in the background.

## Author

Sources live under `design/`: `main.kicad_pro`, `main.kicad_sch`, `main.kicad_pcb`, local
`*.kicad_sym` and `*.pretty`, `sym-lib-table`, `fp-lib-table`. Write valid native S-expressions
for the schematic; write the PCB with `pcbnew`. Rules and netclasses go into the `.kicad_pro`
before routing. Work on candidate copies **outside** `design/` (e.g. `build/cand/`) for anything
the transaction tools do not cover — a copy inside `design/` would change the revision.

First-pass routing, on a copy of the PCB:

```python
# with KiCad's Python
import pcbnew
board = pcbnew.LoadBoard('build/cand/main.kicad_pcb')
pcbnew.ExportSpecctraDSN(board, 'build/cand/main.dsn')
```
```bash
"$KICAD_HARNESS_PYTHON" -c 'import sys; from pathlib import Path; from circuitpy import toolchain; print(toolchain.run_freerouting(Path(sys.argv[1]), Path(sys.argv[2]), passes=5, threads=1, timeout=600))' build/cand/main.dsn build/cand/main.ses
```
then `pcbnew.ImportSpecctraSES(board, 'build/cand/main.ses')`, inspect, and copy the accepted
result into `design/`. The importer replaces tracks; protected wiring may be absent from the SES.
**Export the DSN with no zones on the board.** An unfilled zone still goes into the Specctra
export as a pour and the router treats it as an obstacle; two routes died that way at 34–40
unrouted before the agent removed the zones (harness-17). Add the GND zones after the copper
exists, then refill (`refillZones` in the transaction scope, or the publisher).

## Authoring a schematic by script — the four holes, and the tool that closes them

Every engine that wrote its own `.kicad_sch` emitter fell into the same holes (harness-11, -12,
-14, -15): a symbol placed off the 1.27 mm grid (wires never touch the pins; ERC `endpoint_off_grid`
by the hundred); a sheet-local `label` whose net is `/GND` while the PCB says `GND` (parity
`net_conflict` on every pad); a footprint written without its library nickname
(`C_0402_1005Metric` instead of `Capacitor_SMD:C_0402_1005Metric`); a library symbol that
`extends` a parent, embedded unresolved, so the IC has no pins and "ERC 0" is true of a drawing
that describes nothing. And KiCad flips Y when it places a symbol on the sheet: a pin at
(x, +y) in the library sits at (x, −y) on the sheet.

Do not write an emitter. Give `kicadpy.author` a net table:

```bash
"$KICAD_HARNESS_PYTHON" -m kicadpy.author write spec.json design/     # main.kicad_sch + <lib>.kicad_sym + sym-lib-table
"$KICAD_HARNESS_PYTHON" -m kicadpy.author check design/main.kicad_pro spec.json   # the drawing says what the table says
```

The spec names the libraries (KiCad bundled names or local `.kicad_sym` paths), the parts
(`ref`, `value`, `symbol`, `footprint` as `Lib:Name`, `at`) and the nets (`{net: [[ref, pin], …]}`);
`keep_uuids_from` reuses the instance UUIDs of an existing schematic so PCB footprint paths
survive a rewrite. It refuses a pin that is not on the symbol, a pin in two nets, and a bare
footprint, and it writes global labels, 2.54 mm stubs, no_connects on every unused pin, and
flattened symbols only.

## The transaction tools (`kicadpy`)

```bash
"$KICAD_HARNESS_PYTHON" -m kicadpy <operation> design/main.kicad_pro   # stdin: one JSON object
```

| Operation | Request on stdin | What it does |
|---|---|---|
| `inspect` | — | the project: revision, boards, nets, tracks by UUID, findings |
| `view` | — | a rendered look at the current sources |
| `snapshot` | — | records the current revision; the point `undo` returns to |
| `apply` | `{expected, scope, edits}` | a candidate with the edits applied |
| `route` | `{expected, scope}` | a candidate with Freerouting's proposal for the scope |
| `diff` | `{identifier}` | what a candidate changes |
| `check` | `{identifier}` | native ERC/DRC/parity on a candidate |
| `commit` | `{identifier, expected, allow_improvement?: boolean}` | the candidate becomes the source (revision advances) |
| `undo` | `{target, expected}` | back to a snapshot revision |

`expected` is the revision you read from `inspect` — a stale one is refused, which is the point.
`scope` is `{uuids: [...], nets: [...], regionMm: [x0, y0, x1, y1], refillZones: []}`. Edits:
`{op: "replace_track", uuid, points: [[x, y], ...]}` (first and last point must equal the original
endpoints) and `{op: "set_width", uuid, widthMm}`. Tracks are addressed by UUID, never by index;
a locked track cannot change; an arc supports width changes only. Coordinates are native KiCad
millimetres, y down, no mirroring for the bottom side. `route` proposes only — `check` and
`commit` are still required. Snapshots and candidates live under `.kicadpy/`; keep them for undo.
Close any other writer (KiCad's GUI included) during `commit` and `undo`.

## Verify — measure, don't claim (`kicadpy.verify`)

```bash
"$KICAD_HARNESS_PYTHON" -m kicadpy.verify <command> ...   # readable report, then ONE JSON line
```

| Command | What it measures | When |
|---|---|---|
| `gate [workspace]` | the publisher's own counts: sidecar errors/warnings by kind, `check.json` findings by stage/type, manufacturing findings | after every publish; the only numbers you may quote |
| `netlist design/main.kicad_pro` | every schematic pin's net against every PCB pad's net; symbols with no wire on any pin ("hollow"); nets drawn as sheet-local labels (`/GND`) | after the schematic and again before the first publish |
| `islands design/main.kicad_pro [--net GND] [--near X Y R]` | which pads/vias of a net sit on which filled-zone island (0 = the plane); with `--near`, via sites ranked by clearance to other copper on both layers. A net the board spells `/GND` is found under `GND` too, with a note | on any `unconnected_items` finding of a zoned net |
| `easyeda out.json C123 C456 …` | the JLCPCB/EasyEDA footprint pads of each LCSC part (network, once) | before `rotation`, for parts the knowledge table does not know |
| `rotation design/main.kicad_pro parts.json [out.json]` | the `rotationOffsetDeg` that maps the factory footprint onto yours, per footprint, with the match error; parts the knowledge table knows need no fetch, a disagreement with the table is flagged | before writing `assembly` in `manufacturing.json`; 0 needs it too |
| `stock parts.json out.json [C…]` | JLCPCB library type (basic/extended), stock and price, live, beside what earlier runs verified about the code (maker, MPN, the trap) | at sourcing, and before claiming a part is orderable |
| `power design/main.kicad_pro [--rail VBUS=10] [--limit-mm 3]` | capacitance per rail (every C between the rail and GND, summed) against the limit; for every IC power pin the distance to its nearest capacitor on that rail (pcbnew positions) | after placement, and before writing power.md; the publisher repeats it with the limits from product.json |
| `enables design/main.kicad_pro parts.json` | pins the knowledge table says must sit on a net (an active-low /OE on GND) against the copper | after the schematic |
| `modules design/main.kicad_pro J2 st7789-1.54-module` | the header's pin order against the module card's pin table (aliases: SCL/SCK, RES/RST…) | after the schematic, once per module header |
| `thermal design/main.kicad_pro U2 2` | the filled copper under a pad per layer and the net's vias on that island (pcbnew) | before writing thermal.md; 0 mm² = no pour |
| `stale [workspace]` | source files newer than the newest sidecar | before the final message; must be empty |

All read-only, host Python (`islands` runs pcbnew in the worker). Born from the harness-12 handoff
(2026-09-23/24): "ERC 0" counted errors while the gate counted 630 findings, four ICs had no wires,
the firmware pin table disagreed with the copper, every rotation was guessed at 0.

## What earlier runs already measured (`kicadpy.knowledge`)

```bash
"$KICAD_HARNESS_PYTHON" -m kicadpy.knowledge show C97521 C6186      # factory rotation offset + verified identity
"$KICAD_HARNESS_PYTHON" -m kicadpy.knowledge learn .                 # at the end of a run: add what you measured
```

Read it before searching: fifty-odd parts have a measured factory rotation offset (keyed by LCSC
code — two SOT-23-6 parts differ) and as many a supplier-checked identity with the traps found
(C2687116 is a UMW clone of the USBLC6-2SC6; C1548's maker is FH, not Yageo). `verify rotation`
and `verify stock` read the table first and still measure live for what it lacks; `learn` at the
end of a run is how a measurement stops costing the next run. Every row names the run it came
from; a disagreement is recorded, never overwritten. Stock and price are never in the table.

## Publish

```bash
"$KICAD_HARNESS_PYTHON" -m kicadpy.publish --manufacturing design/main.kicad_pro
```

Runs the native checks on a checked copy, renders `_pcb.svg`, `_pcb_bottom.svg`, `_schematic.svg`
and `board.glb`, exports the prototype packet (gerbers + drill archive, factory BOM/CPL, manual
BOM, position file, project archive, `ORDER.md`, `manufacturing-report.json`), and writes:

- `boards/main_review/<revision>-<hash>/` — the bundle (previews, `reports/`, `manufacturing/`)
- `boards/main.board.json` — the sidecar: `fab.ready`, `native.publication`,
  `native.manufacturing.prototypeReady`, `validation.warnings[]` (`severity` error | warning |
  info, open `kind`)
- `.harness/verdict.json` — the pane header, derived from the sidecar

`fab.ready` is `prototypeReady`: zero error findings across native ERC/DRC/parity, the factory
floor DRC, the independent gerber read, part identities, assembly decisions and the seven
engineering areas. The publisher never sets it any other way, and neither do you.

## The case contract (`kicadpy.enclosure`)

```sh
"$KICAD_HARNESS_PYTHON" -m kicadpy.enclosure .      # regenerate boards/<stem>.enclosure.json and print it
```

Written at every publish too. One frame (mm, origin at the outline centre, +x right, +y up from
the top, z = 0 on the top copper). `interfaces[]`: kind, refs, module, side, centre, size, angle,
`accessFace`, `zMm`, the opening `rule`, `status` (proposed / confirmed / overridden / ignored /
declared). `modules[]`: plug-in modules placed from their header pads. `case/interfaces.json` is
your intent (AGENTS.md, "The case contract"); it is read on every regeneration.

## Firmware wiring guide — part of the firmware deliverable

Write `firmware/WIRING.md` with the firmware, without waiting to be asked. Link it from
`firmware/README.md`; the viewer displays it prominently. Use the current assembly variant,
actual firmware GPIO definitions and final schematic/netlist. Show physical-label-to-label
connections, viewing direction, voltage/polarity, grounds and separate motor supplies, needed
passives, safe unpowered assembly checks and the user's first-power-on observations. Keep it
consistent after pin/assembly changes. Unknown modules remain GIẢ ĐỊNH with no invented pinout.
Do not substitute a chat-only answer or a case-folder link for this file. See AGENTS.md, "Firmware".

## Authorized flashing and a recoverable previous version

If the user explicitly authorizes automatic flashing after builds, record project-scoped
permission in `firmware/FLASH-POLICY.md` and follow
[Firmware recovery](references/firmware-recovery.md). After a fresh build and relevant tests,
pin the previous source/config/binaries and verified recovery artifact before writing the
identified device; verify the new image/boot and restore serial services. Keep a successful
image running, roll back a failed one and never move an old stable tag. Standing permission
does not need repeated confirmation; a later no-flash request overrides it. Without permission,
provide the binary/Flash recipe. Flashing does not authorize motors, printing or orders.

## The case (`kicadpy.case`)

Before modeling or repairing, read [Case assembly and print handoff](references/case-assembly.md).
For moving designs, define the structural mechanism/device mounts first, check that accessible
subassembly, then attach shells and recheck motion. Prototype a changed joint with its actual
mating parts; reference photos supply layout ideas, not measured dimensions or actuator feedback.
Default to simple, rugged, serviceable mechanics: few parts, direct joints, stock hardware and
ordinary tools. Add mechanical complexity only for a named requirement a simpler design cannot meet.
Require a secure **device → mount/frame → shell** attachment chain; preserve working device
mounts and replace reported fragile tabs/prongs with supported geometry. After every repair,
recheck the full intended rotation range including intermediate/combined poses, closed shells,
hardware and wires; give the person an unpowered hand-sweep check. CAD pass does not prove grip
or physical motion. Detailed acceptance rules are in the required reference above.
Plan how each part gets in and how its fasteners are reached; keep a current numbered assembly
guide, separate labeled coupon instructions and a reuse/reprint list. Validate the assembly
sequence as well as the final pose, then regenerate the overview from that same revision.

```sh
"$KICAD_CASE_PYTHON" case/build.py               # your model -> case/out/ (start: kicadpy.case_template.build_box('.'))
"$KICAD_CASE_PYTHON" -m kicadpy.case check .     # the tile's checker -> case/reports/case-check.json
"$KICAD_HARNESS_PYTHON" -m kicadpy.case status . # none / stale / failed / passed, no CadQuery needed
```

Errors: `case_collides`, `case_interface_blocked`, `case_touch_wall`, `case_part_too_big`,
`case_part_missing`, `case_built_from_old_board`. Fix the model, never the board (AGENTS.md, "Case").

### Interactive overview — immediately after the 3D build

Every case build also generates `case/overview.html`, automatically in the same turn. Use the
actual assembled geometry, component labels, shell visibility and wire paths. For a moving
case, include controls for its checked axes and a stop/centre control; for a static case, keep
inspection controls without invented motion. Make it a self-contained phone-friendly HTML
file (embedded geometry/scripts/styles; no network or CDN), set manifest `overview` relative
to `case/out/` (`"../overview.html"`), and verify it in **Case → Overview**. An existing
`case/motion/motion-view.html` can be used with `overview: "../motion/motion-view.html"`.
Call the overview generator from `case/build.py` so final model repairs regenerate it too.
The animation never replaces CAD checks or physical fit tests. See AGENTS.md, "Case".

### Structural repairs — establish the joint before a full build

Preserve existing working designs/parts by default unless the person changes the requirement;
a repair request does not authorize a wholesale redesign. Prefer compatible local add-ons.
The actual printed revision and its mating geometry are constraints.
Fix the failed feature with the smallest compatible replacement/cap/adapter first; save body
reprints even if the repair adds a small part. Validate against the printed baseline and lead
with repair-only plates, keeping the full-kit export optional. See the reference's repair mode.

Follow `references/case-assembly.md`: record the requested joint with a section/sketch, actual
load path, fixed/free interfaces and accessible nut/screw sequence before detailed CAD/slicing.
Keep routine choices autonomous; do not silently substitute unowned stock for a requested
printed member. Reassess inherited loose parts after an architecture change, and check unwanted
play/retention separately from collision-free motion with a representative supported test kit.

### Ready-arranged print plates — same turn, after every repair

Export a native slicer 3MF with the current production parts already oriented and distributed
across the owner's real bed-sized plates, with brim/edge/part clearance. Preserve the known
printer/material profile; state assumptions for unknown settings. Keep coupons in a separate
TEST ONLY project and explain reuse versus reprint. Run the slicer's layout/mesh checks after
the final repair, publish manifest `printProject` and its fresh verifier report as specified in
`references/case-assembly.md`, and check the actual Print all export preserves those plates.
The generic mesh export does not preserve a slicer's plates. Do not leave the person to arrange
a pile manually, and do not start a physical print.

## The verdict

Written by the publisher at every sidecar write. Before your first publish, tell the pane where
you are:

```bash
"$KICAD_HARNESS_PYTHON" -m kicadpy.harness --active build
```

Phases are Build / Checks / Fab / Firmware / Case. Never edit `.harness/verdict.json` by hand.

## The review contract

`MANUFACTURING.md` at `$KICAD_HARNESS_ROOT/packages/kicadpy/MANUFACTURING.md` is the contract for
`manufacturing.json` (seven areas, assembly decisions, `designInputs` hashes, accepted ignored
checks) and for `.circuit/native-review-attestation.json`. Compute the input hashes after the last
source edit:

```bash
"$KICAD_HARNESS_PYTHON" -c 'import json; from kicadpy.project import Project; from kicadpy.manufacture import design_inputs; print(json.dumps(design_inputs(Project("design/main.kicad_pro").root), indent=2))'
```

## Engineering knowledge

`$KICAD_HARNESS_BLOCKS/<block>/BLOCK.md` — the golden blocks' pin maps, values, layout notes and measured
numbers (usb-c-power, usb-c-data, ldo-3v3, rp2040-core, sensor-bme280, ws2812-chain,
servo-header, sw-tact, status-led, i2c-bus). `$KICAD_HARNESS_BLOCKS/../modules/<id>/BLOCK.md` —
header-mounted modules (st7789-1.54-module, ttp223-module): pin order, current, the trap found.
Read them for the engineering; author the KiCad yourself. Verify every pin map and footprint
against the manufacturer before you trust it.

A local repair on an already failing board may commit with `allow_improvement: true`.
The default still requires a passing native check. Improvement mode freshly checks a copy of
its baseline and accepts only a strict subset of the exact previous findings (including warnings),
with identical tools, coverage and ignored checks. A new or changed finding is rejected even if
the total count falls. Scope, source freshness, check hashes and undo remain enforced. This is
progress, not completion: `fabricationReady` remains false; publish the manufacturing packet
and finish the remaining repairs before claiming `fab.ready=true`.
