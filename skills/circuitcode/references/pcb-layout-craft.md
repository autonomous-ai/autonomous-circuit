# Reading `_pcb.png` — what "right" looks like

**Load:** before your first review pass.

## The checklist, in order

1. **Decoupling beside its IC.** Every 100nF should be visibly adjacent to the
   power pin it serves — same side and following the component vendor's
   routed reference envelope, not across the board. A decoupling cap at the
   wrong end of the board is electrically the same as no decoupling cap. Declare
   `product.json.layout.decoupling.maxDistanceMm` (normally `2.0`); the parsed
   gate measures supply-pad to capacitor-pad edges and also requires an
   authored local port-to-port tree. Two independent `pin -> net.V3_3` leaves
   are not a bypass-loop topology. Use `exclude` only for measured non-loads
   such as an ESD clamp's rail-reference pin, never for an inconvenient IC.
   When the manufacturer's routed reference board establishes a larger local
   envelope, record a ref-scoped
   `overrides: [{match, maxDistanceMm, source}]` rule; `source` cites the
   manufacturer reference URI or document identifier, and the block review
   records what was measured. Override patterns must match a
   populated chip, cannot overlap an exclusion, and overlapping rules use the
   strictest bound. The topology, measurable-pad, and exact-copper gates still
   apply; this is not a way to waive a bad route.
2. **Connectors on the promised edge.** USB where the user plugs in, headers
   where the ribbon goes. Check against what you told the user in the plan; the
   enclosure is being designed to that.
   For products with functional placement regions, declare
   `product.json.layout.componentZones`: match references with fnmatch syntax,
   choose a board-coordinate circle, annulus, or rectangle, and normally use
   `containment: "courtyard"` so the full rotated footprint—not just its
   origin—must fit. Boundaries are inclusive; a rule matching no populated
   component and a component outside its zone both block fabrication. These
   zones make a ring, central electronics area, or keep-in corridor measurable;
   they do not waive courtyard overlaps, edge clearances, or routing checks.
3. **Mounting holes clear of copper.** `MIN_COPPER_TO_EDGE_MM` from the edge,
   and nothing routed under a screw head. These holes are what hold the board in
   the printed body.
4. **Silkscreen legible and useful.** Refdes visible, not underneath its own
   part, not overlapping a pad. A board you can populate by hand is a board you
   can repair.
5. **Traces look deliberate.** Trace count itself is not the measured limit;
   fine-pitch congestion and poor functional placement are. Long meandering
   runs, unnecessary vias, or a trace threading between unrelated pads means
   the *placement* or routing phases are wrong. Move connected parts into a
   corridor, then re-route.
   Power rails need a visible wide trunk plus only short fine-pitch escapes;
   two polygons labelled GND are not proof of a plane connection. The
   post-pour island gate must confirm each fanout reaches material plane copper.
   It must also reject touching different-net pour faces. On electrode or touch
   boards, use board-specific pour outlines around the functional copper rather
   than laying a full-board GND polygon over it.
   Stitch top and bottom GND material at no more than 10mm pitch and beside
   important layer transitions/edge-return paths. Stitch coordinates are
   explicit because a blind grid can land in a keepout, split island, or touch
   electrode; the solved-pour gate must prove each via actually joins same-net
   material on both faces.
   Use 0.25mm for ordinary board-level signals. The fabrication minimum is a
   limit, not a useful default. Fine-pitch package escapes and
   controlled-impedance nets such as USB may use a different width, but that
   exception must be explicit, short where it is an escape, and measured by
   the compiled-layout contract.
6. **Nothing crowds the board edge.** Copper, silk, and parts inside the edge
   clearance.
7. **The board is the size you said.** Compare against `product.json`'s
   `envelopeMm`. The enclosure depends on this number.

## The placement rule that fixes most routing

Route quality is mostly placement quality. If the router struggles:

- Put connected things next to each other. Power entry beside the regulator,
  regulator beside what it feeds, sensor beside its bus.
- A ground pour is not permission for a long return. Keep each pad-to-plane
  dogbone at or below 2mm; verify the emitted `fanout:*` copper rather than
  trusting the route's name.
- Orient parts so their pins point at what they connect to.
- Leave a channel for the bus rather than making it thread between components.

An autorouting error is very often a *consequence* — the pipeline reports
`pcb_autorouting_error` alongside placement errors because it skipped routing
after finding them. Fix the placement errors first and the routing error
usually disappears with them.

Plane fanout has the same placement dependency. If the phase reports that only
some drops reached its breakout boundary, do not fall back to a long routed GND
tree. Give the component cluster a wider breakout corridor, direct crowded
drops toward open space, or move the cluster. Treat the partial circuit JSON as
failed even when the CLI process exits zero.

## The honest limit

This picture cannot show you thermal behaviour, current capacity, EMI, or
signal integrity. The gauntlet now measures trace width against attached load,
but a late finding is still a failed first build: size the power trunk with
`helpers.trace_width_for()` and record trunk plus endpoint neck-down rules in
`product.json.layout` before routing.
The reusable default is a 0.8mm power trunk, a short explicit 0.2mm
fine-pitch neck-down, and 0.8mm-outer/0.5mm-drill power vias. A wide surface
trace that silently bottlenecks through a generic 0.6mm/0.3mm signal via does
not satisfy that contract. Override these values only with an explicit
current/thermal calculation and a measured net class; never turn the 0.8mm
trunk into the board-wide signal width.
