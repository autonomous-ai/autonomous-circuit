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
  <board
    width="40mm" height="30mm" thickness={1.6}
    minTraceWidth="0.2mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
  >
    …children…
  </board>
)
```

**Set those four on every board.** The defaults do not meet JLC's economy
process: the router otherwise emits 0.3/0.2mm vias, whose 0.05mm annular ring
is below the fab's floor, and both DFM gates block it. With them set, the same
board routes with 0.15mm annular and passes.

The full set the board accepts, all `Distance` (a number in mm or a string
like `"0.2mm"`) — verified against the pinned `@tscircuit/props`:

| Prop | What it constrains |
|---|---|
| `minTraceWidth` | narrowest track the router may draw |
| `minViaPadDiameter` / `minViaHoleDiameter` | via geometry, and so its annular ring |
| `minViaHoleEdgeToViaHoleEdgeClearance` | via-to-via spacing |
| `minViaEdgeToPadEdgeClearance` | via-to-pad spacing |
| `minTraceToPadEdgeClearance` | track-to-pad spacing |
| `minPadEdgeToPadEdgeClearance` | pad-to-pad spacing |
| `minPlatedHoleDrillEdgeToDrillEdgeClearance` | drill-to-drill spacing |
| `minBoardEdgeClearance` | how close copper may come to the outline |

There is **no** via-to-trace clearance prop. If you get
`pcb_via_trace_clearance_error`, the answer is space — spread the placement so
the router is not threading a via between tracks — not a setting.

Other useful board props: `layers`, `borderRadius` (a large radius on a square
is how you get a round board), `material`, `doubleSidedAssembly`,
`isViaInPadAllowed`.

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
- Autorouter cloud modes. The default router is local and deterministic; it is
  also better than its reputation (see below). Reach for placement before you
  reach for a service.

## What the router can and cannot do (measured, 2026-08-10)

The often-repeated "degrades past ~50 traces" is **wrong**. On the Terminal
keyboard — 424 source traces, 50 switches, 15 matrix nets — it routed 349
segments with 259 vias and **zero unrouted nets** in 4 minutes 45. Trace count
is not the limit.

The real limit is **fine pitch**. A 0.4mm-pitch QFN-56 leaves 0.2mm of channel
between pads, and a 0.2mm track cannot escape it. On that board every single
remaining error lived in one 30 × 20mm patch around the MCU; the entire 50-key
field was clean.

So when routing fights you:

1. **Raise the effort before changing the design.**
   `autorouterEffortLevel="5x"` on the board took that same layout from 46
   errors to 18. It costs build time (4:45 → ~17 min) and nothing else.
   Values: `"1x" | "2x" | "5x" | "10x" | "100x"`.
2. **Give the crowded part room.** Spreading the crystal cluster took 18 → 3;
   growing the board 84 → 90mm took 3 → 1. Placement is the lever.
3. **Do not raise the clearance floor to "be safe".** Going from 0.1 to 0.15mm
   made the same board *worse* — 7 errors to 125. Those props gate the
   **checker**, not the router, so tightening them only adds findings.
4. **`layers={4}` is not an escape hatch yet** — it routes better (6 errors)
   but breaks the exporter (`Inner layer … only supports copper gerber`),
   which kills the BOM and CPL with it.
5. A fine-pitch part may simply need a narrower local track than the 0.2mm
   default. That is a real trade against the fab floor (0.127mm), so make it
   deliberately and say so.

## The exit code lies

`tscircuit-cli build` exits 0 even when the board has real errors. Errors are
elements *inside* `circuit.json` whose `type` ends in `_error`. The pipeline
parses them; you read them in the verdict's `warnings`. Never conclude "it
built, so it's fine" from a successful command.
