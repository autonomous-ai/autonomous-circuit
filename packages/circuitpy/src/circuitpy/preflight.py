"""Everything a board can be wrong about before anybody routes it — in seconds.

The loop an engineer actually lives in is *place, check, place again*, and until
now the only check that saw a placement cost a full build. Measured on
hydrate-coaster while six other builds were running: **20-40 minutes**. Ledger
#48 is the report of that from an engineer who followed the documentation into
it, and the reason is not slow checks — it is that **the compile routes the
board**, and routing is the expensive part.

So this compiles with the router switched off.

    <board routingDisabled …>

is a first-class tscircuit prop (`subcircuitGroupProps.routingDisabled`, read in
the shipped bundle). With it, hydrate-coaster compiles in **17 seconds** and
`fastcheck` grades the result in **434ms** — the same element scan, schematic
truth, footprint IoU, DFM limits and `@tscircuit/checks` the build runs.

**What that answers**: parts overlapping, a footprint that is not the part, a
component off the board, a hole in a pad, a pad-to-pad clearance, an assembly
risk, a board that misses the fab's price tier, decoupling too far from its pin.
Every one of them a placement question, which is what an engineer is iterating
on when they are moving parts.

**What it cannot answer, and says so rather than implying**: anything about
copper. There are no traces, so "port not connected" and "trace missing" are
guaranteed rather than informative, and they are dropped — with the drop
recorded in `not_checked`, because a check that silently withholds findings is
the thing this pipeline refuses to be. A clean pre-flight is not a clean board;
it is a board worth paying a build for.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

from circuitpy import checks, fastcheck, toolchain

#: Findings that exist *because* nothing was routed. Dropping them is not
#: leniency — with `routingDisabled` every one of them is guaranteed, so
#: keeping them would bury the placement findings this pass exists for under a
#: wall of noise nobody can act on.
ROUTING_DEPENDENT = frozenset({
    "pcb_port_not_connected_error",
    "pcb_trace_missing_error",
    "pcb_trace_error",
    "pcb_trace_too_long_warning",
    "pcb_via_trace_clearance_error",
    "pcb_pad_trace_clearance_error",
    "pcb_trace_non_overlapping_error",
    "source_trace_not_connected_error",
    "trace_left_its_pad",
    "dfm_power_trace_width",
    "power_width_widened",
    "netclass_trace_width",
    "netclass_pair_skew",
    "netclass_pair_coupling",
    "netclass_pair_reference",
    "diffpair_not_routed",
    "dfm_hole_clearance",
})

#: What a pre-flight is blind to, in the caller's own words. Printed, never
#: implied: the whole contract of this pass is that it is cheap *because* it
#: skipped something.
NOT_CHECKED = (
    {
        "what": "anything about routing",
        "why": "this board was compiled with the router switched off, which is "
               "what makes the answer take seconds. No traces exist, so nothing "
               "here can say whether the board can be wired, whether copper "
               "clears a drill, or how wide a rail came out.",
    },
    {
        "what": "the fab packet",
        "why": "no gerbers were plotted and no BOM was gated. 'orderable' is "
               "not a word this answer may use.",
    },
    {
        "what": "KiCad's ERC and DRC",
        "why": "the second substrate needs a converted board, which needs a "
               "routed one.",
    },
)

#: How long a routerless compile may take before something is wrong with it.
#:
#: The measured cost is ~17s on our densest board, and the first version capped
#: this at 300s on the reasoning that past five minutes the engineer would
#: rather know than wait. That was wrong in the one situation where a cheap
#: check matters most. An engineer building `env-logger-usb` (2026-08-17) hit
#: the ceiling **three times in a row** and lost ~20 minutes, then reproduced
#: the same board by hand in 15 seconds: the machine was carrying five
#: concurrent builds, and the documented wall-clock load factor on this
#: hardware is 5.2x on its own (see `generation.DEFAULT_BUILD_TIMEOUT_S`) —
#: a fleet of agents pushes well past that. A timeout that fires under load
#: does not save the engineer time, it costs them the whole answer and hands
#: back nothing they can act on.
#:
#: So the ceiling is a safety valve, not a budget: generous enough that a busy
#: machine still gets an answer, with `SLOW_COMPILE_S` below covering the other
#: half — a pre-flight that took ten minutes says so, rather than quietly
#: letting "17 seconds" stand as the promise.
COMPILE_TIMEOUT_S = float(os.environ.get("CIRCUIT_PREFLIGHT_TIMEOUT_S") or 1800.0)

#: Past this, the answer is still an answer but the speed claim is not true,
#: and the result says which. ~7x the measured cost on the densest board.
SLOW_COMPILE_S = 120.0

_BOARD_TAG = re.compile(r"<board\b")


def disable_routing(source: str) -> tuple[str, bool]:
    """Add `routingDisabled` to the board tag. Returns the text and whether it
    changed anything.

    Inserted immediately after `<board` rather than before the tag's close: the
    close may be many lines down, past props carrying `>` inside strings, and a
    boolean prop is valid JSX in first position.
    """
    text = str(source or "")
    if "routingDisabled" in text:
        return text, False
    match = _BOARD_TAG.search(text)
    if not match:
        return text, False
    at = match.end()
    return f"{text[:at]} routingDisabled{text[at:]}", True


def preflight(
    project_root: Path | str,
    entry: str = "boards/main.tsx",
    *,
    fab: str | None = None,
    keep: bool = False,
) -> dict:
    """Compile without the router and grade the placement. Never raises."""
    started = time.perf_counter()
    root = Path(project_root).expanduser().resolve()
    entry_rel = Path(entry)
    if entry_rel.is_absolute():
        try:
            entry_rel = entry_rel.relative_to(root)
        except ValueError:
            return _failed(f"{entry} is not inside {root}", started)
    if not (root / entry_rel).is_file():
        return _failed(f"no board source at {entry_rel}", started)

    scratch = Path(tempfile.mkdtemp(prefix="circuit-preflight-"))
    try:
        mirror = scratch / root.name
        # The project's own blocks and config travel with it; the build cache
        # and every generated artifact stay behind, so the mirror compiles from
        # source exactly as a fresh clone would.
        shutil.copytree(
            root,
            mirror,
            ignore=shutil.ignore_patterns(
                ".circuit", "*_fab", "*_review", "*.circuit.json", "*.board.json", "dist"
            ),
        )
        source_path = mirror / entry_rel
        patched, changed = disable_routing(source_path.read_text(encoding="utf-8"))
        if not changed:
            # Either the board already says it, or there is no `<board>` to
            # patch — the second is a source problem the compile will name.
            pass
        source_path.write_text(patched, encoding="utf-8")

        compile_started = time.perf_counter()
        try:
            toolchain.run_node(
                [toolchain.tscircuit_cli_exe(), "build", entry_rel.as_posix()],
                timeout=COMPILE_TIMEOUT_S,
                cwd=str(mirror),
            )
        except Exception as exc:  # noqa: BLE001
            return _failed(f"the board did not compile: {type(exc).__name__}: {exc}", started)
        compile_s = time.perf_counter() - compile_started

        stem = entry_rel.stem
        built = mirror / "dist" / "boards" / stem / "circuit.json"
        if not built.is_file():
            found = list((mirror / "dist").rglob("circuit.json"))
            if not found:
                return _failed("the compile produced no circuit.json", started)
            built = found[0]

        graded = fastcheck.fast_check(built, project_root=mirror, fab=fab)
        warnings = [
            w for w in graded.get("warnings", [])
            if str(w.get("kind")) not in ROUTING_DEPENDENT
        ]
        counts = {"error": 0, "warning": 0, "info": 0}
        for warning in warnings:
            severity = str(warning.get("severity") or "warning")
            if severity in counts:
                counts[severity] += 1
        dropped = len(graded.get("warnings", [])) - len(warnings)
        if compile_s > SLOW_COMPILE_S:
            # The verdict is sound; the promise on the tin is not. Said out
            # loud so an engineer knows the machine is the problem and does not
            # go looking for one in their board.
            warnings.append(checks.check_failed(
                f"this pre-flight took {compile_s:.0f}s, against ~17s on an idle "
                f"machine — the box is loaded, not your board. The findings "
                f"below are still exact"
            ))
        return {
            "ok": True,
            "verdict": "clean" if counts["error"] == 0 else "blocked",
            "geometry": "placement_only",
            "warnings": warnings,
            "counts": counts,
            "dropped_routing_findings": dropped,
            "checked": [
                line for line in graded.get("checked", [])
                if "checkTracesAreContiguous" not in line
            ],
            "not_checked": [dict(entry) for entry in NOT_CHECKED],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    finally:
        if not keep:
            shutil.rmtree(scratch, ignore_errors=True)


def _failed(reason: str, started: float) -> dict:
    return {
        "ok": False,
        "error": reason,
        "verdict": "unavailable",
        "geometry": "unknown",
        "warnings": [checks.check_failed(reason)],
        "counts": {"error": 0, "warning": 1, "info": 0},
        "dropped_routing_findings": 0,
        "checked": [],
        "not_checked": [dict(entry) for entry in NOT_CHECKED],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """One JSON line — the house convention, last line wins."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m circuitpy.preflight",
        description="Grade a board's placement in seconds, without routing it.",
    )
    parser.add_argument("project", type=Path)
    parser.add_argument("--board", default="boards/main.tsx",
                        help="board source, relative to the project root")
    parser.add_argument("--fab", default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    result = preflight(args.project, args.board, fab=args.fab)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
