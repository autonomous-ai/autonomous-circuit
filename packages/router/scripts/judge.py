"""Independent judge for the router tournament.

Runs one (router, instance) pair in its own process, twice, and writes a JSON
row: the score, the determinism verdict, the wall clock, and the routed
circuit.json elements so a *separate* pass can take the copper through the real
pipeline (circuitpy DFM + kicad-cli DRC) rather than the harness's own scorer.

Deliberately one pair per process:

* a router that hangs or dies takes down one cell, not the tournament
* no algorithm module can leak state into another family's run
* the wall-clock safety valve is per-pair, as the contract intends

    python3.12 scripts/judge.py --router maze-astar --instance harness-puck \
        --out work/tournament/maze-astar/harness-puck.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
ALGOS = PACKAGE / "algorithms"
for extra in (PACKAGE / "src", PACKAGE.parent / "circuitpy" / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def load_routers(path: Path) -> dict:
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
    for path in sorted(ALGOS.glob("*.py")):
        if path.name in {"bench.py", "bench_plane_and_classes.py"}:
            continue
        try:
            out.update(load_routers(path))
        except Exception as exc:  # noqa: BLE001
            print(f"skipping {path.name}: {exc}", file=sys.stderr)
    from routerlib.baseline import PatternRouter

    out.setdefault(PatternRouter.name, PatternRouter)
    return out


def _payload_hash(problem, solution) -> tuple[str, list]:
    from routerlib.adapters import solution_to_elements

    elements = solution_to_elements(problem, solution)
    blob = json.dumps(elements, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest(), elements


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--router", required=True)
    ap.add_argument("--instance", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--copper-out", default=None)
    ap.add_argument("--max-iterations", type=int, default=2_000_000)
    ap.add_argument("--max-nodes", type=int, default=20_000_000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args(argv)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    row: dict = {
        "router": args.router,
        "instance": args.instance,
        "budget": {
            "maxIterations": args.max_iterations,
            "maxNodes": args.max_nodes,
            "seed": args.seed,
        },
    }
    try:
        from routerlib.bench import INSTANCE_DIR, load_instance
        from routerlib.model import Budget
        from routerlib.scoring import score

        problem = load_instance(Path(INSTANCE_DIR) / f"{args.instance}.json")
        budget = Budget(
            max_iterations=args.max_iterations,
            max_nodes=args.max_nodes,
            seed=args.seed,
        )
        routers = registry()
        if args.router not in routers:
            raise KeyError(f"unknown router {args.router!r}")
        factory = routers[args.router]

        fingerprints: list[str] = []
        payloads: list[str] = []
        walls: list[float] = []
        first_elements = None
        first_solution = None
        # run 1: a fresh router instance, the one that gets scored.
        # run 2: the SAME instance routed again — the harness's own determinism
        # semantics, which also catches state leaking between calls.
        router = factory()
        for i in range(args.runs):
            t0 = time.perf_counter()
            solution = router.route(problem, budget)
            walls.append(time.perf_counter() - t0)
            h, elements = _payload_hash(problem, solution)
            fingerprints.append(solution.fingerprint())
            payloads.append(h)
            if i == 0:
                first_elements = elements
                first_solution = solution

        result = score(problem, first_solution)
        row["score"] = result.as_dict()
        row["wallClockS"] = [round(w, 3) for w in walls]
        row["deterministic"] = (
            len(set(fingerprints)) == 1 and len(set(payloads)) == 1
        )
        row["fingerprints"] = fingerprints
        row["payloadHashes"] = payloads
        row["solutionNotes"] = list(first_solution.notes or ())
        row["claimedComplete"] = first_solution.complete
        row["unroutedNets"] = list(first_solution.unrouted_nets or ())
        row["traceCount"] = len(first_solution.traces)
        row["viaCount"] = len(first_solution.vias)
        row["ok"] = True
        if args.copper_out:
            cp = Path(args.copper_out)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(first_elements), encoding="utf-8")
            row["copperPath"] = str(cp)
    except BaseException as exc:  # noqa: BLE001 - a crash is a result
        row["ok"] = False
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc()[-4000:]

    out_path.write_text(json.dumps(row, indent=1) + "\n", encoding="utf-8")
    print(f"{args.router} {args.instance} ok={row.get('ok')}", flush=True)
    return 0 if row.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
