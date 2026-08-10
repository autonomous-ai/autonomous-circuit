#!/usr/bin/env bash
# Install the pinned Node toolchain circuitpy shells out to (tscircuit CLI, checks,
# svg renderers). Idempotent; run by scripts/dev.sh. See docs/circuit-interfaces.md §1.
set -euo pipefail
cd "$(dirname "$0")/../toolchain"
if [ -f package-lock.json ]; then
  npm ci --no-audit --no-fund
else
  npm install --save-exact --no-audit --no-fund
fi
BIN="node_modules/.bin/tscircuit-cli"
if [ -x "$BIN" ]; then
  echo "toolchain ok: tscircuit-cli present ($(node -p "require('./node_modules/tscircuit/package.json').version"))"
else
  echo "toolchain install incomplete: $BIN missing" >&2
  exit 1
fi
