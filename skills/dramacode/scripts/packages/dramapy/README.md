# dramapy (vendored)

This directory is a **build-time vendor target** for `packages/dramapy/`
from the repo root. It is left empty in source control on purpose.

The build script `scripts/build/build-skill-runtimes.sh` (repo root) copies
the canonical `packages/dramapy/src/dramapy/` tree into this folder so the
dramacode skill remains self-contained at runtime — per the repo rule that
a skill never reaches outside its own directory at runtime.

## During development

This directory ships empty. The runner imports
`dramapy.generation.generate_episode` via the standard `import dramapy`
mechanism. Tests inject a fast in-process stub via
`DRAMACODE_TEST_DRAMAPY_PATH` (see `tests/conftest.py`) so the suite
doesn't pay for real provider renders per assertion. The real package is
Track B's deliverable and is exercised end-to-end by its own tests under
`packages/dramapy/tests/`.
