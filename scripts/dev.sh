#!/usr/bin/env bash
# Autonomous Circuit dev entry — web mode.
#
# Starts the whole app: Vite dev server + the Circuit API middleware (chat
# driver, SSE, catalog, projects) mounted by viewer/vite.config.mjs. One process.
#
# Node: the test runner needs >=22.6 (--experimental-strip-types) and Vite 7
# wants >=22.12. If the system node is older, prefer Homebrew's keg-only
# node@22 (Apple Silicon prefix first, then Intel).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

NODE22=""
for prefix in /opt/homebrew /usr/local; do
  if [ -x "$prefix/opt/node@22/bin/node" ]; then
    NODE22="$prefix/opt/node@22/bin"
    break
  fi
done
if [ -n "$NODE22" ]; then
  # Prefer node@22 whenever the system node is broken (crashes) or older than
  # Vite 7's floor (22.12). `node --version` runs in a guarded subshell — a
  # dyld-broken brew node aborts instead of failing politely.
  v="$( (node --version 2>/dev/null || true) | sed 's/^v//' )"
  major="${v%%.*}"; rest="${v#*.}"; minor="${rest%%.*}"
  case "$major" in (*[!0-9]*|"") major=0; minor=0;; esac
  if [ "${major:-0}" -lt 22 ] || { [ "$major" -eq 22 ] && [ "${minor:-0}" -lt 12 ]; }; then
    export PATH="$NODE22:$PATH"
    echo "[dev] using $("$NODE22/node" --version) from node@22 (system node missing, broken, or <22.12)"
  fi
fi

if [ ! -d "$REPO_ROOT/viewer/node_modules" ]; then
  echo "[dev] installing viewer dependencies…"
  npm --prefix "$REPO_ROOT/viewer" install
fi

# Vendor shared Python packages into skill runtimes so live skill runs work
# from a fresh clone (the vendored trees are gitignored).
RUNTIMES="$REPO_ROOT/scripts/build/build-skill-runtimes.sh"
if [ -x "$RUNTIMES" ]; then
  "$RUNTIMES"
fi

# The claude CLI drives every chat turn; fail loud now, not mid-turn.
if ! command -v claude >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/claude" ]; then
  echo "[dev] WARNING: claude CLI not found on PATH — chat turns will fail." >&2
fi
# Pinned Node toolchain for circuitpy (tscircuit CLI, checks, svg renderers).
# Idempotent; fails loud if the install comes up incomplete.
"$REPO_ROOT/scripts/setup-toolchain.sh"

# circuitpy needs python >= 3.10; the system python3 on this Mac is 3.9.
# Resolution order: CIRCUIT_PYTHON > python3.12 on PATH > the known miniconda
# install > python3. Warn (don't abort) — chat turns fail with a clear error.
CIRCUIT_PY=""
for cand in "${CIRCUIT_PYTHON:-}" "$(command -v python3.12 || true)" \
            /Users/d/miniconda/bin/python3.12 "$(command -v python3 || true)"; do
  [ -n "$cand" ] && [ -x "$cand" ] || continue
  if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    CIRCUIT_PY="$cand"
    break
  fi
done
if [ -n "$CIRCUIT_PY" ]; then
  echo "[dev] circuitpy python: $CIRCUIT_PY ($("$CIRCUIT_PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'))"
  export CIRCUIT_PYTHON="$CIRCUIT_PY"
else
  echo "[dev] WARNING: no python >= 3.10 found (set CIRCUIT_PYTHON) — board builds will fail." >&2
fi

# kicad-cli is optional (reported, not required): without it boards build but
# fab packets are never fab-ready (unverified_gerbers blocks shipping).
if ! command -v kicad-cli >/dev/null 2>&1 \
   && [ ! -x "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli" ]; then
  echo "[dev] WARNING: kicad-cli not found — ERC/DRC + verified gerbers unavailable (brew install --cask kicad)." >&2
fi

exec npm --prefix "$REPO_ROOT/viewer" run dev
