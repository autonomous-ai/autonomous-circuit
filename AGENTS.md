# AGENTS.md

> ⚠ v0 scaffold: drama-domain content below is being replaced by the circuit tracks.

This repo is a consumer web app: chat → circuit design → PCB/PCBA fab packet.
Bootstrapped from the autonomous-tv donor; once bootstrapped, no runtime
dependency on the donor's drama stack.

## Repo Rules

- Each skill must be self-contained at runtime. No skill imports from another skill or
  from repo-root modules. Shared runtime helpers live under `packages/` and get vendored
  into skill runtimes at build time (`scripts/build/build-skill-runtimes.sh`).
- `packages/dramapy/` is the source of truth for the Python episode pipeline. The
  vendored copy under `skills/dramacode/scripts/packages/dramapy/` is generated;
  do not hand-edit. (Donor package — the pipeline track forks it.)
- Edit sources first, then regenerate explicit derived outputs.
- `docs/video-interfaces.md` is frozen; changes go through
  `docs/video-interfaces-CHANGES.md` (append-only, entry template inside).
  (Donor contract — the circuit contract lands as its own doc.)
- The `mock` provider is the default everywhere; tests and CI never touch the network.
- Out of scope for v1: the Tauri desktop shell, slicing/printing/social donor paths.

## Checks

Run only the checks relevant to the change.

- dramapy: `cd packages/dramapy && python3.12 -m pytest -q`
- viewer (client + server): `npm --prefix viewer test && npm --prefix viewer run build`
- skills: `cd skills/dramacode && python3.12 -m pytest tests/ -q`
