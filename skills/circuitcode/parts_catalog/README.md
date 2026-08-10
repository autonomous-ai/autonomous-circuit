# Vendored parts catalog — generated, do not hand-edit

A copy of `packages/parts-catalog/` (the snapshot of JLCPCB's stocked Basic and
Preferred libraries, plus its query API), written by
`scripts/build/build-skill-runtimes.sh`.

It is vendored so the skill can answer "what part goes here" instantly and
offline. The live service takes 47–90 seconds on a cold query and must never
sit inside a design loop.

Refresh the snapshot with `packages/parts-catalog/fetch_catalog.py`, then
re-run the vendor script. Reach it from skill code via `circuitlib.parts`.
