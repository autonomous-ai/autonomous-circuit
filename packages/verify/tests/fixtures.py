"""Hand-built circuit-json fixtures.

Real projects on disk with the real toolchain are the discipline for the
pipeline's own tests. These checks consume circuit-json *as data*, so a
fixture that constructs exactly the geometry under test is both faster and far
sharper: a defect that has to be coaxed out of a real board is a defect the
test cannot promise is there.

Every builder returns a plain ``list[dict]`` — the same shape
``json.load(circuit.json)`` gives.
"""

from __future__ import annotations

import math
from typing import Iterable


def board(width: float = 40.0, height: float = 30.0, thickness: float = 1.6) -> dict:
    return {
        "type": "pcb_board",
        "pcb_board_id": "pcb_board_0",
        "center": {"x": 0, "y": 0},
        "width": width,
        "height": height,
        "thickness": thickness,
        "num_layers": 2,
    }


def component(
    name: str,
    *,
    index: int,
    x: float,
    y: float,
    width: float = 2.0,
    height: float = 1.0,
    layer: str = "top",
    ftype: str = "simple_resistor",
    pads: Iterable[tuple[float, float, float, float]] | None = None,
    courtyard: tuple[float, float] | None = None,
    courtyard_rotation_deg: float = 0.0,
    lcsc: str | None = "C25905",
    **source_fields: object,
) -> list[dict]:
    """One part: source + pcb component, its pads, and a courtyard.

    ``pads`` are ``(dx, dy, w, h)`` offsets from the component centre. Omit and
    two pads are placed at the footprint's short ends.
    ``courtyard`` is ``(w, h)``; rotate it with ``courtyard_rotation_deg`` to
    build the rotated-diamond case that bounding-box collision tests get wrong.
    """
    sid = f"source_component_{index}"
    pid = f"pcb_component_{index}"
    source: dict = {
        "type": "source_component",
        "source_component_id": sid,
        "ftype": ftype,
        "name": name,
    }
    source.update(source_fields)
    if lcsc:
        source["supplier_part_numbers"] = {"jlcpcb": [lcsc]}

    out: list[dict] = [
        source,
        {
            "type": "pcb_component",
            "pcb_component_id": pid,
            "source_component_id": sid,
            "center": {"x": x, "y": y},
            "width": width,
            "height": height,
            "layer": layer,
            "rotation": 0,
            "do_not_place": False,
        },
    ]

    if pads is None:
        pads = [
            (-width / 2 + 0.25, 0.0, 0.5, height),
            (width / 2 - 0.25, 0.0, 0.5, height),
        ]
    for i, (dx, dy, pw, ph) in enumerate(pads):
        port_id = f"pcb_port_{index}_{i}"
        source_port_id = f"source_port_{index}_{i}"
        out.append(
            {
                "type": "source_port",
                "source_port_id": source_port_id,
                "name": f"pin{i + 1}",
                "pin_number": i + 1,
                "source_component_id": sid,
                "subcircuit_connectivity_map_key": f"net_{name}_{i}",
            }
        )
        out.append(
            {
                "type": "pcb_port",
                "pcb_port_id": port_id,
                "pcb_component_id": pid,
                "source_port_id": source_port_id,
                "x": x + dx,
                "y": y + dy,
            }
        )
        out.append(
            {
                "type": "pcb_smtpad",
                "pcb_smtpad_id": f"pcb_smtpad_{index}_{i}",
                "pcb_component_id": pid,
                "pcb_port_id": port_id,
                "layer": layer,
                "shape": "rect",
                "x": x + dx,
                "y": y + dy,
                "width": pw,
                "height": ph,
            }
        )

    if courtyard is not None:
        cw, ch = courtyard
        if courtyard_rotation_deg:
            theta = math.radians(courtyard_rotation_deg)
            corners = [(-cw / 2, -ch / 2), (cw / 2, -ch / 2), (cw / 2, ch / 2), (-cw / 2, ch / 2)]
            outline = [
                {
                    "x": x + px * math.cos(theta) - py * math.sin(theta),
                    "y": y + px * math.sin(theta) + py * math.cos(theta),
                }
                for px, py in corners
            ]
            out.append(
                {
                    "type": "pcb_courtyard_outline",
                    "pcb_courtyard_outline_id": f"pcb_courtyard_outline_{index}",
                    "pcb_component_id": pid,
                    "layer": layer,
                    "outline": outline,
                }
            )
        else:
            out.append(
                {
                    "type": "pcb_courtyard_rect",
                    "pcb_courtyard_rect_id": f"pcb_courtyard_rect_{index}",
                    "pcb_component_id": pid,
                    "layer": layer,
                    "center": {"x": x, "y": y},
                    "width": cw,
                    "height": ch,
                }
            )
    return out


def net(
    net_index: int,
    name: str,
    *,
    is_power: bool = False,
    is_ground: bool = False,
) -> dict:
    return {
        "type": "source_net",
        "source_net_id": f"source_net_{net_index}",
        "name": name,
        "is_power": is_power,
        "is_ground": is_ground,
        "subcircuit_connectivity_map_key": f"conn_{name}",
    }


def wire(*pin_refs: tuple[str, int], to_net: str) -> list[dict]:
    """Re-key existing ports onto a named net. Returns patch instructions the
    caller applies with :func:`connect`."""
    return [{"component": c, "pin": p, "net": to_net} for c, p in pin_refs]


def connect(elements: list[dict], component_name: str, pin_index: int, net_name: str) -> None:
    """Move one port onto ``net_name`` (matching :func:`net`'s key scheme)."""
    sid = next(
        e["source_component_id"]
        for e in elements
        if e.get("type") == "source_component" and e.get("name") == component_name
    )
    ports = [
        e
        for e in elements
        if e.get("type") == "source_port" and e.get("source_component_id") == sid
    ]
    ports[pin_index]["subcircuit_connectivity_map_key"] = f"conn_{net_name}"


def trace(
    trace_id: str,
    net_name: str,
    points: Iterable[tuple[float, float]],
    *,
    width: float = 0.15,
    layer: str = "top",
) -> dict:
    route = [
        {"route_type": "wire", "x": x, "y": y, "width": width, "layer": layer}
        for x, y in points
    ]
    return {
        "type": "pcb_trace",
        "pcb_trace_id": trace_id,
        "connection_name": f"source_net_{net_name}",
        "route": route,
    }


def trace_on(
    trace_id: str,
    net_index: int,
    points: Iterable[tuple[float, float]],
    *,
    width: float = 0.15,
    layer: str = "top",
) -> dict:
    """A trace whose ``connection_name`` points at ``source_net_<index>``."""
    route = [
        {"route_type": "wire", "x": x, "y": y, "width": width, "layer": layer}
        for x, y in points
    ]
    return {
        "type": "pcb_trace",
        "pcb_trace_id": trace_id,
        "connection_name": f"source_net_{net_index}",
        "route": route,
    }


def clean_board() -> list[dict]:
    """A small board that violates nothing this package checks."""
    elements = [board(40, 30)]
    elements += component("R1", index=1, x=-8, y=0, courtyard=(2.4, 1.4))
    elements += component("R2", index=2, x=-4, y=0, courtyard=(2.4, 1.4))
    elements += component("C1", index=3, x=0, y=0, ftype="simple_capacitor",
                          courtyard=(2.4, 1.4), capacitance=1e-7)
    elements += component("U1", index=4, x=6, y=0, width=4, height=4,
                          ftype="simple_chip", courtyard=(4.6, 4.6),
                          pads=[(dx, dy, 0.4, 0.4)
                                for dx in (-1.6, 1.6) for dy in (-1.2, 0.0, 1.2)])
    return elements
