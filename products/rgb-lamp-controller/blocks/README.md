# Vendored golden blocks — generated, do not hand-edit

Copies of `packages/golden-blocks/blocks/`, written by
`scripts/build/build-skill-runtimes.sh` (run by `scripts/dev.sh`).

These are the blocks circuitcode copies into a **new board project**, so the
project owns a frozen snapshot of the library and its gerbers stay reproducible
even as the library moves on (contract §1: "copied in at project creation").

Author blocks in `packages/golden-blocks/` — that is where their testbenches,
snapshots and `BLOCK.md` docs live, and where CI checks them. Then re-run the
vendor script. Everything here except this README and `.gitignore` is ignored
by git.
