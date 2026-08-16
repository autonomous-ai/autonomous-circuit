#!/usr/bin/env python3
"""Recombine the copper already on disk, and score the result.

No router runs. Every tournament cell's ``pcb_trace``/``pcb_via`` elements were
written out by ``judge.py``, so a merge over N families costs a few seconds per
instance against the minutes each family cost to produce. That is the whole
reason this experiment is cheap enough to run three ways and keep the honest
answer rather than the first one.

    python3.12 packages/router/scripts/recombine_matrix.py \
        --set work/tournament-truepads --out work/recombine/truepads.json

``--set`` must point at a tournament output directory (``copper/<family>/<instance>.json``
plus ``rows/<family>/<instance>.json``). A cell whose ``placementHash`` no longer
matches its instance is dropped rather than merged: copper about a board that has
been re-extracted is copper about a different board.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for extra in (REPO / "packages" / "router" / "src", REPO / "packages" / "circuitpy" / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

os.environ.setdefault("CIRCUIT_PARTS_ENGINE", "off")


def load_cells(root: Path, instance: str, problem, families: list[str] | None):
    """``{family: solution}`` for one instance, stale cells dropped."""
    from routerlib.adapters import solution_from_elements
    from routerlib.bench import placement_hash

    from routerlib.model import RoutingSolution

    copper_root = root / "copper"
    if not copper_root.is_dir():
        raise SystemExit(f"no copper under {root}")
    want = families or sorted(p.name for p in copper_root.iterdir() if p.is_dir())
    current = placement_hash(problem)
    out: dict[str, RoutingSolution] = {}
    dropped: list[str] = []
    for family in want:
        path = copper_root / family / f"{instance}.json"
        if not path.is_file():
            continue
        row_path = root / "rows" / family / f"{instance}.json"
        if row_path.is_file():
            row = json.loads(row_path.read_text(encoding="utf-8"))
            if not row.get("ok"):
                dropped.append(f"{family}: cell did not complete")
                continue
            was = row.get("placementHash")
            if was and was != current:
                dropped.append(f"{family}: placement moved {was} -> {current}")
                continue
        elements = json.loads(path.read_text(encoding="utf-8"))
        out[family] = solution_from_elements(problem, elements, router=family)
    return out, dropped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="root", default=str(REPO / "work" / "tournament-truepads"))
    ap.add_argument("--families", default=None, help="comma-separated, default all")
    ap.add_argument("--instances", default=None)
    ap.add_argument("--rankings", default="obstruction,scorer,source")
    ap.add_argument("--modes", default="anchored,free")
    ap.add_argument("--clearance", type=float, default=None)
    ap.add_argument(
        "--max-evictions", type=int, default=2,
        help="how many placed nets the repair may rip up for one more; 0 disables it",
    )
    ap.add_argument(
        "--reroute", default="",
        help="comma-separated families the repair may re-route an evicted net with",
    )
    ap.add_argument(
        "--residual", default="",
        help="comma-separated families that relay whatever the merge left open",
    )
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    from routerlib import connectivity as conn
    from routerlib.bench import INSTANCE_DIR, load_instance
    from routerlib.compositions.registry import load_algorithms
    from routerlib.compositions.recombine import recombine, relay_residual
    from routerlib.scoring import score

    chains = [c for c in (args.reroute, args.residual) if c]
    registry = load_algorithms() if chains else {}
    reroute = (
        relay_residual(registry, args.reroute.split(",")) if args.reroute else None
    )
    residual = (
        relay_residual(registry, args.residual.split(",")) if args.residual else None
    )

    root = Path(args.root)
    families = args.families.split(",") if args.families else None
    wanted = set(args.instances.split(",")) if args.instances else None

    rows: list[dict] = []
    ruler = None
    for path in sorted(INSTANCE_DIR.glob("*.json")):
        instance = path.stem
        if wanted and instance not in wanted:
            continue
        problem = load_instance(path)
        solutions, dropped = load_cells(root, instance, problem, families)
        if not solutions:
            print(f"{instance:<46} no usable copper", flush=True)
            continue
        inputs = {
            name: conn.analyse(problem, sol).completeness
            for name, sol in solutions.items()
        }
        row: dict = {
            "instance": instance,
            "routableNets": len(problem.routable_nets),
            "inputs": {k: round(v, 6) for k, v in sorted(inputs.items())},
            "dropped": dropped,
            "rankings": {},
        }
        for mode in args.modes.split(","):
            for ranking in args.rankings.split(","):
                cell = f"{mode}/{ranking}"
                result = recombine(
                    problem, solutions, mode=mode, ranking=ranking,
                    clearance_mm=args.clearance,
                    max_evictions=args.max_evictions,
                    reroute=reroute, residual=residual,
                )
                scored = score(problem, result.solution)
                ruler = ruler or scored.ruler
                # Determinism is not a nicety: a merge that answers differently
                # twice cannot be compared to a relay, or to itself yesterday.
                again = recombine(
                    problem, solutions, mode=mode, ranking=ranking,
                    clearance_mm=args.clearance,
                    max_evictions=args.max_evictions,
                    reroute=reroute, residual=residual,
                )
                deterministic = (
                    result.solution.fingerprint() == again.solution.fingerprint()
                )
                row["rankings"][cell] = {
                    **result.as_dict(),
                    "deterministic": deterministic,
                    "score": {
                        "completeness": round(scored.completeness, 6),
                        "errors": scored.errors,
                        "warnings": scored.warnings,
                        "errorKinds": scored.error_kinds,
                        "vias": scored.quality.via_count,
                        "copperMm": scored.quality.copper_mm,
                        "clean": scored.clean,
                    },
                }
                print(
                    f"{instance:<40} {cell:<24} "
                    f"{scored.completeness * 100:5.1f}% "
                    f"(best in {max(inputs.values()) * 100:5.1f}%, "
                    f"ceil {result.ceiling * 100:5.1f}%)  "
                    f"{scored.errors:>3}e {scored.warnings:>3}w  "
                    f"{scored.quality.via_count:>4}v  "
                    f"base={result.base[:12]:<12} "
                    f"xp={result.transplanted:>3} rep={result.repairs:>2} "
                    f"lost={len(result.lost_to_conflict):>3}"
                    f"{'' if deterministic else '  NONDETERMINISTIC'}",
                    flush=True,
                )
        rows.append(row)

    if rows:
        print()
        total = sum(r["routableNets"] for r in rows)
        for mode in args.modes.split(","):
            for ranking in args.rankings.split(","):
                cell = f"{mode}/{ranking}"
                connected = sum(
                    r["rankings"][cell]["score"]["completeness"] * r["routableNets"]
                    for r in rows
                )
                ceiling = sum(
                    r["rankings"][cell]["ceiling"] * r["routableNets"] for r in rows
                )
                errors = sum(r["rankings"][cell]["score"]["errors"] for r in rows)
                warns = sum(r["rankings"][cell]["score"]["warnings"] for r in rows)
                clean = sum(1 for r in rows if r["rankings"][cell]["score"]["clean"])
                cross = sum(r["rankings"][cell]["transplanted"] for r in rows)
                lost = sum(len(r["rankings"][cell]["lostToConflict"]) for r in rows)
                vias = sum(r["rankings"][cell]["score"]["vias"] for r in rows)
                det = sum(1 for r in rows if r["rankings"][cell]["deterministic"])
                reps = sum(r["rankings"][cell]["repairs"] for r in rows)
                print(
                    f"{cell:<24} {connected:6.0f}/{total} nets "
                    f"({connected / total * 100:.1f}%)  ceiling {ceiling / total * 100:.1f}%  "
                    f"{errors} err {warns} warn  {clean}/{len(rows)} clean  "
                    f"{vias} vias  {cross} transplanted  {reps} repairs  "
                    f"{lost} lost  {det}/{len(rows)} deterministic"
                )
        best_input = sum(max(r["inputs"].values()) * r["routableNets"] for r in rows)
        print(
            f"{'best input':<12} {best_input:6.0f}/{total} nets "
            f"({best_input / total * 100:.1f}%)  — per-instance best of the inputs"
        )
        if ruler:
            print(ruler.line())

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "schema": "routerlib/recombine-matrix@1",
                    "set": str(root),
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
