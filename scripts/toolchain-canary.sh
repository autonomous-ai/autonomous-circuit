#!/usr/bin/env bash
# Does the newest tscircuit still build our boards the same way?
#
# The stack we bet on ships roughly seven releases a day, with no changelog and
# no semver — the single largest risk named in the substrate bake-off. We
# defend by pinning exact versions, but a pin only delays the question. This
# answers it on a schedule instead of during an upgrade panic:
#
#   1. build every golden block with the PINNED toolchain (the baseline)
#   2. install the LATEST tscircuit into a scratch toolchain
#   3. build them again and diff the resulting circuit JSON summaries
#
# Output is a report, never a mutation: it never touches toolchain/package.json.
# Upgrading stays a deliberate PR that re-runs every block testbench.
#
# Usage: scripts/toolchain-canary.sh [report-path]
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPORT="${1:-$REPO_ROOT/.circuit-canary/report.md}"
SCRATCH="$REPO_ROOT/.circuit-canary/toolchain"
PY="${CIRCUIT_PYTHON:-/Users/d/miniconda/bin/python3.12}"

mkdir -p "$(dirname "$REPORT")" "$SCRATCH"

pinned_version() {
  "$PY" - "$REPO_ROOT/toolchain/package.json" <<'EOF'
import json, sys
print(json.load(open(sys.argv[1]))["dependencies"]["tscircuit"])
EOF
}

PINNED="$(pinned_version)"
LATEST="$(curl -s --max-time 60 https://registry.npmjs.org/tscircuit/latest \
  | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("version","?"))' 2>/dev/null || echo "?")"

{
  echo "# Toolchain canary — $(date -u '+%Y-%m-%d %H:%M UTC')"
  echo
  echo "| | version |"
  echo "|---|---|"
  echo "| pinned | \`$PINNED\` |"
  echo "| latest | \`$LATEST\` |"
  echo
} > "$REPORT"

if [ "$PINNED" = "$LATEST" ]; then
  echo "Pinned version is current. Nothing to compare." >> "$REPORT"
  echo "canary: up to date ($PINNED)"
  exit 0
fi

echo "## Baseline (pinned)" >> "$REPORT"
echo >> "$REPORT"
if "$PY" -m pytest "$REPO_ROOT/packages/golden-blocks/tests" -q > /tmp/canary-pinned.txt 2>&1; then
  echo "Every golden block builds and matches its committed snapshot." >> "$REPORT"
else
  {
    echo '**The pinned toolchain is already failing** — fix that before reading'
    echo 'anything below; the comparison is meaningless against a broken baseline.'
    echo
    echo '```'
    tail -20 /tmp/canary-pinned.txt
    echo '```'
  } >> "$REPORT"
  echo "canary: PINNED BASELINE BROKEN — see $REPORT"
  exit 1
fi

echo >> "$REPORT"
echo "## Candidate (latest)" >> "$REPORT"
echo >> "$REPORT"

# Mirror the real dependency set, then move the whole @tscircuit family to
# latest together.
#
# Two lessons, both learned the hard way on 2026-08-10. Installing `tscircuit`
# alone leaves out tsx (the CLI's TSX runner), @tscircuit/checks, circuit-to-svg
# and sharp, so every block fails for want of a runner — a false alarm
# indistinguishable from a real break, which is how a canary stops being
# believed. And bumping `tscircuit` while pinning `@tscircuit/cli` fails
# outright: 0.0.2280 wanted a circuit-json the pinned CLI's peer range
# rejected. These packages are released as a set and have to be tested as one.
"$PY" - "$REPO_ROOT/toolchain/package.json" "$SCRATCH/package.json" "$LATEST" <<'EOF'
import json, sys
src, dest, latest = sys.argv[1], sys.argv[2], sys.argv[3]
deps = dict(json.load(open(src)).get("dependencies", {}))
deps["tscircuit"] = latest
# Everything in the @tscircuit family (and its circuit-* siblings) rides the
# same release train; pinning one against a bumped other is not a real test.
for name in list(deps):
    if name.startswith("@tscircuit/") or name.startswith("circuit-"):
        deps[name] = "latest"
json.dump(
    {"name": "circuit-toolchain-canary", "private": True, "dependencies": deps},
    open(dest, "w"), indent=2,
)
EOF

rm -f "$SCRATCH/package-lock.json"
if ! (cd "$SCRATCH" && npm install --no-audit --no-fund > /tmp/canary-install.txt 2>&1); then
  {
    echo "Installing the family at \`$LATEST\` failed — that IS the finding:"
    echo "these packages are released together, and a set that cannot even"
    echo "resolve is not a set we can upgrade onto."
    echo
    echo '```'
    grep -E "npm error" /tmp/canary-install.txt | head -12
    echo '```'
  } >> "$REPORT"
  echo "canary: candidate install failed — see $REPORT"
  exit 1
fi

# Same testbenches, newer toolchain. Snapshot mismatches are the signal: they
# mean the upstream produces different copper for identical source.
if CIRCUIT_TOOLCHAIN="$SCRATCH" "$PY" -m pytest "$REPO_ROOT/packages/golden-blocks/tests" -q \
    > /tmp/canary-latest.txt 2>&1; then
  {
    echo "**Clean.** Every golden block builds identically on \`$LATEST\`."
    echo "Upgrading is a one-line change to \`toolchain/package.json\` plus a"
    echo "re-run of the full suite."
  } >> "$REPORT"
  echo "canary: $LATEST is clean — upgrade is safe to propose"
else
  {
    echo "**Divergence on \`$LATEST\`.** Identical source, different output —"
    echo "read the failures before upgrading:"
    echo
    echo '```'
    tail -40 /tmp/canary-latest.txt
    echo '```'
  } >> "$REPORT"
  echo "canary: $LATEST diverges — see $REPORT"
fi

echo
echo "report: $REPORT"
