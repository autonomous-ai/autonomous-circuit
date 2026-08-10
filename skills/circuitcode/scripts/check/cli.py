"""``python scripts/check <boards/main.tsx | project_dir>`` — validate cheaply.

The structural pass: does the TSX compile, and does the compiled JSON come
back clean? Contract §1 stages 0-2 — compile, the ``*_error``/``*_warning``
element scan, and the independent ``@tscircuit/checks`` re-check — with no
KiCad crossing and no fab packet to keep.

Artifacts go to a tempdir and are deleted, so the path fields of the full
``scripts/circuit`` verdict are stripped: what is left is ``{ok, board, bom,
warnings, error?}``. Use it to sanity-check a board before asking for the
real packet.

Implementation note (deviation logged in ``docs/circuit-interfaces-CHANGES.md``):
circuitpy exposes no stages-limited entry point, so this calls the same
``build_board`` into the tempdir and presents a stages-0-2-shaped result —
the tempdir keeps the workspace clean, and the two warning kinds that only
describe the discarded packet (``kicad_unavailable``, ``unverified_gerbers``)
are dropped so they do not appear on every single run.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from circuit.cli import resolve_input
from common.runner import WALL_CLOCK_TIMEOUT_S, run_sandboxed_sync

# Path members of CircuitcodeResult — every one pointed into the tempdir.
STRIPPED_KEYS = (
    "circuit_json_path",
    "metadata_path",
    "schematic_png",
    "pcb_png",
    "fab",
    "unchanged",
)

# Warnings about the fab packet itself, not about the source under test.
PACKET_ONLY_KINDS = frozenset({"kicad_unavailable", "unverified_gerbers"})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts/check",
        description=(
            "Validate a board structurally: it compiles, and the compiled "
            "circuit.json survives the element scan and @tscircuit/checks. "
            "Artifacts are written to a tempdir and discarded."
        ),
    )
    p.add_argument(
        "input",
        type=Path,
        help="A board source .tsx, or a project directory holding boards/main.tsx.",
    )
    p.add_argument(
        "--stem",
        default=None,
        help="Override the temporary output stem (default: the source's stem).",
    )
    p.add_argument(
        "--fab",
        default=None,
        help="Fab profile id for the DFM limit table (default: env CIRCUIT_FAB, else jlcpcb).",
    )
    p.add_argument(
        "--wall-clock-s",
        type=float,
        default=WALL_CLOCK_TIMEOUT_S,
        help=f"Wall-clock timeout (seconds, default {WALL_CLOCK_TIMEOUT_S:g}).",
    )
    return p


def _fail(message: str, code: str = "VALIDATION_FAILED") -> int:
    print(json.dumps({"ok": False, "error": {"code": code, "message": message}}))
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    resolved = resolve_input(args.input)
    if isinstance(resolved, str):
        return _fail(resolved)
    source, default_stem, _ = resolved
    stem = args.stem or default_stem or "main"

    tmp = Path(tempfile.mkdtemp(prefix="circuitcode-check-"))
    try:
        payload = run_sandboxed_sync(
            source_path=source,
            out_dir=tmp,
            stem=stem,
            fab=args.fab,
            wall_clock_s=args.wall_clock_s,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for key in STRIPPED_KEYS:
        payload.pop(key, None)
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        kept = [
            w
            for w in warnings
            if not (isinstance(w, dict) and w.get("kind") in PACKET_ONLY_KINDS)
        ]
        if kept:
            payload["warnings"] = kept
        else:
            payload.pop("warnings", None)

    print(json.dumps(payload))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
