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
- `toolchain/init-workspace.sh` — folders, the project clock, the seed verdict, and the team marker
  for new Codex projects.
- `toolchain/viewer.sh` — the Circuit viewer in viewer-only mode over the one workspace.
- `prompts/` — the prompts every engine is measured on (`claude-pet.md`), so a run on any tile
  starts from the same words.

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

The engine is **`codex`** with `agent.args` = GPT-6.1 Sol at high effort for the Harness entry
agent. In a new project, that agent dispatches the board prompt to an Astra lead and Sol workers
through `kicadpy.parallel_tile` (below). The manifest also supplies **`-c approval_policy=never -c
sandbox_mode=danger-full-access`**, so a KiCad harness runs unsandboxed and never asks, whatever the
person picked in New Harness — the pipeline (kicad-cli, pcbnew, the supplier's catalog, Freerouting)
does not survive codex's `workspace-write` sandbox (a DRC sat in an uninterruptible exit for two hours,
2026-09-21). Measured on codex 0.155: the `-c` overrides coexist with every flag the daemon adds; the
`--dangerously-bypass-approvals-and-sandbox` flag cannot go in `args` (it refuses to appear twice or
beside `--approve-for-me`). One exception: the daemon's "auto" mode adds `--approve-for-me`, which
wins over the overrides and keeps the sandbox — pick "full" or "ask", never "auto". The claude manifest differs in two lines (`"engine": "claude"`,
`CIRCUIT_SKILLS_DIR` → `${workspace}/.claude/skills`) and no `args`. **The Grok arm is the sibling
tile [`../kicad-grok`](../kicad-grok/README.md)** (`autonomous/kicad-grok`, engine `grok`, Grok 4.7):
symlinks to this folder's `AGENTS.md`, `skills`, `template` and `toolchain`, its own manifest, and
`init-workspace.sh` writes the Stop hook as `.grok/hooks/kicad.json` for it. `toolchain/doctor.sh`
reads the manifest's engine and checks that CLI (claude, codex or grok). The store wrapper (`store/agents/kicad` in openharness) pins a commit of this repository and
points at `upstream/harness/kicad/…`, the way Copper's does.

## New Codex project: one prompt starts the team

Harness's workspace init writes `.circuit/team-auto` only for a new Codex KiCad project. The
person opens KiCad, chooses a folder, and sends a board prompt. The entry agent saves the exact
run brief, viewer-owned `prompts/original.md` and `prompts/current.md`, and referenced image files
in `prompts/`, then runs
`kicadpy.parallel_tile start <workspace> --prompt <brief> --execute`. The bridge starts the
contract-first Astra/Sol pilot, seeds follow-up changes from the last clean integrated checkpoint,
and brings checked output back to the same folder the viewer watches. It builds and checks the
board first, gets lead approval on the actual board, then runs firmware and case workers together
against it. The viewer receives the checked board and each independently completed role as it
finishes. The bridge marks the whole team ready only after the final native check and lead review.
The user does not run a pilot CLI.
Questions and requests for options, plans or prices remain read-only. A failed pilot is reported
with its findings; the agent does not silently switch to a single-agent build.

The marker is never inserted into an existing project, including one with an active session.
Saved Harness sessions retain their launch snapshot; use a **new project/session** to get this
path. The Claude and Grok sibling tiles keep their own single-agent workflow. This is an
experimental team workflow: a candidate-ready result still requires physical validation.

## Codex auto-finish for existing single-agent projects

The manifest supplies Codex `Stop` and `Interrupt` command hooks through `agent.args`;
no Harness source change or separate background agent is required. Requires a Codex CLI
with stable hooks (validated config parsing on 0.155.1). In the tile, open `/hooks` and review
and trust the two Circuit commands before the first build. Approval/sandbox settings do
not grant hook trust. Changed hook definitions need review again; no trust bypass is installed.
See https://learn.chatgpt.com/docs/hooks for Codex's hook contract.

In a project without `.circuit/team-auto`, an approved build's
`kicadpy.harness --active build` arms `.circuit/autofinish.json`.
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

## Advisor for single-agent projects (optional)

A second model can review what the builder draws, at the two moments it pays: when the blockers
come back unchanged twice, and once when the board first goes green (`kicadpy/advisor.py`, called
from the Stop hook). It reads a small bundle of artifacts (product.json, pinout, power, bringup,
firmware README and flash.json, the gate findings) in a read-only sandbox, answers in at most 8
points that each name a file, or `NO CONCERNS`. At most 3 calls per run; a failure or timeout is
skipped. **On by default** (`codex:gpt-6-astra`). Change or turn it off for this machine:

```sh
echo off > ~/.harness/kicad-advisor                   # or claude:<model> / codex:<model>
```

`KICAD_ADVISOR` in the environment wins over that file, and `<workspace>/.circuit/advisor.conf` wins over
both, for one run (an A/B: `off` in one folder, `codex:gpt-6-astra` in the other). Measured 2026-10-08 on the Sol-only desk
pet: Astra at medium effort took 139 s and raised two power points the builder missed. Checked
against the files: 10.1 µF across USB VBUS (C1 10 µF + C7 0.1 µF) is over USB's 10 µF, and the
builder had raised its own `railLimitsUF.VBUS` to 11 to pass; the LDO's 10 µF output cap meets the
LD1117 minimum only at nominal, not after its ±20 % tolerance. Each call's seconds and tokens are
in its log.

### Standalone parallel pilot

An explicitly requested isolated experiment can use `python -m kicadpy.parallel` through this
tile's Python wrapper. That lower-level CLI remains useful for controlled comparisons outside
Harness; it does not replace the automatic bridge for a new Codex KiCad project. See the self-contained
[operator guide](skills/kicad/references/parallel-pilot.md) for Astra planning/review, Sol
worktrees, native gates, revision, receipts and comparison controls. Installing the code alone
starts no model calls; `--execute` runs only after the person submits a board request.
