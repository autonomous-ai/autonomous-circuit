#!/usr/bin/env bash
# Harness DSH workspace init — run once, after the template copy, cwd = the new workspace.
#
# The app's project_create writes only project.json; the skeleton (product.json, boards/main.tsx,
# tsconfig, tscircuit.config.json) comes from the template Harness already copied, and blocks/ is
# seeded by the first build from the library (never copied by hand — see circuitcode's SKILL.md).
# So the only thing left is the folder the verdict lands in.
set -euo pipefail
mkdir -p .harness
