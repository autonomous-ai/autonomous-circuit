# Golden-block composition — the doctrine

**Load:** before your first board. This is the rule the whole product rests on.

## Why blocks exist

The verification gauntlet is genuinely good at what it checks: connectivity,
overlaps, clearances, footprint presence, fab limits. It is completely blind to
whether the circuit is *correct*. From the measured gap list
(`docs/circuit-research-2026-08-10.md`):

- A board passes every single gate with a 10Ω resistor where 10kΩ belongs.
- A reversed LED passes. Missing decoupling passes. A wrong feedback divider on
  a regulator passes.
- A mirrored SOIC-8 passes — the footprint is present and legal, it is just
  backwards.
- A hand-typed pinout that swaps SDA and SCL passes *both* substrates, because
  the schematic and the layout inherit the same wrong source.

No amount of extra checking fixes this class. The only thing that fixes it is
not making the mistake: freeze values, polarities, pinouts, and land patterns
inside a block that a human verified once, then compose.

## What you may and may not do

**May:** instantiate blocks; place them; wire between them with named nets; add
glue — resistors, capacitors, LEDs, headers, connectors, mounting holes; set the
props a block exposes.

**May not:** place a bare IC that has no block; hand-write a pinout; copy a
circuit out of a datasheet; import from the `@tsci/…` registry; pull a footprint
over the network with `footprint="jlcpcb:C…"` or `"kicad:…"`.

## When there is no block

Say so. `board_plan()` returns the gap in `unavailable`, and the honest response
is: "we don't have a validated block for X yet — here is the nearest thing, or
this needs a block authored and bench-checked first." A board that ships with an
invented subcircuit is worse than a board that ships without the feature,
because the failure arrives after the user has paid for five of them.

## Reading a block before you use it

Every block has a `BLOCK.md` next to its source. Read these three sections:

- **Pin contract** — the nets it exposes and the nets it demands. `requires`
  that nothing `provides` is a build-stopping mistake you can catch for free.
- **Rail budget** — what it draws and what it can supply.
- **Design-rule notes** — the "don't do this" list. (`usb-c-data` is a superset
  of `usb-c-power`; placing both double-populates the connector. Two `i2c-bus`
  instances halve the pull-up resistance.)

## Composing cleanly

1. Power first: one power-entry block, then the rail blocks it feeds.
2. Brain next: the MCU block, wired to its rail and its USB pair.
3. Peripherals: sensors, indicators, buttons — each to the bus block it needs.
4. Service access before cosmetics: physically compose every net in
   `plan.must_expose` (for RP2040, SWCLK/SWD) to the actual connector/probe,
   then re-plan with exactly those `exposed_nets`. A bare MCU plan is
   intentionally not buildable.
5. Glue and mechanics last: holes, test points, silkscreen.

`missing_requirements()` after each step is cheaper than a build. The final
pre-source gate is `plan.buildable`, not merely an empty `unmet` tuple.
