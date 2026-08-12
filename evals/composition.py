#!/usr/bin/env python3
"""The composition matrix — proving the block library is closed under composition.

**Why this exists.** Every golden block passes its own full-gauntlet test, so
every block is individually fab-ready-clean. That is half a guarantee. All
three example boards on 2026-08-10 failed *in composition* — not in a block
alone, but in a block sitting on a real board beside other things. A library
where each part is proven and no combination is proven promises nothing about
what a user will actually ask for.

So: build every combination the planner can legally emit, as a real board,
through the real seven-stage pipeline, and record the verdict per cell.

Dee, 2026-08-11: *"just not these 3. they are just the first 3. make sure our
software is good enough to make everything fab ready."* Three boards passing is
a demo. A closed matrix is a structural guarantee.

**The rule this file enforces: anything the planner can legally emit must be
inside the tested space.** A composition the agent can produce but we have
never built is an untested claim.

Tiers, cheapest first (the exact cell count is derived from the live registry;
never copy a stale count into this contract):

| Tier | Coverage | What it proves |
|---|---|---|
| `singles` | every registered block | each block alone (duplicates `packages/golden-blocks/tests/test_gauntlet.py`; off by default) |
| `pairs` | every legal two-block combination | no two blocks break each other — the missing half |
| `spine` | protected USB + 3V3 + each compatible block | the realistic minimum powered board |
| `triples` | every legal three-block combination | every three-block combination the planner can reach |

Dee's budget rule, 2026-08-11: *"even if you send 1-day, 2-day or even 3-day to
get the build right and verify everything, that's still better than waiting 2
weeks from JLCPCB."* So the matrix is **exhaustive in compute and fast in
wall-clock** — cover every cell, and run them all at once. Sampling is a last
resort, and a sampled report says so on its face (`"sampled": true`).

**But the parallelism budget is shared.** Several agents build boards on the
same machine. At `cpu_count - 1` this run drove load to 79 on 16 cores and
starved an example board into two build timeouts, which is a worse outcome
than a slower matrix. `--jobs` defaults to 4 for that reason; raise it when
you own the box.

A failing cell is **never** fixed by teaching the agent a repair. It is fixed in
the block, in ``circuitlib.layout``, or in the planner's defaults — so that no
user ever meets the failure. Shift left or it is not fixed.

Usage:
    python evals/composition.py                      # pairs + spine, all cells
    python evals/composition.py --tier pairs
    python evals/composition.py --jobs 12          # when you own the machine
    python evals/composition.py --only usb-c-power,ldo-3v3      # one cell
    python evals/composition.py --sample 12          # random subset, seeded

Exit: non-zero when any built cell is not fab-ready, including a build with no
validation errors whose manufacturing packet is still incomplete. Minimal
``KNOWN_UNSUPPORTED`` cells are rejected at the earlier planner-law boundary
and omitted here; a structural eval pins that exclusion. Writes
``evals/composition-matrix.json`` — the record of what has been built, which is
the evidence behind the closure claim.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import itertools
import json
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages" / "circuitpy" / "src"))
sys.path.insert(0, str(REPO / "skills" / "circuitcode"))  # circuitlib

os.environ.setdefault("CIRCUIT_PARTS_ENGINE", "off")

SKELETON = REPO / "skills" / "circuitcode" / "templates" / "project_skeleton"
BLOCKS_DIR = REPO / "packages" / "golden-blocks" / "blocks"
REPORT = Path(__file__).resolve().parent / "composition-matrix.json"

from scripts.sync_golden_blocks import sync_project  # noqa: E402

#: Board props every real board sets. A cell judged under different rules than
#: the boards that will use it is not really being judged (same reasoning as
#: the block gauntlet's BOARD_PROPS).
BOARD_PROPS = (
    'thickness={1.6} minTraceWidth="0.15mm" '
    'minTraceToPadEdgeClearance="0.15mm" '
    'minViaEdgeToPadEdgeClearance="0.15mm" '
    'minViaPadDiameter="0.6mm" minViaHoleDiameter="0.3mm"'
)


# ---------------------------------------------------------------------------
# How to instantiate each block
# ---------------------------------------------------------------------------

#: block id -> (exported symbol, extra JSX props). Placement props are added by
#: the generator from circuitlib.layout, so no coordinate is ever guessed here.
#: Keep in sync with circuitlib.blocks.BLOCKS — test_matrix_covers_registry
#: below fails if the registry grows a block this table does not know.
INSTANTIATION: dict[str, tuple[str, str]] = {
    "usb-c-power": (
        "UsbCPower",
        'vbusBoundaryRefs={{ right: "N3", left: "N4" }} '
        'vbusRailNodeRef="N15"',
    ),
    "usb-c-data": (
        "UsbCData",
        'vbusBoundaryRefs={{ right: "N3", left: "N4" }} '
        'vbusRailNodeRef="N15" vbusClampNodeRef="N16" '
        'pairRules={{ pcbTraceGapMm: 0.15, maxLengthSkewMm: 3.8, '
        'maxUncoupledLengthMm: 3 }}',
    ),
    "usb-power-entry": ("UsbPowerEntry", ""),
    "ldo-3v3": ("Ldo3v3", ""),
    "i2c-bus": ("I2cBus", ""),
    "status-led": ("StatusLed", ""),
    "sw-tact": ("SwTact", 'name="SW1" signal="KEY1"'),
    "rp2040-core": (
        "Rp2040Core",
        'debugPortPcbX={14} debugPortPcbY={-2} '
        'debugSwclkBoundaryRef="N1" debugSwdBoundaryRef="N2" '
        'powerRailNodeRefs={{ westUpper: "N5", westLower: "N6", '
        'south: "N7", eastLower: "N8", eastUpper: "N9", '
        'topRight: "N10", topMiddle: "N11", topLeft: "N12", '
        'bulk: "N13", flash: "N14", dvddLeft: "N17", '
        'dvddRight: "N18", dvddSouth: "N19", dvddJunction: "N20" }}',
    ),
    "sensor-bme280": ("SensorBme280", ""),
    "ws2812-level-shifter": ("Ws2812LevelShifter", ""),
    "ws2812-chain": ("Ws2812Chain", "count={4}"),
}

#: Compositions the planner must NEVER emit, and why. These are excluded from
#: routed composition because the planner-law structural eval proves the
#: minimal invalid pair is rejected before board generation. The matrix must
#: not claim it built an excluded cell.
KNOWN_UNSUPPORTED: dict[tuple[str, ...], str] = {
    ("usb-c-data", "usb-c-power"): (
        "usb-c-data is a superset of usb-c-power and reuses the same refdes "
        "block (J1/R1/R2/U1/C1). Two USB-C entries on one board is a design "
        "error, not a layout problem — circuitlib.blocks says so in the "
        "usb-c-power notes, and helpers.board_plan never picks both."
    ),
}

#: The protected power spine every realistic USB board starts from.
SPINE = ("usb-c-power", "usb-power-entry", "ldo-3v3")


def block_ids() -> list[str]:
    from circuitlib.blocks import BLOCKS

    return sorted(BLOCKS)


def cells(tier: str) -> list[tuple[str, ...]]:
    ids = block_ids()
    out: list[tuple[str, ...]] = []
    if tier in ("singles", "all"):
        out += [(b,) for b in ids]
    if tier in ("pairs", "all", "default"):
        out += [tuple(sorted(p)) for p in itertools.combinations(ids, 2)]
    if tier in ("triples", "all"):
        out += [tuple(sorted(c)) for c in itertools.combinations(ids, 3)]
    if tier in ("spine", "all", "default"):
        out += [
            tuple(sorted(set(SPINE) | {b})) for b in ids if b not in SPINE
        ]
    # de-dup while keeping order, and drop the known-illegal compositions
    seen: set[tuple[str, ...]] = set()
    unique = []
    for cell in out:
        if cell in seen or cell in KNOWN_UNSUPPORTED:
            continue
        # A superset of an illegal composition is illegal too: two USB-C
        # entries stay two USB-C entries however many blocks join them.
        if any(set(bad) <= set(cell) for bad in KNOWN_UNSUPPORTED):
            continue
        seen.add(cell)
        unique.append(cell)
    return unique


# ---------------------------------------------------------------------------
# Board source generation — placement comes from circuitlib.layout, never by eye
# ---------------------------------------------------------------------------


def board_source(cell: tuple[str, ...]) -> tuple[str, tuple[float, float]]:
    """The TSX for one matrix cell, plus the board size it needs.

    Everything geometric comes from ``circuitlib.layout``: this is the same
    call the skill tells an agent to make, so a matrix pass is evidence about
    the advice we give, not about a layout the harness hand-tuned.
    """
    from circuitlib import layout

    ids = list(cell)
    plan = layout.place_board(ids)
    width, height = plan["width_mm"], plan["height_mm"]
    centres = plan["placements"]

    # The placement helper must agree with itself before we pay for a build:
    # a cell that fails here would fail as pcb_component_outside_board_error
    # after 90 seconds of compute, and the bug would be in this file, not in
    # the composition under test.
    if plan["warnings"]:
        raise AssertionError(
            "circuitlib.layout produced a plan it does not believe in: "
            + "; ".join(w["detail"] for w in plan["warnings"])
        )

    imports = "\n".join(
        f'import {{ {INSTANTIATION[b][0]} }} from "../blocks/{b}/{b}"'
        for b in ids
    )
    imports += '\nimport { GndPlanes, MountingHole } from "../blocks/glue"'
    lines = []
    for index, b in enumerate(ids):
        symbol, extra = INSTANTIATION[b]
        x, y = centres[b]
        sch_x = -6 + index * 6
        lines.append(
            f"    <{symbol} {extra} pcbX={{{x}}} pcbY={{{y}}} "
            f"schX={{{sch_x}}} schY={{0}} />".replace("  ", " ", 1)
        )
    for hole in plan["holes"]:
        lines.append(
            f'    <MountingHole name="{hole["name"]}" '
            f'diameter={{{hole["diameter_mm"]}}} '
            f'pcbX={{{hole["pcbX"]}}} pcbY={{{hole["pcbY"]}}} />'
        )
    # All golden powered blocks author one-port GND fanouts into the reserved
    # plane phase. A composition fixture without a plane tests a different
    # electrical contract and falsely turns short local drops into remote-net
    # max-length failures. One top pour is sufficient here because every
    # matrix block uses its default top-side placement; double-face behavior
    # has its own golden regressions.
    lines.append('    <GndPlanes layers={["top"]} />')
    body = "\n".join(lines)
    source = (
        f"// composition-matrix cell: {' + '.join(ids)}\n"
        f"// tscircuit dialect: pinned toolchain (see toolchain/package.json)\n"
        f"{imports}\n\n"
        f"export default () => (\n"
        f'  <board width="{width}mm" height="{height}mm" {BOARD_PROPS}>\n'
        f"{body}\n"
        f"  </board>\n"
        f")\n"
    )
    return source, (width, height)


def prepare_cell(root: Path, cell: tuple[str, ...]) -> tuple[Path, tuple[float, float]]:
    """Materialise one matrix cell as a real project on disk."""
    name = "__".join(cell)
    project = root / name
    project.mkdir(parents=True, exist_ok=True)
    for config in ("tsconfig.json", "tscircuit.config.json"):
        shutil.copy(SKELETON / config, project / config)
    sync_project(
        project,
        blocks=cell,
        source=BLOCKS_DIR,
        source_label="packages/golden-blocks/blocks",
    )
    source, (width, height) = board_source(cell)
    project.joinpath("product.json").write_text(
        json.dumps(
            {
                "name": f"matrix-{name}"[:60],
                "description": f"composition matrix cell: {' + '.join(cell)}",
                "power": "usb-c-5v",
                # Generous: the matrix tests composition, not size policy.
                "envelopeMm": [200, 200],
                "layers": 2,
                "fab": "jlcpcb",
                "assembly": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    boards = project / "boards"
    boards.mkdir(exist_ok=True)
    (boards / "main.tsx").write_text(source, encoding="utf-8")
    return project, (width, height)


def _record(outcome, size: tuple[float, float]) -> dict:
    """One cell's row in the matrix, read from the sidecar the build wrote."""
    cell = tuple(outcome.job.meta["cell"])
    record: dict = {
        "cell": list(cell),
        "blocks": len(cell),
        "seconds": round(outcome.seconds, 1),
        "boardMm": list(size),
    }
    if not outcome.ok:
        record.update({
            "status": "error",
            "fabReady": False,
            "blocking": [{"kind": "BuildCrashed", "part": "",
                          "detail": (outcome.error or "")[:400]}],
        })
        return record

    sidecar_path = Path(str(outcome.job.output)).with_name("main.board.json")
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        record.update({
            "status": "error", "fabReady": False,
            "blocking": [{"kind": "NoSidecar", "part": "", "detail": str(exc)}],
        })
        return record

    warnings = sidecar.get("validation", {}).get("warnings", [])
    blocking = [w for w in warnings if w.get("severity") == "error"]
    fab_ready = sidecar.get("fab", {}).get("ready") is True
    reported_blocking = [
        {"kind": w["kind"], "part": w.get("part", ""),
         "detail": w.get("detail", "")[:220]}
        for w in blocking
    ]
    if not fab_ready and not reported_blocking:
        reported_blocking.append({
            "kind": "NotFabReady",
            "part": "board",
            "detail": (
                "validation had no error finding, but the selected build did "
                "not produce a fab-ready manufacturing packet"
            ),
        })
    record.update({
        "status": _cell_status(blocking=blocking, fab_ready=fab_ready),
        "fabReady": fab_ready,
        "autorouterEffort": sidecar.get("build", {}).get(
            "autorouterEffort", "default"
        ),
        "blockingByAttempt": sidecar.get("build", {}).get("blockingByAttempt"),
        "bomLines": sidecar.get("bom", {}).get("lines"),
        "blocking": reported_blocking,
        "warningCount": len(warnings),
    })
    return record


def _cell_status(*, blocking: list[dict], fab_ready: bool) -> str:
    """A composition is clean only when its packet is orderable."""

    if blocking:
        return "blocked"
    return "clean" if fab_ready else "not-ready"


# ---------------------------------------------------------------------------
# Runner — exhaustive in compute, fast in wall-clock
# ---------------------------------------------------------------------------


def run(
    tier: str = "default",
    *,
    jobs: int | None = None,
    only: list[tuple[str, ...]] | None = None,
    sample: int | None = None,
    seed: int = 11,
    keep: bool = False,
) -> dict:
    from circuitpy.batch import BuildJob, build_many

    todo = only if only is not None else cells(tier)
    sampled_from = len(todo)
    if sample and sample < len(todo):
        todo = random.Random(seed).sample(todo, sample)

    root = Path(tempfile.mkdtemp(prefix="composition-matrix-"))
    build_jobs = []
    sizes: dict[tuple[str, ...], tuple[float, float]] = {}
    for cell in todo:
        project, size = prepare_cell(root, cell)
        sizes[cell] = size
        build_jobs.append(
            BuildJob(
                source=project / "boards" / "main.tsx",
                output=project / "boards" / "main.circuit.json",
                label=" + ".join(cell),
                meta={"cell": list(cell)},
            )
        )

    def _progress(outcome, done: int, total: int) -> None:
        cell = tuple(outcome.job.meta["cell"])
        mark = "ok  " if outcome.fab_ready else ("ERR " if not outcome.ok else "FAIL")
        print(
            f"[{done:>3}/{total}] {mark} {' + '.join(cell):<50} "
            f"{outcome.seconds:>5.0f}s",
            flush=True,
        )

    report = build_many(build_jobs, workers=jobs, on_done=_progress)
    results = [_record(o, sizes[tuple(o.job.meta["cell"])]) for o in report.outcomes]
    results.sort(key=lambda r: (r["blocks"], r["cell"]))

    clean = [r for r in results if r["status"] == "clean"]
    ready = [r for r in results if r.get("fabReady")]
    payload = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tier": tier,
        "cellsBuilt": len(results),
        "cellsInTier": sampled_from,
        "sampled": bool(sample and sample < sampled_from),
        "clean": len(clean),
        "fabReady": len(ready),
        "closureRate": round(len(ready) / len(results), 3) if results else 0.0,
        # Both numbers, always: the claim is about the ratio between them.
        "computeMinutes": round(report.compute_s / 60, 1),
        "wallMinutes": round(report.wall_s / 60, 1),
        "speedup": round(report.speedup, 1),
        "workers": report.workers,
        "summary": report.summary(),
        "knownUnsupported": {
            " + ".join(k): v for k, v in KNOWN_UNSUPPORTED.items()
        },
        "cells": results,
    }
    if keep:
        payload["projects"] = str(root)
    else:
        shutil.rmtree(root, ignore_errors=True)
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tier",
        default="default",
        choices=["singles", "pairs", "triples", "spine", "all", "default"],
    )
    parser.add_argument(
        "--jobs", type=int, default=4,
        help=(
            "workers (default 4). The parallelism budget is shared across "
            "every agent on this machine, not owned by this run: at "
            "cpu_count-1 on 2026-08-11 the matrix drove load to 79 on 16 "
            "cores and starved an example board into two build timeouts. "
            "Raise it when you own the box."
        ),
    )
    parser.add_argument("--keep", action="store_true",
                        help="keep the built projects for inspection")
    parser.add_argument("--sample", type=int, default=None)
    parser.add_argument("--only", default=None, help="comma-separated block ids")
    parser.add_argument("--report", default=str(REPORT))
    args = parser.parse_args(argv[1:])

    only = None
    if args.only:
        only = [tuple(sorted(b.strip() for b in args.only.split(",")))]

    known = set(INSTANTIATION)
    registry = set(block_ids())
    if registry - known:
        print(
            "the registry has blocks the matrix cannot instantiate — the "
            f"planner can emit an untested composition: {sorted(registry - known)}"
        )
        return 2

    report = run(args.tier, jobs=args.jobs, only=only, sample=args.sample,
                 keep=args.keep)
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    failed = [r for r in report["cells"] if r["status"] != "clean"]
    print(f"\n{report['summary']}")
    print(
        f"closure: {report['fabReady']}/{report['cellsBuilt']} compositions "
        f"fab-ready ({report['closureRate']:.0%})"
    )
    if failed:
        print("\nopen holes in the tested space:")
        for r in failed:
            kinds = ", ".join(sorted({b['kind'] for b in r["blocking"]}))
            print(f"  {' + '.join(r['cell'])}: {kinds}")
        print(f"\nreport: {args.report}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
