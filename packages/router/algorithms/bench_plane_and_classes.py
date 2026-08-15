"""Run the benchmark for ``plane-and-classes`` without touching the harness.

Identical to ``python -m routerlib run`` in everything that produces a number:
same ``routerlib.bench.run_suite``, same ``routerlib.scoring.score``, same
determinism check, same default budget. It exists only because registering a
router means editing ``routerlib.cli.registry()``, and several algorithm
families are being built against this contract at the same time.

    python3.12 algorithms/bench_plane_and_classes.py --report /tmp/run.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from routerlib.bench import load_all, run_suite  # noqa: E402
from routerlib.model import Budget  # noqa: E402

from plane_and_classes_loader import router_class  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=None)
    parser.add_argument("--only", default=None)
    parser.add_argument("--max-iterations", type=int, default=2_000_000)
    parser.add_argument("--max-nodes", type=int, default=20_000_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--no-determinism", action="store_true")
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)

    problems = load_all(args.dir)
    if args.only:
        wanted = set(args.only.split(","))
        problems = [p for p in problems if p.id in wanted]
    budget = Budget(
        max_iterations=args.max_iterations,
        max_nodes=args.max_nodes,
        seed=args.seed,
    )
    factory = router_class()
    report = run_suite(
        factory,
        problems,
        budget,
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
