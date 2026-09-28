#!/usr/bin/env python3
"""The Grok Build settings the KiCad (Grok) tile needs, written into the user's ~/.grok/config.toml.

Grok Build reads `[model.*]` and `[compat.*]` only from its own config file: the `GROK_CONFIG` /
`GROK_CONFIG_PATH` overlays accept the `models` section alone (measured 2026-09-24, grok 1.0.41),
so a tile cannot carry these in its manifest. Setup writes them once, marked, and never rewrites
a table the user already has.

    grok-config.py            append the missing tables (idempotent); prints what it did
    grok-config.py --check    doctor mode: `ok`/`warn` lines, exit 1 when a table is missing

What and why (harness-12, 2026-09-23):
- `[model."grok-4.7"] context_window = 176000` — Grok compacts at 85 % of this, i.e. near 150k,
  so no request crosses the 200k tier where x.ai doubles the price. The real window is 500k;
  this number only times compaction. Without it $20 lasted 25–38 minutes.
- `[compat.cursor]` / `[compat.claude]` `mcps = false`, `hooks = false` — Grok otherwise imports the
  machine's Cursor MCP servers (a firecrawl scrape of jlcpcb.com cost 5k tokens of marketing text
  each) and the Claude Code hooks. Harness's own hooks live in ~/.grok/hooks and are untouched.

Never written here: the API key (XAI_API_KEY / OPENROUTER_API_KEY belongs in the shell rc) and the
provider route (api.x.ai or OpenRouter) — that is the user's choice; see README.md.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tomllib

MARK = 'autonomous/kicad-grok'
TABLES = [
    (('model', 'grok-4.7'),
     '[model."grok-4.7"]\n'
     '# context_window drives auto-compaction (85 % of it): 176k compacts near 150k, under the 200k price tier.\n'
     '# The model\'s real window is 500k; this number only times compaction.\n'
     'context_window = 176000\n',
     lambda t: isinstance(t.get('context_window'), int) and t['context_window'] <= 200000,
     'context_window <= 200000'),
    (('compat', 'cursor'), '[compat.cursor]\nmcps = false\nhooks = false\n',
     lambda t: t.get('mcps') is False and t.get('hooks') is False, 'mcps = false, hooks = false'),
    (('compat', 'claude'), '[compat.claude]\nmcps = false\nhooks = false\n',
     lambda t: t.get('mcps') is False and t.get('hooks') is False, 'mcps = false, hooks = false'),
]


def config_path():
    return Path(os.environ.get('GROK_HOME') or Path.home() / '.grok') / 'config.toml'


def load(path):
    try:
        return tomllib.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


def table(cfg, keys):
    node = cfg
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, dict) else None


def apply(path):
    """Append every table that is absent; report tables present but set differently. Returns (added, kept, wrong)."""
    cfg = load(path)
    added, kept, wrong = [], [], []
    chunks = []
    for keys, text, good, want in TABLES:
        name = '.'.join(keys)
        existing = table(cfg, keys)
        if existing is None:
            chunks.append(text)
            added.append(name)
        elif good(existing):
            kept.append(name)
        else:
            wrong.append((name, want))
    if chunks:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as fh:
            fh.write(f'\n# >>> {MARK}: written by the tile\'s setup; the tile needs these, edit or delete as you like\n')
            fh.write('\n'.join(chunks))
            fh.write(f'# <<< {MARK}\n')
        tomllib.loads(path.read_text(encoding='utf-8'))  # the file must still parse
    return added, kept, wrong


def check(path):
    cfg = load(path)
    status = 0
    for keys, _, good, want in TABLES:
        name = '.'.join(keys)
        existing = table(cfg, keys)
        if existing is None:
            print(f'warn {path}: [{name}] missing — run harness/kicad-grok/toolchain/setup.sh (or `python grok-config.py`)')
            status = 1
        elif good(existing):
            print(f'ok   grok config [{name}]')
        else:
            print(f'warn {path}: [{name}] is set differently; the tile wants {want}')
            status = 1
    return status


def main(argv):
    path = config_path()
    if '--check' in argv:
        sys.exit(check(path))
    added, kept, wrong = apply(path)
    for name in added:
        print(f'[kicad-grok:setup] added [{name}] to {path}')
    for name in kept:
        print(f'[kicad-grok:setup] kept [{name}] (already as the tile wants)')
    for name, want in wrong:
        print(f'[kicad-grok:setup] left [{name}] alone: it exists but differs; the tile wants {want}')
    if not added:
        print(f'[kicad-grok:setup] {path} needed nothing')


if __name__ == '__main__':
    main(sys.argv[1:])
