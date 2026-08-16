#!/usr/bin/env python3
"""A/B the shipped autorouter against the *compositions*, on the real boards.

``ab_incumbent.py`` can put a single family or the portfolio selector against
the incumbent. It cannot put a composition there, because a composition is not
in the tournament registry — it is a router that calls routers. That is the one
thing this adds, and everything else is imported from ``ab_incumbent`` so the
two scripts cannot drift about what a control is or which findings are copper.

    python3.12 packages/router/scripts/ab_compositions.py --board harness-puck \
        --arms relay,spatial-best,netclass:pairs,recombine --out work/ab

Arms:

``relay``           the four-router chain, the previous best
``spatial-best``    escapes before crossings, then the relay's chain
``netclass:<plan>`` net-class decomposition at a named plan
``recombine``       all nine families **and** the relay on this board, merged
                    net by net with eviction repair — the benchmark winner
anything else       one family from the tournament registry

Every arm goes through the identical control-subtracted pipeline check the
incumbent does, so the only thing different between two rows is the copper.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
REPO = PACKAGE.parent.parent
for extra in (PACKAGE / "src", PACKAGE.parent / "circuitpy" / "src", HERE):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import ab_incumbent as ab  # noqa: E402

#: The relay's chain, as measured everywhere else in this package.
CHAIN = ("pathfinder-negotiated", "maze-astar", "plane-and-classes",
         "exact-and-structured")

#: The nine tournament families, in the order the tournament lists them.
FAMILIES = (
    "baseline-pattern", "maze-astar", "pathfinder-negotiated", "ripup-reroute",
    "meta-anneal", "meta-genetic", "topological-graph", "exact-and-structured",
    "plane-and-classes",
)


def solve(arm: str, problem, budget, registry):
    """``(solution, detail)`` for one arm on one board."""
    from routerlib.compositions.recombine import recombine, relay_residual

    if arm == "relay":
        return relay_residual(registry, CHAIN)(problem, budget), {"chain": list(CHAIN)}

    if arm.startswith("spatial"):
        import spatial_suite

        solution, detail = spatial_suite.run_one(arm, problem, budget, registry)
        return solution, detail

    if arm.startswith("netclass:"):
        from routerlib.compositions import netclass as nc

        result = nc.compose(problem, budget, registry, plan=arm.split(":", 1)[1])
        return result.solution, {"stages": [s.as_dict() for s in result.stages]}

    if arm == "recombine":
        # The merge needs finished boards to cut apart, so this arm pays for
        # ten of them. That is the honest cost of the benchmark's best arm on
        # a board nobody has routed yet, and it is reported as such.
        inputs = {}
        timings = {}
        for family in FAMILIES:
            t0 = time.perf_counter()
            inputs[family] = registry[family]().route(problem, budget)
            timings[family] = round(time.perf_counter() - t0, 1)
        t0 = time.perf_counter()
        inputs["relay"] = relay_residual(registry, CHAIN)(problem, budget)
        timings["relay"] = round(time.perf_counter() - t0, 1)
        result = recombine(
            problem, inputs, mode="anchored", ranking="obstruction",
            max_evictions=2, max_reroutes=16,
            reroute=relay_residual(registry, ["maze-astar"]),
        )
        return result.solution, {"inputSeconds": timings, **result.as_dict()}

    if arm not in registry:
        raise KeyError(f"unknown arm {arm!r}")
    return registry[arm]().route(problem, budget), {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True)
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--arms", default="relay,spatial-best,netclass:pairs,recombine")
    ap.add_argument("--out", default=str(REPO / "work" / "ab-compositions"))
    ap.add_argument("--max-iterations", type=int, default=2_000_000)
    ap.add_argument("--max-nodes", type=int, default=20_000_000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    from routerlib.adapters import problem_from_circuit_json
    from routerlib.bench import features_of
    from routerlib.model import Budget, RoutingSolution
    from routerlib.scoring import score

    from judge import registry

    board_json, sha = ab.board_at(args.rev, args.board)
    pours = sum(1 for e in board_json if e.get("type") == "pcb_copper_pour")
    board_json = [e for e in board_json if e.get("type") != "pcb_copper_pour"]

    problem = problem_from_circuit_json(
        board_json, problem_id=args.board, strip_routes=True, strip_planes=True
    )
    with_copper = problem_from_circuit_json(
        board_json, problem_id=args.board, strip_routes=False, strip_planes=True
    )
    incumbent = RoutingSolution(
        router="tscircuit-autorouter", traces=with_copper.existing_traces,
        vias=with_copper.existing_vias, complete=False,
    )
    empty = RoutingSolution(router="empty", traces=(), vias=(), complete=False)

    budget = Budget(max_iterations=args.max_iterations,
                    max_nodes=args.max_nodes, seed=args.seed)
    reg = registry()

    ours: list[tuple[str, RoutingSolution]] = []
    meta: dict = {}
    for arm in [a for a in args.arms.split(",") if a]:
        t0 = time.perf_counter()
        solution, detail = solve(arm, problem, budget, reg)
        meta[arm] = {
            "seconds": round(time.perf_counter() - t0, 1),
            "fingerprint": solution.fingerprint(),
            "detail": detail,
        }
        ours.append((arm, solution))
        print(f"  routed {arm} in {meta[arm]['seconds']}s", flush=True)

    out: dict = {
        "schema": "routerlib/ab-compositions@1",
        "board": args.board,
        "rev": args.rev,
        "commit": sha,
        "poursStripped": pours,
        "budget": {"maxIterations": args.max_iterations,
                   "maxNodes": args.max_nodes, "seed": args.seed},
        "features": vars(features_of(problem)),
        "ours": meta,
        "harness": {},
        "pipeline": {},
    }
    for name, solution in [("incumbent", incumbent)] + ours:
        s = score(problem, solution)
        out["harness"][name] = {
            "completeness": round(s.completeness, 6),
            "connectedNets": s.connected_nets,
            "routableNets": s.routable_nets,
            "errors": s.errors,
            "warnings": s.warnings,
            "byKind": s.violations_by_kind,
            "vias": s.quality.via_count,
            "copperMm": round(s.quality.copper_mm, 1),
            "diffPairCoupling": s.quality.diff_pair_coupling,
            "rulerHash": s.ruler.hash,
        }

    for name, solution in ([("control", empty), ("incumbent", incumbent)] + ours):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        work = Path(tempfile.mkdtemp(prefix=f"abc-{safe}-"))
        t = time.perf_counter()
        try:
            out["pipeline"][name] = ab.check(board_json, problem, solution, work)
        except BaseException as exc:  # noqa: BLE001
            out["pipeline"][name] = {"error": f"{type(exc).__name__}: {exc}",
                                     "traceback": traceback.format_exc()[-2000:]}
        finally:
            shutil.rmtree(work, ignore_errors=True)
        out["pipeline"][name]["seconds"] = round(time.perf_counter() - t, 1)
        print(f"  checked {name} in {out['pipeline'][name]['seconds']}s", flush=True)

    def kicad_count(rowd: dict):
        k = rowd.get("kicad") or {}
        if k.get("error") or k.get("skipped"):
            return None
        return k.get("copperFindingCount")

    ctrl = out["pipeline"]["control"]
    ck = kicad_count(ctrl)
    ct = ab.routing_count(ctrl)
    for name in ["incumbent"] + [n for n, _ in ours]:
        rowd = out["pipeline"][name]
        k = kicad_count(rowd)
        t_ = ab.routing_count(rowd)
        rowd["net"] = {
            "kicadCopper": None if (k is None or ck is None) else k - ck,
            "tscircuitRouting": None if (t_ is None or ct is None) else t_ - ct,
        }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{args.board}.json"
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")

    h = out["harness"]
    print(f"\n{args.board} @ {sha[:9]}  ({pours} pour(s) stripped)  "
          f"ruler {h['incumbent']['rulerHash']}")
    print(f"{'':<24}{'routed':>9}{'harnessErr':>12}{'kicadErr':>10}{'tsErr':>8}"
          f"{'vias':>7}{'copper mm':>11}{'coupling':>10}")
    for name in ["incumbent"] + [n for n, _ in ours]:
        n_ = out["pipeline"][name]["net"]
        cpl = h[name]["diffPairCoupling"]
        print(f"{name:<24}{h[name]['completeness'] * 100:>8.1f}%"
              f"{h[name]['errors']:>12}{str(n_['kicadCopper']):>10}"
              f"{str(n_['tscircuitRouting']):>8}{h[name]['vias']:>7}"
              f"{h[name]['copperMm']:>11.0f}"
              f"{('—' if cpl is None else f'{cpl * 100:.0f}%'):>10}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
