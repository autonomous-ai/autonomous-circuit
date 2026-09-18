---
name: kicad
description: The tool card for a KiCad harness (KiCad-native) workspace — every kicadpy command, its request shape, the publisher, the verdict, and how to find KiCad's CLI and Python on this machine. Read before the first board and whenever a command's shape is in doubt.
---

# KiCad harness tool card

Every command runs through the host Python Harness gave you: `"$KICAD_HARNESS_PYTHON"`. It already has
`kicadpy` and `circuitpy` on its path and `CIRCUIT_TOOLCHAIN` set. Each tool prints exactly one
JSON line, `{ok: true, result}` or `{ok: false, error, kind}`; parse the last line of stdout.

## Discover KiCad first

```bash
ls /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli || command -v kicad-cli
ls /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
"$KICAD_HARNESS_PYTHON" -c 'from kicadpy import toolchain; print(toolchain.executable("cli")); print(toolchain.executable("python"))'
```

`kicadpy` finds the macOS bundle by itself; on another layout set `KICADPY_CLI` and
`KICADPY_PYTHON`. Use the absolute paths you discovered. The bundled Python is the one that can
`import pcbnew` — author and inspect PCB objects with it (`tools/*.py`, run with that
interpreter). The host Python cannot import `pcbnew`, and KiCad's Python cannot import `kicadpy`.

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
| `commit` | `{identifier, expected}` | the candidate becomes the source (revision advances) |
| `undo` | `{target, expected}` | back to a snapshot revision |

`expected` is the revision you read from `inspect` — a stale one is refused, which is the point.
`scope` is `{uuids: [...], nets: [...], regionMm: [x0, y0, x1, y1], refillZones: []}`. Edits:
`{op: "replace_track", uuid, points: [[x, y], ...]}` (first and last point must equal the original
endpoints) and `{op: "set_width", uuid, widthMm}`. Tracks are addressed by UUID, never by index;
a locked track cannot change; an arc supports width changes only. Coordinates are native KiCad
millimetres, y down, no mirroring for the bottom side. `route` proposes only — `check` and
`commit` are still required. Snapshots and candidates live under `.kicadpy/`; keep them for undo.
Close any other writer (KiCad's GUI included) during `commit` and `undo`.

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

## The verdict

Written by the publisher at every sidecar write. Before your first publish, tell the pane where
you are:

```bash
"$KICAD_HARNESS_PYTHON" -m kicadpy.harness --active build
```

Phases are Build / Checks / Fab. Never edit `.harness/verdict.json` by hand.

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
servo-header, sw-tact, status-led, i2c-bus). Read them for the engineering; author the KiCad
yourself. Verify every pin map and footprint against the manufacturer before you trust it.
