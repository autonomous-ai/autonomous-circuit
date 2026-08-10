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
