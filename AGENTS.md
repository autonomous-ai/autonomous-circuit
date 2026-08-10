# AGENTS.md

This repo is a consumer web app: chat → engineering spec → board source → verified,
fab-ready PCB/PCBA packet. Bootstrapped from the autonomous-tv donor; once bootstrapped,
no runtime dependency on the donor's drama stack.

## Repo Rules

- Each skill must be self-contained at runtime. No skill imports from another skill or
  from repo-root modules. Shared runtime helpers live under `packages/` and get vendored
  into skill runtimes at build time (`scripts/build/build-skill-runtimes.sh`).
- `packages/circuitpy/` is the source of truth for the Python board pipeline. The
  vendored copy under `skills/circuitcode/scripts/packages/circuitpy/` is generated;
  do not hand-edit.
- Edit sources first, then regenerate explicit derived outputs.
- `docs/circuit-interfaces.md` is frozen; changes go through
  `docs/circuit-interfaces-CHANGES.md` (append-only, entry template inside). Never
  silently drift.
- Gate on parsed artifacts, never exit codes — `tscircuit-cli build` exits 0 with real
  errors; they live as `*_error` elements inside circuit.json.
- Node tooling runs out-of-process from the exact-pinned `toolchain/`; the CLI binary is
  `tscircuit-cli` — never `npx tsci` (unrelated package). `python3` on PATH may be 3.9;
  the pipeline needs 3.10+ (`/Users/d/miniconda/bin/python3.12` on this machine).
- Tests and CI never touch the network: `CIRCUIT_PARTS_ENGINE=off` suite-wide; parts
  resolve from `parts.json` + committed block pins; kicad-dependent tests skip (not
  fail) when kicad-cli is absent.
- The safety envelope is contract-level and refuses at spec time: no mains ever,
  battery only via the sealed validated block, radio only as certified modules.
- Out of scope for v1: ordering APIs, the 3D viewer tab, the screening loop, registry
  publishing.

## Checks

Run only the checks relevant to the change.

- circuitpy: `cd packages/circuitpy && python3.12 -m pytest -q`
- viewer (client + server): `npm --prefix viewer test && npm --prefix viewer run build`
- skills: `cd skills/circuitcode && python3.12 -m pytest tests/ -q`
