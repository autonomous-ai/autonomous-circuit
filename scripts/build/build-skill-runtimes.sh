#!/usr/bin/env bash
# Vendor shared Python packages into skill runtimes.
#
# Repo rule: each skill must be self-contained at runtime — it never
# imports from outside its own directory. Shared helpers live under
# `packages/` and get copied into per-skill vendor directories by this
# script. The vendored directories are gitignored / regenerated on build.
#
# Currently vendors:
#   packages/dramapy/src/dramapy/  →  skills/dramacode/scripts/packages/dramapy/
#   packages/dramapy/src/dramapy/  →  skills/screening-room/scripts/packages/dramapy/
#
# Run automatically by scripts/dev.sh (before `cargo run`) and
# scripts/build/build-app.sh (before bundling), so the generators always
# ship a populated runtime. The vendored trees are gitignored; only
# README.md and .gitignore are tracked.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# vendor_package <src_dir> <dest_dir>
# rsync excludes Python caches and keeps the tracked files we ship (the README
# documentation and the .gitignore that keeps the rest of the vendored tree out
# of git). `P` protects them from `--delete`, which would otherwise remove any
# dest file absent from the source tree.
vendor_package() {
  local src="$1" dest="$2"
  mkdir -p "${dest}"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude='__pycache__' \
      --exclude='*.pyc' \
      --filter='P README.md' \
      --filter='P .gitignore' \
      "${src}/" "${dest}/"
  else
    # Portable fallback for environments without rsync (e.g. Windows Git Bash).
    # Mirror the rsync behaviour: wipe the vendored tree except the tracked
    # README.md / .gitignore, then copy the source minus Python caches.
    find "${dest}" -mindepth 1 \
      ! -name README.md ! -name .gitignore \
      -prune -exec rm -rf {} +
    ( cd "${src}" && \
      find . -name '__pycache__' -prune -o -name '*.pyc' -prune -o -type f -print0 \
      | while IFS= read -r -d '' f; do
          mkdir -p "${dest}/$(dirname "$f")"
          cp "$f" "${dest}/$f"
        done )
  fi
}

DRAMAPY_SRC="${REPO_ROOT}/packages/dramapy/src/dramapy"
DRAMACODE_VENDOR="${REPO_ROOT}/skills/dramacode/scripts/packages/dramapy"
SCREENING_VENDOR="${REPO_ROOT}/skills/screening-room/scripts/packages/dramapy"

if [ ! -d "${DRAMAPY_SRC}" ]; then
  echo "error: dramapy source not found at ${DRAMAPY_SRC}" >&2
  exit 1
fi
vendor_package "${DRAMAPY_SRC}" "${DRAMACODE_VENDOR}"
echo "vendored dramapy → skills/dramacode/scripts/packages/dramapy"
vendor_package "${DRAMAPY_SRC}" "${SCREENING_VENDOR}"
echo "vendored dramapy → skills/screening-room/scripts/packages/dramapy"
