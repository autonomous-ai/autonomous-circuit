"""Collect every portfolio number into one committed results file.

Reads what the three measuring runs left on disk and writes
``benchmarks/tournament/portfolio-2026-08-16.json``:

* ``work/portfolio/relay-thorough/suite.json`` — the harness score of the relay
  over all 16 instances, two runs each for determinism;
* ``work/tournament/verify-real/<cell>/portfolio-relay.json`` — the same copper
  through ``@tscircuit/checks`` and ``kicad-cli pcb drc`` on the rebuilt
  composition-cell boards, with the empty-solution control subtracted;
* ``work/portfolio/ab/<board>.json`` — the A/B against the shipped autorouter.

A kicad row that carries an ``error`` or ``skipped`` is read as **no
measurement**, not as zero findings. That is not defensive coding: a
conversion that produced no ``board.kicad_pcb`` left
``copperFindingCount: 0`` on ``matrix-status-led__ws2812-chain`` while this was
being assembled, and the copper it scored clean was byte-identical to copper
another run scored at seven findings.

    python3.12 packages/router/scripts/collect_portfolio_results.py
"""

import json, subprocess
from pathlib import Path

REPO = Path("/Users/d/code/autonomous-circuit")
PKG = REPO / "packages" / "router"
OUT = PKG / "benchmarks" / "tournament" / "portfolio-2026-08-16.json"

TOURN = json.load(open(PKG / "benchmarks/tournament/results-2026-08-16.json"))
cells = {(c["router"], c["instance"]): c for c in TOURN["cells"]}
PRIMARY = ["baseline-pattern", "exact-and-structured", "topological-graph",
           "meta-genetic", "meta-anneal", "ripup-reroute", "plane-and-classes",
           "maze-astar", "pathfinder-negotiated"]
suite = json.load(open(REPO / "work/portfolio/relay-thorough/suite.json"))
rows = {r["instance"]: r for r in suite["rows"]}
vroot = REPO / "work/tournament/verify-real"

head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                      capture_output=True, text=True).stdout.strip()
dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPO,
                            capture_output=True, text=True).stdout.strip())


def net_pipeline(board, router):
    p = vroot / board / f"{router}.json"
    c = vroot / board / "_empty-control.json"
    if not (p.is_file() and c.is_file()):
        return None
    d, ctrl = json.loads(p.read_text()), json.loads(c.read_text())
    if not d.get("ok"):
        return None
    for row in (d, ctrl):
        k = row.get("kicad") or {}
        if k.get("error") or k.get("skipped"):
            # A conversion that produced no board leaves copperFindingCount 0
            # beside an error, and that reads as a clean board.
            return None
    return {
        "kicadCopper": d["kicad"]["copperFindingCount"]
        - ctrl["kicad"]["copperFindingCount"],
        "tscircuitRouting": ((d.get("tscircuit") or {}).get("routing") or {}).get("count", 0)
        - ((ctrl.get("tscircuit") or {}).get("routing") or {}).get("count", 0),
        "kicadKinds": {k: v for k, v in (d["kicad"].get("kindCounts") or {}).items()},
    }


per_instance = []
for inst in sorted(rows):
    r = rows[inst]
    s = r["score"]
    best = max(cells[(x, inst)]["harness"]["completeness"] for x in PRIMARY)
    per_instance.append({
        "instance": inst,
        "chain": [st["router"] for st in r["portfolio"]["stages"]],
        "stages": r["portfolio"]["stages"],
        "completeness": s["completeness"],
        "bestSingleCompleteness": round(best, 6),
        "harnessErrors": s["errors"],
        "harnessWarnings": s["warnings"],
        "vias": s["quality"]["via_count"],
        "copperMm": s["quality"]["copper_mm"],
        "deterministic": r["deterministic"],
        "wallClockS": r["wallClockS"],
        "pipelineRealBoard": net_pipeline(inst, "portfolio-relay"),
    })

ab = {}
for b in ("harness-puck", "hydrate-coaster", "terminal-keyboard"):
    d = json.load(open(REPO / f"work/portfolio/ab/{b}.json"))
    ab[b] = {
        "commit": d["commit"],
        "poursStripped": d["poursStripped"],
        "selection": d["selection"],
        "stages": d["stages"],
        "portfolioSeconds": d["portfolioSeconds"],
        "incumbent": {**d["harness"]["incumbent"],
                      "pipeline": d["pipeline"]["incumbent"]["net"],
                      "kicadKinds": d["pipeline"]["incumbent"]["kicad"]["kindCounts"]},
        "portfolio": {**d["harness"]["portfolio"],
                      "pipeline": d["pipeline"]["portfolio"]["net"],
                      "kicadKinds": d["pipeline"]["portfolio"]["kicad"]["kindCounts"]},
        "control": {
            "kicadCopper": d["pipeline"]["control"]["kicad"]["copperFindingCount"],
            "tscircuitRouting": ((d["pipeline"]["control"].get("tscircuit") or {})
                                 .get("routing") or {}).get("count", 0),
        },
    }

CELLS = [i for i in sorted(rows) if i.startswith("matrix-")
         and net_pipeline(i, "portfolio-relay") is not None]
compare = {}
for r in PRIMARY:
    e = sum(cells[(r, b)]["pipelineRealBoard"]["kicadCopper"]
            - cells[(r, b)]["pipelineRealBoard"]["controlKicadCopper"] for b in CELLS)
    cp = sum(cells[(r, b)]["harness"]["copperMm"] for b in CELLS)
    n = sum(cells[(r, b)]["harness"]["connectedNets"] for b in CELLS)
    comp = sum(cells[(r, b)]["harness"]["completeness"] for b in CELLS) / len(CELLS)
    compare[r] = {"meanCompleteness": round(comp, 4), "kicadCopper": e,
                  "copperMm": round(cp, 1), "connectedNets": n,
                  "errPer100mm": round(100 * e / cp, 2)}
e = sum(net_pipeline(b, "portfolio-relay")["kicadCopper"] for b in CELLS)
cp = sum(rows[b]["score"]["quality"]["copper_mm"] for b in CELLS)
n = sum(rows[b]["score"]["connectedNets"] for b in CELLS)
comp = sum(rows[b]["score"]["completeness"] for b in CELLS) / len(CELLS)
compare["portfolio-relay"] = {"meanCompleteness": round(comp, 4), "kicadCopper": e,
                              "copperMm": round(cp, 1), "connectedNets": n,
                              "errPer100mm": round(100 * e / cp, 2)}

payload = {
    "measuredAt": "2026-08-16",
    "measuredAgainst": {"gitHead": head, "gitDirty": dirty,
                        "tournament": "results-2026-08-16.json",
                        "rulerHash": TOURN["rulerHash"]},
    "mode": "relay",
    "budgetClass": "thorough",
    "chain": ["pathfinder-negotiated", "maze-astar", "plane-and-classes",
              "exact-and-structured"],
    "budget": suite["budget"],
    "summary": {
        "instances": len(per_instance),
        "meanCompleteness": round(
            sum(p["completeness"] for p in per_instance) / len(per_instance), 4),
        "meanBestSingleCompleteness": round(
            sum(p["bestSingleCompleteness"] for p in per_instance) / len(per_instance), 4),
        "harnessCleanInstances": sum(
            1 for p in per_instance if p["completeness"] >= 1.0 and p["harnessErrors"] == 0),
        "harnessErrors": sum(p["harnessErrors"] for p in per_instance),
        "deterministic": sum(1 for p in per_instance if p["deterministic"]),
        "totalSeconds": round(sum(p["wallClockS"] for p in per_instance), 1),
    },
    "perInstance": per_instance,
    "verifiedOnRebuiltCells": {"cells": CELLS, "byRouter": compare},
    "againstShippedAutorouter": ab,
    "method": {
        "relay": "the lead router routes the board; each follower is asked ONLY "
                 "for the nets still unconnected, with the copper already down "
                 "as obstacles (existing_traces / existing_vias)",
        "harness": "routerlib.scoring.score — the same scorer the tournament used, "
                   "with the same known inscribed-stadium pad-model blind spot",
        "pipelineRealBoard": "the portfolio's copper dropped into the rebuilt "
                             "composition-cell board via routerlib.adapters."
                             "apply_solution, then @tscircuit/checks and "
                             "kicad-cli pcb drc; an empty-solution control runs "
                             "on every board and is subtracted",
        "againstShippedAutorouter": "packages/router/scripts/ab_incumbent.py — "
                                    "the board taken from a named git revision, "
                                    "pours stripped, the problem derived from that "
                                    "same file so the placement matches by "
                                    "construction, and three copper sets (control, "
                                    "incumbent, portfolio) through one checker",
        "reproduce": [
            "python3.12 packages/router/portfolio.py suite --mode relay "
            "--budget-class thorough --runs 2 --out work/portfolio/relay-thorough",
            "python3.12 packages/router/scripts/verify_real_board.py --board <cell> "
            "--circuit-json <rebuilt board> --tournament work/tournament "
            "--routers portfolio-relay",
            "python3.12 packages/router/scripts/ab_incumbent.py --board <example> "
            "--rev <commit>",
        ],
    },
}
OUT.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
print("wrote", OUT)
print(json.dumps(payload["summary"], indent=1))
print(json.dumps(compare, indent=1))
