#!/usr/bin/env bash
# Put the example boards into the app so they can be opened and looked at.
#
# The examples live in the repo (they are reviewed, tested artefacts). The app
# reads projects from ~/.autonomous-circuit/projects/<uuid>/. This copies each
# example across and writes the project.json the app needs, so `scripts/dev.sh`
# opens with real boards in the rail instead of an empty workspace.
#
# Re-runnable: an example already installed is refreshed in place, keeping its
# project id (and therefore its chat history) intact.
#
# Usage: scripts/install-examples.sh [name ...]     (default: all)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXAMPLES_DIR="$REPO_ROOT/examples"
PROJECTS_DIR="${CIRCUIT_HOME:-$HOME/.autonomous-circuit}/projects"
PY="${CIRCUIT_PYTHON:-/Users/d/miniconda/bin/python3.12}"

mkdir -p "$PROJECTS_DIR"

names=("$@")
if [ ${#names[@]} -eq 0 ]; then
  names=()
  for dir in "$EXAMPLES_DIR"/*/; do
    [ -d "$dir" ] && names+=("$(basename "$dir")")
  done
fi

for name in "${names[@]}"; do
  src="$EXAMPLES_DIR/$name"
  if [ ! -d "$src" ]; then
    echo "skip $name (no such example)" >&2
    continue
  fi

  # Reuse the existing project id when this example is already installed, so
  # reinstalling does not orphan the conversation attached to it.
  existing="$("$PY" - "$PROJECTS_DIR" "$name" <<'EOF'
import json, sys
from pathlib import Path
root, name = Path(sys.argv[1]), sys.argv[2]
for project in sorted(root.glob("*/project.json")):
    try:
        if json.loads(project.read_text()).get("name") == name:
            print(project.parent.name)
            break
    except (OSError, json.JSONDecodeError):
        continue
EOF
)"

  if [ -n "$existing" ]; then
    dest="$PROJECTS_DIR/$existing"
    echo "refreshing $name -> $existing"
  else
    uuid="$("$PY" -c 'import uuid; print(uuid.uuid4())')"
    dest="$PROJECTS_DIR/$uuid"
    echo "installing $name -> $uuid"
  fi

  mkdir -p "$dest"
  # Copy the project itself. Build caches are per-machine and worthless here.
  rsync -a --delete \
    --exclude='.circuit' \
    --exclude='node_modules' \
    --exclude='project.json' \
    "$src/" "$dest/"

  "$PY" - "$dest" "$name" <<'EOF'
import json, sys, time
from pathlib import Path
dest, name = Path(sys.argv[1]), sys.argv[2]
path = dest / "project.json"
now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
payload = {"id": dest.name, "name": name, "created_at": now, "updated_at": now}
if path.is_file():
    try:
        prior = json.loads(path.read_text())
        payload["created_at"] = prior.get("created_at", now)
    except (OSError, json.JSONDecodeError):
        pass
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
EOF
done

echo
echo "installed into $PROJECTS_DIR"
echo "run scripts/dev.sh and open http://127.0.0.1:4179"
