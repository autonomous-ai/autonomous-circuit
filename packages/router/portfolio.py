#!/usr/bin/env python3.12
"""The portfolio, from the command line. The selector itself is
``routerlib.portfolio`` — importable, tested, and what the pipeline calls.

    python3.12 packages/router/portfolio.py select --instance harness-puck
    python3.12 packages/router/portfolio.py run --instance harness-puck --mode relay
    python3.12 packages/router/portfolio.py suite --mode relay --out work/portfolio
    python3.12 packages/router/portfolio.py rules

``run`` and ``suite`` load the algorithm families the same way the tournament
judge does — by path, out of ``algorithms/`` — so a family that is registered
for the tournament is in the portfolio with no second registration to forget.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for extra in (HERE / "src", HERE.parent / "circuitpy" / "src", HERE / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def registry() -> dict:
    """Every family the tournament knows, loaded from ``algorithms/``."""
    from judge import registry as judge_registry  # scripts/judge.py

    return judge_registry()


def _problem(instance: str):
    from routerlib.bench import INSTANCE_DIR, load_instance

    return load_instance(Path(INSTANCE_DIR) / f"{instance}.json")


def _budget(args):
    from routerlib.model import Budget

    return Budget(
        max_iterations=args.max_iterations,
        max_nodes=args.max_nodes,
        seed=args.seed,
    )


def cmd_select(args) -> int:
    from routerlib import portfolio
    from routerlib.bench import features_of

    problem = _problem(args.instance)
    features = features_of(problem)
    selection = portfolio.select(
        features, budget_class=args.budget_class, mode=args.mode
    )
    if args.json:
        print(json.dumps(
            {"instance": args.instance, "features": features.as_dict()
             if hasattr(features, "as_dict") else vars(features),
             "selection": selection.as_dict()}, indent=1, default=str))
    else:
        print(f"{args.instance}")
        print(f"  nets={features.routable_net_count} pads={features.pad_count} "
              f"area={features.board_area_mm2:.0f}mm2 "
              f"density={features.pad_density_per_cm2:.1f}/cm2 "
              f"plane={features.has_plane} grid={features.grid_score:.2f}")
        print(f"  -> {selection.line()}")
        print(f"     rule  : {selection.why}")
        print(f"     expect: {selection.expectation.evidence}")
        print(f"     fallback: {selection.fallback}")
    return 0


def cmd_rules(args) -> int:
    from routerlib import portfolio

    print(f"evidence: {portfolio.EVIDENCE}\n")
    print("rules, in order (first that fires wins):")
    for rule in portfolio.RULES:
        print(f"  {rule.name:<20} -> {rule.pick}")
        print(f"      {rule.why}")
    print("\ncandidate order after the lead:", " -> ".join(portfolio.FOLLOWERS))
    print("budget widths:", dict(portfolio.BUDGET_WIDTH))
    print("\nrules tried and REJECTED, with the measurement that killed them:")
    for name, why in portfolio.REJECTED_RULES:
        print(f"  {name}")
        print(f"      {why}")
    return 0


def cmd_run(args) -> int:
    from routerlib import portfolio
    from routerlib.scoring import score

    problem = _problem(args.instance)
    t0 = time.perf_counter()
    result = portfolio.route(
        problem, _budget(args), registry(),
        budget_class=args.budget_class, mode=args.mode,
    )
    seconds = time.perf_counter() - t0
    scored = score(problem, result.solution)
    payload = {
        "instance": args.instance,
        "wallClockS": round(seconds, 3),
        "score": scored.as_dict(),
        "portfolio": result.as_dict(),
        "fingerprint": result.solution.fingerprint(),
    }
    if args.copper_out:
        from routerlib.adapters import solution_to_elements

        out = Path(args.copper_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(solution_to_elements(problem, result.solution)),
            encoding="utf-8",
        )
        payload["copperPath"] = str(out)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, indent=1) + "\n",
                                  encoding="utf-8")
    print(scored.line())
    for stage in result.stages:
        print(f"    {stage.router:<24} asked={stage.asked_nets:>3} "
              f"+{stage.added_nets:>3} nets -> {stage.completeness * 100:5.1f}%  "
              f"{stage.vias:>4} vias {stage.copper_mm:8.1f}mm  ({stage.seconds:.1f}s)")
    for note in result.notes:
        print(f"    note: {note}")
    return 0 if scored.clean else 1


def cmd_suite(args) -> int:
    from routerlib import portfolio
    from routerlib.adapters import solution_to_elements
    from routerlib.bench import INSTANCE_DIR, load_instance
    from routerlib.scoring import score

    paths = sorted(Path(INSTANCE_DIR).glob("*.json"))
    if args.only:
        wanted = set(args.only.split(","))
        paths = [p for p in paths if p.stem in wanted]
    reg = registry()
    budget = _budget(args)
    out_dir = Path(args.out) if args.out else None
    rows = []
    for path in paths:
        problem = load_instance(path)
        t0 = time.perf_counter()
        result = portfolio.route(
            problem, budget, reg, budget_class=args.budget_class, mode=args.mode
        )
        seconds = time.perf_counter() - t0
        scored = score(problem, result.solution)
        fingerprints = [result.solution.fingerprint()]
        if args.runs > 1:
            for _ in range(args.runs - 1):
                again = portfolio.route(
                    problem, budget, reg,
                    budget_class=args.budget_class, mode=args.mode,
                )
                fingerprints.append(again.solution.fingerprint())
        rows.append({
            "instance": problem.id,
            "wallClockS": round(seconds, 3),
            "deterministic": len(set(fingerprints)) == 1,
            "fingerprints": fingerprints,
            "score": scored.as_dict(),
            "portfolio": result.as_dict(),
        })
        print(scored.line() + ("" if rows[-1]["deterministic"] else "  NONDETERMINISTIC"),
              flush=True)
        if out_dir:
            (out_dir / "copper").mkdir(parents=True, exist_ok=True)
            (out_dir / "copper" / f"{problem.id}.json").write_text(
                json.dumps(solution_to_elements(problem, result.solution)),
                encoding="utf-8",
            )
    mean = sum(r["score"]["completeness"] for r in rows) / max(1, len(rows))
    clean = sum(1 for r in rows if r["score"]["clean"])
    print(f"\n{clean}/{len(rows)} clean, {mean * 100:.1f}% mean completeness, "
          f"{sum(r['score']['errors'] for r in rows)} harness errors, "
          f"{sum(1 for r in rows if r['deterministic'])}/{len(rows)} deterministic")
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "suite.json").write_text(
            json.dumps({"mode": args.mode, "budgetClass": args.budget_class,
                        "budget": {"maxIterations": args.max_iterations,
                                   "maxNodes": args.max_nodes, "seed": args.seed},
                        "rows": rows}, indent=1) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {out_dir / 'suite.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="portfolio", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--budget-class", default="standard",
                       choices=("cheap", "standard", "thorough"))
        p.add_argument("--mode", default=None,
                       choices=("single", "best-of-n", "relay"))
        p.add_argument("--max-iterations", type=int, default=2_000_000)
        p.add_argument("--max-nodes", type=int, default=20_000_000)
        p.add_argument("--seed", type=int, default=0)

    p = sub.add_parser("select", help="features in, algorithm out")
    p.add_argument("--instance", required=True)
    p.add_argument("--json", action="store_true")
    p.add_argument("--budget-class", default="standard",
                   choices=("cheap", "standard", "thorough"))
    p.add_argument("--mode", default=None,
                   choices=("single", "best-of-n", "relay"))
    p.set_defaults(func=cmd_select)

    p = sub.add_parser("rules", help="the rules, and the rules that failed")
    p.set_defaults(func=cmd_rules)

    p = sub.add_parser("run", help="route one instance through the portfolio")
    p.add_argument("--instance", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--copper-out", default=None)
    common(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("suite", help="route every instance through the portfolio")
    p.add_argument("--only", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--runs", type=int, default=1,
                   help="repeat each instance and compare fingerprints")
    common(p)
    p.set_defaults(func=cmd_suite)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
