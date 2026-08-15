"""Run ``verify_pipeline.py`` over every cell the tournament has produced."""

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


def cell(router: str, instance: str, root: Path, timeout: int, no_kicad: bool) -> None:
    out = root / "verify" / router / f"{instance}.json"
    if out.exists():
        try:
            if json.loads(out.read_text()).get("ok"):
                return
        except Exception:  # noqa: BLE001
            pass
    cmd = [
        sys.executable, str(HERE / "verify_pipeline.py"),
        "--router", router, "--instance", instance, "--tournament", str(root),
    ]
    if no_kicad:
        cmd.append("--no-kicad")
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, cwd=str(PACKAGE), env=dict(os.environ, CIRCUIT_PARTS_ENGINE="off"),
            capture_output=True, text=True, timeout=timeout,
        )
        status = "ok" if proc.returncode == 0 else f"exit{proc.returncode}"
    except subprocess.TimeoutExpired:
        status = "timeout"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"router": router, "instance": instance, "ok": False,
             "error": f"verify timeout after {timeout}s"}, indent=1) + "\n")
    print(f"[{status:>8}] {time.perf_counter() - t0:7.1f}s  {router:<22} {instance}",
          flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tournament", default=str(PACKAGE.parent.parent / "work" / "tournament"))
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--no-kicad", action="store_true")
    ap.add_argument("--routers", default=None)
    args = ap.parse_args(argv)

    root = Path(args.tournament)
    pairs = []
    instances = sorted(
        p.stem for p in (PACKAGE / "benchmarks" / "instances").glob("*.json")
    )
    if not args.routers or "_empty-control" in (args.routers or ""):
        pairs += [("_empty-control", i) for i in instances]
    for router_dir in sorted((root / "copper").iterdir()):
        if not router_dir.is_dir():
            continue
        if args.routers and router_dir.name not in args.routers.split(","):
            continue
        for f in sorted(router_dir.glob("*.json")):
            pairs.append((router_dir.name, f.stem))
    print(f"{len(pairs)} cells to verify, {args.jobs} at a time", flush=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(
            lambda p: cell(p[0], p[1], root, args.timeout, args.no_kicad), pairs
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
