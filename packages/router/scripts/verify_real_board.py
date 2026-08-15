"""Independent legality check on the *real* board, not a synthesized one.

``circuit_json_for_scoring`` is deliberately minimal — it carries what
``circuitpy.checks`` reads and nothing else. That is right for scoring and
wrong for a second opinion: ``routerlib.model.Pad`` stores a pad as
width/height/rotation, so a ``polygon`` pad loses its vertices and a ``circle``
pad loses its radius. Handed that back, ``@tscircuit/checks`` computes ``NaN``
clearances and ``checkEachPcbTraceNonOverlapping`` throws — the shorts check
never runs, and a crashed check reads exactly like a clean one.

So where the instance's placement still matches a board on disk, this takes the
other road: drop the router's copper into the **real** ``circuit.json`` with
``apply_solution`` and check that. Every pad keeps the geometry it was built
with; the only thing that changed is the copper. The placement is compared
first and the run refuses on a mismatch, because scoring a router against a
board it never saw produces a number that looks like a verdict and is not one.

    python3.12 scripts/verify_real_board.py --board harness-puck \
        --circuit-json examples/harness-puck/boards/main.circuit.json \
        --routers all --tournament work/tournament
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
for extra in (PACKAGE / "src", PACKAGE.parent / "circuitpy" / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import verify_pipeline as vp  # noqa: E402  (same directory)


def check_one(problem, board_json: list[dict], copper: list[dict] | None,
              work: Path) -> dict:
    """``copper=None`` runs the **empty-solution control**: the real board with
    every route removed. Anything the engines find there belongs to the
    placement, the pour or the footprints, and must be subtracted before a
    finding is charged to a router."""
    from routerlib.adapters import apply_solution
    from routerlib.model import RoutingSolution

    if copper is None:
        solution = RoutingSolution(router="empty", traces=(), vias=(), complete=False)
    else:
        solution = vp.rebuild_solution(problem, copper)
    circuit_json = apply_solution(board_json, problem, solution)
    out: dict = {}
    out["tscircuit"] = vp.tscircuit_checks(circuit_json, work)
    kc = vp.kicad_drc(circuit_json, work)
    kc.pop("raw", None)
    counts: dict[str, int] = {}
    copper_findings: list[dict] = []
    for f in kc.get("findings", ()):
        kind = vp.kicad_kind(str(f.get("detail") or ""))
        counts[kind] = counts.get(kind, 0) + 1
        if kind not in vp.KICAD_NON_COPPER:
            copper_findings.append({**f, "kicadKind": kind})
    kc["kindCounts"] = counts
    kc["copperFindings"] = copper_findings[:60]
    kc["copperFindingCount"] = len(copper_findings)
    kc.pop("findings", None)
    out["kicad"] = kc
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True, help="instance id")
    ap.add_argument("--circuit-json", required=True)
    ap.add_argument("--tournament", required=True)
    ap.add_argument("--routers", default="all")
    ap.add_argument("--allow-drift", action="store_true")
    ap.add_argument("--keep-pours", action="store_true")
    args = ap.parse_args(argv)

    from routerlib.adapters import problem_from_circuit_json
    from routerlib.bench import INSTANCE_DIR, correspondence, load_instance

    root = Path(args.tournament)
    problem = load_instance(Path(INSTANCE_DIR) / f"{args.board}.json")
    board_json = json.loads(Path(args.circuit_json).read_text())
    # The instance was extracted with ``strip_planes=True``: the router was
    # never told about this board's pours. Leaving them in would charge it for
    # a zone it could not see — a defect of the harness setup, not of the
    # route. Keepouts stay: those *are* in the instance.
    if not args.keep_pours:
        pours = sum(1 for e in board_json if e.get("type") == "pcb_copper_pour")
        board_json = [e for e in board_json if e.get("type") != "pcb_copper_pour"]
        if pours:
            print(f"stripped {pours} pcb_copper_pour (the instance has none)",
                  file=sys.stderr)
    on_disk = problem_from_circuit_json(
        board_json, problem_id=args.board, strip_routes=False, strip_planes=True
    )
    match = correspondence(problem, on_disk)
    if not match.matches and not args.allow_drift:
        print(match.report(), file=sys.stderr)
        print("refusing — this board is not the instance's placement", file=sys.stderr)
        return 2

    routers = (
        sorted(p.name for p in (root / "copper").iterdir() if p.is_dir())
        if args.routers == "all"
        else args.routers.split(",")
    )
    out_dir = root / "verify-real" / args.board
    out_dir.mkdir(parents=True, exist_ok=True)
    for router in ["_empty-control"] + routers:
        copper_path = root / "copper" / router / f"{args.board}.json"
        if router != "_empty-control" and not copper_path.is_file():
            continue
        out_path = out_dir / f"{router}.json"
        if out_path.exists():
            try:
                if json.loads(out_path.read_text()).get("ok"):
                    continue
            except Exception:  # noqa: BLE001
                pass
        work = Path(tempfile.mkdtemp(prefix="rt-real-"))
        row: dict = {"router": router, "instance": args.board,
                     "board": str(args.circuit_json)}
        t0 = time.perf_counter()
        try:
            row.update(check_one(
                problem,
                board_json,
                None if router == "_empty-control"
                else json.loads(copper_path.read_text()),
                work,
            ))
            row["ok"] = True
        except BaseException as exc:  # noqa: BLE001
            row["ok"] = False
            row["error"] = f"{type(exc).__name__}: {exc}"
            row["traceback"] = traceback.format_exc()[-3000:]
        finally:
            shutil.rmtree(work, ignore_errors=True)
        row["seconds"] = round(time.perf_counter() - t0, 1)
        out_path.write_text(json.dumps(row, indent=1) + "\n", encoding="utf-8")
        ts = (row.get("tscircuit") or {}).get("routing") or {}
        kc = row.get("kicad") or {}
        print(f"{args.board:<20}{router:<24} ts={ts.get('count')} "
              f"threw={len(ts.get('threw') or ())} "
              f"kicad={kc.get('copperFindingCount')} {row['seconds']}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
