#!/usr/bin/env bash
# Harness DSH viewer — the Circuit viewer in viewer-only mode over ONE workspace.
#
# Harness runs this for the life of the agent's pane with:
#   HARNESS_VIEWER_PORT   the loopback port to listen on
#   HARNESS_WORKSPACE     the workspace folder to serve (the agent's cwd)
#   HARNESS_DSH_DIR       this install dir (also the cwd)
# The server exposes that folder as the single project "workspace", hides chat and onboarding,
# and refuses every chat_* command — the conversation lives in the Harness terminal.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
: "${HARNESS_VIEWER_PORT:?HARNESS_VIEWER_PORT is required}"
: "${HARNESS_WORKSPACE:?HARNESS_WORKSPACE is required}"
for prefix in /opt/homebrew /usr/local; do
  if [ -x "$prefix/opt/node@22/bin/node" ] && ! node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a>22||(a===22&&b>=12)?0:1)' 2>/dev/null; then
    export PATH="$prefix/opt/node@22/bin:$PATH"
  fi
done
export VIEWER_PORT="$HARNESS_VIEWER_PORT"
export VIEWER_HOST="127.0.0.1"
export CIRCUIT_WORKSPACE="$HARNESS_WORKSPACE"
exec node "$ROOT/viewer/src/server/server.mjs"
