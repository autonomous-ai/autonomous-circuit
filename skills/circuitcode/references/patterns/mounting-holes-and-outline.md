# Pattern: mounting holes and board outline

**Trigger:** "it needs to fit the case", "make it smaller", "how do I mount
it", "design the enclosure around it".

**Why this exists:** this board goes inside a 3D-printed body. The outline and
the hole pattern are the contract between the two, and they are the cheapest
thing to get wrong and the most expensive to discover after fabrication.

**The minimum:**

```tsx
<board width="40mm" height="30mm" thickness={1.6}>
  …
  <hole name="H1" diameter="3.2mm" pcbX={-17} pcbY={-12} />
  <hole name="H2" diameter="3.2mm" pcbX={17}  pcbY={12} />
</board>
```

- **At least two holes**, diagonal, so the board cannot rotate on one screw.
  Four for anything with a connector someone will yank.
- **3.2mm** clears an M3 screw — the default fastener for printed parts.
- Keep holes `MIN_COPPER_TO_EDGE_MM` (0.30mm) from the edge and clear of copper;
  a screw head sits on a washer of nothing.

**What to hand the enclosure:** the numbers Vibe needs to model the body —
board outline (W × H × 1.6mm), hole positions and diameter, which edge each
connector is on and how far it overhangs, and the tallest component. State them
in your final response; they are the interface.

**Sizing:**

- Stay inside `product.json`'s `envelopeMm`. Exceeding it is a blocking
  `board_exceeds_envelope` warning, because the case is already sized.
- Minimum 3×3mm (fab floor), but the practical floor is whatever the connectors
  and the hole pattern need.
- "Make it smaller" is usually a placement problem before it is a component
  problem: tighten the block spacing, then reconsider parts.

**Pitfalls:**

- A connector flush with the edge is not the same as a connector overhanging it.
  USB-C receptacles are designed to sit at the edge with the shell protruding —
  get this wrong and the cable never seats through the case wall.
- Don't route under mounting holes; the router doesn't know a screw is coming.
- Changing the outline after the enclosure is modelled means re-printing the
  case. Fix the size in the plan phase, not the review phase.
