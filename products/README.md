# The fleet

Boards built **with the app**, by the AI electrical engineers who are testing
it. `examples/` holds the three the test suite pins; this holds everything
after that, and it exists for one reason:

> Three boards prove the pipeline can build a board. Tens of boards prove the
> tool is ready for someone else's hands.

Every product here is a real device someone might order — not a demo, not a
skeleton — and every one carries the same verdict the first three do.

## The bar

A product is **done** when its sidecar says `"fab": {"ready": true}` with zero
blocking findings, and the fab packet under `boards/main_fab/` is complete
(gerbers, BOM, CPL, ORDER.md). Anything short of that stays in the table below
with its real state, because a fleet that only lists its successes measures
nothing.

`fab.ready: true` is a **floor**. A later build may never report `false` after
a `true`; the change that did it gets reverted and the finding recorded.

## How one gets built

```bash
SKILL=~/.claude/skills/circuitcode
mkdir -p products/<slug>
cp -R "$SKILL/templates/project_skeleton/." products/<slug>/
cp -R "$SKILL/blocks" products/<slug>/blocks
# edit products/<slug>/product.json first — name, power, envelopeMm, layers
# then write products/<slug>/boards/main.tsx from the golden blocks
/Users/d/miniconda/bin/python3.12 "$SKILL/scripts/circuit" \
    /Users/d/code/autonomous-circuit/products/<slug>/boards/main.tsx
```

Read the verdict off the last line of stdout (one JSON object) and then off
`boards/main.board.json` → `validation.warnings[]` by severity. **Look at both
review PNGs** before calling anything finished.

The golden blocks are the vocabulary: `usb-c-power`, `usb-c-data`, `ldo-3v3`,
`rp2040-core`, `status-led`, `sw-tact`, `ws2812-chain`, `sensor-bme280`,
`i2c-bus`, plus `glue.tsx` (mounting holes, pours). A product that needs
something outside that list is a **finding about the library**, not a reason to
invent a circuit inline.

## What the builders owe us besides the board

Every engineer who builds one writes down where the tool got in their way, in
`work/ee-feedback/<slug>.md`: what they reached for that was not there, what
they had to do by hand, what the app told them that turned out to be wrong.
That file is the input to the next round of IDE work — the boards are the
product, the friction is the point.

## The fleet

| Product | What it is | `fab.ready` | Blocking | Notes |
|---|---|---|---|---|
| _(the first entries land as the team finishes them)_ | | | | |
