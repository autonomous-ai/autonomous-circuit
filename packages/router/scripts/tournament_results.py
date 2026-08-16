"""Build the tournament's one results file: scores, independent legality, tables.

Three measurements per (router, instance), kept separate because they answer
different questions:

* **harness** — ``routerlib.scoring.score``. What the families were graded on.
* **synthesized pipeline** — the instance rebuilt as circuit.json and run
  through ``@tscircuit/checks`` and ``kicad-cli pcb drc``. Every pad is the
  rotated rectangle ``routerlib`` itself models, so a disagreement here is a
  disagreement about *check logic*, not about geometry.
* **real board** — the router's copper dropped into the actual
  ``examples/*/main.circuit.json`` with ``apply_solution``, where every pad
  keeps its true shape. Only available for the instances whose placement still
  matches a board on disk.

The empty-solution control runs in both pipeline modes and is subtracted: a
finding that survives an empty board belongs to the placement.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent

EMPTY = "_empty-control"

FAMILY = {
    "maze-astar": "maze-astar", "maze-astar-ripup": "maze-astar",
    "pathfinder-negotiated": "pathfinder-negotiated",
    "ripup-reroute": "ripup-reroute", "ripup-greedy-control": "ripup-reroute",
    "meta-anneal": "metaheuristic", "meta-genetic": "metaheuristic",
    "maze-greedy": "metaheuristic", "maze-off-greedy": "metaheuristic",
    "topological-graph": "topological-graph",
    "exact-and-structured": "exact-and-structured",
    "plane-and-classes": "plane-and-classes",
    "baseline-pattern": "baseline",
}
HEADLINE = [
    "baseline-pattern", "exact-and-structured", "topological-graph",
    "meta-genetic", "meta-anneal", "ripup-reroute", "plane-and-classes",
    "maze-astar", "pathfinder-negotiated",
]


def read_tree(root: Path, name: str) -> dict:
    out: dict = {}
    d = root / name
    if not d.is_dir():
        return out
    for rdir in sorted(d.iterdir()):
        if not rdir.is_dir():
            continue
        for f in sorted(rdir.glob("*.json")):
            out[(rdir.name, f.stem)] = json.loads(f.read_text())
    return out


def synth_findings(v: dict) -> dict | None:
    """The pipeline's verdict, or ``None`` when there is not one.

    ``None`` and "zero findings" are different answers and the difference is
    the whole point: one thrown check inside ``@tscircuit/checks`` returns an
    empty findings list, and counted as zero it reads as a clean board. A
    report that did not complete is not a report.
    """
    if not v or not v.get("ok"):
        return None
    ts = (v.get("tscircuit") or {})
    if "error" in ts:
        return None
    routing = ts.get("routing") or {}
    if routing.get("complete") is False:
        return None
    kic = v.get("kicad") or {}
    return {
        "tscircuitRouting": routing.get("count", 0),
        "tscircuitKinds": routing.get("kindCounts") or {},
        "tscircuitThrew": routing.get("threw") or [],
        "kicadCopper": kic.get("copperFindingCount"),
        "kicadKinds": kic.get("kindCounts") or {},
        "kicadError": kic.get("error") or kic.get("skipped"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tournament", default=str(PACKAGE.parent.parent / "work" / "tournament"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    root = Path(args.tournament)

    rows = read_tree(root, "rows")
    verify = read_tree(root, "verify")
    real: dict = {}
    rdir = root / "verify-real"
    if rdir.is_dir():
        for board in sorted(rdir.iterdir()):
            if not board.is_dir():
                continue
            for f in sorted(board.glob("*.json")):
                real[(f.stem, board.name)] = json.loads(f.read_text())
    corner = {}
    cp = root / "pad-corner-gap.json"
    if cp.is_file():
        corner = json.loads(cp.read_text())

    routers = sorted({r for r, _ in rows})
    instances = sorted({i for _, i in rows})
    ruler = None
    cells = []
    for (router, instance), row in sorted(rows.items()):
        s = row.get("score") or {}
        ruler = ruler or (s.get("measuredAgainst") or {}).get("rulerHash")
        c = {
            "router": router,
            "family": FAMILY.get(router, router),
            "instance": instance,
            "ok": row.get("ok"),
            "harness": {
                "completeness": s.get("completeness"),
                "connectedNets": s.get("connectedNets"),
                "routableNets": s.get("routableNets"),
                "errors": s.get("errors"),
                "warnings": s.get("warnings"),
                "violationsByKind": s.get("violationsByKind"),
                "clean": s.get("clean"),
                "vias": (s.get("quality") or {}).get("via_count"),
                "copperMm": (s.get("quality") or {}).get("copper_mm"),
                "diffPairCoupling": (s.get("quality") or {}).get("diff_pair_coupling"),
                "powerWidthRatio": (s.get("quality") or {}).get("power_width_ratio"),
                "iterations": s.get("iterations"),
                "nodesExpanded": s.get("nodesExpanded"),
                "claimHonest": s.get("claimHonest"),
                "orphanCopper": len(s.get("orphanCopper") or ()),
            },
            "deterministic": row.get("deterministic"),
            "fingerprints": row.get("fingerprints"),
            "wallClockS": row.get("wallClockS"),
        }
        sv = synth_findings(verify.get((router, instance)))
        base = synth_findings(verify.get((EMPTY, instance)))
        if sv is not None:
            c["pipelineSynthesized"] = sv
            if base is not None:
                c["pipelineSynthesized"]["controlTscircuitRouting"] = base["tscircuitRouting"]
                c["pipelineSynthesized"]["controlKicadCopper"] = base["kicadCopper"]
        rb = real.get((router, instance))
        if rb and rb.get("ok"):
            ctl = real.get((EMPTY, instance)) or {}
            c["pipelineRealBoard"] = {
                "tscircuitRouting": (rb["tscircuit"]["routing"]).get("count"),
                "tscircuitKinds": (rb["tscircuit"]["routing"]).get("kindCounts"),
                "kicadCopper": rb["kicad"].get("copperFindingCount"),
                "kicadKinds": rb["kicad"].get("kindCounts"),
                "controlTscircuitRouting": (
                    ((ctl.get("tscircuit") or {}).get("routing") or {}).get("count")
                ),
                "controlKicadCopper": (ctl.get("kicad") or {}).get("copperFindingCount"),
            }
        cg = (corner.get(router) or {}).get(instance)
        if cg:
            c["padModelBlindSpot"] = {
                "missedShorts": cg["missedShorts"],
                "missedClearance": cg["missedClearance"],
                "worstTrueGapMm": cg["worstMissMm"],
            }
        cells.append(c)

    summary = {}
    for router in routers:
        cs = [c for c in cells if c["router"] == router and c["ok"]]
        n = len(cs)
        if not n:
            continue
        real_cs = [c for c in cs if "pipelineRealBoard" in c]
        summary[router] = {
            "family": FAMILY.get(router, router),
            "instances": n,
            "meanCompleteness": round(
                sum(c["harness"]["completeness"] for c in cs) / n, 4),
            "harnessCleanInstances": sum(1 for c in cs if c["harness"]["clean"]),
            "fullyRouted": sum(1 for c in cs if c["harness"]["completeness"] == 1.0),
            "harnessErrors": sum(c["harness"]["errors"] for c in cs),
            "harnessWarnings": sum(c["harness"]["warnings"] for c in cs),
            "vias": sum(c["harness"]["vias"] for c in cs),
            "copperMm": round(sum(c["harness"]["copperMm"] for c in cs), 1),
            "deterministic": sum(1 for c in cs if c["deterministic"]),
            "claimHonest": sum(1 for c in cs if c["harness"]["claimHonest"]),
            "firstRunSeconds": round(sum((c["wallClockS"] or [0])[0] for c in cs), 1),
            "padModelMissedShorts": sum(
                (c.get("padModelBlindSpot") or {}).get("missedShorts", 0) for c in cs),
            "padModelMissedClearance": sum(
                (c.get("padModelBlindSpot") or {}).get("missedClearance", 0) for c in cs),
            "synthesizedCells": sum(1 for c in cs if "pipelineSynthesized" in c),
            "synthesizedTscircuit": sum(
                (c.get("pipelineSynthesized") or {}).get("tscircuitRouting", 0) for c in cs),
            "synthesizedKicad": sum(
                (c.get("pipelineSynthesized") or {}).get("kicadCopper") or 0 for c in cs),
            "realBoards": len(real_cs),
            "realTscircuit": sum(
                c["pipelineRealBoard"]["tscircuitRouting"] for c in real_cs),
            "realKicad": sum(
                (c["pipelineRealBoard"]["kicadCopper"] or 0)
                - (c["pipelineRealBoard"]["controlKicadCopper"] or 0)
                for c in real_cs),
        }

    winners = {}
    for i in instances:
        best = None
        for c in cells:
            if c["instance"] != i or not c["ok"] or c["router"] not in HEADLINE:
                continue
            key = (
                -c["harness"]["completeness"],
                c["harness"]["errors"],
                (c.get("pipelineRealBoard") or {}).get("kicadCopper", 0) or 0,
                c["harness"]["warnings"],
                c["harness"]["vias"],
                c["harness"]["copperMm"],
            )
            if best is None or key < best[0]:
                best = (key, c["router"])
        winners[i] = best[1] if best else None

    out = {
        "measuredAt": "2026-08-16",
        "rulerHash": ruler,
        "budget": {"maxIterations": 2_000_000, "maxNodes": 20_000_000, "seed": 0},
        "runs": 2,
        "instances": instances,
        "routers": routers,
        "summary": summary,
        "winnerByInstance": winners,
        "cells": cells,
    }
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({len(cells)} cells)")

    hdr = (f"{'router':<24}{'clean':>7}{'mean%':>8}{'err':>5}{'warn':>6}{'vias':>7}"
           f"{'mm':>8}{'det':>7}{'blind-s':>9}{'blind-c':>9}{'synTS':>7}{'synKC':>7}"
           f"{'realTS':>8}{'realKC':>8}{'sec':>7}")
    print(hdr)
    print("-" * len(hdr))
    for r, v in sorted(summary.items(), key=lambda kv: -kv[1]["meanCompleteness"]):
        print(f"{r:<24}{v['harnessCleanInstances']:>4}/{v['instances']:<2}"
              f"{v['meanCompleteness'] * 100:>8.1f}{v['harnessErrors']:>5}"
              f"{v['harnessWarnings']:>6}{v['vias']:>7}{v['copperMm']:>8.0f}"
              f"{v['deterministic']:>4}/{v['instances']:<2}"
              f"{v['padModelMissedShorts']:>9}{v['padModelMissedClearance']:>9}"
              f"{v['synthesizedTscircuit']:>7}{v['synthesizedKicad']:>7}"
              f"{v['realTscircuit']:>8}{v['realKicad']:>8}{v['firstRunSeconds']:>7.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
