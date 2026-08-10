# AGENTS.md

This repo is a consumer web app: chat → screenplay → shots → stitched vertical episode.
Bootstrapped from the autonomous-vibe (Panda) donor; once bootstrapped, no runtime
dependency on the donor's CAD stack.

## Repo Rules

- Each skill must be self-contained at runtime. No skill imports from another skill or
  from repo-root modules. Shared runtime helpers live under `packages/` and get vendored
  into skill runtimes at build time (`scripts/build/build-skill-runtimes.sh`).
- `packages/dramapy/` is the source of truth for the Python episode pipeline. The
  vendored copy under `skills/dramacode/scripts/packages/dramapy/` is generated;
  do not hand-edit.
- Edit sources first, then regenerate explicit derived outputs.
- `docs/video-interfaces.md` is frozen; changes go through
  `docs/video-interfaces-CHANGES.md` (append-only, entry template inside).
- The `mock` provider is the default everywhere; tests and CI never touch the network.
  Hosted providers (`fal`, `dashscope`, `minimax`) are dev/production render paths.
- Out of scope for v1: the Tauri desktop shell (donor residue in `desktop/` until
  removed), slicing/printing/social donor paths, LoRA training, the `comfyui` provider.

## Checks

Run only the checks relevant to the change.

- dramapy: `cd packages/dramapy && python3.12 -m pytest -q`
- viewer (client + server): `npm --prefix viewer test && npm --prefix viewer run build`
- skills: `cd skills/dramacode && python3.12 -m pytest tests/ -q`
