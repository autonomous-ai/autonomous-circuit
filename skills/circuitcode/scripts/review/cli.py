"""``python scripts/review <project_or_dir> [--stem NAME]`` — the QA pass.

Reads the ``<stem>.board.json`` sidecar the generator wrote, re-surfaces its
``validation.warnings``, and hands back the two images the review loop must
actually LOOK at:

  <stem>_review/_schematic.png
  <stem>_review/_pcb.png

Either PNG missing is regenerated through ``circuitpy.review.write_review``
from the compiled ``circuit.json``. Rendering never raises — a failed render
yields a null path, not an error, because the warnings are still worth
returning.

Prints a single JSON line on stdout::

  {"ok": true, "stem": "main",
   "warnings": [ { "part", "kind", "detail", "severity" }, ... ],
   "schematic_png": "boards/main_review/_schematic.png",   // null if unrenderable
   "pcb_png": "boards/main_review/_pcb.png",
   "board_json": "boards/main.board.json"}
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common.runner import SIDECAR_SUFFIX, workspace_relative, ensure_circuitpy_path

OUTPUT_SUFFIX = ".circuit.json"

# This CLI reads and renders in-process (no build, no wall clock), so the
# runtime has to be importable here rather than in a worker.
ensure_circuitpy_path()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts/review",
        description=(
            "Re-surface a built board's validation warnings and its "
            "_schematic.png + _pcb.png review images, regenerating the PNGs "
            "when missing."
        ),
    )
    p.add_argument(
        "input",
        type=Path,
        help=(
            "A directory holding <stem>.board.json (boards/ or the project "
            "root), or a path to a .board.json / .circuit.json directly."
        ),
    )
    p.add_argument(
        "--stem",
        default=None,
        help="Disambiguate when a directory holds multiple .board.json sidecars.",
    )
    return p


def resolve_sidecar(input_path: Path, stem: str | None) -> Path | None:
    """Find the ``<stem>.board.json`` sidecar for the given input.

    Raises ``ValueError`` when a directory holds several sidecars and no
    ``--stem`` was given — silently picking one would review the wrong board
    (donor rule). A project root without sidecars falls through to its
    ``boards/`` subdirectory.
    """
    input_path = input_path.resolve()
    if input_path.is_file():
        if input_path.name.endswith(SIDECAR_SUFFIX):
            return input_path
        if input_path.name.endswith(OUTPUT_SUFFIX):
            cand = input_path.with_name(
                input_path.name[: -len(OUTPUT_SUFFIX)] + SIDECAR_SUFFIX
            )
            return cand if cand.is_file() else None
        return None
    if input_path.is_dir():
        search_dirs = [input_path]
        if (input_path / "boards").is_dir():
            search_dirs.append(input_path / "boards")
        for d in search_dirs:
            if stem:
                cand = d / f"{stem}{SIDECAR_SUFFIX}"
                if cand.is_file():
                    return cand
                continue
            sidecars = sorted(d.glob(f"*{SIDECAR_SUFFIX}"))
            if len(sidecars) > 1:
                names = ", ".join(s.name[: -len(SIDECAR_SUFFIX)] for s in sidecars)
                raise ValueError(
                    f"{d} holds {len(sidecars)} {SIDECAR_SUFFIX} sidecars "
                    f"({names}) — pass --stem to pick one"
                )
            if sidecars:
                return sidecars[0]
        return None
    return None


def _err(message: str, code: str = "VALIDATION_FAILED") -> int:
    print(json.dumps({"ok": False, "error": {"code": code, "message": message}}))
    return 2


def _regenerate(circuit_json_path: Path, review_dir: Path) -> None:
    """Re-render both review PNGs from the compiled JSON. Never raises."""
    try:
        import circuitpy.review as review_mod

        circuit_json = json.loads(circuit_json_path.read_text(encoding="utf-8"))
        review_mod.write_review(
            circuit_json_path=circuit_json_path,
            review_dir=review_dir,
            built_schematic_png=None,
            built_pcb_png=None,
            double_sided=review_mod.is_double_sided(circuit_json),
        )
    except Exception:
        pass  # a failed render is a null path, never an error verdict


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if not args.input.exists():
        return _err(f"input not found: {args.input}")

    try:
        sidecar = resolve_sidecar(args.input, args.stem)
    except ValueError as exc:
        return _err(str(exc))
    if sidecar is None:
        return _err(
            f"no {SIDECAR_SUFFIX} sidecar found for {args.input} — build the "
            "board first with scripts/circuit"
        )

    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception as exc:
        return _err(f"failed to read sidecar {sidecar.name}: {exc}")
    if not isinstance(meta, dict):
        return _err(f"sidecar {sidecar.name} is not a JSON object")

    base_dir = sidecar.parent
    stem = sidecar.name[: -len(SIDECAR_SUFFIX)]
    validation = meta.get("validation")
    warnings = (validation or {}).get("warnings", []) if isinstance(validation, dict) else []

    artifacts = meta.get("artifacts") if isinstance(meta.get("artifacts"), dict) else {}
    review_dir = base_dir / f"{stem}_review"
    schematic = base_dir / str(artifacts.get("schematicPng") or f"{stem}_review/_schematic.png")
    pcb = base_dir / str(artifacts.get("pcbPng") or f"{stem}_review/_pcb.png")

    board_meta = meta.get("board") if isinstance(meta.get("board"), dict) else {}
    circuit_json_path = base_dir / str(board_meta.get("path") or f"{stem}{OUTPUT_SUFFIX}")

    if (not schematic.is_file() or not pcb.is_file()) and circuit_json_path.is_file():
        _regenerate(circuit_json_path, review_dir)

    print(json.dumps({
        "ok": True,
        "stem": stem,
        "warnings": warnings,
        "schematic_png": workspace_relative(schematic) if schematic.is_file() else None,
        "pcb_png": workspace_relative(pcb) if pcb.is_file() else None,
        "board_json": workspace_relative(sidecar),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
