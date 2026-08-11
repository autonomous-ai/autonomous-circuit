#!/usr/bin/env python3
"""Regression lock on the example boards — a board may get better, never worse.

`examples/*/boards/main.board.json` is a committed sidecar: the pipeline's own
verdict on a real design, recorded at a moment in time. That makes it a free
ratchet. A board that once reached N blocking warnings must never silently
come back with N+1, and one that reached `fab.ready: true` must never quietly
stop being orderable.

Cheap because it reads the committed sidecars rather than rebuilding: it
catches the commit that regressed a board, at the moment that commit lands.
`--rebuild` does the expensive thing — rebuild every example through the real
pipeline in parallel and compare — which is what you want after a block, a
check or a toolchain bump.

The lock only ever tightens. When a board improves, `--accept` writes the new,
better numbers into the baseline, so the ratchet moves one way. There is no
flag that loosens it; a genuine regression is either fixed or explained in the
baseline's `note`.

    python evals/examples_lock.py             # fast: check committed sidecars
    python evals/examples_lock.py --rebuild   # slow: rebuild and compare
    python evals/examples_lock.py --accept    # ratchet the baseline tighter
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "circuitpy" / "src"))

EXAMPLES = REPO / "examples"
BASELINE = Path(__file__).resolve().parent / "examples-baseline.json"


def sidecar_for(project: Path) -> dict | None:
    path = project / "boards" / "main.board.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def measure(sidecar: dict) -> dict:
    warnings = sidecar.get("validation", {}).get("warnings", [])
    blocking = [w for w in warnings if w.get("severity") == "error"]
    return {
        "blocking": len(blocking),
        "fabReady": bool(sidecar.get("fab", {}).get("ready")),
        "blockingKinds": sorted({w["kind"] for w in blocking}),
        "bomLines": sidecar.get("bom", {}).get("lines"),
        "autorouterEffort": sidecar.get("build", {}).get(
            "autorouterEffort", "default"
        ),
    }


def current(rebuild: bool) -> dict[str, dict]:
    projects = sorted(
        p for p in EXAMPLES.iterdir()
        if p.is_dir() and (p / "product.json").is_file()
    )
    if not rebuild:
        out = {}
        for project in projects:
            sidecar = sidecar_for(project)
            if sidecar is not None:
                out[project.name] = measure(sidecar)
        return out

    from circuitpy.batch import BuildJob, build_many

    jobs = [
        BuildJob(
            source=p / "boards" / "main.tsx",
            output=p / "boards" / "main.circuit.json",
            label=p.name,
            meta={"project": p.name},
        )
        for p in projects
    ]

    def _progress(outcome, done: int, total: int) -> None:
        print(f"[{done}/{total}] {outcome.job.resolved_label():<20} "
              f"{outcome.seconds:.0f}s "
              f"{'fab-ready' if outcome.fab_ready else 'not ready'}", flush=True)

    report = build_many(jobs, on_done=_progress)
    print(report.summary())
    out = {}
    for outcome in report.outcomes:
        name = str(outcome.job.meta["project"])
        if not outcome.ok:
            out[name] = {"blocking": 10_000, "fabReady": False,
                         "blockingKinds": ["BuildCrashed"], "bomLines": None,
                         "autorouterEffort": "default"}
            continue
        sidecar = sidecar_for(EXAMPLES / name)
        out[name] = measure(sidecar) if sidecar else {}
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--accept", action="store_true",
                        help="ratchet the baseline to today's better numbers")
    args = parser.parse_args(argv[1:])

    now = current(args.rebuild)
    baseline: dict = {}
    if BASELINE.is_file():
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    boards: dict = baseline.get("boards", {})

    failures: list[str] = []
    improvements: list[str] = []
    for name, measured in sorted(now.items()):
        locked = boards.get(name)
        if locked is None:
            improvements.append(f"{name}: new board, locking at "
                                f"{measured['blocking']} blocking")
            continue
        if measured["blocking"] > locked["blocking"]:
            failures.append(
                f"{name}: {locked['blocking']} blocking -> {measured['blocking']} "
                f"({', '.join(measured['blockingKinds']) or 'none'})"
            )
        elif measured["blocking"] < locked["blocking"]:
            improvements.append(
                f"{name}: {locked['blocking']} -> {measured['blocking']} blocking"
            )
        if locked.get("fabReady") and not measured["fabReady"]:
            failures.append(f"{name}: was fab-ready, now is not")
        elif measured["fabReady"] and not locked.get("fabReady"):
            improvements.append(f"{name}: now fab-ready")

    for line in improvements:
        print(f"better  {line}")
    for line in failures:
        print(f"REGRESSION  {line}")

    if args.accept:
        merged = dict(boards)
        for name, measured in now.items():
            locked = merged.get(name)
            if locked is None or measured["blocking"] <= locked["blocking"]:
                merged[name] = measured
        BASELINE.write_text(
            json.dumps(
                {
                    "note": (
                        "Ratchet, not a snapshot: these are the best numbers each "
                        "example board has ever reached. A build may improve on "
                        "them and --accept records that; a build may never come "
                        "back worse. Written by evals/examples_lock.py."
                    ),
                    "updatedAt": time.strftime("%Y-%m-%d"),
                    "boards": dict(sorted(merged.items())),
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        print(f"\nbaseline ratcheted: {BASELINE}")
        return 0

    if not boards:
        print("\nno baseline yet — run with --accept to create one")
        return 0
    print(f"\n{len(now)} example boards checked, {len(failures)} regressions")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
