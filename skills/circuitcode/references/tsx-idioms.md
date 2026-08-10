# The tscircuit dialect — what to write, and what not to guess

**Load:** when writing or editing board source.

The toolchain is pinned at `tscircuit@0.0.2279` (`toolchain/package.json`). It
publishes roughly seven versions a day with no semver and no changelog, so:
**never guess a prop name.** If you are not sure a prop exists, check a golden
block that already uses it, or the CLI's own help. An invented prop is silently
ignored — the build succeeds and the board is wrong.

## The shape of a board

```tsx
export default () => (
  <board width="40mm" height="30mm" thickness={1.6}>
    …children…
  </board>
)
```

Every placed element carries both coordinate systems:

- `pcbX` / `pcbY` / `pcbRotation` — physical placement, millimetres, origin at
  board centre.
- `schX` / `schY` / `schRotation` — schematic placement, arbitrary units.

Skipping `schX/schY` produces a legible board and an unreadable schematic. You
have to Read that schematic in step 6, so this costs you directly.

## Nets and traces

```tsx
<trace name="TR_U1_sda" from=".U1 > .SDA" to="net.I2C_SDA" />
```

- Selector syntax is `.<refdes> > .<pinLabel>`. Pin labels come from the block's
  `pinLabels`, not from the datasheet's numbering. Get them from `BLOCK.md`.
- Every trace gets a `name`. Unnamed traces produce
  `source_unnamed_trace_warning` and an unreadable drawing.
- Power and ground go to `net.V3_3`, `net.V5`, `net.GND` — the names in
  `tables.RAILS`. Consistency here is what makes the schematic readable.
- A trace to a port that does not exist is `source_trace_not_connected_error` —
  almost always a pin-label typo. Check `BLOCK.md`.

## Glue components

```tsx
<resistor name="R7" resistance="4.7k" footprint="0402"
          pcbX={2} pcbY={-3} schX={1} schY={0}
          supplierPartNumbers={{ jlcpcb: ["C25900"] }} />
<capacitor name="C9" capacitance="100nF" footprint="0402" … />
<led name="D2" footprint="0402" … />
<hole name="H1" diameter="3.2mm" pcbX={-17} pcbY={-12} />
```

Give glue an explicit `supplierPartNumbers` where you know the part — otherwise
the parts engine picks for you at build time, and the choice can change between
runs. Prefer Basic parts (see the fab profile reference).

## Grouping

Wrap a subcircuit in `<group pcbX={…} pcbY={…} schX={…} schY={…}>` so it moves as
a unit. Golden blocks already do this internally — placing a block is placing
its group, which is why one `pcbX/pcbY` pair moves the whole subcircuit.

## Things that are banned here, not merely discouraged

- `import … from "@tsci/…"` — the registry is mutable and unreviewed; an AI loop
  that auto-imports it is a supply-chain hole.
- `footprint="jlcpcb:C1234"` or `footprint="kicad:Library/Part"` — these fetch
  over the network **at build time**. The same source would produce different
  gerbers on different days, and CI could not run offline. Blocks carry their
  land patterns inline for exactly this reason.
- Autorouter cloud modes. The default router is local and deterministic. It
  degrades past roughly 50 traces — if you are hitting that, the board wants
  fewer parts or a real layout pass, not a cloud service.

## The exit code lies

`tscircuit-cli build` exits 0 even when the board has real errors. Errors are
elements *inside* `circuit.json` whose `type` ends in `_error`. The pipeline
parses them; you read them in the verdict's `warnings`. Never conclude "it
built, so it's fine" from a successful command.
