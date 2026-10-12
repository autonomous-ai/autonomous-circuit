#!/usr/bin/env bash
# CadQuery for the KiCad tile's Case phase, vendored INTO this checkout (toolchain/case/.venv), never
# onto the machine. The case builder (the agent's case/build.py) and the case checker
# (kicadpy.case) run on this interpreter; toolchain/case-python is the entry point.
#
# Pinned: CadQuery 2.7.0 on Python 3.12 (OCP wheels exist for macOS arm64/x86_64 and Linux).
# Idempotent: a venv that already imports the pinned version is left alone.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$ROOT/toolchain/case"
VENV="$DEST/.venv"
CQ_VERSION="2.7.0"
PY_VERSION="3.12"

if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c "import cadquery, sys; sys.exit(0 if cadquery.__version__ == '$CQ_VERSION' else 1)" 2>/dev/null; then
  echo "ok   cadquery $CQ_VERSION already in toolchain/case"
  exit 0
fi
mkdir -p "$DEST"
rm -rf "$VENV.partial"
if command -v uv >/dev/null 2>&1; then
  uv venv --quiet --python "$PY_VERSION" "$VENV.partial"
  uv pip install --quiet --python "$VENV.partial/bin/python" "cadquery==$CQ_VERSION"
else
  py="$(command -v "python$PY_VERSION" || true)"
  [ -n "$py" ] || { echo "miss python$PY_VERSION (or uv) to build the CadQuery venv" >&2; exit 1; }
  "$py" -m venv "$VENV.partial"
  "$VENV.partial/bin/pip" install --quiet "cadquery==$CQ_VERSION"
fi
"$VENV.partial/bin/python" -c "import cadquery; print('ok   cadquery', cadquery.__version__)"
rm -rf "$VENV"
mv "$VENV.partial" "$VENV"
