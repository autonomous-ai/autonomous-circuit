#!/usr/bin/env python3
"""The relay, measured against the corrected pad model, on all 16 instances.

The 98.0% in ``docs/architecture/routing.md`` was measured with the
inscribed-stadium pad model, and two of the four families in the chain route
very differently once their workspace stops lying to them — ``pathfinder``
alone lost 12.8 points of completeness to the correction. So the relay's number
under the new ruler is not known, and without it there is nothing to compare a
recombination against. This produces it, from the same chain, the same budget
and the same seed.

    python3.12 packages/router/scripts/relay_baseline.py \
        --out work/recombine/relay-truepads.json

Serial by choice: the machine is shared and a benchmark that starves another
agent's build is a worse outcome than a slower benchmark.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for extra in (REPO / "packages" / "router" / "src", REPO / "packages" / "circuitpy" / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

os.environ.setdefault("CIRCUIT_PARTS_ENGINE", "off")

#: The chain measured in the routing document, in its order.
CHAIN = (
    "pathfinder-negotiated",
    "maze-astar",
    "plane-and-classes",
    "exact-and-structured",
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", default=",".join(CHAIN))
    ap.add_argument("--instances", default=None)
    ap.add_argument("--max-iterations", type=int, default=2_000_000)
    ap.add_argument("--max-nodes", type=int, default=20_000_000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--cells-out", default=None,
        help=(
            "write the relay's copper as a tournament family, so a composition "
            "can take the relay as an input rather than only as a rival"
        ),
    )
    args = ap.parse_args(argv)

    from routerlib import connectivity as conn
    from routerlib.bench import INSTANCE_DIR, load_instance, placement_hash
    from routerlib.compositions.recombine import relay_residual
    from routerlib.compositions.registry import load_algorithms
    from routerlib.model import Budget, RoutingSolution
    from routerlib.scoring import score

    registry = load_algorithms()
    chain = [c for c in args.chain.split(",") if c]
    missing = [c for c in chain if c not in registry]
    if missing:
        print(f"not registered: {', '.join(missing)}", file=sys.stderr)
        return 2
    budget = Budget(
        max_iterations=args.max_iterations,
        max_nodes=args.max_nodes,
        seed=args.seed,
    )
    wanted = set(args.instances.split(",")) if args.instances else None

    relay = relay_residual(registry, chain)
    rows: list[dict] = []
    ruler = None
    for path in sorted(INSTANCE_DIR.glob("*.json")):
        instance = path.stem
        if wanted and instance not in wanted:
            continue
        problem = load_instance(path)
        t0 = time.perf_counter()
        solution = relay(problem, budget)
        seconds = time.perf_counter() - t0
        linked = conn.analyse(problem, solution)
        solution = RoutingSolution(
            router="relay[" + "+".join(chain) + "]",
            traces=solution.traces,
            vias=solution.vias,
            complete=linked.completeness >= 1.0,
            unrouted_nets=linked.unconnected_nets,
            wall_clock_s=seconds,
        )
        scored = score(problem, solution)
        ruler = ruler or scored.ruler
        if args.cells_out:
            from routerlib.adapters import solution_to_elements

            cells = Path(args.cells_out)
            copper = cells / "copper" / "relay"
            rowdir = cells / "rows" / "relay"
            copper.mkdir(parents=True, exist_ok=True)
            rowdir.mkdir(parents=True, exist_ok=True)
            (copper / f"{instance}.json").write_text(
                json.dumps(solution_to_elements(problem, solution), indent=1) + "\n",
                encoding="utf-8",
            )
            (rowdir / f"{instance}.json").write_text(
                json.dumps(
                    {
                        "router": "relay",
                        "instance": instance,
                        "ok": True,
                        "placementHash": placement_hash(problem),
                        "traceCount": len(solution.traces),
                        "viaCount": len(solution.vias),
                    },
                    indent=1,
                )
                + "\n",
                encoding="utf-8",
            )
        rows.append({
            "instance": instance,
            "placementHash": placement_hash(problem),
            "routableNets": len(problem.routable_nets),
            "completeness": round(scored.completeness, 6),
            "errors": scored.errors,
            "warnings": scored.warnings,
            "errorKinds": scored.error_kinds,
            "vias": scored.quality.via_count,
            "copperMm": scored.quality.copper_mm,
            "clean": scored.clean,
            "seconds": round(seconds, 3),
            "fingerprint": solution.fingerprint(),
        })
        print(
            f"{instance:<46} {scored.completeness * 100:5.1f}%  "
            f"{scored.errors:>3} err {scored.warnings:>3} warn  "
            f"{scored.quality.via_count:>4} vias  {seconds:6.1f}s",
            flush=True,
        )

    if rows:
        total = sum(r["routableNets"] for r in rows)
        connected = sum(r["completeness"] * r["routableNets"] for r in rows)
        print()
        print(
            f"relay {connected:.0f}/{total} nets ({connected / total * 100:.1f}%), "
            f"{sum(r['errors'] for r in rows)} errors, "
            f"{sum(1 for r in rows if r['clean'])}/{len(rows)} clean, "
            f"{sum(r['vias'] for r in rows)} vias, "
            f"{sum(r['seconds'] for r in rows):.0f}s"
        )
        if ruler:
            print(ruler.line())
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "schema": "routerlib/relay-baseline@1",
                    "chain": chain,
                    "budget": {
                        "maxIterations": args.max_iterations,
                        "maxNodes": args.max_nodes,
                        "seed": args.seed,
                    },
                    "rulerHash": ruler.hash if ruler else None,
                    "rows": rows,
                },
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
