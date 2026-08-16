"""Take a tournament cell's copper through the *real* pipeline, not the harness.

The harness scorer delegates its DFM checks to ``circuitpy.checks.dfm_warnings``
but owns copper-to-copper clearance, shorts, via-in-pad, keepout and edge
itself. Those five are exactly the checks nobody upstream has ever cross-
examined, so this script asks two engines that share no code with routerlib:

1. ``@tscircuit/checks`` ``runAllChecks`` via the packaged node helper — the
   same call ``circuitpy`` makes in stage 2 of a real build.
2. ``kicad-cli pcb drc`` on the converted board, with this fab's design rules
   written into the project file — the same call stage 3 makes.

A router that is clean in the harness and dirty here has found a bug in the
harness, and that matters more than its place in the ranking.

    python3.12 scripts/verify_pipeline.py --router maze-astar \
        --instance harness-puck --tournament work/tournament
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
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

KICAD_TIMEOUT_S = 600
EXPORT_TIMEOUT_S = 900


def rebuild_solution(problem, copper_elements: list[dict]):
    """Parse saved ``pcb_trace``/``pcb_via`` rows back into a solution.

    Round-trips through the adapter rather than hand-parsing, so the copper the
    pipeline sees is the copper the scorer saw.
    """
    from routerlib.adapters import circuit_json_for_scoring, problem_from_circuit_json
    from routerlib.model import RoutingSolution

    static = circuit_json_for_scoring(
        problem, RoutingSolution(router="empty", traces=(), vias=(), complete=False)
    )
    merged = static + list(copper_elements)
    routed = problem_from_circuit_json(
        merged, problem_id=problem.id, strip_routes=False, strip_planes=False
    )
    return RoutingSolution(
        router="rebuilt",
        traces=routed.existing_traces,
        vias=routed.existing_vias,
        complete=False,
    )


def netted_circuit_json(problem, solution) -> list[dict]:
    """``circuit_json_for_scoring`` plus the connectivity the *downstream tools*
    need to see nets at all.

    The scorer's minimal circuit.json is minimal on purpose — it carries exactly
    what ``circuitpy.checks`` reads. But ``@tscircuit/checks`` resolves a
    trace's legitimate landings through ``source_trace.connected_source_port_ids``
    (empty there), and ``circuit-json-to-kicad`` assigns a pad's net through its
    ``pcb_component``/``source_component`` (absent there). Handed the minimal
    file, both engines report every pad as netless and every trace touching its
    own pad as an accidental short: **41 phantom shorts on the baseline's
    output**, a router that by construction never places copper it cannot
    defend. Those findings are about the file, not the copper.

    So this adds the connective tissue and nothing else. No geometry is added,
    moved or removed — a check can still find anything it would have found.
    """
    from routerlib.adapters import circuit_json_for_scoring

    cj = circuit_json_for_scoring(problem, solution)
    comp_of = {p.id: (p.component or "unknown") for p in problem.pads}
    pad_of_port = {(p.port_id or f"port_{p.id}"): p.id for p in problem.pads}
    comps = sorted(set(comp_of.values()))
    src_comp = {c: f"source_component_{i}" for i, c in enumerate(comps)}
    pcb_comp = {c: f"pcb_component_{i}" for i, c in enumerate(comps)}

    header: list[dict] = []
    for c in comps:
        xs = [p.center.x for p in problem.pads if (p.component or "unknown") == c]
        ys = [p.center.y for p in problem.pads if (p.component or "unknown") == c]
        header.append({
            "type": "source_component",
            "source_component_id": src_comp[c],
            "ftype": "simple_chip",
            "name": c or "U?",
        })
        header.append({
            "type": "pcb_component",
            "pcb_component_id": pcb_comp[c],
            "source_component_id": src_comp[c],
            "center": {"x": (min(xs) + max(xs)) / 2, "y": (min(ys) + max(ys)) / 2},
            "width": max(max(xs) - min(xs), 0.1),
            "height": max(max(ys) - min(ys), 0.1),
            "layer": "top",
            "rotation": 0,
        })

    ports_by_net: dict[str, list[str]] = {}
    for e in cj:
        t = e.get("type")
        if t == "source_port":
            e["source_component_id"] = src_comp[comp_of.get(e["name"], "unknown")]
            ports_by_net.setdefault(
                e["subcircuit_connectivity_map_key"], []
            ).append(e["source_port_id"])
        elif t == "pcb_port":
            pad = pad_of_port.get(e["pcb_port_id"])
            e["pcb_component_id"] = pcb_comp[comp_of.get(pad, "unknown")]
            e["layers"] = ["top", "bottom"]
        elif t in ("pcb_smtpad", "pcb_plated_hole"):
            pid = e.get("pcb_smtpad_id") or e.get("pcb_plated_hole_id")
            e["pcb_component_id"] = pcb_comp[comp_of.get(pid, "unknown")]
            if t == "pcb_smtpad":
                # Every SMD pad used to go out as ``rotated_rect``, because the
                # instance had only ``x, y, w, h, rotation`` to give — a polygon
                # pad's vertices were discarded on the way in, and written back
                # as ``polygon`` with no ``points`` they made tscircuit's
                # geometry read undefined: NaN clearances, and
                # ``checkEachPcbTraceNonOverlapping`` throwing outright, which
                # cost 108 of 144 cells their shorts check. That workaround was
                # honest about the geometry routerlib reasoned with and wrong
                # about the board.
                #
                # Since the shape model landed, the pad carries its real
                # outline, so the real outline is what the second engine gets.
                # Reproduced 2026-08-16: strip the ``points`` back off and
                # ``runAllChecks`` throws in ``SpatialObjectIndex.addObject``
                # and returns nothing, which reads exactly like a clean board.
                e["ccw_rotation"] = float(e.get("ccw_rotation") or 0.0)
    for e in cj:
        if e.get("type") == "source_trace":
            e["connected_source_port_ids"] = sorted(
                ports_by_net.get(e["subcircuit_connectivity_map_key"], [])
            )
    return header + cj


#: KiCad DRC kinds that are about the environment or about completeness, not
#: about whether this copper is legal. Reported, never counted as a violation.
KICAD_NON_COPPER = (
    "lib_footprint_issues",
    "lib_footprint_mismatch",
    "footprint_type_mismatch",
    "unconnected_items",
    "silk_overlap",
    "silk_edge_clearance",
    "silk_over_copper",
    "text_height",
    "text_thickness",
    "missing_courtyard",
    "malformed_courtyard",
    "assertion_failure",
    "footprint_filters_mismatch",
    "duplicate_footprints",
    "extra_footprint",
    "net_conflict",
    "schematic_parity",
    "zones_intersect",
)


def kicad_kind(detail: str) -> str:
    detail = detail.strip()
    if detail.startswith("[") and "]" in detail:
        return detail[1:detail.index("]")]
    return "unknown"


def tscircuit_checks(circuit_json: list[dict], work: Path) -> dict:
    """``@tscircuit/checks``, one check at a time, failures isolated.

    Not ``runAllChecks``: that call runs everything in one pass with no guard,
    so any one check that throws loses the whole report — and a lost report is
    an empty findings list, which reads exactly like a clean board. Twelve of
    the sixteen instances carry a USB-C receptacle and twelve of sixteen came
    back empty that way.

    Two defences, because one is not enough. Each check runs by name with its
    own guard, and the result carries ``complete``: false the moment anything
    threw. **A caller that reads ``count`` without reading ``complete`` is
    reading a number that may mean nothing**, so the flag is not optional
    decoration — ``tournament_results.py`` drops an incomplete report rather
    than counting it as zero findings.
    """
    from circuitpy import toolchain

    path = work / "circuit.json"
    path.write_text(json.dumps(circuit_json), encoding="utf-8")
    t0 = time.perf_counter()
    try:
        output = toolchain.run_node(
            [str(HERE / "_js" / "routing_checks.cjs"), str(path)], timeout=600
        )
        report = json.loads(output.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}

    out: dict = {"seconds": round(time.perf_counter() - t0, 2)}
    for group in ("routing", "connectivity", "placement"):
        findings: list[dict] = []
        threw: list[str] = []
        counts: dict[str, int] = {}
        for entry in report.get(group, ()):
            if entry.get("status") != "ok":
                threw.append(f"{entry['name']}: {entry.get('error') or entry['status']}")
                continue
            for f in entry.get("findings", ()):
                kind = str(f.get("type") or f.get("error_type") or "unknown")
                counts[kind] = counts.get(kind, 0) + 1
                findings.append({
                    "check": entry["name"],
                    "kind": kind,
                    "message": str(f.get("message") or "")[:300],
                })
        out[group] = {
            "count": len(findings),
            "kindCounts": counts,
            "threw": threw,
            # False means "this number is not a measurement". Never read
            # ``count`` without it.
            "complete": not threw,
            "findings": findings[:40],
        }
    out["complete"] = all(
        out[g].get("complete") for g in ("routing", "connectivity", "placement")
    )
    return out


def dfm_checks(circuit_json: list[dict]) -> dict:
    from circuitpy import checks, fab
    from circuitpy.spec import ResolvedProduct

    profile = fab.get_profile("jlcpcb")
    product = ResolvedProduct(
        name="router-tournament",
        description="independent verification",
        power="usb-c-5v",
        envelope_mm=None,
        layers=2,
        fab="jlcpcb",
        assembly=True,
        path=Path("."),
    )
    findings = checks.dfm_warnings(circuit_json, product, profile)
    return {"findings": [dict(f) for f in findings]}


def kicad_drc(circuit_json: list[dict], work: Path) -> dict:
    from circuitpy import checks, fab, kicad_normalize, toolchain

    exe = toolchain.kicad_cli_exe()
    if exe is None:
        return {"skipped": "kicad-cli absent"}
    profile = fab.get_profile("jlcpcb")
    path = work / "circuit.json"
    path.write_text(json.dumps(circuit_json), encoding="utf-8")
    out: dict = {}
    t0 = time.perf_counter()
    try:
        toolchain.run_cli(
            ["export", str(path), "-f", "kicad_pcb", "-o", "board.kicad_pcb"],
            cwd=work,
            timeout=EXPORT_TIMEOUT_S,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"kicad conversion crashed: {exc}"}
    pcb = work / "board.kicad_pcb"
    if not pcb.is_file():
        return {"error": "kicad conversion produced no board.kicad_pcb"}
    out["convertSeconds"] = round(time.perf_counter() - t0, 2)
    try:
        kicad_normalize.normalize_for_fab(pcb, profile)
    except Exception as exc:  # noqa: BLE001
        out["normalizeError"] = str(exc)
    try:
        fab.write_kicad_project(pcb, profile)
    except Exception as exc:  # noqa: BLE001
        out["projectError"] = str(exc)
    drc_json = work / "drc.json"
    t1 = time.perf_counter()
    try:
        toolchain.run_kicad(
            [
                "pcb", "drc", "--all-track-errors", "--format", "json",
                "--severity-all", "--exit-code-violations",
                "-o", str(drc_json), str(pcb),
            ],
            timeout=KICAD_TIMEOUT_S,
            ok_codes=(0, 5),
        )
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"kicad DRC failed: {exc}"
        return out
    out["drcSeconds"] = round(time.perf_counter() - t1, 2)
    out["findings"] = [
        dict(f) for f in checks.parse_kicad_report(drc_json, kind="drc_violation")
    ]
    try:
        out["raw"] = json.loads(drc_json.read_text())
    except Exception:  # noqa: BLE001
        pass
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--router", required=True)
    ap.add_argument("--instance", required=True)
    ap.add_argument("--tournament", required=True)
    ap.add_argument("--no-kicad", action="store_true")
    ap.add_argument("--keep", default=None)
    args = ap.parse_args(argv)

    root = Path(args.tournament)
    copper_path = root / "copper" / args.router / f"{args.instance}.json"
    out_path = root / "verify" / args.router / f"{args.instance}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    row: dict = {"router": args.router, "instance": args.instance}
    work = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="rt-verify-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        from routerlib.bench import INSTANCE_DIR, load_instance
        from routerlib.scoring import score

        problem = load_instance(Path(INSTANCE_DIR) / f"{args.instance}.json")
        if args.router == "_empty-control":
            # The instance with no copper at all. Whatever the two engines find
            # here belongs to the placement, and must be subtracted before a
            # finding is charged to any router.
            from routerlib.model import RoutingSolution

            solution = RoutingSolution(
                router="empty", traces=(), vias=(), complete=False
            )
        else:
            copper = json.loads(copper_path.read_text())
            solution = rebuild_solution(problem, copper)
        circuit_json = netted_circuit_json(problem, solution)

        # Self-consistency: re-score the rebuilt copper. If this disagrees with
        # the judge's row, the round-trip lost something and every number below
        # is about a different board.
        rescored = score(problem, solution)
        row["rescored"] = {
            "completeness": round(rescored.completeness, 6),
            "errors": rescored.errors,
            "warnings": rescored.warnings,
            "violationsByKind": rescored.violations_by_kind,
            "viaCount": rescored.quality.via_count,
            "copperMm": rescored.quality.copper_mm,
        }
        row["dfm"] = dfm_checks(circuit_json)
        row["tscircuit"] = tscircuit_checks(circuit_json, work)
        if not args.no_kicad:
            kc = kicad_drc(circuit_json, work)
            kc.pop("raw", None)
            counts: dict[str, int] = {}
            copper_findings: list[dict] = []
            for f in kc.get("findings", ()):
                kind = kicad_kind(str(f.get("detail") or ""))
                counts[kind] = counts.get(kind, 0) + 1
                if kind not in KICAD_NON_COPPER:
                    copper_findings.append({**f, "kicadKind": kind})
            kc["kindCounts"] = counts
            kc["copperFindings"] = copper_findings[:60]
            kc["copperFindingCount"] = len(copper_findings)
            kc.pop("findings", None)
            row["kicad"] = kc
        row["ok"] = True
    except BaseException as exc:  # noqa: BLE001
        row["ok"] = False
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc()[-4000:]
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)

    out_path.write_text(json.dumps(row, indent=1) + "\n", encoding="utf-8")
    print(f"verify {args.router} {args.instance} ok={row.get('ok')}", flush=True)
    return 0 if row.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
