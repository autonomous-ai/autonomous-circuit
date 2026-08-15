"""Fold the tournament's per-cell rows into one results JSON and one table."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
for extra in (PACKAGE / "src", PACKAGE.parent / "circuitpy" / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def load(root: Path) -> tuple[dict, dict]:
    rows: dict[tuple[str, str], dict] = {}
    for rdir in sorted((root / "rows").iterdir()):
        if not rdir.is_dir():
            continue
        for f in sorted(rdir.glob("*.json")):
            rows[(rdir.name, f.stem)] = json.loads(f.read_text())
    verify: dict[tuple[str, str], dict] = {}
    vroot = root / "verify"
    if vroot.is_dir():
        for rdir in sorted(vroot.iterdir()):
            if not rdir.is_dir():
                continue
            for f in sorted(rdir.glob("*.json")):
                verify[(rdir.name, f.stem)] = json.loads(f.read_text())
    return rows, verify


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tournament", default=str(PACKAGE.parent.parent / "work" / "tournament"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    root = Path(args.tournament)
    rows, verify = load(root)

    routers = sorted({r for r, _ in rows})
    instances = sorted({i for _, i in rows})

    out: dict = {"routers": {}, "instances": instances, "cells": []}
    ruler = None
    for (router, instance), row in sorted(rows.items()):
        s = row.get("score") or {}
        v = verify.get((router, instance), {})
        ruler = ruler or (s.get("measuredAgainst") or {}).get("rulerHash")
        cell = {
            "router": router,
            "instance": instance,
            "ok": row.get("ok"),
            "error": row.get("error"),
            "completeness": s.get("completeness"),
            "connectedNets": s.get("connectedNets"),
            "routableNets": s.get("routableNets"),
            "errors": s.get("errors"),
            "warnings": s.get("warnings"),
            "violationsByKind": s.get("violationsByKind"),
            "vias": (s.get("quality") or {}).get("via_count"),
            "copperMm": (s.get("quality") or {}).get("copper_mm"),
            "diffPairCoupling": (s.get("quality") or {}).get("diff_pair_coupling"),
            "iterations": s.get("iterations"),
            "nodesExpanded": s.get("nodesExpanded"),
            "deterministic": row.get("deterministic"),
            "claimHonest": s.get("claimHonest"),
            "orphanCopper": len(s.get("orphanCopper") or ()),
            "clean": s.get("clean"),
            "wallClockS": row.get("wallClockS"),
            "notes": row.get("solutionNotes"),
        }
        if v:
            cell["verify"] = {
                "ok": v.get("ok"),
                "rescoreMatches": (
                    v.get("rescored", {}).get("completeness") == s.get("completeness")
                    and v.get("rescored", {}).get("errors") == s.get("errors")
                ),
                "rescored": v.get("rescored"),
                "dfmFindings": len(v.get("dfm", {}).get("findings") or ()),
                "tscircuitCopperFindings": v.get("tscircuit", {}).get("copperFindingCount"),
                "tscircuitKinds": v.get("tscircuit", {}).get("kindCounts"),
                "kicadCopperFindings": v.get("kicad", {}).get("copperFindingCount"),
                "kicadKinds": v.get("kicad", {}).get("kindCounts"),
                "kicadError": v.get("kicad", {}).get("error") or v.get("kicad", {}).get("skipped"),
            }
        out["cells"].append(cell)

    for router in routers:
        cells = [c for c in out["cells"] if c["router"] == router and c["ok"]]
        n = len(cells)
        if not n:
            continue
        out["routers"][router] = {
            "instances": n,
            "meanCompleteness": round(sum(c["completeness"] for c in cells) / n, 4),
            "cleanInstances": sum(1 for c in cells if c["clean"]),
            "fullyRouted": sum(1 for c in cells if c["completeness"] == 1.0),
            "totalErrors": sum(c["errors"] for c in cells),
            "totalWarnings": sum(c["warnings"] for c in cells),
            "totalVias": sum(c["vias"] for c in cells),
            "totalCopperMm": round(sum(c["copperMm"] for c in cells), 1),
            "deterministic": sum(1 for c in cells if c["deterministic"]),
            "claimHonest": sum(1 for c in cells if c["claimHonest"]),
            "orphanCopperCells": sum(1 for c in cells if c["orphanCopper"]),
            "totalWallClockS": round(
                sum((c["wallClockS"] or [0])[0] for c in cells), 1
            ),
            "kicadCopperFindings": sum(
                (c.get("verify") or {}).get("kicadCopperFindings") or 0 for c in cells
            ),
            "tscircuitCopperFindings": sum(
                (c.get("verify") or {}).get("tscircuitCopperFindings") or 0 for c in cells
            ),
            "verified": sum(1 for c in cells if (c.get("verify") or {}).get("ok")),
        }
    out["rulerHash"] = ruler

    text = json.dumps(out, indent=1) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    hdr = (f"{'router':<24}{'clean':>7}{'mean%':>8}{'100%':>6}{'err':>5}{'warn':>6}"
           f"{'vias':>7}{'mm':>9}{'det':>6}{'ksDRC':>7}{'tsDRC':>7}{'sec':>8}")
    print(hdr)
    print("-" * len(hdr))
    for router, r in sorted(
        out["routers"].items(), key=lambda kv: (-kv[1]["meanCompleteness"],)
    ):
        print(f"{router:<24}{r['cleanInstances']:>4}/{r['instances']:<2}"
              f"{r['meanCompleteness'] * 100:>8.1f}{r['fullyRouted']:>6}"
              f"{r['totalErrors']:>5}{r['totalWarnings']:>6}{r['totalVias']:>7}"
              f"{r['totalCopperMm']:>9.0f}{r['deterministic']:>4}/{r['instances']:<2}"
              f"{r['kicadCopperFindings']:>7}{r['tscircuitCopperFindings']:>7}"
              f"{r['totalWallClockS']:>8.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
