"""Review images — the artifacts the driver's craft loop ``Read``s.

``write_review`` normalizes the build's native PNGs into ``<stem>_review/``
(``_schematic.png`` + ``_pcb.png``), writes the SVG sources next to them via
circuit-to-svg, and adds ``_pcb_bottom.png`` (circuit-to-svg + sharp) when
parts sit on both sides. When a native build PNG is missing the same helper
rasterizes it from the SVG — sharp's prebuilt libvips does the work, no
external rasterizer.

Raises plain ``RuntimeError``/``TimeoutError`` from the toolchain layer;
:mod:`circuitpy.generation` wraps into ``ExportError`` (review images are a
required artifact — a board without them cannot enter the review loop).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Sequence

from circuitpy import toolchain

_RENDER_TIMEOUT_S = 120.0


def is_double_sided(circuit_json: Sequence[dict]) -> bool:
    """True when any component or pad sits on the bottom layer."""
    for element in circuit_json:
        if not isinstance(element, dict):
            continue
        if element.get("type") in ("pcb_component", "pcb_smtpad") and (
            str(element.get("layer") or "").lower() == "bottom"
        ):
            return True
    return False


def write_review(
    *,
    circuit_json_path: Path,
    review_dir: Path,
    built_schematic_png: Path | None,
    built_pcb_png: Path | None,
    double_sided: bool,
) -> dict[str, Path]:
    """Populate ``<stem>_review/``; returns name -> path of everything written."""
    review_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    need_schematic_raster = built_schematic_png is None or not built_schematic_png.is_file()
    need_pcb_raster = built_pcb_png is None or not built_pcb_png.is_file()

    args = [
        toolchain.helper_js("render_review.cjs"),
        str(circuit_json_path),
        str(review_dir),
    ]
    if double_sided:
        args.append("--bottom")
    if need_schematic_raster:
        args.append("--schematic-png")
    if need_pcb_raster:
        args.append("--pcb-png")
    toolchain.run_node(args, timeout=_RENDER_TIMEOUT_S)

    for name in ("_schematic.svg", "_pcb.svg"):
        path = review_dir / name
        if not path.is_file():
            raise RuntimeError(f"review renderer did not produce {name}")
        written[name] = path

    if not need_schematic_raster:
        target = review_dir / "_schematic.png"
        shutil.copy2(built_schematic_png, target)  # type: ignore[arg-type]
        written["_schematic.png"] = target
    if not need_pcb_raster:
        target = review_dir / "_pcb.png"
        shutil.copy2(built_pcb_png, target)  # type: ignore[arg-type]
        written["_pcb.png"] = target
    for name in ("_schematic.png", "_pcb.png"):
        path = review_dir / name
        if not path.is_file():
            raise RuntimeError(f"review image missing after normalization: {name}")
        written[name] = path
    if double_sided:
        bottom = review_dir / "_pcb_bottom.png"
        if not bottom.is_file():
            raise RuntimeError("double-sided board but _pcb_bottom.png missing")
        written["_pcb_bottom.png"] = bottom
    return written
