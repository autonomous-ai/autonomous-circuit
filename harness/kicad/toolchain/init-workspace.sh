#!/usr/bin/env bash
# KiCad harness — Harness DSH workspace init. Runs once, after the template copy, cwd = the new workspace,
# HARNESS_DSH_DIR = the install dir.
#
# The template brought project.json (engine: kicad-native — the marker) and product.json. This lays
# out the folders the native workflow writes into, stamps the project's clock, and seeds the first
# verdict so the pane header has a state before the first prompt (Build / Checks / Fab, all pending).
set -euo pipefail
mkdir -p .harness design tools engineering boards .circuit
python="${HARNESS_DSH_DIR:-$(cd "$(dirname "$0")/.." && pwd)}/toolchain/python"
if [ -x "$python" ]; then
  "$python" - <<'PY'
import json, time
from pathlib import Path
p = Path('project.json')
try:
    meta = json.loads(p.read_text())
except (OSError, ValueError):
    meta = {}
now = int(time.time() * 1000)
meta.setdefault('id', 'workspace')
meta.setdefault('name', Path.cwd().name)
if not meta.get('created_at'):
    meta['created_at'] = now
meta['updated_at'] = now
meta['engine'] = 'kicad-native'
p.write_text(json.dumps(meta, indent=2) + '\n')
PY
  "$python" -m kicadpy.harness "$PWD" >/dev/null
else
  # The install is not there yet (a check without setup): a static seed keeps the header honest.
  cat > .harness/verdict.json <<'JSON'
{"spec": 1, "ready": false, "summary": "No board yet", "findings": [],
 "phases": [{"id": "build", "name": "Build", "state": "pending"},
            {"id": "checks", "name": "Checks", "state": "pending"},
            {"id": "fab", "name": "Fab", "state": "pending"}]}
JSON
fi
