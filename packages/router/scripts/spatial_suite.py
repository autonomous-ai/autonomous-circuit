#!/usr/bin/env python3.12
"""Measure spatial decomposition against the relay, on one ruler.

Four arms, same benchmark, same budget, same seed. The arms exist to separate
two effects that a single "spatial vs relay" number would confound:

``relay``
    The number to beat: ``routerlib.portfolio`` in relay mode. Re-measured
    here rather than quoted, because the published 98.0% was taken against
    ruler ``b3c77d55b171`` — the inscribed-stadium pad model — and a score is
    only comparable to a score taken with the same ruler.

``spatial``
    Crossings first, then each region to the expert its character names.

``spatial-flat``
    The identical partition and the identical order, every stage routed by the
    global router. The difference between this and ``spatial`` is what the
    expert *table* is worth; the difference between this and ``relay`` is what
    the crossing-first *ordering* is worth.

``spatial-residue``
    ``spatial`` plus the relay's follower chain on whatever is left, so the
    comparison with ``relay`` is four routers against four routers.

    python3.12 packages/router/scripts/spatial_suite.py --arm spatial \
        --out work/spatial/spatial.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
for extra in (PACKAGE / "src", PACKAGE.parent / "circuitpy" / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

ARMS = ("relay", "spatial", "spatial-flat", "spatial-residue")

#: The relay's chain, so ``spatial-residue`` and ``relay`` run four routers each.
FOLLOWERS = ("maze-astar", "plane-and-classes", "exact-and-structured")


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=PACKAGE, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - provenance is best effort, never a gate
        return "unknown"


def ruler_hash() -> str:
    from routerlib.model import DesignRules
    from routerlib.scoring import ruler_for

    return ruler_for(DesignRules.jlcpcb()).hash


def uniquified(solution):
    """The same copper with every id made unique.

    Geometry untouched, so this is the *honest* score of the same board.
    :mod:`routerlib.connectivity` unions ``(copper id, layer)`` nodes and
    :mod:`routerlib.drc` skips a pair when the two ids match, so two stages
    that both mint ``v0`` are one node carrying two nets — a connection that
    does not exist and a short nobody checked. Scoring a solution twice, once
    as returned and once like this, prices exactly that.
    """
    import dataclasses

    return dataclasses.replace(
        solution,
        traces=tuple(dataclasses.replace(t, id=f"{i}!{t.id}")
                     for i, t in enumerate(solution.traces)),
        vias=tuple(dataclasses.replace(v, id=f"{i}!{v.id}")
                   for i, v in enumerate(solution.vias)),
    )


def run_one(arm: str, problem, budget, registry):
    """One instance, one arm. Returns ``(solution, detail_dict)``."""
    from routerlib import portfolio
    from routerlib.compositions import spatial

    if arm == "relay":
        result = portfolio.route(
            problem, budget, registry, budget_class="thorough", mode="relay"
        )
        return result.solution, {"portfolio": result.as_dict()}

    options: dict = {}
    if arm == "spatial-flat":
        options["experts"] = {}
    if arm == "spatial-residue":
        options["residue"] = FOLLOWERS
    result = spatial.route(problem, budget, registry, **options)
    return result.solution, {"spatial": result.as_dict()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", required=True, choices=ARMS)
    ap.add_argument("--only", default=None, help="comma-separated instance ids")
    ap.add_argument("--out", default=None)
    ap.add_argument("--runs", type=int, default=1,
                    help="repeat each instance and compare fingerprints")
    ap.add_argument("--max-iterations", type=int, default=2_000_000)
    ap.add_argument("--max-nodes", type=int, default=20_000_000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    from routerlib.bench import INSTANCE_DIR, load_instance
    from routerlib.compositions.registry import load_algorithms
    from routerlib.drc import copper_items
    from routerlib.model import Budget
    from routerlib.scoring import score

    paths = sorted(Path(INSTANCE_DIR).glob("*.json"))
    if args.only:
        wanted = set(args.only.split(","))
        paths = [p for p in paths if p.stem in wanted]

    registry = load_algorithms()
    budget = Budget(max_iterations=args.max_iterations,
                    max_nodes=args.max_nodes, seed=args.seed)
    ruler_before = ruler_hash()
    rows = []
    for path in paths:
        problem = load_instance(path)
        started = time.perf_counter()
        solution, detail = run_one(args.arm, problem, budget, registry)
        seconds = time.perf_counter() - started
        scored = score(problem, solution)
        fingerprints = [solution.fingerprint()]
        for _ in range(args.runs - 1):
            again, _ = run_one(args.arm, problem, budget, registry)
            fingerprints.append(again.fingerprint())
        ids = [item.id for item in copper_items(problem, solution)]
        honest = score(problem, uniquified(solution))
        row = {
            "instance": problem.id,
            "arm": args.arm,
            "wallClockS": round(seconds, 3),
            "deterministic": len(set(fingerprints)) == 1,
            "fingerprints": fingerprints,
            # A colliding copper id is a false connection and an unchecked
            # short at once, so it is counted beside the score rather than
            # assumed away, and the same copper is scored again with unique
            # ids to price what the collisions were worth.
            "collidingCopperIds": len(ids) - len(set(ids)),
            "score": scored.as_dict(),
            "scoreWithUniqueIds": {
                "completeness": round(honest.completeness, 6),
                "connectedNets": honest.connected_nets,
                "errors": honest.errors,
                "warnings": honest.warnings,
                "clean": honest.clean,
            },
            **detail,
        }
        rows.append(row)
        extra = ""
        if row["collidingCopperIds"]:
            extra += (
                f"  {row['collidingCopperIds']} COLLIDING IDS "
                f"(honest: {honest.completeness * 100:.1f}% / {honest.errors} err)"
            )
        if not row["deterministic"]:
            extra += "  NONDETERMINISTIC"
        print(scored.line() + extra, flush=True)
        if args.arm.startswith("spatial"):
            part = detail["spatial"]["partition"]
            print(f"    partition: {len(part['regions'])} regions, "
                  f"{part['interiorNets']}/{part['routableNets']} interior, "
                  f"seam={part['seam']}", flush=True)
        for stage in detail.get("spatial", detail.get("portfolio", {})).get("stages", []):
            label = stage.get("stage", stage.get("router"))
            print(f"      {label:<16} {stage['router']:<24} "
                  f"asked={stage['asked_nets']:>3} +{stage['added_nets']:>3} "
                  f"-> {stage['completeness'] * 100:5.1f}%  ({stage['seconds']:.1f}s)",
                  flush=True)

    ruler_after = ruler_hash()
    mean = sum(r["score"]["completeness"] for r in rows) / max(1, len(rows))
    honest_mean = sum(
        r["scoreWithUniqueIds"]["completeness"] for r in rows
    ) / max(1, len(rows))
    summary = {
        "arm": args.arm,
        "instances": len(rows),
        "meanCompleteness": round(mean, 4),
        "meanCompletenessWithUniqueIds": round(honest_mean, 4),
        "cleanInstances": sum(1 for r in rows if r["score"]["clean"]),
        "cleanInstancesWithUniqueIds": sum(
            1 for r in rows if r["scoreWithUniqueIds"]["clean"]
        ),
        "harnessErrors": sum(r["score"]["errors"] for r in rows),
        "harnessErrorsWithUniqueIds": sum(
            r["scoreWithUniqueIds"]["errors"] for r in rows
        ),
        "deterministic": sum(1 for r in rows if r["deterministic"]),
        "collidingCopperIds": sum(r["collidingCopperIds"] for r in rows),
        "totalSeconds": round(sum(r["wallClockS"] for r in rows), 1),
    }
    print("\n" + json.dumps(summary))
    payload = {
        "schema": "routerlib/spatial-suite@1",
        "measuredAgainst": {
            "rulerHashBefore": ruler_before,
            "rulerHashAfter": ruler_after,
            "rulerStable": ruler_before == ruler_after,
            "gitHead": _git("rev-parse", "--short", "HEAD"),
            "gitDirty": bool(_git("status", "--porcelain")),
        },
        "budget": {"maxIterations": args.max_iterations,
                   "maxNodes": args.max_nodes, "seed": args.seed},
        "summary": summary,
        "rows": rows,
    }
    if ruler_before != ruler_after:
        print(f"WARNING: the ruler moved mid-run, {ruler_before} -> {ruler_after}; "
              f"this run is not a measurement", file=sys.stderr)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
