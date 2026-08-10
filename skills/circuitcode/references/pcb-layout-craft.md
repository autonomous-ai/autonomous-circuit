# Reading `_pcb.png` — what "right" looks like

**Load:** before your first review pass.

## The checklist, in order

1. **Decoupling beside its IC.** Every 100nF should be visibly adjacent to the
   power pin it serves — same side, a millimetre or two away, not across the
   board. A decoupling cap at the wrong end of the board is electrically the
   same as no decoupling cap.
2. **Connectors on the promised edge.** USB where the user plugs in, headers
   where the ribbon goes. Check against what you told the user in the plan; the
   enclosure is being designed to that.
3. **Mounting holes clear of copper.** `MIN_COPPER_TO_EDGE_MM` from the edge,
   and nothing routed under a screw head. These holes are what hold the board in
   the printed body.
4. **Silkscreen legible and useful.** Refdes visible, not underneath its own
   part, not overlapping a pad. A board you can populate by hand is a board you
   can repair.
5. **Traces look deliberate.** The default router is fast and dumb; it degrades
   past ~50 traces. Long meandering runs where a short one was possible, or a
   trace threading between pads it did not need to, means the *placement* is
   wrong. Move parts, then re-route.
6. **Nothing crowds the board edge.** Copper, silk, and parts inside the edge
   clearance.
7. **The board is the size you said.** Compare against `product.json`'s
   `envelopeMm`. The enclosure depends on this number.

## The placement rule that fixes most routing

Route quality is mostly placement quality. If the router struggles:

- Put connected things next to each other. Power entry beside the regulator,
  regulator beside what it feeds, sensor beside its bus.
- Orient parts so their pins point at what they connect to.
- Leave a channel for the bus rather than making it thread between components.

An autorouting error is very often a *consequence* — the pipeline reports
`pcb_autorouting_error` alongside placement errors because it skipped routing
after finding them. Fix the placement errors first and the routing error
usually disappears with them.

## The honest limit

This picture cannot show you thermal behaviour, current capacity, EMI, or
signal integrity. There is no trace-width-versus-amps check in the gauntlet —
that is why you size power traces with `helpers.trace_width_for()` up front
rather than discovering the problem later.
