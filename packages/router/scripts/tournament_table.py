"""The algorithm x instance table, and the winner per instance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent

PRIMARY = [
    "baseline-pattern", "exact-and-structured", "topological-graph",
    "meta-genetic", "meta-anneal", "ripup-reroute", "plane-and-classes",
    "maze-astar", "pathfinder-negotiated",
]


def load(root: Path):
    rows = {}
    for rdir in sorted((root / "rows").iterdir()):
        if not rdir.is_dir():
            continue
        for f in sorted(rdir.glob("*.json")):
            rows[(rdir.name, f.stem)] = json.loads(f.read_text())
    verify = {}
    if (root / "verify").is_dir():
        for rdir in sorted((root / "verify").iterdir()):
            if not rdir.is_dir():
                continue
            for f in sorted(rdir.glob("*.json")):
                verify[(rdir.name, f.stem)] = json.loads(f.read_text())
    return rows, verify


def pipeline_errors(v: dict) -> int | None:
    """Copper findings the *pipeline* reports, not the harness."""
    if not v or not v.get("ok"):
        return None
    n = 0
    ts = v.get("tscircuit") or {}
    if "error" in ts:
        return None
    n += (ts.get("routing") or {}).get("count", 0)
    k = v.get("kicad") or {}
    if k.get("error") or k.get("skipped"):
        return n
    n += k.get("copperFindingCount", 0)
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tournament", default=str(PACKAGE.parent.parent / "work" / "tournament"))
    ap.add_argument("--metric", default="completeness")
    args = ap.parse_args(argv)
    root = Path(args.tournament)
    rows, verify = load(root)
    routers = [r for r in PRIMARY if any(k[0] == r for k in rows)]
    routers += sorted({k[0] for k in rows} - set(routers))
    instances = sorted({k[1] for k in rows})

    w = 7
    print("completeness % — algorithm x instance")
    print(f"{'instance':<48}" + "".join(f"{r[:w - 1]:>{w}}" for r in routers) + "  winner")
    for i in instances:
        line = f"{i:<48}"
        best, bestr = -1.0, ""
        for r in routers:
            row = rows.get((r, i))
            if not row or not row.get("ok"):
                line += f"{'-':>{w}}"
                continue
            c = row["score"]["completeness"] * 100
            line += f"{c:>{w}.1f}"
            if c > best + 1e-9:
                best, bestr = c, r
        print(line + f"  {bestr}")
    print()

    print("pipeline copper findings (tscircuit routing + kicad DRC) — 0 is the bar")
    print(f"{'instance':<48}" + "".join(f"{r[:w - 1]:>{w}}" for r in routers))
    for i in instances:
        line = f"{i:<48}"
        for r in routers:
            n = pipeline_errors(verify.get((r, i)))
            line += f"{'?' if n is None else n:>{w}}"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
