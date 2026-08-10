# Vendored runtime — generated, do not hand-edit

`circuitpy/` here is a copy of `packages/circuitpy/src/circuitpy/`, written by
`scripts/build/build-skill-runtimes.sh` (which `scripts/dev.sh` runs on every
start). It exists because a skill must be self-contained at runtime: it never
imports from outside its own directory.

Edit the source package, then re-run the vendor script. Everything in this
directory except this README and `.gitignore` is ignored by git.

Resolution order at runtime (see `scripts/common/runner.py`): the test stub
(`CIRCUITCODE_TEST_CIRCUITPY_PATH`) → this vendored copy → the repo's
`packages/circuitpy/src` as a dev fallback.
