# `packages/verify` — the checks the pipeline did not have

Seven standalone checks that see things our four existing detection sources
cannot. Built 2026-08-11 against the gap analysis in
`docs/verification/gap-analysis.md`.

```bash
PYTHONPATH=packages/verify/src python3.12 -m verifylib <project-or-circuit.json> --text
PYTHONPATH=packages/verify/src python3.12 -m verifylib examples/terminal-keyboard   # one JSON line
cd packages/verify && python3.12 -m pytest                                          # 100+ tests, under 2s
```

## What each one attacks

| Check | The blind spot it closes | Reads |
|---|---|---|
| `assembly` | copper rules say nothing about a component *body*: conveyor clearance, part-to-part spacing, single-side SMT, pin pitch, rotation-prone packages | `circuit.json` |
| `netclass` | every net is routed at one width; nothing compares it to the current the net carries (IPC-2221B), or checks via capacity or USB pair skew | `circuit.json` |
| `dc` | no check knew Ohm's law on a *built* board — a nodal solve with the rails still called by their names | `circuit.json` |
| `corners` | every number in the pipeline is nominal; this re-solves at every tolerance corner | `circuit.json` |
| `review` | the electrical half of an EE design review — decoupling, bulk, crystal load caps, floating pins, ESD, test points, debug access | `circuit.json` |
| `thermal` | dissipation against package ratings at *peak* load — the LDO helper, but reading the built board, plus every chip resistor | `circuit.json` |
| `gerber` | **the packet we actually ship, which nothing had ever opened** | `gerbers.zip` |

## Three rules the package holds itself to

**Independence.** No import of `circuitpy` or of any skill runtime, and the
gerber reader shares no code with the exporter. A second opinion computed with
the first opinion's code is not a second opinion.

**Coverage is part of the answer.** Every check reports what it could *not*
see, next to what it found. Silence is never a pass — which is exactly why the
SPICE path stays unwired (see `dc.py`: `circuit-json-to-spice` renames every
node to `N1..N36`, so a rail cannot be identified, so the check would always
find nothing and imply coverage we do not have).

**Measure the noise floor before trusting a check.** Four false-positive
classes were found and removed *before* shipping, each now a regression test:

| What looked like a defect | What it actually was |
|---|---|
| nine courtyard overlaps on `harness-puck` | WS2812 footprints rotated 22.5°, whose bounding box is 40% bigger than the part |
| four missing drills on every board | `G85` routed slots read as round holes at one endpoint |
| a poured board looking empty | `G36` region contours skipped by the reader |
| a 120 mA indicator LED, then an 8.7 GA one | a piecewise-linear diode that never converged, then a Newton step trusted while the limiter was holding it down |

## Wiring it in

Nothing here is wired into the pipeline — that is deliberate, since
`packages/circuitpy` belongs to another track. Each check already returns the
pipeline's warning shape, so wiring is a call, not a translation:

```python
from verifylib import (
    assembly, corners, dc, gerber_truth, model, netclass, review, thermal
)

board = model.load(circuit_json_path)
warnings.extend(assembly.check(board, assembly=product.assembly).findings)
warnings.extend(netclass.check(board).findings)
warnings.extend(dc.check(board).findings)
warnings.extend(review.check(board).findings)
warnings.extend(thermal.check(board).findings)
warnings.extend(gerber_truth.check(board, str(gerbers_zip)).findings)   # after stage 5
warnings.extend(corners.check(board).findings)                          # off the critical path
```

Suggested placement in the seven-stage gauntlet (`docs/circuit-interfaces.md`
§1):

- **stage 4** (DFM + BOM) gains `assembly`, `netclass`, `dc`, `review` and
  `thermal` — all five read `circuit.json` and together add well under two
  seconds.
- **a new stage 5b**, after the fab export, gains `gerber` — it cannot run
  earlier because the artifact does not exist yet, and it is the only check
  that inspects what the fab receives.
- `corners` should run **beside** the build, not inside it. It is the only
  check whose cost is noticeable (a 500-corner sweep is seconds), it never
  produces an `error`, and its whole purpose is to catch what a nominal-only
  pass already called clean.

Two contract notes for whoever wires it:

1. Every `kind` here is new, and the driver switches only on `severity`, so
   nothing downstream needs to learn them (contract §1: "the driver never
   switches on `kind`").
2. The severities are chosen so `fab.ready` stays hard to earn but does not
   move: blocking errors are only ever things the fab or the line will refuse
   (a bottom-side part on a single-side order, a missing drill, a pad with no
   mask opening, an LED drawing 120 mA). Everything advisory carries the
   measurement that justifies it.

## Wall-clock, not compute

`terminal-keyboard`, the largest example, 200 random corners:

```
0 error, 6 warning, 10 info — 7.7s wall, 9.1s compute
  assembly 0.28s   netclass 0.36s   dc      0.32s   thermal 0.42s
  corners  7.11s   review   0.22s   gerber  0.86s
```

The six fast checks disappear behind the slow one because the CLI fans them
out across processes, and the corner sweep fans out again inside itself
(18.7s → 7.2s at 200 trials). Compute is free against a two-week fab queue;
wall-clock is not. Anything slower than this belongs beside the build rather
than inside it.
