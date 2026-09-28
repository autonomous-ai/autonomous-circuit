# KiCad (Claude) — the KiCad tile on Claude Code

The same harness as [`../kicad`](../kicad/README.md) — same `AGENTS.md`, skill and toolchain
(symlinks; `template/` is a real copy because the daemon copies it without dereferencing, and the
test pins it byte-identical) — with the claude manifest: `"engine": "claude"`,
`CIRCUIT_SKILLS_DIR` → `${workspace}/.claude/skills` (where Harness links a claude tile's skills),
and no `args`. Harness writes the workspace `CLAUDE.md` with an `@AGENTS.md` import and passes
`--dangerously-skip-permissions` when the harness is created in "full" mode (the viewer's New
board button does; pick "full" in the desktop's Advanced step).

No Stop hook yet on this arm: the codex tile passes `kicadpy.autofinish` as `-c hooks.Stop=…` and
the grok tile writes `.grok/hooks/kicad.json`; a `.claude/settings.json` hook in the workspace is
the equivalent and is left for a later commit. Until then the review loop runs inside the turn,
as `AGENTS.md` says.

```sh
harness dsh check "$PWD/harness/kicad-claude"
harness dsh install "$PWD/harness/kicad-claude" --link
harness dsh doctor autonomous/kicad-claude
```

Three tiles then sit side by side (`autonomous/kicad` on codex, `autonomous/kicad-grok`,
`autonomous/kicad-claude`), so ⌘N offers the same board on any engine.
