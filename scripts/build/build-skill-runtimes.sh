#!/usr/bin/env bash
# Vendor shared Python packages into skill runtimes.
#
# Repo rule: each skill must be self-contained at runtime — it never
# imports from outside its own directory. Shared helpers live under
# `packages/` and get copied into per-skill vendor directories by this
# script. The vendored directories are gitignored / regenerated on build.
#
# Currently vendors:
#   packages/circuitpy/src/circuitpy/  →  skills/circuitcode/scripts/packages/circuitpy/
#   packages/golden-blocks/blocks/     →  skills/circuitcode/blocks/
#
# The blocks are vendored because circuitcode copies them into each new board
# project (contract §1: "copied in at project creation"), which is what freezes
# a project's block versions and keeps its gerbers reproducible.
#
# Run automatically by scripts/dev.sh, so live skill runs work from a fresh
# clone. The vendored trees are gitignored; only README.md and .gitignore
# are tracked.

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

CIRCUITPY_SRC="${REPO_ROOT}/packages/circuitpy/src/circuitpy"
CIRCUITCODE_VENDOR="${REPO_ROOT}/skills/circuitcode/scripts/packages/circuitpy"

if [ ! -d "${CIRCUITPY_SRC}" ]; then
  # Nothing to vendor (source package missing) — not an error for a scaffold.
  echo "note: no vendorable package at ${CIRCUITPY_SRC}; skipping"
  exit 0
fi
vendor_package "${CIRCUITPY_SRC}" "${CIRCUITCODE_VENDOR}"
echo "vendored circuitpy → skills/circuitcode/scripts/packages/circuitpy"

BLOCKS_SRC="${REPO_ROOT}/packages/golden-blocks/blocks"
BLOCKS_VENDOR="${REPO_ROOT}/skills/circuitcode/blocks"

if [ ! -d "${BLOCKS_SRC}" ]; then
  echo "note: no golden blocks at ${BLOCKS_SRC}; skipping"
  exit 0
fi
vendor_package "${BLOCKS_SRC}" "${BLOCKS_VENDOR}"
echo "vendored golden blocks → skills/circuitcode/blocks"

# The parts catalog: a snapshot of JLCPCB's stocked Basic/Preferred libraries.
# Vendored so the skill can answer "what part should I use" instantly and
# offline — the live service takes 47-90s cold and must never sit in the loop.
CATALOG_SRC="${REPO_ROOT}/packages/parts-catalog"
CATALOG_VENDOR="${REPO_ROOT}/skills/circuitcode/parts_catalog"

if [ ! -d "${CATALOG_SRC}/catalog" ]; then
  echo "note: no parts catalog at ${CATALOG_SRC}/catalog; skipping"
  exit 0
fi
mkdir -p "${CATALOG_VENDOR}"
vendor_package "${CATALOG_SRC}/catalog" "${CATALOG_VENDOR}/catalog"
cp "${CATALOG_SRC}/catalog.py" "${CATALOG_VENDOR}/catalog.py"
echo "vendored parts catalog → skills/circuitcode/parts_catalog"
