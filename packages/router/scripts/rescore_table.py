#!/usr/bin/env python3
"""The corrected results table, and what it did to the ranking.

Reads ``rescore.json`` (the same copper under the new ruler) next to
``benchmarks/tournament/results-2026-08-16.json`` (the old ruler, plus the
pipeline's independent verdict on the 12 instances with a real board) and
prints three things:

* per family, old ruler against new: clean cells, mean completeness, errors
* the new error mix, by kind, because the whole point is *which* findings the
  inscribed stadium was hiding
* the rank correlation between the harness and the pipeline, before and after.
  That number is the actual claim: a harness that ranks routers differently
  from the engine that ships boards is not measuring the right thing.

    python3.12 packages/router/scripts/rescore_table.py \\
        --rescore work/tournament/rescore.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RESULTS = REPO / "packages" / "router" / "benchmarks" / "tournament" / "results-2026-08-16.json"

HEADLINE = [
    "pathfinder-negotiated", "maze-astar", "plane-and-classes", "ripup-reroute",
    "meta-anneal", "meta-genetic", "topological-graph", "exact-and-structured",
    "baseline-pattern",
]


def spearman(a: list[float], b: list[float]) -> float:
    """Rank correlation, ties averaged. -1 is a perfect inversion."""
    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            mean = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = mean
            i = j + 1
        return out

    ra, rb = ranks(a), ranks(b)
    n = len(a)
    mean_a, mean_b = sum(ra) / n, sum(rb) / n
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb))
    den = (
        sum((x - mean_a) ** 2 for x in ra) ** 0.5
        * sum((y - mean_b) ** 2 for y in rb) ** 0.5
    )
    return num / den if den else 0.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rescore", default=str(REPO / "work" / "tournament" / "rescore.json"))
    ap.add_argument("--results", default=str(RESULTS))
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    data = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
    cells = [c for c in data["cells"] if c.get("new")]
    old_results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    real_kicad = {
        (c["router"], c["instance"]): (
            (c["pipelineRealBoard"]["kicadCopper"] or 0)
            - (c["pipelineRealBoard"]["controlKicadCopper"] or 0)
        )
        for c in old_results["cells"]
        if c.get("pipelineRealBoard")
    }
    real_instances = {i for _, i in real_kicad}

    print(f"old ruler {', '.join(data['oldRulerHash'])}"
          f"  ->  new ruler {', '.join(data['newRulerHash'])}")
    print(f"{len(cells)} cells, same copper, re-scored\n")

    rows = {}
    for router in sorted({c["router"] for c in cells}):
        mine = [c for c in cells if c["router"] == router]
        n = len(mine)
        real = [c for c in mine if c["instance"] in real_instances]
        rows[router] = {
            "cells": n,
            "oldClean": sum(1 for c in mine if c["old"]["clean"]),
            "newClean": sum(1 for c in mine if c["new"]["clean"]),
            "oldErrors": sum(c["old"]["errors"] for c in mine),
            "newErrors": sum(c["new"]["errors"] for c in mine),
            "oldCompleteness": sum(c["old"]["completeness"] for c in mine) / n,
            "newCompleteness": sum(c["new"]["completeness"] for c in mine) / n,
            "realCells": len(real),
            "oldCleanReal": sum(1 for c in real if c["old"]["clean"]),
            "newCleanReal": sum(1 for c in real if c["new"]["clean"]),
            "newErrorsReal": sum(c["new"]["errors"] for c in real),
            "kicad": sum(real_kicad.get((router, c["instance"]), 0) for c in real),
            "byKind": {},
        }
        for c in mine:
            for kind, count in (c["new"].get("errorKinds") or {}).items():
                rows[router]["byKind"][kind] = rows[router]["byKind"].get(kind, 0) + count

    hdr = (f"{'family':<24}{'clean old':>10}{'clean new':>10}{'err old':>9}"
           f"{'err new':>9}{'routed%':>9}{'kicad':>7}")
    print(hdr)
    print("-" * len(hdr))
    for router in HEADLINE:
        v = rows.get(router)
        if not v:
            continue
        print(f"{router:<24}{v['oldClean']:>7}/{v['cells']:<2}{v['newClean']:>7}/{v['cells']:<2}"
              f"{v['oldErrors']:>9}{v['newErrors']:>9}"
              f"{v['newCompleteness'] * 100:>9.1f}{v['kicad']:>7}")

    print(f"\nOn the {len(real_instances)} instances with a real board on disk:")
    hdr2 = f"{'family':<24}{'clean old':>10}{'clean new':>10}{'err new':>9}{'kicad':>7}"
    print(hdr2)
    print("-" * len(hdr2))
    for router in HEADLINE:
        v = rows.get(router)
        if not v or not v["realCells"]:
            continue
        print(f"{router:<24}{v['oldCleanReal']:>7}/{v['realCells']:<2}"
              f"{v['newCleanReal']:>7}/{v['realCells']:<2}"
              f"{v['newErrorsReal']:>9}{v['kicad']:>7}")

    print("\nNew errors by kind, summed over every cell:")
    kinds: dict[str, int] = {}
    for v in rows.values():
        for kind, count in v["byKind"].items():
            kinds[kind] = kinds.get(kind, 0) + count
    for kind, count in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"  {kind:<24}{count:>7}")

    families = [r for r in HEADLINE if rows.get(r) and rows[r]["realCells"]]
    old_err = [rows[r]["oldClean"] * -1.0 for r in families]
    new_err = [rows[r]["newErrorsReal"] * 1.0 for r in families]
    kicad = [rows[r]["kicad"] * 1.0 for r in families]
    old_real_err = []
    for r in families:
        mine = [
            c for c in cells
            if c["router"] == r and c["instance"] in real_instances
        ]
        old_real_err.append(float(sum(c["old"]["errors"] for c in mine)))
    print("\nAgreement with KiCad on the real boards (Spearman over families,")
    print("harness errors vs KiCad copper errors; 1.0 is perfect agreement):")
    print(f"  old ruler : {spearman(old_real_err, kicad):+.2f}")
    print(f"  new ruler : {spearman(new_err, kicad):+.2f}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"perRouter": rows, "byKind": kinds}, indent=1) + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
