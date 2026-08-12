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
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
  >
    …children…
  </board>
)
```

**Set those six on every board.** The defaults do not meet JLC's economy
process: the router otherwise emits 0.3/0.2mm vias, whose 0.05mm annular ring
is below the fab's floor, and does not preserve the product's 0.15mm
trace-to-pad and via-to-pad clearance contract. These props are manufacturing
gates; the parsed artifact must still prove that the router actually obeyed
them.

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
- Do not put one wide automatic trace on a shared power net and assume it is a
  trunk. The pinned router can promote that width into fine-pitch escapes. Use
  golden `PowerTrunk` as the **sole physical-source-to-named-rail branch** to
  create an explicit wide corridor with short narrow endpoint escapes, and
  declare the matching `layout.netClasses` contract. Do not connect two pads
  that already belong to the rail: that closes a redundant source-net cycle,
  and two such cycles can stall compilation before routing begins.
  When the source pad and useful trunk corridor are on opposite faces, use
  `PowerTrunk`'s all-or-nothing `sourceLayer` / `trunkLayer` / `sourcePoint` /
  `trunkVia` mode. The board owns the absolute pad centre and a cleared off-pad
  .8/.5mm transition; the helper retains the short .2mm source neck and one
  connected .8mm boundary tree. Do not ask the generic router to infer that
  layer transition from a multi-terminal rail.
- A reusable block may expose a narrowly typed attachment opt-out when a
  board-owned tree replaces one of its ordinary rail leaves. For example,
  `UsbPowerEntry externalFaultPullupPort="R32"` attaches V3_3 at R32.pin2 and
  `StatusLed externalRailAttachmentPort="R"` attaches at the series
  resistor's pin1. Suppress only the documented leaf, then author the
  replacement tree in the same composition; never delete local block copper
  or leave the attachment pad floating.
- A decoupling capacitor is an authored local branch, not another independent
  rail leaf: connect the IC supply port to the capacitor rail port with a
  bounded two-port trace/tree, then give that tree one
  `authoredNetTreeBoundary` edge to the named rail. Declare the product's
  `layout.decoupling.maxDistanceMm` so the independent artifact gate measures
  the actual supply-pad-to-capacitor-pad spacing and refuses MST-only topology.
- Do not hang downstream bulk capacitance directly on USB VBUS. The connector
  block exposes `net.VBUS_RAW`; feed that through golden `UsbPowerEntry` into
  protected `net.V5`, keep only the declared small raw bypass before the
  limiter, and encode the measured cap/current contract in
  `product.json.powerBudget.usb` with
  `helpers.usb_power_budget_for_plan()`. A firmware brightness cap is valid
  only when its declared fixed-plus-controlled operating load stays below the
  limiter's worst-case trip point; the physical LED peak remains in the
  contract. The generator supplies the board's actual refdes pattern but does
  not retype the limiter part, ILIM resistor/value/topology, or current
  arithmetic.
- Do not delete GND connections to get an unrouted-looking plane. Keep every
  logical connection. Use golden `GndFanoutTrace` only for direct one-port pad
  drops and one board-level `GndPlanes` phase/pour declaration. Keep deliberate
  multi-port ties (for example IC GND -> local bypass capacitor) as ordinary
  traces, and keep PTH shell/test-point connections ordinary when the fanout
  layer is not below their source layer.
- On a dual-face `GndPlanes`, map both fanout faces. A pad already inside
  same-face solved GND material becomes a zero-length plane contact; only a
  sole opposite-face plane needs a cross-layer dogbone. Supply real stitching
  coordinates and prove the dominant top/bottom material graph. The parsed
  artifact must contain every expected fanout and have no
  `pcb_plane_connectivity_error` or `pcb_copper_pour_short_error`; a same-net
  label is not proof that a via lands on the connected material island.
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

### Fixed paths inside rotated groups

`pcbPath` vertices are resolved from the trace's `from` component frame. If
that component or its parent group is rotated, feeding the trace coordinates
that were already rotated into board space rotates them a second time. This
was measured on the eight-pixel puck: pre-rotated capacitor paths landed near
`±46mm`, outside a 70mm board, while the same component-local path compiled to
eight correct 1.8003–1.8005mm branches.

Keep reusable path vertices in the owning component's local frame and let the
component/group transform them exactly once. Prefer `pcbPathRelativeTo` when a
path has a named local anchor; it makes the coordinate owner explicit. A
rotated repeated block must have a parsed-artifact regression over every
orientation—source arithmetic alone cannot prove that compiled copper stayed
inside the board.

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

1. **Fix functional placement and corridors first.** The three reference-board
   audit found the repeatable failures in dense MCU/crystal/flash clusters,
   missing plane fanout, and power topology. On harness-puck, a full 5x route
   reproduced the same 0.085mm crystal-net clearance exactly; extra search did
   not create space that the placement withheld.
2. **Use effort only as a bounded secondary attempt.** The older Terminal
   46-to-18 claim is withdrawn: core's cache identity omitted effort and could
   reuse the first route for a nominally different run. Values are
   `"1x" | "2x" | "5x" | "10x" | "100x"`, but only cold, configuration-keyed
   attempts are comparable; parsed artifacts, not effort, pick the winner.
3. **Treat clearance as a requirement, not a repair knob.** Going from 0.1 to
   0.15mm after placement made this same board report 7 → 125 errors because
   the pinned router does not reliably steer away from the tighter boundary.
   Declare the product margin up front, set both
   `minTraceToPadEdgeClearance` and `minViaEdgeToPadEdgeClearance` before the
   first route, and make enough placement corridors for it. Never tighten the
   checker late and call the new findings a routing regression.
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
