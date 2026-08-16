#!/usr/bin/env python3.12
"""Run net-class decomposition over the benchmark and print the stage table.

    python3.12 packages/router/scripts/netclass_suite.py plans
    python3.12 packages/router/scripts/netclass_suite.py run \
        --instance matrix-ldo-3v3__rp2040-core__usb-c-power-plane --plan default
    python3.12 packages/router/scripts/netclass_suite.py suite \
        --plan default,plane-last,monolithic --runs 2 \
        --out work/netclass/suite.json

``suite`` scores every instance with ``routerlib.scoring`` and prints the ruler
beside the numbers, because a score without its ruler is not a score. ``--runs``
above one re-routes and compares fingerprints: a composition that is not
deterministic cannot be compared to anything, including itself.

One instance per subprocess is *not* done here — a family that hangs takes the
run with it. Use ``--only`` to bisect, or ``scripts/judge.py`` when a family is
suspect.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
for extra in (PACKAGE / "src", PACKAGE.parent / "circuitpy" / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _budget(args):
    from routerlib.model import Budget

    return Budget(
        max_iterations=args.max_iterations,
        max_nodes=args.max_nodes,
        seed=args.seed,
    )


def _instances(only: str | None):
    from routerlib.bench import INSTANCE_DIR

    paths = sorted(Path(INSTANCE_DIR).glob("*.json"))
    if only:
        wanted = set(only.split(","))
        paths = [p for p in paths if p.stem in wanted]
        missing = wanted - {p.stem for p in paths}
        if missing:
            raise SystemExit(f"no such instance(s): {', '.join(sorted(missing))}")
    return paths


def cmd_plans(args) -> int:
    from routerlib.compositions import netclass as nc

    for name, plan in nc.PLANS.items():
        print(f"{name}")
        for stage in plan:
            what = "everything left" if stage.rest else (
                f"class={'|'.join(stage.classes) or 'any'}"
                + ("" if stage.planed is None
                   else f" planed={stage.planed}")
            )
            print(f"    {stage.label:<8} -> {stage.router:<22} {what}")
    return 0


def _run_one(problem, budget, registry, plan_name, runs: int):
    from routerlib.compositions import netclass as nc
    from routerlib.scoring import score

    t0 = time.perf_counter()
    result = nc.compose(problem, budget, registry, plan=plan_name)
    seconds = time.perf_counter() - t0
    scored = score(problem, result.solution)
    fingerprints = [result.solution.fingerprint()]
    for _ in range(max(0, runs - 1)):
        again = nc.compose(problem, budget, registry, plan=plan_name)
        fingerprints.append(again.solution.fingerprint())
    return result, scored, seconds, fingerprints


def cmd_run(args) -> int:
    from routerlib.bench import INSTANCE_DIR, load_instance
    from routerlib.compositions.registry import load_algorithms

    problem = load_instance(Path(INSTANCE_DIR) / f"{args.instance}.json")
    result, scored, seconds, fingerprints = _run_one(
        problem, _budget(args), load_algorithms(), args.plan, args.runs
    )
    print(scored.line())
    print(result.table())
    print(f"    wall {seconds:.1f}s  deterministic="
          f"{len(set(fingerprints)) == 1}")
    print(scored.ruler.line())
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps({"instance": problem.id, "score": scored.as_dict(),
                        "netclass": result.as_dict(),
                        "fingerprints": fingerprints}, indent=1) + "\n",
            encoding="utf-8",
        )
    return 0 if scored.clean else 1


def cmd_suite(args) -> int:
    from routerlib.bench import load_instance
    from routerlib.compositions.registry import load_algorithms

    registry = load_algorithms()
    budget = _budget(args)
    paths = _instances(args.only)
    plans = args.plan.split(",")
    payload: dict = {
        "budget": {"maxIterations": args.max_iterations,
                   "maxNodes": args.max_nodes, "seed": args.seed},
        "plans": {},
    }
    for plan_name in plans:
        rows = []
        print(f"\n=== plan {plan_name} ===", flush=True)
        for path in paths:
            problem = load_instance(path)
            result, scored, seconds, fingerprints = _run_one(
                problem, budget, registry, plan_name, args.runs
            )
            deterministic = len(set(fingerprints)) == 1
            rows.append({
                "instance": problem.id,
                "wallClockS": round(seconds, 3),
                "deterministic": deterministic,
                "fingerprints": fingerprints,
                "score": scored.as_dict(),
                "netclass": result.as_dict(),
            })
            print(scored.line() + ("" if deterministic else "  NONDETERMINISTIC"),
                  flush=True)
            if args.stages:
                print(result.table(), flush=True)
        mean = sum(r["score"]["completeness"] for r in rows) / max(1, len(rows))
        clean = sum(1 for r in rows if r["score"]["clean"])
        errors = sum(r["score"]["errors"] for r in rows)
        det = sum(1 for r in rows if r["deterministic"])
        print(f"{plan_name}: {mean * 100:.1f}% mean completeness, {clean}/{len(rows)} "
              f"clean, {errors} harness errors, {det}/{len(rows)} deterministic, "
              f"{sum(r['wallClockS'] for r in rows):.0f}s", flush=True)
        payload["plans"][plan_name] = {
            "rows": rows,
            "meanCompleteness": round(mean, 6),
            "clean": clean,
            "harnessErrors": errors,
            "deterministic": det,
            "instances": len(rows),
            "wallClockS": round(sum(r["wallClockS"] for r in rows), 1),
        }
    if paths:
        from routerlib.bench import load_instance as _li
        from routerlib.scoring import ruler_for

        payload["ruler"] = ruler_for(_li(paths[0]).rules).hash
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, indent=1) + "\n",
                                  encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="netclass_suite", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--max-iterations", type=int, default=2_000_000)
        p.add_argument("--max-nodes", type=int, default=20_000_000)
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--runs", type=int, default=1)
        p.add_argument("--out", default=None)

    p = sub.add_parser("plans", help="the named plans and their stages")
    p.set_defaults(func=cmd_plans)

    p = sub.add_parser("run", help="one instance, one plan, with the stage table")
    p.add_argument("--instance", required=True)
    p.add_argument("--plan", default="default")
    common(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("suite", help="every instance, one or more plans")
    p.add_argument("--plan", default="default")
    p.add_argument("--only", default=None)
    p.add_argument("--stages", action="store_true",
                   help="print the per-stage table for every instance")
    common(p)
    p.set_defaults(func=cmd_suite)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
