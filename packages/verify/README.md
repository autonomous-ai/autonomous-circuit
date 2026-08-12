# `packages/verify` — the checks the pipeline did not have

Ten standalone checks that see things our four existing detection sources
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
| `layout` | routed critical geometry: explicit source trace minima, reference planes, critical-route vias and protection placement | `circuit.json` |
| `intent` | the compiled board must implement the product's declared outline, assembly sides, edge connectors, local decoupling topology/distance (including populated-ref-scoped vendor overrides), ground planes and net classes | `circuit.json` + `product.json.layout` |
| `power_intent` | raw USB attach capacitance, an exact populated current-limit boundary **and its setting resistor/value/topology**, and firmware-bounded loads below the worst-case trip point | `circuit.json` + `product.json.powerBudget` |
| `thermal` | dissipation against package ratings at *peak* load — the LDO helper, but reading the built board, plus every chip resistor | `circuit.json` |
| `gerber` | **the packet we actually ship, which nothing had ever opened** | `gerbers.zip` |

### Measurable component zones

`product.json.layout.componentZones` binds populated reference-designator
globs to board-global geometry. Each rule has `match` (one string or a list),
`containment` (`center` or `courtyard`), and one `shape`:

```json
{
  "match": ["D1[0-7]", "C4[0-7]"],
  "containment": "courtyard",
  "shape": {
    "kind": "annulus",
    "center": [0, 0],
    "innerRadiusMm": 23.5,
    "outerRadiusMm": 32.5
  }
}
```

Circles use `radiusMm`; rectangles use `widthMm` and `heightMm`. Courtyard
containment measures the compiled, rotated courtyard polygons (falling back
to the footprint body), not an oversized axis-aligned guess. Annuli check the
filled polygon against the inner void as well as the outer radius. A rule that
matches no populated component and any matched component outside its zone are
both fab-blocking errors; a misspelled pattern cannot silently claim coverage.

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

## Wiring it in — done 2026-08-11

All ten are live in the pipeline as **stage 4c** (the circuit-json
checks, beside the DFM gate) and **stage 5b** (the gerber check, after the
packet is written). See `docs/circuit-interfaces-CHANGES.md` for the contract
entry. The adapter is `circuitpy/verify_bridge.py`; it finds `verifylib`
vendored beside `circuitpy` or in the repo, and emits one `verify_unavailable`
info if it cannot — an absent check must be visible, never silent.

**The severity policy lives on the fab profile, not in the checks**
(`circuitpy/fab.py`: `VERIFY_BLOCKING_KINDS`, `VERIFY_ESCALATED_KINDS`,
`apply_verify_policy`). Three states, and the default is the important one:

- **blocking** — the check's own `error` is honoured and stops `fab.ready`
- **escalated** — this fab raises a `warning` to `error`, with the reason in
  the table beside it
- **default** — capped at `warning`, whatever the check said

A kind nobody has classified is capped. Adding a check must never move the bar
on its own: a bar that improves for a reason nobody chose is indistinguishable
from a bar that broke.

`corners` is deliberately **not** in the build. It is the only check whose cost
is noticeable, it can never block, and its job is to catch what a nominal pass
already called clean. `CIRCUIT_VERIFY_CORNERS=1` opts in; `CIRCUIT_VERIFY_OFF=1`
turns the whole thing off for a bisect.

The call shape, if you need it elsewhere:

```python
from verifylib import (
    assembly, corners, dc, gerber_truth, intent, layout, model, netclass,
    power_intent, review, thermal
)

board = model.load(circuit_json_path)
warnings.extend(assembly.check(board, assembly=product.assembly).findings)
warnings.extend(netclass.check(board).findings)
warnings.extend(dc.check(board).findings)
warnings.extend(review.check(board).findings)
warnings.extend(layout.check(board).findings)
warnings.extend(intent.check(board, product.layout).findings)
warnings.extend(power_intent.check(board, product.power_budget).findings)
warnings.extend(thermal.check(board).findings)
warnings.extend(gerber_truth.check(board, str(gerbers_zip)).findings)   # after stage 5
warnings.extend(corners.check(board).findings)                          # off the critical path
```

Two contract notes:

1. Every `kind` here is new, and the driver switches only on `severity`, so
   nothing downstream had to learn them (contract §1: "the driver never
   switches on `kind`").
2. `fab.ready` is hard to earn and its *definition* did not move. Blocking is
   reserved for what the fab refuses or what arrives unusable. Three findings
   are escalated from warning to error, each with its measurement:
   silkscreen below the fab's line-width floor (100% of strokes on all three
   example boards, 1145 of them at 0.033 mm against 0.15 — the layer will not
   print, and a board with no reference designators cannot be reviewed or
   reworked), mask webs at 0.114 mm against a 0.2 mm minimum (they burn off and
   the pads bridge), and a debug interface reaching no connector (the board can
   never run its firmware).

   Deliberately **not** escalated, with the reasoning recorded in
   `fab.py` so nobody re-derives it: a 96 °C junction (inside the part's own
   125 °C rating with 29 °C spare), and 649 mA on copper rated 604 mA by an
   IPC-2221 figure that already ignores the adjacent plane — 7% over a
   conservative advisory number is an 11 °C rise instead of 10, not a defect.
   The net-class check still blocks below 70% of required capacity.

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
