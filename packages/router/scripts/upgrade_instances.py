#!/usr/bin/env python3
"""Re-baseline the benchmark fixtures onto the true pad model (schema @1 -> @2).

Why this is not ``build_instances.py``
--------------------------------------

``build_instances.py`` re-derives an instance from whatever ``examples/`` and
the composition matrix contain *today*. Run now it would move the placement:
``terminal-keyboard`` alone has drifted 104 pads since the tournament. Moving
the placement would orphan the 208 copper sets already on disk and make the
whole re-score impossible — we would be measuring different boards, not a
different ruler.

So this upgrades **in place**. Board, pads, drills, keepouts and nets keep the
exact coordinates they had; what changes is only what was thrown away on the
way in:

* a ``polygon`` pad gets its **vertices** back (they were collapsed to a
  bounding box, then to that box's inscribed stadium)
* a pad gets its ``radius`` back as a corner radius
* a drill gets its own ``hole_shape``
* a keepout gets its rotation and, where it has one, its outline

Shapes are copied from the board each instance came from, matched by pad id and
refused unless the centre and size still agree to within two coordinate quanta
— a drifted board cannot inject a foreign outline into a fixture, and a pad that
really moved moves by microns.

The placement hash moves for every instance, on purpose: shape is part of the
placement now, and the hash that ignored it was calling two different boards the
same board. Old and new are both recorded, per instance, in the manifest's
``rebaseline`` block, because a fixture that changes silently is worse than one
that does not change at all.

    python3.12 packages/router/scripts/upgrade_instances.py           # dry run
    python3.12 packages/router/scripts/upgrade_instances.py --write
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for extra in (REPO / "packages" / "router" / "src", REPO / "packages" / "circuitpy" / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

os.environ.setdefault("CIRCUIT_PARTS_ENGINE", "off")

#: Two coordinate quanta. Every coordinate is snapped to 1nm, and the old
#: bounding box was computed before that snap while the new one is computed
#: after it, so a polygon pad can legitimately differ by one quantum. A pad
#: that really moved moves by microns.
TOL_MM = 2e-6

#: Where each instance's shapes come from. The three examples read their own
#: board; a matrix cell reads the durable rebuild ``rebuild_cells.py`` made,
#: which is the same board the pipeline verification ran against.
CELLS_DIR = REPO / "work" / "tournament" / "cells"


def source_for(instance_id: str) -> Path | None:
    base = instance_id[:-6] if instance_id.endswith("-plane") else instance_id
    if base.startswith("matrix-"):
        path = CELLS_DIR / base[len("matrix-"):] / "boards" / "main.circuit.json"
        return path if path.is_file() else None
    path = REPO / "examples" / base / "boards" / "main.circuit.json"
    return path if path.is_file() else None


def shapes_from(path: Path) -> tuple[dict, dict, dict]:
    """``(pad id -> shape fields, drill id -> shape, keepout id -> shape fields)``."""
    from routerlib.adapters import problem_from_circuit_json

    elements = json.loads(path.read_text(encoding="utf-8"))
    problem = problem_from_circuit_json(
        elements, problem_id="src", strip_routes=True, strip_planes=True
    )
    pads = {
        p.id: {
            "x": p.center.x,
            "y": p.center.y,
            "w": p.width_mm,
            "h": p.height_mm,
            "shape": p.shape,
            "points": [[v.x, v.y] for v in p.vertices] or None,
            "cornerRadiusMm": p.corner_radius_mm or None,
        }
        for p in problem.pads
    }
    drills = {d.id: d.shape for d in problem.drills}
    keepouts = {
        k.id: {
            "x": k.center.x,
            "y": k.center.y,
            "rot": k.rotation_deg,
            "points": [[v.x, v.y] for v in k.vertices] or None,
        }
        for k in problem.keepouts
    }
    return pads, drills, keepouts


def upgrade(data: dict, source: Path | None) -> dict:
    pads, drills, keepouts = shapes_from(source) if source else ({}, {}, {})
    report = {"padsShaped": 0, "padsUnmatched": 0, "polygonsRecovered": 0,
              "drillsShaped": 0, "keepoutsShaped": 0}

    for pad in data.get("pads", ()):
        found = pads.get(pad["id"])
        if found is None:
            if pad.get("shape") == "polygon":
                report["padsUnmatched"] += 1
            continue
        if (
            abs(found["x"] - pad["x"]) > TOL_MM
            or abs(found["y"] - pad["y"]) > TOL_MM
            or abs(found["w"] - pad["w"]) > TOL_MM
            or abs(found["h"] - pad["h"]) > TOL_MM
            or found["shape"] != pad.get("shape")
        ):
            report["padsUnmatched"] += 1
            continue
        pad["points"] = found["points"]
        pad["cornerRadiusMm"] = found["cornerRadiusMm"]
        if found["points"]:
            # A polygon pad's centre and size are derived from its vertices,
            # and the two readers derive them differently: the old one boxed
            # raw floats, the new one boxes vertices already snapped to the 1nm
            # grid. Sub-nanometre either way — and enough to make a fresh read
            # of the same board hash differently, which makes
            # ``verify_real_board.py`` refuse a board that never moved. The
            # fixture records what today's reader produces.
            pad["x"], pad["y"] = found["x"], found["y"]
            pad["w"], pad["h"] = found["w"], found["h"]
            report["polygonsRecovered"] += 1
        report["padsShaped"] += 1

    for drill in data.get("drills", ()):
        drill["shape"] = drills.get(drill["id"], "pill")
        report["drillsShaped"] += 1

    for keepout in data.get("keepouts", ()):
        found = keepouts.get(keepout["id"])
        if found is None or abs(found["x"] - keepout["x"]) > TOL_MM:
            keepout.setdefault("rot", 0.0)
            keepout.setdefault("points", None)
            continue
        keepout["rot"] = found["rot"]
        keepout["points"] = found["points"]
        report["keepoutsShaped"] += 1

    data["schema"] = "routerlib/instance@2"
    return report


def main(argv=None) -> int:
    from routerlib.bench import (
        INSTANCE_DIR,
        MANIFEST,
        baseline_of,
        problem_from_dict,
        problem_to_dict,
    )

    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="rewrite the fixtures")
    ap.add_argument("--out", default=str(INSTANCE_DIR))
    args = ap.parse_args(argv)
    out_dir = Path(args.out)

    rebaseline = []
    for path in sorted(Path(INSTANCE_DIR).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        old_hash = data.get("placementHash", "")
        old_schema = data.get("schema")
        source = source_for(data["id"])
        report = upgrade(data, source)
        problem = problem_from_dict(data)
        payload = problem_to_dict(problem, source=data.get("source", {}))
        payload["baseline"] = baseline_of(problem)
        old_baseline = (data.get("baseline") or {}).get("errors")
        row = {
            "id": data["id"],
            "source": str(source.relative_to(REPO)) if source else None,
            "oldSchema": old_schema,
            "oldPlacementHash": old_hash,
            "newPlacementHash": payload["placementHash"],
            "baselineErrorsBefore": old_baseline,
            "baselineErrorsAfter": payload["baseline"].get("errors"),
            **report,
        }
        rebaseline.append(row)
        print(
            f"{data['id']:<46} {old_hash} -> {payload['placementHash']}  "
            f"poly={report['polygonsRecovered']} unmatched={report['padsUnmatched']} "
            f"baseline {old_baseline}->{payload['baseline'].get('errors')}"
        )
        if args.write:
            (out_dir / f"{problem.id}.json").write_text(
                json.dumps(payload, indent=1) + "\n", encoding="utf-8"
            )

    if not args.write:
        print("\ndry run — pass --write to rewrite the fixtures")
        return 0

    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO),
        capture_output=True, text=True,
    ).stdout.strip()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["schema"] = "routerlib/manifest@2"
    manifest["rebaseline"] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "gitHead": head,
        "why": (
            "pads and keepouts are measured as their true shapes instead of "
            "their inscribed stadiums; shape joined the placement hash, so "
            "every hash moved once. Placement coordinates are unchanged — the "
            "fixtures were upgraded in place, not rebuilt."
        ),
        "instances": rebaseline,
    }
    for entry in manifest.get("instances", []):
        data = json.loads((out_dir / entry["file"]).read_text(encoding="utf-8"))
        entry["placementHash"] = data["placementHash"]
        entry["features"] = data.get("features", {})
        entry["baseline"] = data.get("baseline", {})
    MANIFEST.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"\n{len(rebaseline)} instances rewritten; manifest -> {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
