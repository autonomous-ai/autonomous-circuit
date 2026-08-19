"""One command that runs every check, in parallel, and prints one JSON line.

**Why parallel.** The exchange rate says compute is free and wall-clock is not:
a missed defect costs two serial weeks, and the fab queue is the one thing that
cannot be parallelised. Every check here is independent of every other, so they
run in separate processes and the run costs the slowest one rather than their
sum. On ``terminal-keyboard`` that is the difference between ~8 seconds and
~17.

Processes rather than threads because the solving is CPU-bound Python, and each
worker loads the ``circuit.json`` itself rather than shipping a ``Board``
across a pickle boundary.

Output is the contract shape the pipeline already speaks: one JSON line, with
``findings[]`` in ``{part, kind, detail, severity}`` form. See
``packages/verify/README.md`` for how this should be wired in.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from verifylib import (
    assembly,
    corners,
    crystal,
    dc,
    gerber_truth,
    model,
    netclass,
    pour,
    review,
    thermal,
)
from verifylib.findings import CheckResult, finding

#: name -> (needs the gerber packet, description)
CHECKS = {
    "assembly": (False, "pick-and-place rules: bodies, spacing, sides, pitch"),
    "netclass": (False, "current capacity, via bottlenecks, pair skew"),
    "dc": (False, "DC operating point over the real netlist"),
    "corners": (False, "the same solve at every tolerance corner"),
    "crystal": (False, "crystal net length against the router's hard ceiling"),
    "pour": (False, "is there a ground plane, on which layers, covering how much"),
    "review": (False, "the electrical half of an EE design review"),
    "thermal": (False, "dissipation against package ratings, at peak load"),
    "gerber": (True, "the shipped packet, reconciled against the design"),
}


def resolve(target: str | Path) -> tuple[Path, Path | None]:
    """``(circuit.json, gerbers.zip or None)`` from a project dir or a file."""
    path = Path(target)
    if path.is_dir():
        candidates = sorted((path / "boards").glob("*.circuit.json"))
        if not candidates:
            candidates = sorted(path.glob("*.circuit.json"))
        if not candidates:
            raise FileNotFoundError(f"no *.circuit.json under {path}")
        path = candidates[0]
    stem = path.name[: -len(".circuit.json")] if path.name.endswith(".circuit.json") else path.stem
    zip_path = path.parent / f"{stem}_fab" / "gerbers.zip"
    return path, zip_path if zip_path.is_file() else None


def run_one(name: str, circuit_json: str, gerbers: str | None, trials: int) -> dict:
    """Run a single check. Always returns a dict, never raises — a verifier
    that takes the run down with it is a verifier that gets deleted."""
    started = time.time()
    try:
        board = model.load(circuit_json)
        if name == "assembly":
            result = assembly.check(board)
        elif name == "netclass":
            result = netclass.check(board)
        elif name == "pour":
            result = pour.check(board)
        elif name == "dc":
            result = dc.check(board)
        elif name == "corners":
            result = corners.check(board, trials=trials)
        elif name == "crystal":
            result = crystal.check(board)
        elif name == "review":
            result = review.check(board)
        elif name == "thermal":
            result = thermal.check(board)
        elif name == "gerber":
            if gerbers is None:
                result = CheckResult(
                    name="gerber_truth",
                    findings=[
                        finding(
                            "packet",
                            "gerber_absent",
                            "no gerbers.zip beside this board, so nothing "
                            "verified what the fab would actually build",
                            "warning",
                        )
                    ],
                )
            else:
                result = gerber_truth.check(board, gerbers)
        else:
            raise ValueError(f"unknown check {name!r}")
    except Exception as exc:  # noqa: BLE001
        payload = CheckResult(
            name=name,
            findings=[
                finding(
                    "board",
                    "check_failed",
                    f"{name} raised {type(exc).__name__}: {exc}",
                    "warning",
                )
            ],
        ).as_dict()
    else:
        payload = result.as_dict()
    payload["seconds"] = round(time.time() - started, 3)
    return payload


def verify(
    target: str | Path,
    *,
    trials: int = corners.DEFAULT_TRIALS,
    only: list[str] | None = None,
    parallel: bool = True,
) -> dict:
    circuit_json, gerbers = resolve(target)
    names = [n for n in CHECKS if not only or n in only]
    started = time.time()

    if parallel and len(names) > 1:
        with ProcessPoolExecutor(max_workers=len(names)) as pool:
            futures = {
                name: pool.submit(
                    run_one,
                    name,
                    str(circuit_json),
                    str(gerbers) if gerbers else None,
                    trials,
                )
                for name in names
            }
            results = [futures[name].result() for name in names]
    else:
        results = [
            run_one(name, str(circuit_json), str(gerbers) if gerbers else None, trials)
            for name in names
        ]

    findings = [f for r in results for f in r.get("findings", [])]
    counts = {level: 0 for level in ("error", "warning", "info")}
    for f in findings:
        counts[f.get("severity", "info")] = counts.get(f.get("severity", "info"), 0) + 1
    serial = sum(r.get("seconds", 0.0) for r in results)
    wall = time.time() - started
    return {
        "ok": counts["error"] == 0,
        "board": str(circuit_json),
        "gerbers": str(gerbers) if gerbers else None,
        "checks": results,
        "findings": findings,
        "summary": counts,
        "seconds": round(wall, 3),
        "compute_seconds": round(serial, 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verifylib",
        description="Run the standalone pre-fabrication checks over one board.",
    )
    parser.add_argument("target", help="a project directory or a *.circuit.json")
    parser.add_argument(
        "--only", action="append", choices=sorted(CHECKS),
        help="run only these checks (repeatable)",
    )
    parser.add_argument(
        "--trials", type=int, default=corners.DEFAULT_TRIALS,
        help="random corners to sample on top of the deterministic ones",
    )
    parser.add_argument(
        "--serial", action="store_true",
        help="run checks one at a time (for profiling; the default is parallel)",
    )
    parser.add_argument(
        "--text", action="store_true", help="human-readable output instead of JSON"
    )
    args = parser.parse_args(argv)

    try:
        report = verify(
            args.target,
            trials=args.trials,
            only=args.only,
            parallel=not args.serial,
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 2

    if not args.text:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0

    print(f"{report['board']}")
    print(
        f"  {report['summary']['error']} error, "
        f"{report['summary']['warning']} warning, "
        f"{report['summary']['info']} info "
        f"— {report['seconds']}s wall, {report['compute_seconds']}s compute"
    )
    for check in report["checks"]:
        coverage = check.get("coverage") or {}
        seen = (
            f" [{coverage.get('examined')}/{coverage.get('total')} "
            f"{coverage.get('unit')}]"
            if coverage
            else ""
        )
        print(f"\n  {check['name']} ({check['seconds']}s){seen}")
        for note in check.get("notes", []):
            print(f"    note: {note}")
        for blind in coverage.get("blind", []):
            print(f"    not seen: {blind}")
        for item in check.get("findings", []):
            print(f"    {item['severity'].upper():7s} {item['part']}: {item['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
