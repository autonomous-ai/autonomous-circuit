#!/usr/bin/env bash
# KiCad harness — Harness DSH setup. Runs once at install, cwd = the install dir.
#
# Vendors what a KiCad-native board build needs INTO this checkout, never onto the machine:
#   1. Freerouting 2.4.1 + the Temurin JRE it needs, under toolchain/freerouting/ (the router
#      behind `kicadpy route` and the Specctra round trip — without it every board is routed by
#      hand, which is what happened on the v2 clone that had none).
#   2. The Circuit viewer, built, so viewer.sh can serve the board beside the terminal.
# KiCad itself is the machine's (brew install --cask kicad): 1 GB, not a thing to vendor.
# doctor.sh is what reports that, and the Pythons, afterwards.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"

# Homebrew node@22 is keg-only; if the PATH node is older than the viewer needs, prefer it.
for prefix in /opt/homebrew /usr/local; do
  if [ -x "$prefix/opt/node@22/bin/node" ]; then
    v="$( (node --version 2>/dev/null || true) | sed 's/^v//' )"
    major="${v%%.*}"; rest="${v#*.}"; minor="${rest%%.*}"
    case "$major" in (*[!0-9]*|"") major=0; minor=0;; esac
    if [ "${major:-0}" -lt 22 ] || { [ "$major" -eq 22 ] && [ "${minor:-0}" -lt 12 ]; }; then
      export PATH="$prefix/opt/node@22/bin:$PATH"
    fi
    break
  fi
done

echo "[kicad:setup] 1/2 Freerouting (pinned jar + JRE)"
scripts/toolchain/install-freerouting.sh

echo "[kicad:setup] 2/2 viewer"
if [ -f viewer/package-lock.json ]; then
  npm --prefix viewer ci --no-audit --no-fund
else
  npm --prefix viewer install --no-audit --no-fund
fi
npm --prefix viewer run build
[ -f viewer/dist/index.html ] || { echo "[kicad:setup] viewer build incomplete: viewer/dist/index.html missing" >&2; exit 1; }

echo "[kicad:setup] done"
