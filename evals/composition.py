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

Three tiers, cheapest first:

| Tier | Cells | What it proves |
|---|---|---|
| `singles` | 9 | each block alone (duplicates `packages/golden-blocks/tests/test_gauntlet.py`; off by default) |
| `pairs` | 36 | no two blocks break each other — the missing half |
| `spine` | 9 | the realistic minimum board: USB-C in, 3V3 rail, one block |
| `triples` | 84 | every three-block combination the planner can reach |

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

Exit: non-zero when any built cell carries a blocking warning, or when a cell
listed in ``KNOWN_UNSUPPORTED`` unexpectedly passes (a stale exclusion hides a
fix). Writes ``evals/composition-matrix.json`` — the record of what has been
built, which is the evidence behind the closure claim.
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
sys.path.insert(0, str(REPO / "packages" / "circuitpy" / "src"))
sys.path.insert(0, str(REPO / "skills" / "circuitcode"))  # circuitlib

os.environ.setdefault("CIRCUIT_PARTS_ENGINE", "off")

SKELETON = REPO / "skills" / "circuitcode" / "templates" / "project_skeleton"
BLOCKS_DIR = REPO / "packages" / "golden-blocks" / "blocks"
REPORT = Path(__file__).resolve().parent / "composition-matrix.json"

#: Board props every real board sets. A cell judged under different rules than
#: the boards that will use it is not really being judged (same reasoning as
#: the block gauntlet's BOARD_PROPS).
BOARD_PROPS = (
    'thickness={1.6} minTraceWidth="0.2mm" '
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
    "usb-c-power": ("UsbCPower", ""),
    "usb-c-data": ("UsbCData", ""),
    "ldo-3v3": ("Ldo3v3", ""),
    "i2c-bus": ("I2cBus", ""),
    "status-led": ("StatusLed", ""),
    "sw-tact": ("SwTact", 'name="SW1" signal="KEY1"'),
    "rp2040-core": ("Rp2040Core", ""),
    "sensor-bme280": ("SensorBme280", ""),
    "ws2812-chain": ("Ws2812Chain", "count={4}"),
}

#: Compositions the planner must NEVER emit, and why. These are excluded from
#: the matrix *and* asserted against the registry: if one ever builds clean the
#: exclusion is stale, and a stale exclusion hides a fix.
KNOWN_UNSUPPORTED: dict[tuple[str, ...], str] = {
    ("usb-c-data", "usb-c-power"): (
        "usb-c-data is a superset of usb-c-power and reuses the same refdes "
        "block (J1/R1/R2/U1/C1). Two USB-C entries on one board is a design "
        "error, not a layout problem — circuitlib.blocks says so in the "
        "usb-c-power notes, and helpers.board_plan never picks both."
    ),
}

#: The power spine every realistic board starts from.
SPINE = ("usb-c-power", "ldo-3v3")


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
            f'    <hole name="{hole["name"]}" '
            f'diameter="{hole["diameter_mm"]}mm" '
            f'pcbX={{{hole["pcbX"]}}} pcbY={{{hole["pcbY"]}}} />'
        )
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
    shutil.copytree(BLOCKS_DIR, project / "blocks", dirs_exist_ok=True)
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


def ruler() -> dict:
    """What the closure rate was measured against.

    A closure rate is only comparable to another one taken with the same
    checks against the same library. It can improve for two reasons and only
    one of them is good: the compositions got better, or the ruler got
    shorter. The agent eval has recorded this since 2026-08-11; the matrix did
    not, so its numbers could not be compared to each other at all — 56% on
    08-11 and a number today are two rates with no shared meaning unless the
    check set travels with them.

    Never raises: a missing ruler is a caveat on the report, not a lost run.
    """
    import subprocess

    out: dict[str, object] = {}
    try:
        out["gitHead"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO),
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        # NB: do not strip the whole blob before splitting — porcelain's first
        # column is a space for unstaged changes, so `.strip()` shifts line one
        # by a character and reports a path that does not exist.
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(REPO),
            capture_output=True, text=True, timeout=15,
        ).stdout
        paths = [line[3:] for line in dirty.splitlines() if line[3:].strip()]
        out["gitDirty"] = bool(paths)
        if paths:
            # Which files, not just the flag: a dirty tree that only touches
            # examples/ does not change what the matrix builds, and a reader
            # cannot tell those two situations apart from a boolean.
            out["gitDirtyPaths"] = sorted(paths)[:40]
    except Exception:  # noqa: BLE001
        pass
    try:
        from circuitpy import fab as fab_mod
        from circuitpy import toolchain

        out["toolchain"] = toolchain.versions()
        blocking = sorted(fab_mod.VERIFY_BLOCKING_KINDS)
        escalated = sorted(fab_mod.VERIFY_ESCALATED_KINDS)
        out["verifyBlockingKinds"] = blocking
        out["verifyEscalatedKinds"] = escalated
        # One short string that changes whenever the bar moves, so two runs
        # can be compared at a glance instead of by diffing two lists.
        import hashlib

        digest = hashlib.sha256(
            json.dumps(
                {"blocking": blocking, "escalated": escalated,
                 "toolchain": out["toolchain"]},
                sort_keys=True,
            ).encode()
        ).hexdigest()[:12]
        out["checkSetHash"] = digest
    except Exception as exc:  # noqa: BLE001
        out["rulerNote"] = f"could not read the check set: {exc}"
    return out


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
    record.update({
        "status": "clean" if not blocking else "blocked",
        "fabReady": bool(sidecar.get("fab", {}).get("ready")),
        "autorouterEffort": sidecar.get("build", {}).get(
            "autorouterEffort", "default"
        ),
        "blockingByAttempt": sidecar.get("build", {}).get("blockingByAttempt"),
        "bomLines": sidecar.get("bom", {}).get("lines"),
        "blocking": [
            {"kind": w["kind"], "part": w.get("part", ""),
             "detail": w.get("detail", "")[:220]}
            for w in blocking
        ],
        "warningCount": len(warnings),
    })
    return record


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
        # The ruler, recorded with the score. See ruler().
        "measuredAgainst": ruler(),
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
    rule = report.get("measuredAgainst", {})
    print(
        f"ruler  : {rule.get('gitHead', '?')}"
        f"{'+dirty' if rule.get('gitDirty') else ''}, "
        f"check-set {rule.get('checkSetHash', '?')} — "
        f"{len(rule.get('verifyBlockingKinds') or [])} blocking + "
        f"{len(rule.get('verifyEscalatedKinds') or [])} escalated verify kinds"
        f" — compare only against a run with the same set"
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
