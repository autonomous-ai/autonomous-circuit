#!/usr/bin/env bash
# Harness DSH setup — run ONCE at install, cwd = the Circuit install dir (the repo root).
#
# Installs everything a board build and the viewer pane need on this machine:
#   1. the exact-pinned Node toolchain circuitpy shells out to   (scripts/setup-toolchain.sh)
#   2. the vendored skill runtimes                               (scripts/build/build-skill-runtimes.sh)
#   3. the viewer's dependencies and its production bundle       (viewer/dist)
# Idempotent. Exit non-zero on anything that would leave a build or the viewer unable to run;
# doctor.sh is what reports the softer facts (kicad, python) afterwards.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Vite 7 wants node >= 22.12; prefer Homebrew's keg-only node@22 when the system node is older,
# the same rule scripts/dev.sh applies.
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

echo "[circuit:setup] 1/3 pinned toolchain"
scripts/setup-toolchain.sh

echo "[circuit:setup] 2/3 skill runtimes"
scripts/build/build-skill-runtimes.sh

echo "[circuit:setup] 3/3 viewer"
if [ -f viewer/package-lock.json ]; then
  npm --prefix viewer ci --no-audit --no-fund
else
  npm --prefix viewer install --no-audit --no-fund
fi
npm --prefix viewer run build
[ -f viewer/dist/index.html ] || { echo "[circuit:setup] viewer build incomplete: viewer/dist/index.html missing" >&2; exit 1; }

echo "[circuit:setup] done"
