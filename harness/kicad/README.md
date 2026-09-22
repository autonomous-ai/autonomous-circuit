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

The engine is **`codex`** with `agent.args` = Astra at high effort **plus `-c approval_policy=never -c
sandbox_mode=danger-full-access`**, so a KiCad harness runs unsandboxed and never asks, whatever the
person picked in New Harness — the pipeline (kicad-cli, pcbnew, the supplier's catalog, Freerouting)
does not survive codex's `workspace-write` sandbox (a DRC sat in an uninterruptible exit for two hours,
2026-09-21). Measured on codex 0.155: the `-c` overrides coexist with every flag the daemon adds; the
`--dangerously-bypass-approvals-and-sandbox` flag cannot go in `args` (it refuses to appear twice or
beside `--approve-for-me`). One exception: the daemon's "auto" mode adds `--approve-for-me`, which
wins over the overrides and keeps the sandbox — pick "full" or "ask", never "auto". The claude manifest differs in two lines (`"engine": "claude"`,
`CIRCUIT_SKILLS_DIR` → `${workspace}/.claude/skills`) and no `args`. The store wrapper (`store/agents/kicad` in openharness) pins a commit of this repository and
points at `upstream/harness/kicad/…`, the way Copper's does.

## Codex auto-finish

The manifest supplies Codex `Stop` and `Interrupt` command hooks through `agent.args`;
no Harness source change or separate background agent is required. Requires a Codex CLI
with stable hooks (validated config parsing on 0.155.1). In the tile, open `/hooks` and review
and trust the two Circuit commands before the first build. Approval/sandbox settings do
not grant hook trust. Changed hook definitions need review again; no trust bypass is installed.
See https://learn.chatgpt.com/docs/hooks for Codex's hook contract.

An approved build's `kicadpy.harness --active build` arms `.circuit/autofinish.json`.
At Stop, Circuit republishes native manufacturing packets, parses results and current sidecars,
and returns current blockers as an automatic repair prompt. An unchanged blocker list requests
a different strategy. Up to eight repair continuations are allowed within a four-hour window;
the final continuation reports exhaustion honestly. A user interrupt cancels the run. Planning
and ordinary questions do not arm it. State and publisher logs remain under `.circuit/`.
The gate remains the publisher's `fab.ready`; this does not guarantee any arbitrary board will
converge or validate physical hardware. A trusted hook is required for automatic continuation.

Existing tiles must reload the updated manifest and restart Codex to receive its new arguments;
editing a linked checkout alone does not change a running CLI's hooks. No workspace migration is
needed. Do not restart a working session without coordinating with its owner.
