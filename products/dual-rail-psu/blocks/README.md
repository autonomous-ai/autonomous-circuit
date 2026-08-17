# Vendored golden blocks — generated, and committed on purpose

Copies of `packages/golden-blocks/blocks/`, written by
`scripts/build/build-skill-runtimes.sh` (run by `scripts/dev.sh`).

These are the blocks circuitcode copies into a **new board project**, so the
project owns a frozen snapshot of the library and its gerbers stay reproducible
even as the library moves on (contract §1: "copied in at project creation").

**They are tracked in git, and that is the whole point.** For a while every
product's `blocks/` was ignored, which made that paragraph above a lie: a fresh
clone had no snapshot, so a rebuild composed whatever the library happens to be
*today*, and a fix made to a block for one product vanished silently. An
engineer building `dual-sensor-node` (2026-08-17) hit both halves — they had to
patch `sensor-bme280` so the board's two sensors could take different I2C
addresses, and that patch was ignored by git, so the board would have
reproduced with both sensors answering to 0x76 and no error anywhere.
`examples/` had it right the whole time; products did not.

Author blocks in `packages/golden-blocks/` — that is where their testbenches,
snapshots and `BLOCK.md` docs live, and where CI checks them. Then re-run the
vendor script. A block edited **here** and not there is a product-local
deviation: legitimate, sometimes necessary, and it must be committed and said
out loud in the product's own notes, because nothing upstream will preserve it.
