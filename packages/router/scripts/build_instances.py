#!/usr/bin/env python3
"""Rebuild ``packages/router/benchmarks/`` from real boards.

Instances are committed fixtures, not something the suite regenerates on every
run, for one reason: ``examples/`` is rebuilt by other agents several times a
day and a benchmark that moves under you cannot be compared to itself. This
script is how the fixtures get refreshed **on purpose**, with the commit they
came from recorded in each file.

Sources:

* the three example boards, read from ``examples/*/boards/main.circuit.json``
* a spread of composition-matrix cells, built here through the real pipeline
  (``evals/composition.py`` owns how a cell becomes a board — this only chooses
  which cells and keeps the circuit.json)
* synthetic ground-plane variants of a few of the above, because a 2-layer
  board with a plane and one without are different routing problems and the
  router we ship can only express the second

Usage::

    python packages/router/scripts/build_instances.py            # everything
    python packages/router/scripts/build_instances.py --examples-only
    python packages/router/scripts/build_instances.py --only harness-puck
    python packages/router/scripts/build_instances.py --jobs 2

``--only`` re-extracts one instance and leaves every other fixture untouched.
That is the usual case, because re-extracting is not free: the placement moves,
and any copper already measured against the old one stops being scoreable
against the new. Do it when the board it came from has moved and settled —
``harness-puck`` on 2026-08-16, whose SW1 shifted 1.2mm to clear a courtyard —
and not on a schedule.

``--jobs`` defaults to 2. The machine is shared; a matrix run at
``cpu_count - 1`` once drove load to 79 on 16 cores and starved another agent's
board into a build timeout.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "packages" / "router" / "src"))
sys.path.insert(0, str(REPO / "packages" / "circuitpy" / "src"))
sys.path.insert(0, str(REPO / "evals"))
sys.path.insert(0, str(REPO / "skills" / "circuitcode"))

import os

os.environ.setdefault("CIRCUIT_PARTS_ENGINE", "off")

from routerlib.adapters import problem_from_circuit_json  # noqa: E402
from routerlib.bench import (  # noqa: E402
    INSTANCE_DIR,
    MANIFEST,
    baseline_of,
    features_of,
    problem_to_dict,
    with_ground_plane,
)

#: (instance id, example directory). The three boards the whole project is
#: measured on.
EXAMPLES = (
    ("hydrate-coaster", "hydrate-coaster"),
    ("harness-puck", "harness-puck"),
    ("terminal-keyboard", "terminal-keyboard"),
)

#: Composition-matrix cells, chosen to spread the feature space rather than to
#: be representative of anything: one block alone, a pair, and the realistic
#: spine (USB-C in, 3V3 rail) plus one block. Every one of these is a
#: composition the planner can legally emit, so a router that fails here fails
#: on a board a user can ask for.
CELLS: tuple[tuple[str, ...], ...] = (
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

#: Instances that also get a synthetic ground-plane twin. Chosen where a plane
#: is what a competent engineer would actually do on 2 layers.
PLANE_VARIANTS = (
    "hydrate-coaster",
    "terminal-keyboard",
    "matrix-ldo-3v3__rp2040-core__usb-c-power",
)


def git_head() -> dict:
    out: dict = {}
    try:
        out["gitHead"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO), capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(REPO), capture_output=True, text=True, timeout=15,
        ).stdout
        out["gitDirty"] = bool([l for l in dirty.splitlines() if l[3:].strip()])
    except Exception:  # noqa: BLE001
        pass
    return out


def write_instance(problem, source: dict, out_dir: Path) -> Path:
    payload = problem_to_dict(problem, source=source)
    payload["baseline"] = baseline_of(problem)
    path = out_dir / f"{problem.id}.json"
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return path


def from_circuit_json(path: Path, instance_id: str, source: dict, out_dir: Path):
    elements = json.loads(path.read_text(encoding="utf-8"))
    problem = problem_from_circuit_json(
        elements, problem_id=instance_id, strip_routes=True, strip_planes=True
    )
    written = [write_instance(problem, source, out_dir)]
    if instance_id in PLANE_VARIANTS:
        try:
            written.append(
                write_instance(
                    with_ground_plane(problem),
                    {**source, "variant": "synthetic ground plane, bottom layer"},
                    out_dir,
                )
            )
        except ValueError as exc:
            print(f"  no plane variant for {instance_id}: {exc}")
    return written


def build_cells(cells, jobs: int, out_dir: Path, source_base: dict) -> list[Path]:
    from circuitpy.batch import BuildJob, build_many
    from composition import prepare_cell

    root = Path(tempfile.mkdtemp(prefix="router-instances-"))
    jobs_list = []
    for cell in cells:
        project, _ = prepare_cell(root, cell)
        jobs_list.append(
            BuildJob(
                source=project / "boards" / "main.tsx",
                output=project / "boards" / "main.circuit.json",
                label=" + ".join(cell),
                meta={"cell": list(cell)},
            )
        )

    def _progress(outcome, done, total):
        mark = "ok " if outcome.ok else "ERR"
        print(f"  [{done}/{total}] {mark} {outcome.job.label} {outcome.seconds:.0f}s",
              flush=True)

    report = build_many(jobs_list, workers=jobs, on_done=_progress)
    written: list[Path] = []
    for outcome in report.outcomes:
        cell = tuple(outcome.job.meta["cell"])
        path = Path(str(outcome.job.output))
        if not outcome.ok or not path.is_file():
            print(f"  SKIP {' + '.join(cell)}: build failed")
            continue
        instance_id = "matrix-" + "__".join(cell)
        written += from_circuit_json(
            path,
            instance_id,
            {**source_base, "kind": "composition-cell", "cell": list(cell)},
            out_dir,
        )
    shutil.rmtree(root, ignore_errors=True)
    return written


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--examples-only", action="store_true")
    parser.add_argument("--only", default=None,
                        help="comma list of instance ids; everything else is "
                             "left exactly as it is on disk")
    parser.add_argument("--out", default=str(INSTANCE_DIR))
    args = parser.parse_args(argv[1:])
    only = {i for i in (args.only or "").split(",") if i} or None

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = git_head()
    base["extractedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    written: list[Path] = []

    for instance_id, folder in EXAMPLES:
        if only is not None and instance_id not in only:
            continue
        path = REPO / "examples" / folder / "boards" / "main.circuit.json"
        if not path.is_file():
            print(f"missing {path}")
            continue
        print(f"example {instance_id}")
        written += from_circuit_json(
            path, instance_id, {**base, "kind": "example", "path": str(path)}, out_dir
        )

    if not args.examples_only:
        cells = [
            c for c in CELLS
            if only is None or ("matrix-" + "__".join(c)) in only
        ]
        if cells:
            print(f"building {len(cells)} composition cells on {args.jobs} workers")
            written += build_cells(cells, args.jobs, out_dir, base)

    # The manifest is rewritten from what is on disk, but anything it carries
    # that is not derived from an instance — the ``rebaseline`` record of which
    # placement hash moved when, and why — survives. A re-extract that erased
    # the history of the last one would be the second worst thing this script
    # could do.
    manifest = {}
    if MANIFEST.is_file():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["schema"] = manifest.get("schema", "routerlib/manifest@2")
    manifest["generatedAt"] = base["extractedAt"]
    manifest["measuredAgainst"] = base
    manifest["instances"] = []
    for path in sorted(out_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        manifest["instances"].append(
            {
                "id": data["id"],
                "file": path.name,
                "placementHash": data.get("placementHash", ""),
                "source": data.get("source", {}),
                "features": data.get("features", {}),
                "baseline": data.get("baseline", {}),
            }
        )
    MANIFEST.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"\n{len(manifest['instances'])} instances -> {out_dir}")
    print(f"manifest -> {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
