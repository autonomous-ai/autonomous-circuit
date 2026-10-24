# KiCad parallel-agent pilot

The experimental implementation is `python -m kicadpy.parallel`. It is opt-in and does not
change tile defaults, the viewer, Stop hooks or active pet runs.

The complete self-contained operator guide is shipped with the KiCad skill:
[parallel-pilot.md](../harness/kicad/skills/kicad/references/parallel-pilot.md).
It covers initialization, Astra planning and board review, the board worker followed by a native
gate, then two isolated Sol workers for firmware and frame whose validated imports appear in the
viewer as each completes, frame-before-cover integration, bounded revision, failure receipts and
comparable controls.

Offline validation (no models, hardware, network or native CAD):

```bash
PYTHONPATH=packages/kicadpy/src:packages/circuitpy/src CIRCUIT_PARTS_ENGINE=off \
  python3 -m unittest discover -s packages/kicadpy/tests -p test_parallel.py -q
node --test viewer/src/server/circuit/caseExport.test.mjs
```

A live model/CAD benchmark is deliberately separate from implementation validation. Measure
all roles and repairs against matching inputs before changing the default orchestration.
