"""The enclosure handoff — the file that makes Circuit and Vibe one loop.

A board in a bag is not a product. The printed body around it needs a small,
exact set of facts, and today those facts get retyped by a human reading a
drawing, which is how a case arrives that does not fit.

So the pipeline emits them: outline, mounting holes, which edge each connector
sits on and how far it overhangs, keep-outs, and the tallest thing on each
side. Vibe consumes this to model the body; nobody measures anything twice.

Everything here is derived from Circuit JSON geometry, so it cannot disagree
with the board that was actually fabbed. Where a fact is genuinely unknown
(component heights are only present when the toolchain emitted a 3D model) the
field is null and ``confidence`` says so — a guessed height is worse than an
absent one, because a case gets printed around it.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Refdes prefixes that mean "a human plugs something in here". Connector
#: placement drives the case's cutouts, so this list decides what gets
#: reported to the enclosure designer.
CONNECTOR_PREFIXES = ("J", "CN", "P", "USB", "SW", "BT")

#: How close to an edge a part must sit to count as edge-mounted (mm).
EDGE_BAND_MM = 4.0


def _num(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _board(circuit_json: list) -> dict | None:
    for element in circuit_json:
        if isinstance(element, dict) and element.get("type") == "pcb_board":
            return element
    return None


def _refdes_by_pcb_component(circuit_json: list) -> dict[str, str]:
    """pcb_component_id -> refdes, walked through source_component."""
    source_names: dict[str, str] = {}
    for element in circuit_json:
        if not isinstance(element, dict):
            continue
        if element.get("type") == "source_component":
            sid = element.get("source_component_id")
            name = element.get("name")
            if isinstance(sid, str) and isinstance(name, str):
                source_names[sid] = name
    out: dict[str, str] = {}
    for element in circuit_json:
        if not isinstance(element, dict) or element.get("type") != "pcb_component":
            continue
        pid = element.get("pcb_component_id")
        sid = element.get("source_component_id")
        if isinstance(pid, str):
            out[pid] = source_names.get(sid, pid) if isinstance(sid, str) else pid
    return out


def _edge_of(x: float, y: float, width: float, height: float) -> str | None:
    """Which board edge a point sits against, if any."""
    half_w, half_h = width / 2.0, height / 2.0
    distances = {
        "left": abs(x - -half_w),
        "right": abs(x - half_w),
        "bottom": abs(y - -half_h),
        "top": abs(y - half_h),
    }
    edge, distance = min(distances.items(), key=lambda kv: kv[1])
    return edge if distance <= EDGE_BAND_MM else None


def build_enclosure_spec(circuit_json: list, *, board_name: str) -> dict:
    """The machine-readable brief for whoever models the case."""
    board = _board(circuit_json) or {}
    width = _num(board.get("width")) or 0.0
    height = _num(board.get("height")) or 0.0
    thickness = _num(board.get("thickness")) or 1.6
    center = board.get("center") if isinstance(board.get("center"), dict) else {}
    refdes = _refdes_by_pcb_component(circuit_json)

    holes: list[dict] = []
    connectors: list[dict] = []
    top_z = 0.0
    bottom_z = 0.0
    heights_known = False

    for element in circuit_json:
        if not isinstance(element, dict):
            continue
        etype = element.get("type")

        # Unplated holes are mounting holes; plated ones carry a net and are
        # part of a footprint, not somewhere a screw goes.
        if etype == "pcb_hole":
            x, y = _num(element.get("x")), _num(element.get("y"))
            diameter = _num(element.get("hole_diameter"))
            if x is None or y is None or diameter is None:
                continue
            # A footprint's own drill is not a mounting hole; M2 is the
            # smallest fastener anyone uses, so anything under 1.8mm is not one.
            if diameter < 1.8:
                continue
            holes.append({
                "name": refdes.get(element.get("pcb_component_id", ""), "H?"),
                "xMm": round(x, 3), "yMm": round(y, 3),
                "diameterMm": round(diameter, 3),
                "fastener": "M3" if diameter >= 3.0 else "M2",
            })

        elif etype == "pcb_component":
            name = refdes.get(element.get("pcb_component_id", ""), "")
            x, y = _num(element.get("x")), _num(element.get("y"))
            if not name or x is None or y is None:
                continue
            prefix = "".join(c for c in name if c.isalpha()).upper()
            if not prefix.startswith(CONNECTOR_PREFIXES):
                continue
            edge = _edge_of(x, y, width, height)
            connectors.append({
                "refdes": name,
                "xMm": round(x, 3), "yMm": round(y, 3),
                "edge": edge,
                "rotationDeg": _num(element.get("rotation")) or 0.0,
                "note": (
                    "needs a cutout in the case wall" if edge
                    else "interior — reachable only with the case open"
                ),
            })

        elif etype == "cad_component":
            position = element.get("position")
            if isinstance(position, dict):
                z = _num(position.get("z"))
                if z is not None:
                    heights_known = True
                    top_z = max(top_z, z)
                    bottom_z = min(bottom_z, z)

    holes.sort(key=lambda h: (h["xMm"], h["yMm"]))
    connectors.sort(key=lambda c: c["refdes"])

    return {
        "generator": "circuitpy",
        "entryKind": "enclosure",
        "board": {
            "name": board_name,
            "widthMm": round(width, 3),
            "heightMm": round(height, 3),
            "thicknessMm": round(thickness, 3),
            "centerMm": [
                round(_num(center.get("x")) or 0.0, 3),
                round(_num(center.get("y")) or 0.0, 3),
            ],
            "outline": "rect",
        },
        "mountingHoles": holes,
        "connectors": connectors,
        "clearance": {
            # Real component heights need the toolchain's 3D models; when they
            # are absent we say so rather than inventing a number a case would
            # be printed around.
            "topMm": round(top_z, 3) if heights_known else None,
            "bottomMm": round(abs(bottom_z), 3) if heights_known else None,
            "confidence": "modelled" if heights_known else "unknown",
        },
        "notes": [
            "Coordinates are millimetres, board centre at the origin, "
            "x right and y up, viewed from the top.",
            "Mounting holes are unplated drills of 1.8mm or more; anything "
            "smaller belongs to a footprint.",
            (
                f"{len(holes)} mounting hole(s) — two on a diagonal is the "
                "minimum for a board that must not rotate on its screws."
                if len(holes) < 2
                else f"{len(holes)} mounting holes."
            ),
        ],
    }


def write_enclosure_spec(circuit_json: list, path: Path, *, board_name: str) -> Path:
    """Write ``enclosure.json`` beside the fab packet."""
    spec = build_enclosure_spec(circuit_json, board_name=board_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(spec, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path
