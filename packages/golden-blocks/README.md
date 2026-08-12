# golden-blocks — the validated subcircuit library

The safety mechanism of Autonomous Circuit is **composition from golden blocks**:
values, polarities, pinouts, and land patterns live frozen inside a block, verified
once at authoring time — the AI composes blocks and glue (passives, LEDs, connectors,
headers), never invents an IC circuit from a datasheet. See
`docs/circuit-interfaces.md` §1 "Board-source rules".

## Layout

```
blocks/<id>/<id>.tsx    the block — a self-contained tscircuit component
blocks/<id>/BLOCK.md    pin contract, rail budget, pinned LCSC parts, provenance
testbench/<id>.tsx      a minimal board mounting the block (built by the tests)
tests/                  graded testbenches: topology + pinned-BOM + snapshot
tests/snapshots/        committed circuit-summary snapshots per testbench
```

Reusable board furniture lives in `blocks/glue.tsx`: guarded mounting holes,
GND pours, an explicit acyclic source-to-rail mixed-width `PowerTrunk`, and a
physical SWD `DebugPort`. `PowerTrunk` keeps its original same-face API and can
also make one explicit source-face-to-trunk-face transition: the board authors
the source point, bounded neck, off-pad via, .8/.5mm via geometry, and minimum
via-to-probe clearance as one validated tree. These helpers encode
manufacturing/electrical boundaries that a bare primitive or one net-wide
trace cannot express safely.

`blocks/` is copied into every project workspace at creation (frozen with the
project for byte-stable fab reproducibility). The `circuitcode` skill also
self-initializes `blocks/` on first run when a workspace is missing it.

## Rules

- **No `@tsci` imports, ever.** The registry survey (2026-08-10) found nothing
  import-grade; registry packages are mutable, unsigned, and network-fetched.
  Every block is authored here, self-contained, zero imports.
- **No network at build time.** Land patterns for real parts were imported once
  at authoring time (`tscircuit-cli import <C#> --jlcpcb`, 2026-08-10) and are
  committed inline. Builds run with `--disable-parts-engine`.
- **Every part is pinned** via `supplierPartNumbers` to one exact LCSC number,
  preferring JLCPCB Basic parts (extended parts carry a ~$3/line loading fee).
- **Every powered IC/pixel owns an explicit local bypass tree.** A power pin
  connects port-to-port to its nearby capacitor before one marked wide rail
  boundary; independent pin-to-net and capacitor-to-net leaves are not an
  acceptable substitute because they let the aggregate router choose the
  decoupling loop.
- **Bottom is a geometry transform, not only a layer prop.** Blocks with
  authored local placement mirror their X coordinates, complement rotations,
  and mirror fixed paths together. Focused top/bottom benches compare compiled
  pad endpoints and copper so a footprint cannot flip while its bypass stays
  in the top-side position.
- **Default refdes are allocated globally** across the v1 registry so blocks
  compose without collisions (see each BLOCK.md); override via props when
  instantiating a block twice.
- **Toolchain pinned** at `tscircuit@0.0.2279` (repo `toolchain/`). An upgrade is
  a deliberate PR that re-runs every testbench and reviews snapshot diffs.
- The Python-side registry mirror is `skills/circuitcode/circuitlib/blocks.py` —
  the two must not drift (circuitcode's tests cross-check ids and parts).

## Running the testbenches

```
/Users/d/miniconda/bin/python3.12 -m pytest packages/golden-blocks/tests -q
```

Each testbench builds with the real pinned toolchain and asserts: zero `*_error`
elements, the block's topology (net connectivity by traversal of `source_trace`
elements), the pinned BOM (every real part's LCSC number present), and a
committed summary snapshot (`CIRCUIT_UPDATE_SNAPSHOTS=1` regenerates).
A seeded-defect sentinel board MUST produce errors — if the sentinel passes,
the eval went blind.
