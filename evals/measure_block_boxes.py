#!/usr/bin/env python3
"""Measure each golden block's true bounding box, offset included.

**Why this exists.** ``circuitlib.layout`` is the placement advice the skill
gives every agent: ``min_board_for()`` sizes the outline, ``place_row()``
positions the blocks. On 2026-08-11 the composition matrix built all 42 legal
compositions and **36 failed**, overwhelmingly on
``pcb_component_outside_board_error`` and ``dfm_edge_clearance`` — parts hanging
off the edge of a board that ``min_board_for()`` had just declared big enough.

Two bugs, one cause. The stored table recorded a block's *size* but not where
that size sits relative to the block's origin, and ``place_row()`` assumed the
geometry was centred on ``pcbX``/``pcbY``. It usually is not: ``status-led``'s
own testbench places it at ``pcbY={-1}`` precisely because it is not. A block
whose copper sits 2mm above its origin gets placed 2mm too low, and on a board
sized to the millimetre that puts it over the edge.

So the table becomes a **box** — ``(min_x, min_y, max_x, max_y)`` relative to
the origin — measured from a real build, never estimated.

What counts as the box: every copper feature (SMT pads, plated holes, vias),
every drill, and every component courtyard. Silkscreen is excluded — it may
legally overhang, and including it would inflate every board.

Run after any footprint change:

    python evals/measure_block_boxes.py            # print the table
    python evals/measure_block_boxes.py --write    # rewrite layout.py's table
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages" / "circuitpy" / "src"))
sys.path.insert(0, str(REPO / "skills" / "circuitcode"))

BLOCKS_DIR = REPO / "packages" / "golden-blocks" / "blocks"
SKELETON = REPO / "skills" / "circuitcode" / "templates" / "project_skeleton"
LAYOUT_PY = REPO / "skills" / "circuitcode" / "circuitlib" / "layout.py"
TOOLCHAIN_BIN = REPO / "toolchain" / "node_modules" / ".bin"

from scripts.sync_golden_blocks import sync_project  # noqa: E402

#: Element types that must sit inside the board outline. Silkscreen is
#: deliberately absent: it may overhang, and counting it inflates every board.
BOX_TYPES = {
    "pcb_smtpad",
    "pcb_plated_hole",
    "pcb_hole",
    "pcb_via",
    "pcb_component",
    "pcb_courtyard_outline",
    "pcb_courtyard_rect",
}


def instantiation() -> dict[str, tuple[str, str]]:
    sys.path.insert(0, str(REPO / "evals"))
    from composition import INSTANTIATION

    return INSTANTIATION


def _element_box(element: dict) -> tuple[float, float, float, float] | None:
    etype = element.get("type")
    if etype not in BOX_TYPES:
        return None
    if etype == "pcb_courtyard_outline":
        outline = element.get("outline") or []
        points = [
            (float(point["x"]), float(point["y"]))
            for point in outline
            if isinstance(point, dict)
            and isinstance(point.get("x"), (int, float))
            and isinstance(point.get("y"), (int, float))
        ]
        if not points:
            return None
        return (
            min(point[0] for point in points),
            min(point[1] for point in points),
            max(point[0] for point in points),
            max(point[1] for point in points),
        )
    x, y = element.get("x"), element.get("y")
    if etype in {"pcb_component", "pcb_courtyard_rect"}:
        centre = element.get("center") or {}
        x, y = centre.get("x", x), centre.get("y", y)
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    width = element.get("width")
    height = element.get("height")
    if not isinstance(width, (int, float)):
        diameter = (
            element.get("outer_diameter")
            or element.get("hole_diameter")
            or element.get("radius", 0) * 2
            or 0
        )
        width = height = float(diameter)
    if not isinstance(height, (int, float)):
        height = width
    if etype == "pcb_courtyard_rect" and round(
        float(element.get("ccw_rotation", 0) or 0)
    ) % 180 == 90:
        width, height = height, width
    return (
        float(x) - float(width) / 2,
        float(y) - float(height) / 2,
        float(x) + float(width) / 2,
        float(y) + float(height) / 2,
    )


def measure(block_id: str) -> tuple[str, tuple[float, float, float, float] | str]:
    symbol, extra = instantiation()[block_id]
    root = Path(tempfile.mkdtemp(prefix=f"measure-{block_id}-"))
    try:
        for config in ("tsconfig.json", "tscircuit.config.json"):
            shutil.copy(SKELETON / config, root / config)
        sync_project(
            root,
            blocks=[block_id],
            source=BLOCKS_DIR,
            source_label="packages/golden-blocks/blocks",
        )
        (root / "package.json").write_text(
            json.dumps({"name": "measure", "private": True, "version": "0.0.0"})
        )
        boards = root / "boards"
        boards.mkdir()
        # A deliberately huge outline with routing off: the question is where
        # the block's own geometry lands, not whether it routes.
        (boards / "main.tsx").write_text(
            f'import {{ {symbol} }} from "../blocks/{block_id}/{block_id}"\n\n'
            f"export default () => (\n"
            f'  <board width="300mm" height="300mm" thickness={{1.6}} '
            f"routingDisabled={{true}}>\n"
            f"    <{symbol} {extra} pcbX={{0}} pcbY={{0}} schX={{0}} schY={{0}} />\n"
            f"  </board>\n)\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["PATH"] = f"{TOOLCHAIN_BIN}{os.pathsep}{env.get('PATH', '')}"
        env["NODE_PATH"] = str(REPO / "toolchain" / "node_modules")
        completed = subprocess.run(
            [str(TOOLCHAIN_BIN / "tscircuit-cli"), "build", "boards/main.tsx",
             "--disable-parts-engine"],
            cwd=root, env=env, capture_output=True, text=True, timeout=300,
        )
        built = root / "dist" / "boards" / "main" / "circuit.json"
        if not built.is_file():
            detail = "\n".join(
                part.strip()
                for part in (completed.stdout, completed.stderr)
                if part.strip()
            )
            if len(detail) > 1200:
                detail = detail[-1200:]
            return block_id, (
                f"no circuit.json produced (exit {completed.returncode})"
                + (f": {detail}" if detail else "")
            )
        elements = json.loads(built.read_text(encoding="utf-8"))
        boxes = [b for b in (_element_box(e) for e in elements) if b]
        if not boxes:
            return block_id, "no placeable geometry found"
        return block_id, (
            round(min(b[0] for b in boxes), 2),
            round(min(b[1] for b in boxes), 2),
            round(max(b[2] for b in boxes), 2),
            round(max(b[3] for b in boxes), 2),
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="rewrite BLOCK_BOX_MM in circuitlib/layout.py")
    parser.add_argument("--jobs", type=int, default=9)
    args = parser.parse_args(argv[1:])

    ids = sorted(instantiation())
    results: dict[str, tuple[float, float, float, float]] = {}
    with futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for block_id, box in pool.map(measure, ids):
            if isinstance(box, str):
                print(f"FAIL {block_id}: {box}")
                continue
            results[block_id] = box
            w, h = round(box[2] - box[0], 2), round(box[3] - box[1], 2)
            cx, cy = round((box[0] + box[2]) / 2, 2), round((box[1] + box[3]) / 2, 2)
            print(
                f"{block_id:<16} box={box}  size={w}x{h}  "
                f"centre offset from origin=({cx}, {cy})"
            )

    if args.write and len(results) == len(ids):
        table = "\n".join(
            f'    "{bid}": ({box[0]}, {box[1]}, {box[2]}, {box[3]}),'
            for bid, box in sorted(results.items())
        )
        text = LAYOUT_PY.read_text(encoding="utf-8")
        new = re.sub(
            r"(BLOCK_BOX_MM: dict\[str, tuple\[float, float, float, float\]\] = \{\n).*?(\n\})",
            lambda m: m.group(1) + table + m.group(2),
            text,
            flags=re.S,
        )
        if new == text:
            print("\ncould not find BLOCK_BOX_MM in layout.py — not written")
            return 1
        LAYOUT_PY.write_text(new, encoding="utf-8")
        print(f"\nwrote {len(results)} boxes into {LAYOUT_PY}")
    return 0 if len(results) == len(ids) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
