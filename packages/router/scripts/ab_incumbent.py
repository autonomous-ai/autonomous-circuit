"""A/B the shipped autorouter against the portfolio, on the same board.

One board, one checker, one thing different: the copper.

    python3.12 packages/router/scripts/ab_incumbent.py --board hydrate-coaster \
        --rev HEAD --out work/portfolio/ab

The board comes out of a **named revision** rather than the working tree.
``examples/`` is rebuilt by other agents several times a day — while this was
being written, ``harness-puck``'s ``circuit.json`` on disk had zero traces
mid-rebuild — and a comparison against a file that is moving is not a
comparison. The revision is recorded in the output.

Three copper sets go through the identical path:

``control``    the board with every route removed. Whatever the engines find
               here belongs to the placement, the footprints or the pour, and
               is subtracted from both sides. A finding neither router caused
               must not be charged to either.
``incumbent``  the copper already in the file — the shipped tscircuit
               autorouter's answer, at whatever effort that build used.
``portfolio``  ``routerlib.portfolio`` over the same placement, copper stripped.

Pours are stripped from all three. A ``pcb_copper_pour`` in a built board was
generated *after* routing and is carved around the incumbent's traces; leaving
it in would hand the incumbent a zone shaped like its own answer and hand the
portfolio a zone shaped like somebody else's. That is a real limitation of
replacing copper this late and it is reported, not hidden.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
REPO = PACKAGE.parent.parent
for extra in (PACKAGE / "src", PACKAGE.parent / "circuitpy" / "src", HERE):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import verify_pipeline as vp  # noqa: E402


def board_at(rev: str, board: str) -> tuple[list[dict], str]:
    """The board's circuit.json at a revision, plus the resolved commit."""
    rel = f"examples/{board}/boards/main.circuit.json"
    sha = subprocess.run(
        ["git", "rev-parse", rev], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()
    blob = subprocess.run(
        ["git", "show", f"{sha}:{rel}"], cwd=REPO,
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(blob), sha


def check(board_json: list[dict], problem, solution, work: Path) -> dict:
    from routerlib.adapters import apply_solution

    circuit_json = apply_solution(board_json, problem, solution)
    ts = vp.tscircuit_checks(circuit_json, work)
    kc = vp.kicad_drc(circuit_json, work)
    kc.pop("raw", None)
    counts: dict[str, int] = {}
    copper: list[dict] = []
    for f in kc.get("findings", ()):
        kind = vp.kicad_kind(str(f.get("detail") or ""))
        counts[kind] = counts.get(kind, 0) + 1
        if kind not in vp.KICAD_NON_COPPER:
            copper.append({**f, "kicadKind": kind})
    kc["kindCounts"] = counts
    kc["copperFindings"] = copper[:40]
    kc["copperFindingCount"] = len(copper)
    kc.pop("findings", None)
    return {"tscircuit": ts, "kicad": kc}


def routing_count(row: dict) -> int | None:
    ts = row.get("tscircuit") or {}
    if "error" in ts:
        return None
    return (ts.get("routing") or {}).get("count", 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True)
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--out", default=str(REPO / "work" / "portfolio" / "ab"))
    ap.add_argument("--mode", default="relay",
                    choices=("single", "best-of-n", "relay"))
    ap.add_argument("--budget-class", default="thorough",
                    choices=("cheap", "standard", "thorough"))
    ap.add_argument("--max-iterations", type=int, default=2_000_000)
    ap.add_argument("--max-nodes", type=int, default=20_000_000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    from routerlib import portfolio
    from routerlib.adapters import problem_from_circuit_json
    from routerlib.bench import features_of
    from routerlib.model import Budget, RoutingSolution
    from routerlib.scoring import score

    from judge import registry  # scripts/judge.py — the tournament's own loader

    board_json, sha = board_at(args.rev, args.board)
    pours = sum(1 for e in board_json if e.get("type") == "pcb_copper_pour")
    board_json = [e for e in board_json if e.get("type") != "pcb_copper_pour"]

    # The problem comes from this same file, so the placement matches by
    # construction — no committed instance, no drift question to answer.
    problem = problem_from_circuit_json(
        board_json, problem_id=args.board, strip_routes=True, strip_planes=True
    )
    with_copper = problem_from_circuit_json(
        board_json, problem_id=args.board, strip_routes=False, strip_planes=True
    )
    incumbent = RoutingSolution(
        router="tscircuit-autorouter",
        traces=with_copper.existing_traces,
        vias=with_copper.existing_vias,
        complete=False,
    )
    empty = RoutingSolution(router="empty", traces=(), vias=(), complete=False)

    budget = Budget(max_iterations=args.max_iterations,
                    max_nodes=args.max_nodes, seed=args.seed)
    t0 = time.perf_counter()
    result = portfolio.route(
        problem, budget, registry(),
        budget_class=args.budget_class, mode=args.mode,
    )
    portfolio_seconds = time.perf_counter() - t0

    out: dict = {
        "board": args.board,
        "rev": args.rev,
        "commit": sha,
        "poursStripped": pours,
        "features": vars(features_of(problem)),
        "selection": result.selection.as_dict(),
        "stages": [s.as_dict() for s in result.stages],
        "portfolioSeconds": round(portfolio_seconds, 1),
        "harness": {},
        "pipeline": {},
        "fingerprint": result.solution.fingerprint(),
    }
    for name, solution in (("incumbent", incumbent), ("portfolio", result.solution)):
        s = score(problem, solution)
        out["harness"][name] = {
            "completeness": round(s.completeness, 6),
            "connectedNets": s.connected_nets,
            "routableNets": s.routable_nets,
            "errors": s.errors,
            "warnings": s.warnings,
            "byKind": s.violations_by_kind,
            "vias": s.quality.via_count,
            "copperMm": round(s.quality.copper_mm, 1),
            "diffPairCoupling": s.quality.diff_pair_coupling,
        }
        out["harness"][name]["rulerHash"] = s.ruler.hash

    for name, solution in (("control", empty), ("incumbent", incumbent),
                           ("portfolio", result.solution)):
        work = Path(tempfile.mkdtemp(prefix=f"ab-{name}-"))
        t = time.perf_counter()
        try:
            out["pipeline"][name] = check(board_json, problem, solution, work)
        except BaseException as exc:  # noqa: BLE001
            out["pipeline"][name] = {"error": f"{type(exc).__name__}: {exc}",
                                     "traceback": traceback.format_exc()[-2000:]}
        finally:
            shutil.rmtree(work, ignore_errors=True)
        out["pipeline"][name]["seconds"] = round(time.perf_counter() - t, 1)
        print(f"  checked {name} in {out['pipeline'][name]['seconds']}s", flush=True)

    def kicad_count(row: dict) -> int | None:
        """``None`` when the engine did not run.

        A conversion that produced no ``board.kicad_pcb`` leaves
        ``copperFindingCount: 0`` beside an ``error``, and read as a number
        that is a clean board. It happened once while this was being written —
        ``matrix-status-led__ws2812-chain`` came back with zero findings on
        copper that another run scored at seven. A check that did not run must
        never read as a check that passed.
        """
        k = row.get("kicad") or {}
        if k.get("error") or k.get("skipped"):
            return None
        return k.get("copperFindingCount")

    ctrl = out["pipeline"]["control"]
    ck = kicad_count(ctrl)
    ct = routing_count(ctrl)
    for name in ("incumbent", "portfolio"):
        row = out["pipeline"][name]
        k = kicad_count(row)
        t_ = routing_count(row)
        row["net"] = {
            "kicadCopper": None if (k is None or ck is None) else k - ck,
            "tscircuitRouting": None if (t_ is None or ct is None) else t_ - ct,
        }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{args.board}.json"
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")

    h = out["harness"]
    print(f"\n{args.board} @ {sha[:9]}  ({pours} pour(s) stripped)")
    print(f"{'':<14}{'routed':>9}{'harnessErr':>12}{'kicadErr':>10}{'tsErr':>8}"
          f"{'vias':>7}{'copper mm':>11}")
    for name in ("incumbent", "portfolio"):
        n = out["pipeline"][name]["net"]
        print(f"{name:<14}{h[name]['completeness'] * 100:>8.1f}%{h[name]['errors']:>12}"
              f"{str(n['kicadCopper']):>10}{str(n['tscircuitRouting']):>8}"
              f"{h[name]['vias']:>7}{h[name]['copperMm']:>11.0f}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
