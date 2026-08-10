# Reading `_schematic.png` — what "right" looks like

**Load:** before your first review pass.

You `Read` this image and you actually see it. The pipeline can tell you the
netlist is connected; only the picture tells you whether a human could ever
debug this board.

## The checklist, in order

1. **Is every net labelled?** Power rails read `V5`, `V3_3`, `GND`. Signals read
   `I2C_SDA`, `USB_DP`, `SW1`. A drawing full of anonymous wires is a drawing
   nobody will read twice.
2. **Do the blocks read as blocks?** A USB-C entry, a regulator, an MCU, a
   sensor — each should sit as a recognisable cluster, not be scattered across
   the sheet interleaved with its neighbours. If two blocks' symbols are tangled
   together, their `schX/schY` need separating.
3. **Does anything float?** A pin with a stub going nowhere is either a real
   missing connection or a naming mistake. Cross-check against the block's pin
   contract.
4. **Left to right, in signal order.** Power enters at the left, the brain sits
   in the middle, peripherals fan out right. Nobody enforces this; it is the
   difference between a drawing and a diagram.
5. **Do the values show?** Resistors and capacitors should print their value.
   `4.7k`, `100nF`. A schematic with unlabelled passives cannot be checked by
   the person holding the board.
6. **Is anything overlapping?** Overlapping symbols or text mean two elements
   share coordinates. Move one.

## What to do about what you see

Every fix here is a coordinate change or a name change in the board source —
never an edit to the generated SVG or PNG. Bump `schX/schY`, add a `name`, use
a rail name from `tables.RAILS`, re-run.

## The honest limit

A legible schematic is not a correct one. This picture will not tell you that a
pull-up is 470Ω instead of 4.7kΩ, or that a sensor's address strap is on the
wrong side. That is what golden blocks are for. What the picture catches is the
composition-level mistake: the missing connection, the wrong net, the block
wired to the wrong rail.
