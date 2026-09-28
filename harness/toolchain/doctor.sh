#!/usr/bin/env bash
# Harness DSH doctor — can THIS machine build a board and show it? cwd = the install dir.
#
# One line per check: `ok   <what>` / `warn <what>` / `miss <what>`. Exit 1 only on a miss.
# The probe list mirrors the app's app_prereq_check (viewer/src/server/circuit/http.mjs) and the
# pipeline's own resolution rules (circuitpy/toolchain.py): claude · node >= 22.12 · the pinned
# toolchain · python >= 3.10 (CIRCUIT_PYTHON wins) · the built viewer · kicad-cli (reported, not
# required — without it boards build but are never fab-ready).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
status=0
ok()   { echo "ok   $*"; }
warn() { echo "warn $*"; }
miss() { echo "miss $*"; status=1; }

if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ]; then
  ok "claude on PATH"
else
  miss "claude not found — install Claude Code: https://claude.ai/install"
fi

node_bin="$(command -v node || true)"
[ -z "$node_bin" ] && [ -x /opt/homebrew/opt/node@22/bin/node ] && node_bin=/opt/homebrew/opt/node@22/bin/node
if [ -n "$node_bin" ]; then
  v="$("$node_bin" --version 2>/dev/null | sed 's/^v//')"
  major="${v%%.*}"; rest="${v#*.}"; minor="${rest%%.*}"
  case "$major" in (*[!0-9]*|"") major=0; minor=0;; esac
  if [ "$major" -gt 22 ] || { [ "$major" -eq 22 ] && [ "${minor:-0}" -ge 12 ]; }; then
    ok "node $v"
  else
    miss "node $v is older than 22.12 (brew install node@22)"
  fi
else
  miss "node not found (brew install node@22)"
fi

toolchain="${CIRCUIT_TOOLCHAIN:-$ROOT/toolchain}"
if [ -x "$toolchain/node_modules/.bin/tscircuit-cli" ]; then
  ok "toolchain $(node -p "require('$toolchain/node_modules/tscircuit/package.json').version" 2>/dev/null || echo present)"
else
  miss "toolchain not installed at $toolchain — run harness/toolchain/setup.sh"
fi

py=""
for cand in "${CIRCUIT_PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3; do
  [ -n "$cand" ] || continue
  resolved="$(command -v "$cand" 2>/dev/null || true)"
  [ -n "$resolved" ] || continue
  if "$resolved" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    py="$resolved"; break
  fi
done
if [ -n "$py" ]; then
  ok "python $("$py" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))') ($py)"
else
  miss "no python >= 3.10 found (brew install python@3.12, or set CIRCUIT_PYTHON)"
fi

if [ -f "$ROOT/viewer/dist/index.html" ]; then
  ok "viewer built"
else
  miss "viewer not built — run harness/toolchain/setup.sh"
fi

kicad=""
for cand in "${CIRCUIT_KICAD_CLI:-}" "$(command -v kicad-cli 2>/dev/null || true)" \
            /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli \
            /Applications/KiCad.app/Contents/MacOS/kicad-cli \
            "$HOME/Applications/KiCad.app/Contents/MacOS/kicad-cli" \
            "$HOME/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"; do
  if [ -n "$cand" ] && [ -x "$cand" ]; then kicad="$cand"; break; fi
done
if [ -n "$kicad" ]; then
  ok "kicad-cli $("$kicad" version 2>/dev/null | head -1)"
else
  warn "kicad-cli not found — boards build, but gerbers stay unverified and no ORDER.md is written (brew install --cask kicad)"
fi

exit $status
