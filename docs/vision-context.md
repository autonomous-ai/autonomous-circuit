# Vision context — v1 scope guard

Recorded 2026-08-10 so build decisions stay compatible with where this is going, and so
v1 stays small. **v1 = chat → verified fab packet, perfected.**

## v1 is

One loop: describe a gadget → engineering spec → golden-block board source → the staged
verification gauntlet → a fab packet (gerbers + BOM + CPL + ORDER.md) the user uploads to
JLCPCB themselves. One fab profile (`jlcpcb`). The packet and the walkthrough are the
product; the placement-preview screen at JLCPCB is the last safety net.

## v1 is not

- **No ordering API.** None worth having exists — JLCPCB has no assembly endpoint and
  gates API access on order history (see `circuit-research-2026-08-10.md`). Roadmap, not v1.
- **No 3D tab.** `board.glb` is written best-effort as an artifact; a viewer tab for it
  is post-v1.
- **No screening loop.** The donor's critic pass is deleted; a design-review skill
  (kicad-happy-style audits) may return post-v1.

## The Vibe pairing

Circuit is the electrical layer of the hardware row in the house pattern
(engine / creator tool / network): circuitpy is the engine, this app is the creator
tool, and the physical-social network is Panda. Two attachment points exist today and
must not be designed away:

1. **The enclosure interface travels in the `circuit-brief`** (board outline, mounting
   holes, connector cutouts) so Vibe's CAD loop can design the printed body around real
   geometry.
2. **`board.glb` is the 3D handoff artifact** — best-effort per build, the same
   board-in-a-body loop the field proves with STEP → CAD (StepUp pattern).

Board projects stay portable, self-contained dirs (`product.json` + `parts.json` +
`blocks/` + `boards/`), copyable as a unit — the future remix object, same as the donor's
series dirs.

## What we deliberately do NOT build

- **Our own EDA.** tscircuit authors, kicad-cli verifies and exports. We are a product
  company using AI EDA, not an AI EDA company.
- **Novel circuits.** Composition from golden blocks + glue only — never an IC circuit
  invented from a datasheet. No deterministic check knows Ohm's law; the block is the
  safety mechanism.
- **Anything mains.** The safety envelope is contract-level and refuses at spec time: no
  mains ever (≤24V DC), battery only via the sealed validated block, radio only as
  certified modules.
- **Registry publishing.** No `tsci push`, no `@tsci` imports in the loop; blocks live
  in-repo, pinned and snapshot-tested.
