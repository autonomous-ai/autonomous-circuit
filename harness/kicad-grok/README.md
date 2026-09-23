# KiCad (Grok) — the KiCad tile on xAI's Grok Build, Grok 4.7

The same harness as [`../kicad`](../kicad/README.md) — same `AGENTS.md`, skill and toolchain
(symlinks, so an edit there is an edit here; `template/` is a real copy because the daemon copies
it without dereferencing, and the test pins it byte-identical) — with one different manifest:

- `"engine": "grok"` — Harness spawns [Grok Build](https://docs.x.ai/build/overview) (`grok`,
  found on PATH or at `~/.local/bin/grok`, the installer's default) in the pane.
- `agent.args` — `-m grok-4.7 --reasoning-effort high --permission-mode bypassPermissions
  --trust --no-auto-update`. The daemon (0.2.89) has no permission-mode table for grok, so the
  tile carries its own always-approve, the way the codex manifest carries
  `approval_policy=never`; `--trust` marks the fresh workspace trusted, which is what gates
  Grok's reading of the workspace `AGENTS.md`, the linked skills under `.agents/skills` and the
  project hooks under `.grok/hooks`. Grok's sandbox is off by default, so kicad-cli, pcbnew and
  Freerouting run as they do on codex with `danger-full-access`.
- `toolchain/init-workspace.sh` reads this manifest's engine and writes
  `.grok/hooks/kicad.json` (Stop → `kicadpy.autofinish`, 1200 s; `StopCancelled` → the same
  module, which cancels the run on a user interrupt) — the hook the codex manifest passes as
  `-c hooks.Stop=…`. It also runs `git init` in the workspace: Grok resolves project hooks at
  the git root only (a plain folder gets AGENTS.md and skills, but no hook — measured with
  `grok inspect --json`, 2026-09-23).

## Auth

Grok Build takes an API key from `XAI_API_KEY` (console.x.ai) when no browser session is
active, or a session from `grok login`. The pane's shell is the user's login shell, so an
`export XAI_API_KEY=…` in `~/.zshrc` (or `~/.zprofile`) reaches the agent; nothing in this
repository holds a key. `toolchain/doctor.sh` warns when neither the variable nor
`~/.grok/auth.json` is present.

## Install (dev)

```sh
curl -fsSL https://x.ai/cli/install.sh | bash        # once: Grok Build into ~/.local/bin
harness dsh check "$PWD/harness/kicad-grok"
harness dsh install "$PWD/harness/kicad-grok" --link
harness dsh doctor autonomous/kicad-grok
```

Both tiles install side by side (`autonomous/kicad` on codex, `autonomous/kicad-grok` on grok),
so ⌘N offers the same board on either engine for a like-for-like run.

## Known daemon limits (0.2.89)

- `agent_create` refuses a first prompt for grok (`PROMPT_UNSUPPORTED`) although `grok` takes a
  positional prompt: create the harness without a prompt and paste it into the pane.
- `permissionMode` is refused for grok (`INVALID_PERMISSION_MODE`); the viewer's New board
  button sends it only for claude and codex.
