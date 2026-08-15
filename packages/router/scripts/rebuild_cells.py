"""Rebuild the composition-matrix instances as real boards, and keep them.

``build_instances.py`` builds each matrix cell through the real pipeline and
then deletes the project, so ten of the sixteen instances have no board on disk
to check a route against. This rebuilds them into a durable directory and
reports, per cell, whether the rebuilt placement still hashes to the committed
instance. Only the cells that match can carry an independent legality check —
the rest would be a verdict about a board the router never saw.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for extra in (
    REPO / "packages" / "router" / "src",
    REPO / "packages" / "circuitpy" / "src",
    REPO / "evals",
    REPO / "skills" / "circuitcode",
):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import os  # noqa: E402

os.environ.setdefault("CIRCUIT_PARTS_ENGINE", "off")

CELLS = (
    ("status-led",),
    ("rp2040-core", "sw-tact"),
    ("i2c-bus",),
    ("ldo-3v3", "usb-c-power"),
    ("rp2040-core", "usb-c-data"),
    ("status-led", "ws2812-chain"),
    ("ldo-3v3", "rp2040-core", "usb-c-power"),
    ("ldo-3v3", "sensor-bme280", "usb-c-power"),
    ("ldo-3v3", "usb-c-power", "ws2812-chain"),
    ("i2c-bus", "ldo-3v3", "usb-c-power"),
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=2)
    args = ap.parse_args(argv)

    from circuitpy.batch import BuildJob, build_many
    from composition import prepare_cell
    from routerlib.adapters import problem_from_circuit_json
    from routerlib.bench import INSTANCE_DIR, correspondence, load_instance

    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    jobs = []
    for cell in CELLS:
        project, _ = prepare_cell(root, cell)
        jobs.append(BuildJob(
            source=project / "boards" / "main.tsx",
            output=project / "boards" / "main.circuit.json",
            label=" + ".join(cell),
            meta={"cell": list(cell)},
        ))
    report = build_many(
        jobs, workers=args.jobs,
        on_done=lambda o, d, t: print(
            f"[{d}/{t}] {'ok ' if o.ok else 'ERR'} {o.job.label} {o.seconds:.0f}s",
            flush=True),
    )

    verdict = {}
    for outcome in report.outcomes:
        cell = tuple(outcome.job.meta["cell"])
        iid = "matrix-" + "__".join(cell)
        path = Path(str(outcome.job.output))
        if not outcome.ok or not path.is_file():
            verdict[iid] = {"built": False}
            continue
        elements = json.loads(path.read_text())
        problem = load_instance(Path(INSTANCE_DIR) / f"{iid}.json")
        rebuilt = problem_from_circuit_json(
            elements, problem_id=iid, strip_routes=False, strip_planes=True
        )
        match = correspondence(problem, rebuilt)
        verdict[iid] = {"built": True, "matches": match.matches, "path": str(path)}
        print(f"{iid:<48}placement {'MATCHES' if match.matches else 'DRIFTED'}")
    (root / "verdict.json").write_text(json.dumps(verdict, indent=1) + "\n")
    print(f"{sum(1 for v in verdict.values() if v.get('matches'))}/{len(verdict)} match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
