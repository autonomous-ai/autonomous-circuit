#!/usr/bin/env python3
"""Re-score every tournament cell against a new ruler, from the copper on disk.

A routing run costs minutes; scoring costs milliseconds. So when the ruler
changes, the copper does not have to be produced again — ``judge.py`` already
wrote every cell's ``pcb_trace``/``pcb_via`` elements out, and
``routerlib.adapters.solution_from_elements`` reads them straight back. 208
cells re-score in about a minute, against exactly the boards the old ruler
graded.

What this is **not**: a new tournament. The copper is byte-identical to the run
of 2026-08-16; only the measurement changed. A router that would route
differently against the corrected model is a separate question, and the answer
to it is a re-run, not a re-score.

    python3.12 packages/router/scripts/rescore.py --out work/tournament/rescore.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for extra in (REPO / "packages" / "router" / "src", REPO / "packages" / "circuitpy" / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

os.environ.setdefault("CIRCUIT_PARTS_ENGINE", "off")

TOURNAMENT = REPO / "work" / "tournament"


def score_one(args: tuple[str, str, str, str]) -> dict:
    router, instance, row_path, copper_path = args
    from routerlib.adapters import solution_from_elements
    from routerlib.bench import INSTANCE_DIR, load_instance
    from routerlib.scoring import score

    row = json.loads(Path(row_path).read_text(encoding="utf-8"))
    old = row.get("score") or {}
    out: dict = {
        "router": router,
        "instance": instance,
        "ok": bool(row.get("ok")),
        "old": {
            "rulerHash": (old.get("measuredAgainst") or {}).get("rulerHash"),
            "completeness": old.get("completeness"),
            "connectedNets": old.get("connectedNets"),
            "routableNets": old.get("routableNets"),
            "errors": old.get("errors"),
            "warnings": old.get("warnings"),
            "clean": old.get("clean"),
            "violationsByKind": old.get("violationsByKind"),
            "vias": (old.get("quality") or {}).get("via_count"),
            "copperMm": (old.get("quality") or {}).get("copper_mm"),
        },
    }
    if not row.get("ok") or not Path(copper_path).is_file():
        out["error"] = row.get("error") or "no copper on disk"
        return out
    try:
        problem = load_instance(Path(INSTANCE_DIR) / f"{instance}.json")
        elements = json.loads(Path(copper_path).read_text(encoding="utf-8"))
        solution = solution_from_elements(
            problem,
            elements,
            router=router,
            iterations=int(old.get("iterations") or 0),
            nodes_expanded=int(old.get("nodesExpanded") or 0),
        )
        result = score(problem, solution)
        new = result.as_dict()
        out["new"] = {
            "rulerHash": (new.get("measuredAgainst") or {}).get("rulerHash"),
            "completeness": new.get("completeness"),
            "connectedNets": new.get("connectedNets"),
            "routableNets": new.get("routableNets"),
            "errors": new.get("errors"),
            "warnings": new.get("warnings"),
            "clean": new.get("clean"),
            "violationsByKind": new.get("violationsByKind"),
            "errorKinds": new.get("errorKinds"),
            "vias": (new.get("quality") or {}).get("via_count"),
            "copperMm": (new.get("quality") or {}).get("copper_mm"),
        }
        # The replay has to be the same copper, or the comparison is about two
        # different boards. Via count and copper length are the cheap proof.
        out["replayMatches"] = (
            out["old"]["vias"] == out["new"]["vias"]
            and abs((out["old"]["copperMm"] or 0) - (out["new"]["copperMm"] or 0)) < 0.01
        )
    except Exception as exc:  # noqa: BLE001 - a failure is a result
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tournament", default=str(TOURNAMENT))
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args(argv)
    root = Path(args.tournament)

    work: list[tuple[str, str, str, str]] = []
    for rdir in sorted((root / "rows").iterdir()):
        if not rdir.is_dir():
            continue
        for row in sorted(rdir.glob("*.json")):
            work.append(
                (
                    rdir.name,
                    row.stem,
                    str(row),
                    str(root / "copper" / rdir.name / f"{row.stem}.json"),
                )
            )
    print(f"{len(work)} cells, {args.jobs} at a time", flush=True)
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        cells = list(pool.map(score_one, work, chunksize=4))

    rulers = {c["new"]["rulerHash"] for c in cells if c.get("new")}
    old_rulers = {c["old"]["rulerHash"] for c in cells if c["old"].get("rulerHash")}
    payload = {
        "measuredAt": "2026-08-16",
        "oldRulerHash": sorted(old_rulers),
        "newRulerHash": sorted(rulers),
        "what": (
            "the same copper, re-scored. Pads and keepouts are their true "
            "shapes now instead of their inscribed stadiums."
        ),
        "cells": cells,
    }
    Path(args.out).write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")

    mismatched = [c for c in cells if c.get("new") and not c.get("replayMatches")]
    failed = [c for c in cells if c.get("error")]
    print(f"wrote {args.out}: {len(cells)} cells, {len(failed)} failed, "
          f"{len(mismatched)} replay mismatches")
    for c in failed[:10]:
        print(f"  FAILED {c['router']:<22} {c['instance']:<44} {c['error'][:80]}")
    for c in mismatched[:10]:
        print(f"  REPLAY {c['router']:<22} {c['instance']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
