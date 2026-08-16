"""Score copper that is already on disk, and build the calibration table.

Routing costs minutes; scoring quality costs half a second. Every tournament
cell's ``pcb_trace``/``pcb_via`` elements were written out by ``judge.py``, and
the incumbent's copper is sitting in the built boards under ``work/portfolio``
and ``examples/``. So the whole calibration is a replay — no router runs, no
board is built, and the copper is byte-identical to what the legality tables
were measured on.

    python3.12 -m routerlib.quality table --out work/quality/table.json
    python3.12 -m routerlib.quality render --table work/quality/table.json
    python3.12 -m routerlib.quality table --out work/quality/detail.json --detail
    python3.12 -m routerlib.quality paired --table work/quality/detail.json \
        --family maze-astar --source rerun-truepads

The published summary run is
``packages/router/benchmarks/tournament/quality-2026-08-16.json``. ``--detail``
keeps the per-net rows and multiplies the file size by about twenty-four, so it
is written to ``work/`` and not committed; ``paired`` needs it.

**The placement guard.** A cell is only scored when the copper's placement hash
still matches the instance it claims to be about; a fixture re-extracted since
the run describes a board that no longer exists, and scoring it anyway produces
a plausible number about nothing. Same rule as ``scripts/rescore.py``. The
incumbent rows carry the guard too: the comparison is only an A/B when both
sides are the same board.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
DEFAULT_TOURNAMENT = REPO / "work" / "tournament"
DEFAULT_RERUN = REPO / "work" / "tournament-truepads"
DEFAULT_GATE_OFF = REPO / "work" / "portfolio" / "gate-off"

#: The three boards we actually build, and the instance each was stripped from.
REAL_BOARDS = ("hydrate-coaster", "harness-puck", "terminal-keyboard")


# ---------------------------------------------------------------------------
# Currents, for the one metric that needs them
# ---------------------------------------------------------------------------


def currents_for(circuit_json) -> dict[str, float]:
    """Per-net peak current in mA, from ``verifylib``'s own load table.

    Keyed by the connectivity net id, which is the same string
    ``routerlib.adapters`` uses as a net id — checked, not assumed. Ground gets
    the heaviest rail's current, the same convention
    ``verifylib.netclass._width_findings`` uses, because ground is the return
    for every rail rather than a load of its own.

    Returns ``{}`` rather than raising when ``verifylib`` is not importable: a
    missing current means the power metric reports resistance only, which is a
    smaller loss than a table that will not build.
    """
    try:
        verify_src = str(REPO / "packages" / "verify" / "src")
        if verify_src not in sys.path:
            sys.path.insert(0, verify_src)
        from verifylib.model import Board
        from verifylib.netclass import _Loads
    except Exception:  # noqa: BLE001
        return {}
    try:
        board = Board(circuit_json)
        loads = _Loads(board)
        out = {k: float(v) for k, v in loads.per_net.items() if v > 0}
        heaviest = max(out.values(), default=0.0)
        for net in board.nets:
            if net.is_ground and heaviest > 0:
                out[net.key] = heaviest
        return dict(sorted(out.items()))
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# One cell
# ---------------------------------------------------------------------------


def _score(job: dict) -> dict:
    """Worker: load an instance, replay copper, score quality. Never raises."""
    for extra in (
        str(REPO / "packages" / "router" / "src"),
        str(REPO / "packages" / "circuitpy" / "src"),
    ):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    os.environ.setdefault("CIRCUIT_PARTS_ENGINE", "off")

    out = {
        "source": job["source"],
        "router": job["router"],
        "instance": job["instance"],
    }
    try:
        from routerlib.adapters import problem_from_circuit_json, solution_from_elements
        from routerlib.bench import load_instance, placement_hash
        from routerlib.model import RoutingSolution
        from routerlib.quality import measure

        problem = load_instance(job["instance_path"])
        out["placementHash"] = placement_hash(problem)

        if job["kind"] == "elements":
            expected = job.get("expected_placement")
            if expected and expected != out["placementHash"]:
                out["error"] = (
                    f"placement moved since this copper was routed: "
                    f"{expected} -> {out['placementHash']}"
                )
                out["stale"] = True
                return out
            elements = json.loads(Path(job["copper_path"]).read_text(encoding="utf-8"))
            solution = solution_from_elements(problem, elements, router=job["router"])
        else:  # "board" — the incumbent's own copper, out of a built board
            raw = json.loads(Path(job["copper_path"]).read_text(encoding="utf-8"))
            raw = [e for e in raw if e.get("type") != "pcb_copper_pour"]
            built = problem_from_circuit_json(
                raw,
                problem_id=job["instance"],
                strip_routes=False,
                strip_planes=True,
            )
            board_hash = placement_hash(
                problem_from_circuit_json(
                    raw, problem_id=job["instance"],
                    strip_routes=True, strip_planes=True,
                )
            )
            out["boardPlacementHash"] = board_hash
            out["placementMatch"] = board_hash == out["placementHash"]
            if not out["placementMatch"] and not job.get("allow_drift"):
                out["error"] = (
                    f"board placement {board_hash} is not the instance's "
                    f"{out['placementHash']}"
                )
                out["stale"] = True
                return out
            if not out["placementMatch"]:
                # Score the incumbent against its *own* board rather than an
                # instance it does not match. Flagged, never silently mixed.
                problem = problem_from_circuit_json(
                    raw, problem_id=job["instance"],
                    strip_routes=True, strip_planes=True,
                )
            solution = RoutingSolution(
                router=job["router"],
                traces=built.existing_traces,
                vias=built.existing_vias,
                complete=False,
            )

        report = measure(
            problem, solution, currents_ma=job.get("currents") or None
        )
        out["qualityRuler"] = report.ruler.hash
        out["summary"] = report.summary()
        if job.get("detail"):
            out["detail"] = report.as_dict()
    except Exception as exc:  # noqa: BLE001 - a failure is a result
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


def _instance_dir() -> Path:
    return REPO / "packages" / "router" / "benchmarks" / "instances"


def _jobs_from_tournament(
    root: Path, source: str, currents: dict, *, detail: bool = False
) -> list[dict]:
    jobs: list[dict] = []
    rows = root / "rows"
    if not rows.is_dir():
        return jobs
    for rdir in sorted(rows.iterdir()):
        if not rdir.is_dir():
            continue
        for row_path in sorted(rdir.glob("*.json")):
            copper = root / "copper" / rdir.name / f"{row_path.stem}.json"
            if not copper.is_file():
                continue
            row = json.loads(row_path.read_text(encoding="utf-8"))
            if not row.get("ok"):
                continue
            instance = row_path.stem
            jobs.append(
                {
                    "kind": "elements",
                    "source": source,
                    "router": rdir.name,
                    "instance": instance,
                    "instance_path": str(_instance_dir() / f"{instance}.json"),
                    "copper_path": str(copper),
                    "expected_placement": row.get("placementHash"),
                    "currents": currents.get(instance, {}),
                    "detail": detail,
                }
            )
    return jobs


def _incumbent_jobs(
    gate_off: Path, currents: dict, *, detail: bool = False
) -> list[dict]:
    jobs: list[dict] = []
    if gate_off.is_dir():
        for cell in sorted(gate_off.iterdir()):
            board = cell / "boards" / "main.circuit.json"
            instance = f"matrix-{cell.name}"
            path = _instance_dir() / f"{instance}.json"
            if not (board.is_file() and path.is_file()):
                continue
            jobs.append(
                {
                    "kind": "board",
                    "source": "incumbent",
                    "router": "tscircuit-autorouter",
                    "instance": instance,
                    "instance_path": str(path),
                    "copper_path": str(board),
                    "currents": currents.get(instance, {}),
                    "detail": detail,
                }
            )
    for name in REAL_BOARDS:
        board = REPO / "examples" / name / "boards" / "main.circuit.json"
        path = _instance_dir() / f"{name}.json"
        if not (board.is_file() and path.is_file()):
            continue
        jobs.append(
            {
                "kind": "board",
                "source": "incumbent",
                "router": "tscircuit-autorouter",
                "instance": name,
                "instance_path": str(path),
                "copper_path": str(board),
                # terminal-keyboard's fixture predates the 100x90 rebuild, so
                # its board no longer matches. Scored against its own board and
                # marked, rather than dropped: the incumbent's own quality is
                # still worth knowing, it just is not an A/B row.
                "allow_drift": True,
                "currents": currents.get(name, {}),
                "detail": detail,
            }
        )
    return jobs


def _collect_currents(gate_off: Path) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    if gate_off.is_dir():
        for cell in sorted(gate_off.iterdir()):
            board = cell / "boards" / "main.circuit.json"
            if board.is_file():
                out[f"matrix-{cell.name}"] = currents_for(
                    json.loads(board.read_text(encoding="utf-8"))
                )
    for name in REAL_BOARDS:
        board = REPO / "examples" / name / "boards" / "main.circuit.json"
        if board.is_file():
            out[name] = currents_for(json.loads(board.read_text(encoding="utf-8")))
    for base in ("hydrate-coaster", "terminal-keyboard"):
        if base in out:
            out.setdefault(f"{base}-plane", out[base])
    return out


def cmd_table(args) -> int:
    currents = _collect_currents(Path(args.gate_off))
    jobs: list[dict] = []
    jobs += _jobs_from_tournament(
        Path(args.tournament), "tournament", currents, detail=args.detail
    )
    jobs += _jobs_from_tournament(
        Path(args.rerun), "rerun-truepads", currents, detail=args.detail
    )
    jobs += _incumbent_jobs(Path(args.gate_off), currents, detail=args.detail)
    if args.only:
        wanted = set(args.only.split(","))
        jobs = [j for j in jobs if j["router"] in wanted or j["source"] in wanted]
    print(f"{len(jobs)} cells, {args.jobs} at a time", flush=True)

    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        cells = list(pool.map(_score, jobs, chunksize=2))

    rulers = sorted({c["qualityRuler"] for c in cells if c.get("qualityRuler")})
    payload = {
        "what": (
            "quality metrics over copper already on disk. No router ran; the "
            "copper is byte-identical to the runs the legality tables scored."
        ),
        "qualityRulerHash": rulers,
        "instanceDir": str(_instance_dir()),
        "currentsAvailableFor": sorted(k for k, v in currents.items() if v),
        "cells": cells,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")

    failed = [c for c in cells if c.get("error")]
    print(f"wrote {out}: {len(cells)} cells, {len(failed)} failed, "
          f"ruler(s) {', '.join(rulers)}")
    for c in failed[:10]:
        print(f"  FAILED {c['router']:<24} {c['instance']:<46} {c['error'][:70]}")
    return 0


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

#: (key, header, digits, higher_is_better). The columns a family is ranked on.
FAMILY_COLUMNS = (
    ("meanReturnMm", "return mm", 3, False),
    ("loopAreaMm2", "loop mm2", 0, False),
    ("detourRatio", "detour", 3, False),
    ("crossings", "cross", 0, False),
    ("selfCrossings", "self", 0, False),
    ("bends", "bends", 0, False),
    ("vias", "vias", 0, False),
    ("danglingVias", "dangle", 0, False),
    ("referencedFraction", "ref", 3, True),
    ("pairCoupled", "couple", 3, True),
    ("powerWorstMohm", "pwr mohm", 1, False),
    ("powerWorstDropMv", "drop mV", 1, False),
    ("powerMaxDaisy", "daisy", 0, False),
)


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _fmt(value, digits):
    if value is None:
        return "-"
    return f"{value:.{digits}f}" if digits else f"{value:.0f}"


def cmd_render(args) -> int:
    payload = json.loads(Path(args.table).read_text(encoding="utf-8"))
    cells = [c for c in payload["cells"] if c.get("summary")]

    if args.instances:
        wanted = set(args.instances.split(","))
        cells = [c for c in cells if c["instance"] in wanted]

    groups: dict[tuple[str, str], list[dict]] = {}
    for cell in cells:
        groups.setdefault((cell["source"], cell["router"]), []).append(cell)

    header = f"{'source/family':<38}{'n':>4}"
    for _, name, _, _ in FAMILY_COLUMNS:
        header += f"{name:>11}"
    print(header)
    print("-" * len(header))
    for (source, router), rows in sorted(groups.items()):
        line = f"{source + '/' + router:<38}{len(rows):>4}"
        for key, _, digits, _ in FAMILY_COLUMNS:
            line += f"{_fmt(_mean([r['summary'].get(key) for r in rows]), digits):>11}"
        print(line)
    print(f"\nquality ruler: {', '.join(payload.get('qualityRulerHash') or [])}")
    return 0


# ---------------------------------------------------------------------------
# The paired comparison — the only fair way to read this against the incumbent
# ---------------------------------------------------------------------------


def _common_net_rows(cell: dict) -> dict[str, dict]:
    detail = cell.get("detail") or {}
    rows = {r["net"]: dict(r) for r in (detail.get("detour") or {}).get("nets", ())}
    for loop in (detail.get("loopArea") or {}).get("nets", ()):
        if loop["net"] in rows:
            rows[loop["net"]]["loopAreaMm2"] = loop["loopAreaMm2"]
            rows[loop["net"]]["loopLengthMm"] = loop["lengthMm"]
    return rows


def _restrict(rows: dict[str, dict], nets: set[str]) -> dict:
    routed = sum(rows[n]["routedMm"] for n in nets)
    spanning = sum(rows[n]["mstMm"] for n in nets)
    loop = sum(rows[n].get("loopAreaMm2", 0.0) for n in nets)
    loop_len = sum(rows[n].get("loopLengthMm", 0.0) for n in nets)
    return {
        "nets": len(nets),
        "routedMm": routed,
        "detourRatio": (routed / spanning) if spanning > 0 else None,
        "loopAreaMm2": loop,
        "meanReturnMm": (loop / loop_len) if loop_len > 0 else None,
        "vias": sum(rows[n]["vias"] for n in nets),
        "bends": sum(rows[n]["bends"] for n in nets),
    }


def cmd_paired(args) -> int:
    """Incumbent against one family, on the nets **both** of them connected.

    Raw totals cannot answer this question. Our routers leave nets unrouted, so
    they lay less copper, enclose less loop area and drill fewer vias — a
    router that routes nothing wins every absolute column. So the comparison is
    restricted to the intersection of the two connected sets, and the counts on
    each side are printed beside it so the restriction is visible.
    """
    payload = json.loads(Path(args.table).read_text(encoding="utf-8"))
    cells = [c for c in payload["cells"] if c.get("detail")]
    incumbent = {
        c["instance"]: c for c in cells
        if c["source"] == "incumbent" and c.get("placementMatch")
    }
    ours = {
        c["instance"]: c for c in cells
        if c["router"] == args.family and c["source"] == args.source
    }
    shared = sorted(set(incumbent) & set(ours))
    if not shared:
        print(f"no instance has both the incumbent and {args.source}/{args.family} "
              f"with --detail")
        return 1

    header = (f"{'instance':<44}{'side':<12}{'nets':>6}{'detour':>9}"
              f"{'loop mm2':>11}{'return':>9}{'vias':>7}{'bends':>8}")
    print(header)
    print("-" * len(header))
    totals = {"incumbent": [], "ours": []}
    for instance in shared:
        a = _common_net_rows(incumbent[instance])
        b = _common_net_rows(ours[instance])
        common = set(a) & set(b)
        if not common:
            continue
        for label, rows, cell in (
            ("incumbent", a, incumbent[instance]), ("ours", b, ours[instance])
        ):
            r = _restrict(rows, common)
            totals[label].append(r)
            connected = len(rows)
            print(f"{instance if label == 'incumbent' else '':<44}{label:<12}"
                  f"{connected:>6}{_fmt(r['detourRatio'], 3):>9}"
                  f"{_fmt(r['loopAreaMm2'], 0):>11}{_fmt(r['meanReturnMm'], 3):>9}"
                  f"{r['vias']:>7}{r['bends']:>8}")
    print("-" * len(header))
    for label in ("incumbent", "ours"):
        rows = totals[label]
        routed = sum(r["routedMm"] for r in rows)
        loop = sum(r["loopAreaMm2"] for r in rows)
        detours = [r["detourRatio"] for r in rows if r["detourRatio"] is not None]
        print(f"{'ALL (' + str(len(rows)) + ' boards, common nets)':<44}{label:<12}"
              f"{sum(r['nets'] for r in rows):>6}"
              f"{_fmt(_mean(detours), 3):>9}{_fmt(loop, 0):>11}"
              f"{'':>9}{sum(r['vias'] for r in rows):>7}"
              f"{sum(r['bends'] for r in rows):>8}")
    print(f"\nquality ruler: {', '.join(payload.get('qualityRulerHash') or [])}")
    print(f"family       : {args.source}/{args.family}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="routerlib.quality")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("table", help="score every copper set on disk")
    t.add_argument("--out", required=True)
    t.add_argument("--tournament", default=str(DEFAULT_TOURNAMENT))
    t.add_argument("--rerun", default=str(DEFAULT_RERUN))
    t.add_argument("--gate-off", default=str(DEFAULT_GATE_OFF))
    t.add_argument("--jobs", type=int, default=4)
    t.add_argument("--only", default=None, help="comma list of routers or sources")
    t.add_argument("--detail", action="store_true",
                   help="keep the per-net rows, so `render --mode paired` can "
                        "restrict a comparison to the nets both sides connected")
    t.set_defaults(func=cmd_table)

    r = sub.add_parser("render", help="print the family table")
    r.add_argument("--table", required=True)
    r.add_argument("--instances", default=None, help="comma list, to restrict")
    r.set_defaults(func=cmd_render)

    p = sub.add_parser(
        "paired", help="incumbent vs one family, on the nets both connected"
    )
    p.add_argument("--table", required=True)
    p.add_argument("--family", required=True)
    p.add_argument("--source", default="rerun-truepads")
    p.set_defaults(func=cmd_paired)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
