"""`_placement.png` — the board with its ratsnest, no copper.

What the agent reads before routing: footprints, pads, the outline, and one
hairline per ratsnest edge. Traces, vias and pours are left out on purpose —
the ratsnest is the question, copper is the router's answer, and a picture
that shows both is a picture of the answer.

Never raises: an image that cannot be drawn is reported, not fatal. The score
(`circuitpy.placement`) stands on its own.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import placement, toolchain

_RENDER_TIMEOUT_S = 120.0


def write_placement_png(elements: list, out_png: Path) -> dict:
    """Render ``elements`` (routed or not) with their ratsnest to ``out_png``.

    Returns ``{"ok": bool, "path": str, "edges": int}`` or
    ``{"ok": False, "error": str}``.
    """
    out_png = Path(out_png)
    edges = placement.ratsnest_edges(elements)
    try:
        out_png.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="circuit-placement-") as tmp:
            circuit = Path(tmp) / "circuit.json"
            edges_path = Path(tmp) / "edges.json"
            circuit.write_text(json.dumps(elements), encoding="utf-8")
            edges_path.write_text(json.dumps(edges), encoding="utf-8")
            toolchain.run_node(
                [
                    toolchain.helper_js("render_placement.cjs"),
                    str(circuit),
                    str(edges_path),
                    str(out_png),
                ],
                timeout=_RENDER_TIMEOUT_S,
            )
        if not out_png.is_file():
            return {"ok": False, "error": "renderer wrote nothing"}
        return {"ok": True, "path": str(out_png), "edges": len(edges)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
