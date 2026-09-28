# kicadpy — native repair spike

Python 3.10+ host, matching KiCad 10 CLI/pcbnew. The macOS bundle is discovered
automatically. Set `KICADPY_CLI` / `KICADPY_PYTHON` for another installation.
This is an opt-in tool for an existing project, alongside v1. `kicadpy.publish
--manufacturing` exports a prototype packet but never sets the order gate itself.

From the repo root:

```sh
export PYTHONPATH=packages/kicadpy/src:packages/circuitpy/src
python3.12 -m kicadpy inspect /path/to/design/main.kicad_pro
python3.12 -m kicadpy view /path/to/design/main.kicad_pro
```

Every tool prints one `{ok, result}` or `{ok:false, error, kind}` JSON line.
`inspect`, `view`, `snapshot` take no stdin. All others read one JSON object:

| Operation | Request |
|---|---|
| apply | `{expected, scope, edits}` |
| route | `{expected, scope}` |
| diff / check | `{identifier}` (candidate ID) |
| commit | `{identifier, expected, allow_improvement?: boolean}` |
| undo | `{target, expected}` (snapshot revision and current revision) |

Scope is `{uuids: [...], nets: [...], regionMm: [x0,y0,x1,y1], refillZones: []}`.
Coordinates are native KiCad millimetres (y down), with no bottom-side mirroring.
A replace_track edit is `{op:"replace_track", uuid, points:[[x,y],...]}`;
first/last points must equal the original endpoints. A set_width edit is
`{op:"set_width", uuid, widthMm:0.15}`. Tracks are addressed by UUID, not a
list index. A locked track cannot be changed. An arc supports width changes only.

`route` uses the existing circuitpy Freerouting 2.4.1 launcher (five passes,
one thread, 120 s budget). Install the existing pinned toolchain or set
`CIRCUIT_TOOLCHAIN`; use its documented JAR/JRE overrides if necessary. The
router only proposes a candidate. `check` and `commit` are still required.

Snapshots and candidates live under `.kicadpy/` adjacent to the project root,
separated by project name and path hash. They are not app artifacts. Close other
writers (including KiCad GUI) during commit/undo; locks only coordinate these tools.
Keep the snapshots for undo. There is no automatic deletion in this spike.

## Tests

```sh
CIRCUIT_PARTS_ENGINE=off python3.12 -m pytest -q packages/kicadpy
python3.12 packages/kicadpy/scripts/desk_cube_smoke.py \
  /path/to/desk-cube-release/design/cube.kicad_pro --output /tmp/desk-cube-smoke.json
```

The smoke script copies the release before editing. The small committed fixture
is deliberately defective and is only for testing. Tests replay captured SES
without starting Freerouting or accessing the network. They do run local KiCad.
KiCad-dependent skips do not count as acceptance.

See `docs/architecture/kicad-native-spike.md` for evidence, limits and next slices.

## Experimental app mode

Start the viewer with `CIRCUIT_DEFAULT_ENGINE=kicad-native` to use native
planning/authoring for **new projects**. Engine selection is persisted in
project.json; old projects keep v1. Native implementation turns independently
run `python3.12 -m kicadpy.publish --manufacturing design/<stem>.kicad_pro` after the agent exits.
The publisher renders SVG/GLB and exports a revision-bound prototype packet.
A separate native review/repair loop writes engineering evidence; readiness
requires both that review and independent native/packet checks. Physical hardware
remains untested. Canvas editing is disabled; request edits through chat.
See PROMPT-WORKFLOW.md and MANUFACTURING.md.

A local repair on an already failing board may commit with `allow_improvement: true`.
The default still requires a passing native check. Improvement mode freshly checks a copy of
its baseline and accepts only a strict subset of the exact previous findings (including warnings),
with identical tools, coverage and ignored checks. A new or changed finding is rejected even if
the total count falls. Scope, source freshness, check hashes and undo remain enforced. This is
progress, not completion: `fabricationReady` remains false; publish the manufacturing packet
and finish the remaining repairs before claiming `fab.ready=true`.
