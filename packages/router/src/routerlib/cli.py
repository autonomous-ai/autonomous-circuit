"""``python -m routerlib`` — extract instances, list them, run a router.

Four subcommands:

``extract``   turn a built ``circuit.json`` into a benchmark instance
``list``      the instance table with every measured feature
``run``       route and score the whole suite with one algorithm
``score``     score an already-routed ``circuit.json`` against its instance
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from routerlib.bench import (
    INSTANCE_DIR,
    MANIFEST,
    baseline_of,
    correspondence,
    features_of,
    instance_paths,
    load_all,
    load_instance,
    problem_to_dict,
    run_suite,
    with_ground_plane,
)
from routerlib.model import Budget

#: Every algorithm in the tournament, by name. New entrants register here.
def registry() -> dict:
    from routerlib.baseline import PatternRouter

    return {PatternRouter.name: PatternRouter}


def _extract(args: argparse.Namespace) -> int:
    from routerlib.adapters import problem_from_circuit_json

    source = Path(args.circuit_json).resolve()
    elements = json.loads(source.read_text(encoding="utf-8"))
    problem = problem_from_circuit_json(
        elements, problem_id=args.id, strip_routes=True, strip_planes=True
    )
    out_dir = Path(args.out or INSTANCE_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for candidate in ([problem] + ([with_ground_plane(problem)] if args.plane else [])):
        payload = problem_to_dict(
            candidate,
            source={
                "kind": args.kind,
                "path": str(source),
                "note": args.note or "",
            },
        )
        payload["baseline"] = baseline_of(candidate)
        path = out_dir / f"{candidate.id}.json"
        path.write_text(
            json.dumps(payload, indent=1, sort_keys=False) + "\n", encoding="utf-8"
        )
        written.append(path)
    for path in written:
        print(path)
    return 0


def _list(args: argparse.Namespace) -> int:
    problems = load_all(args.dir)
    if not problems:
        print("no instances", file=sys.stderr)
        return 1
    header = (
        f"{'instance':<34}{'nets':>6}{'pads':>6}{'area':>9}{'pads/cm2':>10}"
        f"{'pitch':>8}{'pairs':>7}{'plane':>7}{'regular':>9}{'demand':>9}"
    )
    print(header)
    print("-" * len(header))
    for problem in problems:
        f = features_of(problem)
        pitch = "-" if f.finest_pin_pitch_mm is None else f"{f.finest_pin_pitch_mm:.2f}"
        print(
            f"{problem.id:<34}{f.routable_net_count:>6}{f.pad_count:>6}"
            f"{f.board_area_mm2:>9.0f}{f.pad_density_per_cm2:>10.1f}"
            f"{pitch:>8}{f.diff_pair_count:>7}{str(f.has_plane):>7}"
            f"{str(f.regular):>9}{f.routing_demand_per_mm2:>9.3f}"
        )
    return 0


def _run(args: argparse.Namespace) -> int:
    routers = registry()
    if args.router not in routers:
        print(
            f"unknown router {args.router!r} (have: {', '.join(sorted(routers))})",
            file=sys.stderr,
        )
        return 2
    factory = routers[args.router]
    problems = load_all(args.dir)
    if args.only:
        wanted = set(args.only.split(","))
        problems = [p for p in problems if p.id in wanted]
    budget = Budget(
        max_iterations=args.max_iterations,
        max_nodes=args.max_nodes,
        seed=args.seed,
    )

    def _progress(score, row) -> None:
        print(score.line(), flush=True)

    report = run_suite(
        factory,
        problems,
        budget,
        check_determinism=not args.no_determinism,
        on_done=_progress,
    )
    print()
    print(report.summary())
    print(report.ruler_line)
    if args.report:
        Path(args.report).write_text(
            json.dumps(report.as_dict(), indent=1) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.report}")
    failed = [r for r in report.rows if not r.get("deterministic", True)]
    return 1 if failed else 0


def _score(args: argparse.Namespace) -> int:
    from routerlib.adapters import problem_from_circuit_json
    from routerlib.scoring import score

    problem = load_instance(args.instance)
    elements = json.loads(Path(args.circuit_json).read_text(encoding="utf-8"))
    routed = problem_from_circuit_json(
        elements, problem_id=problem.id, strip_routes=False, strip_planes=True
    )

    # Before scoring anything: is this the same placed board? An instance is a
    # committed fixture and examples/ is rebuilt several times a day. Scoring
    # copper from a rebuilt board against a stale instance prints "0.0% routed"
    # — a number that looks like a verdict on the router and is not one.
    match = correspondence(problem, routed)
    if not match.matches or not match.copper_elements:
        print(match.report(), file=sys.stderr)
        if not args.allow_drift:
            print(
                "refusing to score — pass --allow-drift to score anyway, "
                "knowing the number is not about the router",
                file=sys.stderr,
            )
            return 2

    from routerlib.model import RoutingSolution

    solution = RoutingSolution(
        router=args.router_name,
        traces=routed.existing_traces,
        vias=routed.existing_vias,
        complete=False,
    )
    result = score(problem, solution)
    print(json.dumps(result.as_dict(), indent=1))
    return 0 if result.clean else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="routerlib", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="circuit.json -> benchmark instance")
    p.add_argument("circuit_json")
    p.add_argument("--id", required=True)
    p.add_argument("--kind", default="example")
    p.add_argument("--note", default="")
    p.add_argument("--out", default=None)
    p.add_argument("--plane", action="store_true",
                   help="also emit a variant with a synthetic ground plane")
    p.set_defaults(func=_extract)

    p = sub.add_parser("list", help="the instance table")
    p.add_argument("--dir", default=None)
    p.set_defaults(func=_list)

    p = sub.add_parser("run", help="route + score the suite")
    p.add_argument("--router", default="baseline-pattern")
    p.add_argument("--dir", default=None)
    p.add_argument("--only", default=None)
    p.add_argument("--max-iterations", type=int, default=2_000_000)
    p.add_argument("--max-nodes", type=int, default=20_000_000)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--no-determinism", action="store_true")
    p.add_argument("--report", default=None)
    p.set_defaults(func=_run)

    p = sub.add_parser("score", help="score a routed circuit.json")
    p.add_argument("instance")
    p.add_argument("circuit_json")
    p.add_argument("--router-name", default="external")
    p.add_argument(
        "--allow-drift",
        action="store_true",
        help="score even when the board is not the instance's placement",
    )
    p.set_defaults(func=_score)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
