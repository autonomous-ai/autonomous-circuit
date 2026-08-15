"""Drive the judge over every (router, instance) pair.

One subprocess per pair so a hang or a crash costs one cell. Modest
concurrency: the machine is shared, and the contract's wall-clock safety valve
means a heavily loaded machine could turn a result into a non-result.

    python3.12 scripts/tournament.py --routers a,b --out work/tournament
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
INSTANCES = sorted(p.stem for p in (PACKAGE / "benchmarks" / "instances").glob("*.json"))

PRIMARY = [
    "baseline-pattern",
    "maze-astar",
    "pathfinder-negotiated",
    "ripup-reroute",
    "meta-anneal",
    "meta-genetic",
    "topological-graph",
    "exact-and-structured",
    "plane-and-classes",
]
CONTROLS = [
    "maze-astar-ripup",
    "maze-greedy",
    "maze-off-greedy",
    "ripup-greedy-control",
]


def cell(router: str, instance: str, out_dir: Path, runs: int, timeout: int) -> dict:
    row_path = out_dir / "rows" / router / f"{instance}.json"
    copper_path = out_dir / "copper" / router / f"{instance}.json"
    if row_path.exists():
        try:
            existing = json.loads(row_path.read_text())
            if existing.get("ok"):
                return {"router": router, "instance": instance, "skipped": True}
        except Exception:  # noqa: BLE001
            pass
    env = dict(os.environ, CIRCUIT_PARTS_ENGINE="off")
    cmd = [
        sys.executable,
        str(HERE / "judge.py"),
        "--router", router,
        "--instance", instance,
        "--out", str(row_path),
        "--copper-out", str(copper_path),
        "--runs", str(runs),
    ]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, cwd=str(PACKAGE), env=env, capture_output=True,
            text=True, timeout=timeout,
        )
        status = "ok" if proc.returncode == 0 else f"exit{proc.returncode}"
        tail = (proc.stderr or "")[-600:]
    except subprocess.TimeoutExpired:
        status = "timeout"
        tail = ""
        row_path.parent.mkdir(parents=True, exist_ok=True)
        row_path.write_text(
            json.dumps(
                {"router": router, "instance": instance, "ok": False,
                 "error": f"harness timeout after {timeout}s"}, indent=1
            ) + "\n", encoding="utf-8",
        )
    dt = time.perf_counter() - t0
    print(f"[{status:>8}] {dt:7.1f}s  {router:<22} {instance}", flush=True)
    if tail.strip():
        print(f"           stderr: {tail.strip()[:400]}", flush=True)
    return {"router": router, "instance": instance, "status": status, "seconds": dt}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--routers", default=",".join(PRIMARY))
    ap.add_argument("--instances", default=",".join(INSTANCES))
    ap.add_argument("--out", default=str(PACKAGE.parent.parent / "work" / "tournament"))
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    routers = [r for r in args.routers.split(",") if r]
    instances = [i for i in args.instances.split(",") if i]
    pairs = [(r, i) for r in routers for i in instances]
    print(f"{len(pairs)} cells, {args.jobs} at a time -> {out_dir}", flush=True)

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(
            lambda p: cell(p[0], p[1], out_dir, args.runs, args.timeout), pairs
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
