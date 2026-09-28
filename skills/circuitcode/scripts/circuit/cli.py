"""``python scripts/circuit <boards/main.tsx | project_dir> [flags]`` — the build.

Hands the board source to ``circuitpy.generation.build_board`` inside a
worker subprocess. circuitpy compiles the TSX with the pinned tscircuit
toolchain, scans and re-checks the compiled JSON, crosses to KiCad for ERC
/DRC and the shipping gerbers, writes the fab packet, and lands the full
contract §1 artifact set next to the output path:

  <stem>.circuit.json  <stem>.board.json  <stem>_review/  <stem>_fab/

Prints a single JSON line on stdout matching contract §3
``CircuitcodeResult``. Every path in it is workspace-relative.

``--recheck`` / ``--edits`` are repair mode (2026-09-10): the routed board is
the input, not the TSX — no compile, no router, ~30 s.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common.runner import WALL_CLOCK_TIMEOUT_S, run_sandboxed_sync


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts/circuit",
        description=(
            "Build a board (boards/<stem>.tsx, or a project directory holding "
            "boards/main.tsx) into the compiled circuit.json + sidecar + review "
            "images + fab packet via the circuitpy pipeline."
        ),
    )
    p.add_argument(
        "input",
        type=Path,
        help=(
            "Path to a board source .tsx file (the normal case: "
            "boards/main.tsx) OR a project directory containing "
            "boards/main.tsx. The project root is the nearest ancestor "
            "holding product.json."
        ),
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Where to write artifacts (default: the board source's directory).",
    )
    p.add_argument(
        "--stem",
        default=None,
        help="Override the output filename stem (default: the source's stem).",
    )
    p.add_argument(
        "--fab",
        default=None,
        help=(
            "Fab profile id. Default: env CIRCUIT_FAB, else 'jlcpcb' (v1's "
            "only real profile)."
        ),
    )
    p.add_argument(
        "--recheck",
        action="store_true",
        help=(
            "REPAIR MODE: skip the compile and the router, take the existing "
            "<stem>.circuit.json as the routed board, and re-run every later "
            "stage on it (checks, KiCad, DFM, packet, renders, sidecar) in "
            "about thirty seconds."
        ),
    )
    p.add_argument(
        "--edits",
        type=Path,
        default=None,
        help=(
            "REPAIR MODE with edits: apply this JSON list of copper edits "
            "(move_point / move_via / insert_point / delete_points, see "
            "circuitpy.repair) to <stem>.circuit.json, then --recheck. A refused "
            "edit leaves the board untouched."
        ),
    )
    p.add_argument(
        "--undo-repair",
        action="store_true",
        help=(
            "REPAIR MODE: put <stem>.circuit.json back to the checkpoint taken "
            "before the last --edits, then --recheck. Every --edits takes one "
            "(the last 10 are kept under .circuit/repair-undo/)."
        ),
    )
    p.add_argument(
        "--wall-clock-s",
        type=float,
        default=WALL_CLOCK_TIMEOUT_S,
        help=(
            f"Wall-clock timeout for the worker subprocess (seconds, default "
            f"{WALL_CLOCK_TIMEOUT_S:g}). Raise for large boards on a cold "
            "toolchain cache."
        ),
    )
    return p


def _fail(message: str, code: str = "VALIDATION_FAILED") -> int:
    print(json.dumps({"ok": False, "error": {"code": code, "message": message}}))
    return 2


def project_root_for(source: Path) -> Path:
    """The nearest ancestor of the board source holding `product.json`, else the
    conventional `<project>/boards/<stem>.tsx` parent-of-parent."""
    for ancestor in source.resolve().parents:
        if (ancestor / "product.json").is_file():
            return ancestor
    return source.resolve().parent.parent


def write_failure_verdict(source: Path, payload: dict) -> Path | None:
    """`.harness/verdict.json` for a build that never reached the sidecar.

    The pipeline writes the verdict beside the sidecar on success
    (`circuitpy.generation.write_harness_verdict`); a build that dies in compile,
    times out, or cannot even import the pipeline writes no sidecar — and a host
    that only reads the verdict file would keep showing the previous build's
    state. So the CLI writes the failure itself, from the same error the stdout
    line carries. Self-contained on purpose (no circuitpy import: the failure may
    be that circuitpy is missing). Best-effort, atomic, never raises; a miss goes
    to stderr because stdout is the one-JSON-line channel.
    """
    try:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(error.get("code") or "RUNTIME_ERROR")
        message = str(error.get("message") or "the build failed before it produced a board")
        verdict = {
            "spec": 1,
            "ready": False,
            "summary": f"Build failed: {code}",
            "findings": [{"severity": "error", "kind": code, "message": message, "ref": "build"}],
            "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        target = project_root_for(source) / ".harness" / "verdict.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        return target
    except (OSError, ValueError, TypeError) as exc:
        print(f"[circuitcode] harness failure verdict not written: {exc}", file=sys.stderr)
        return None


def resolve_input(input_path: Path) -> tuple[Path, str, Path] | str:
    """Resolve the CLI input to ``(source, stem, out_dir)``.

    Returns an error message string instead when the input is unusable —
    fail fast on project shape before paying for a subprocess (donor rule).
    """
    if not input_path.exists():
        return f"input not found: {input_path}"
    if input_path.is_dir():
        source = input_path / "boards" / "main.tsx"
        if not source.is_file():
            return (
                f"project directory {input_path} has no boards/main.tsx — pass "
                "a board source (boards/<stem>.tsx) or create boards/main.tsx"
            )
    else:
        if input_path.suffix != ".tsx":
            return (
                f"input must be a .tsx board source or a project dir, got "
                f"{input_path.name} — never point the generator at a "
                "generated artifact"
            )
        source = input_path
    resolved = source.resolve()
    return resolved, resolved.stem, resolved.parent


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    resolved = resolve_input(args.input)
    if isinstance(resolved, str):
        return _fail(resolved)
    source, default_stem, default_out_dir = resolved

    stem = args.stem or default_stem
    out_dir = (args.out_dir or default_out_dir).resolve()

    payload = run_sandboxed_sync(
        source_path=source,
        out_dir=out_dir,
        stem=stem,
        fab=args.fab,
        wall_clock_s=args.wall_clock_s,
        recheck=args.recheck,
        edits=(args.edits.resolve() if args.edits else None),
        undo=args.undo_repair,
    )

    if not payload.get("ok"):
        write_failure_verdict(source, payload)
    print(json.dumps(payload))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
