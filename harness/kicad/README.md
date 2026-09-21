# KiCad — Circuit's KiCad-native pipeline as a Harness domain harness

[Harness](https://github.com/autonomous-ai/openharness) package for the KiCad-native (v2) pipeline
of this repository: describe a board in the chat pane and get a real KiCad project — a wired
schematic, DRC-checked copper, a prototype packet — with the project's own board viewer in the pane
beside the agent. The v1 (tscircuit) pipeline is a separate harness, **Copper** (`harness/` at the
repository root, wrapped in the store as `autonomous/autonomous-circuit`); the KiCad tile is its sibling,
not its replacement.

- `harness.json` — the manifest: id `autonomous/kicad`, category PCB, the engine, the paths below.
- `AGENTS.md` — the whole workflow for the agent: where things are, plan → build → review, done.
- `skills/kicad/SKILL.md` — the tool card (`kicadpy` commands, publisher, verdict, KiCad discovery).
- `template/` — `project.json` (engine `kicad-native`, the workspace marker) and `product.json`.
- `toolchain/python` — the host Python with `kicadpy` + `circuitpy` on its path; `$KICAD_HARNESS_PYTHON`.
- `toolchain/setup.sh` — vendors Freerouting (jar + JRE) and builds the viewer; nothing on the machine.
- `toolchain/doctor.sh` — engine CLI, node ≥ 22.12, host Python, **kicad-cli and pcbnew (required)**,
  Freerouting, the built viewer.
- `toolchain/init-workspace.sh` — folders, the project clock, the seed verdict.
- `toolchain/viewer.sh` — the Circuit viewer in viewer-only mode over the one workspace.

The verdict, `.harness/verdict.json` (spec 1), is written by `kicadpy.publish` at every sidecar
write and by `python -m kicadpy.harness` on demand — `packages/kicadpy/src/kicadpy/harness.py`.

## Develop

Link this folder as the installed harness; the app then runs the code in this checkout:

```sh
harness dsh check "$PWD/harness/kicad"
harness dsh install "$PWD/harness/kicad" --link     # runs toolchain/setup.sh once
harness dsh doctor autonomous/kicad
python3 -m unittest harness/kicad/tests/test_package.py packages/kicadpy/tests/test_harness_verdict.py
```

Then ⌘N in the app → KiCad → a folder → a prompt. `AGENTS.md` and the skill are symlinked, so an
edit is live in the next session; after editing `viewer.sh` kill the viewer process and the daemon
respawns it; after editing `harness.json`, `harness dsh remove autonomous/kicad` and install again.

The engine is **`codex`** with `agent.args: ["-m", "gpt-6-astra"]` — the tile was always meant to be
KiCad + Astra; Harness spawns codex with `--dangerously-bypass-approvals-and-sandbox`, so the KiCad
gate runs unsandboxed. The claude manifest differs in two lines (`"engine": "claude"`,
`CIRCUIT_SKILLS_DIR` → `${workspace}/.claude/skills`) and no `args`. The store wrapper (`store/agents/kicad` in openharness) pins a commit of this repository and
points at `upstream/harness/kicad/…`, the way Copper's does.
