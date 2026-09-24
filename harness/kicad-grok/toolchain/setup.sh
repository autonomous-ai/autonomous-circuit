#!/usr/bin/env bash
# KiCad (Grok) — Harness DSH setup: the shared KiCad setup (Freerouting, the viewer), then the
# Grok Build settings this tile needs in the user's ~/.grok/config.toml (see grok-config.py:
# compaction under the 200k price tier, no Cursor/Claude MCP or hook imports). Written once,
# marked, never over a table the user already has; the API key and the provider route are the
# user's own (README.md).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
"$HERE/../../kicad/toolchain/setup.sh" "$@"

echo "[kicad-grok:setup] grok config"
"$HERE/python" "$HERE/grok-config.py"
