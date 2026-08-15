"""Run one algorithms/ router over the benchmark, using the harness as-is.

``routerlib.cli.registry()`` is shared by every family in the tournament, so a
family that edits it to add itself collides with the three families doing the
same thing on the same afternoon. This runner calls ``routerlib.bench.run_suite``
directly instead — the same runner, the same scorer, the same ruler, no edit to
anything the other families read.

    python3.12 algorithms/bench.py --router ripup-reroute --report /tmp/rr.json
    python3.12 algorithms/bench.py --router ripup-greedy-control --only harness-puck
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
for extra in (PACKAGE / "src", PACKAGE.parent / "circuitpy" / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def load_routers(path: Path) -> dict:
    """Import an algorithm file whose name is not a Python identifier."""
    spec = importlib.util.spec_from_file_location(
        f"_algo_{path.stem.replace('-', '_')}", path
    )
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return dict(getattr(module, "ROUTERS", {}))


def registry() -> dict:
    out: dict = {}
    for path in sorted(HERE.glob("*.py")):
        if path.name == "bench.py":
            continue
        try:
            out.update(load_routers(path))
        except Exception as exc:  # noqa: BLE001 - one bad file must not hide the rest
            print(f"skipping {path.name}: {exc}", file=sys.stderr)
    from routerlib.baseline import PatternRouter

    out.setdefault(PatternRouter.name, PatternRouter)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="algorithms/bench.py", description=__doc__)
    parser.add_argument("--router", default="ripup-reroute")
    parser.add_argument("--dir", default=None)
    parser.add_argument("--only", default=None)
    parser.add_argument("--max-iterations", type=int, default=2_000_000)
    parser.add_argument("--max-nodes", type=int, default=20_000_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--no-determinism", action="store_true")
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)

    from routerlib.bench import load_all, run_suite
    from routerlib.model import Budget

    routers = registry()
    if args.router not in routers:
        print(f"unknown router {args.router!r} (have: {', '.join(sorted(routers))})",
              file=sys.stderr)
        return 2
    problems = load_all(args.dir)
    if args.only:
        wanted = set(args.only.split(","))
        problems = [p for p in problems if p.id in wanted]
    budget = Budget(max_iterations=args.max_iterations,
                    max_nodes=args.max_nodes, seed=args.seed)

    report = run_suite(
        routers[args.router], problems, budget,
        check_determinism=not args.no_determinism,
        on_done=lambda score, row: print(score.line(), flush=True),
    )
    print()
    print(report.summary())
    print(report.ruler_line)
    if args.report:
        Path(args.report).write_text(
            json.dumps(report.as_dict(), indent=1) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.report}")
    return 1 if any(not r.get("deterministic", True) for r in report.rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
