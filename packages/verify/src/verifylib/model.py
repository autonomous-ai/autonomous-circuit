"""A typed read of ``circuit.json`` — the shared substrate for every check.

Circuit JSON is a flat list of ~2300 dicts with cross-references by id. Every
check in this package needs the same handful of joins (component → pads,
port → net, trace → net, component → courtyard), so they live here once.

Two joins are worth explaining because they are not obvious from the file:

* **Nets.** ``source_net`` carries the name and the ``is_power`` / ``is_ground``
  flags, but ports do not point at nets directly. Both carry
  ``subcircuit_connectivity_map_key``, and that key *is* the net identity —
  every port sharing one is electrically the same node. Grouping by it
  reconstructs the full netlist with real names, including the nets that have
  no ``source_net`` element (the compiler leaves those unnamed).
* **Traces.** ``pcb_trace.connection_name`` is either a ``source_net_id`` or a
  ``source_trace_id``; the second form is resolved through the source trace's
  connected ports. Falling back to ``connectsTo`` (pcb ports) covers the rest.

Nothing here raises on malformed input — a missing field yields ``None`` and
the caller decides. Checks report what they could not read as coverage.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

Element = dict


# ---------------------------------------------------------------------------
# Geometry primitives.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rect:
    """An axis-aligned box in board millimetres."""

    x0: float
    y0: float
    x1: float
    y1: float

    @staticmethod
    def from_center(cx: float, cy: float, width: float, height: float) -> "Rect":
        return Rect(cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)

    @staticmethod
    def bounding(points: Iterable[tuple[float, float]]) -> "Rect | None":
        xs: list[float] = []
        ys: list[float] = []
        for x, y in points:
            xs.append(x)
            ys.append(y)
        if not xs:
            return None
        return Rect(min(xs), min(ys), max(xs), max(ys))

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    def inset_margin(self, outer: "Rect") -> float:
        """Smallest distance from this rect's edge to ``outer``'s edge.
        Negative when this rect pokes outside."""
        return min(
            self.x0 - outer.x0,
            self.y0 - outer.y0,
            outer.x1 - self.x1,
            outer.y1 - self.y1,
        )

    def gap_to(self, other: "Rect") -> float:
        """Edge-to-edge gap. 0 when touching, negative when overlapping (the
        magnitude of the overlap along the *least* overlapping axis)."""
        dx = max(self.x0 - other.x1, other.x0 - self.x1)
        dy = max(self.y0 - other.y1, other.y0 - self.y1)
        if dx >= 0 and dy >= 0:
            return math.hypot(dx, dy)
        if dx >= 0:
            return dx
        if dy >= 0:
            return dy
        return max(dx, dy)  # both negative: least-overlapping axis

    def as_poly(self) -> "Poly":
        return Poly(
            [(self.x0, self.y0), (self.x1, self.y0), (self.x1, self.y1), (self.x0, self.y1)]
        )


@dataclass(frozen=True)
class Poly:
    """A closed polygon in board millimetres, used for courtyards.

    Courtyards are frequently **rotated** — a WS2812 placed at 22.5 degrees has
    a diamond courtyard whose axis-aligned bounding box is 40% larger than the
    part. Testing collisions on bounding boxes invented nine "overlaps" on
    ``harness-puck`` that do not exist, which is exactly the kind of noise that
    trains everyone to ignore a gate. So the real outline is kept and the
    separation is computed with a separating-axis test.

    The test runs on each polygon's **convex hull**. That is exact for the
    convex quads footprints actually emit, and for a concave outline it can
    only ever over-report proximity — never miss a real collision.
    """

    #: The outline exactly as given. Kept raw because a solder-mask region is
    #: not convex and its hull is not it: replacing a mask opening by its hull
    #: hides the very gaps the sliver check exists to measure.
    points: tuple[tuple[float, float], ...]

    def __init__(self, points: Sequence[tuple[float, float]]):
        cleaned: list[tuple[float, float]] = []
        for p in points:
            pt = (float(p[0]), float(p[1]))
            if not cleaned or pt != cleaned[-1]:
                cleaned.append(pt)
        if len(cleaned) > 1 and cleaned[0] == cleaned[-1]:
            cleaned.pop()
        object.__setattr__(self, "points", tuple(cleaned))

    @property
    def hull(self) -> tuple[tuple[float, float], ...]:
        return tuple(_convex_hull(list(self.points)))

    @property
    def bounds(self) -> Rect:
        rect = Rect.bounding(self.points)
        if rect is None:
            raise ValueError("empty polygon")
        return rect

    def _axes(self, points: tuple[tuple[float, float], ...]) -> list[tuple[float, float]]:
        axes: list[tuple[float, float]] = []
        n = len(points)
        for i in range(n):
            x0, y0 = points[i]
            x1, y1 = points[(i + 1) % n]
            dx, dy = x1 - x0, y1 - y0
            length = math.hypot(dx, dy)
            if length < 1e-12:
                continue
            axes.append((-dy / length, dx / length))
        return axes

    def _project(
        self, axis: tuple[float, float], points: tuple[tuple[float, float], ...]
    ) -> tuple[float, float]:
        values = [axis[0] * x + axis[1] * y for x, y in points]
        return min(values), max(values)

    def min_distance_to(self, other: "Poly") -> float:
        """Exact edge-to-edge distance between two arbitrary polygons.

        Unlike :meth:`gap_to` this makes no convexity assumption, so it is the
        right tool for a solder-mask region — which is whatever shape KiCad
        merged the openings into. Returns 0.0 when they touch or overlap.
        """
        if not self.points or not other.points:
            return 0.0
        if self._intersects(other):
            return 0.0
        best = math.inf
        for a0, a1 in self._edges():
            for b0, b1 in other._edges():
                best = min(best, _segment_distance(a0, a1, b0, b1))
                if best == 0.0:
                    return 0.0
        return best if best < math.inf else 0.0

    def _edges(self):
        n = len(self.points)
        for i in range(n):
            yield self.points[i], self.points[(i + 1) % n]

    def _intersects(self, other: "Poly") -> bool:
        if any(other.contains(x, y) for x, y in self.points):
            return True
        return any(self.contains(x, y) for x, y in other.points)

    def gap_to(self, other: "Poly") -> float:
        """Separation between two convex polygons.

        Positive: the largest gap found along any separating axis — the true
        edge-to-edge distance for convex shapes that miss along an edge normal.
        Negative: the smallest penetration depth, i.e. how far one would have
        to move to stop overlapping.
        """
        mine, theirs = self.hull, other.hull
        if len(mine) < 2 or len(theirs) < 2:
            return 0.0
        best_gap = -math.inf
        least_overlap = math.inf
        for axis in self._axes(mine) + other._axes(theirs):
            a0, a1 = self._project(axis, mine)
            b0, b1 = other._project(axis, theirs)
            gap = max(b0 - a1, a0 - b1)
            if gap > 0:
                best_gap = max(best_gap, gap)
            else:
                least_overlap = min(least_overlap, -gap)
        if best_gap > -math.inf:
            return best_gap
        return -least_overlap if least_overlap < math.inf else 0.0

    def contains(self, x: float, y: float) -> bool:
        inside = False
        n = len(self.points)
        for i in range(n):
            x0, y0 = self.points[i]
            x1, y1 = self.points[(i + 1) % n]
            if (y0 > y) != (y1 > y):
                t = (y - y0) / (y1 - y0)
                if x < x0 + t * (x1 - x0):
                    inside = not inside
        return inside

    def distance_to_point(self, x: float, y: float) -> float:
        """Distance from a point to the polygon boundary; negative inside."""
        n = len(self.points)
        best = math.inf
        for i in range(n):
            x0, y0 = self.points[i]
            x1, y1 = self.points[(i + 1) % n]
            dx, dy = x1 - x0, y1 - y0
            length_sq = dx * dx + dy * dy
            t = 0.0 if length_sq == 0 else max(
                0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / length_sq)
            )
            best = min(best, math.hypot(x - (x0 + t * dx), y - (y0 + t * dy)))
        return -best if self.contains(x, y) else best


def _segment_distance(a0, a1, b0, b1) -> float:
    """Minimum distance between two line segments."""
    def point_to_segment(px, py, x0, y0, x1, y1) -> float:
        dx, dy = x1 - x0, y1 - y0
        length_sq = dx * dx + dy * dy
        t = 0.0 if length_sq == 0 else max(
            0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / length_sq)
        )
        return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))

    (ax0, ay0), (ax1, ay1) = a0, a1
    (bx0, by0), (bx1, by1) = b0, b1
    # Proper crossing means zero distance.
    d1 = (bx1 - bx0) * (ay0 - by0) - (by1 - by0) * (ax0 - bx0)
    d2 = (bx1 - bx0) * (ay1 - by0) - (by1 - by0) * (ax1 - bx0)
    d3 = (ax1 - ax0) * (by0 - ay0) - (ay1 - ay0) * (bx0 - ax0)
    d4 = (ax1 - ax0) * (by1 - ay0) - (ay1 - ay0) * (bx1 - ax0)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(
        point_to_segment(ax0, ay0, bx0, by0, bx1, by1),
        point_to_segment(ax1, ay1, bx0, by0, bx1, by1),
        point_to_segment(bx0, by0, ax0, ay0, ax1, ay1),
        point_to_segment(bx1, by1, ax0, ay0, ax1, ay1),
    )


def _convex_hull(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Monotone chain. Returns the input unchanged when it has under 3 points."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return list(pts)

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


# ---------------------------------------------------------------------------
# Board entities.
# ---------------------------------------------------------------------------


@dataclass
class Pad:
    """One copper landing: an SMT pad or a plated hole."""

    id: str
    component_id: str | None
    port_id: str | None
    layer: str
    x: float
    y: float
    width: float
    height: float
    plated_hole: bool = False
    hole_diameter: float | None = None

    @property
    def rect(self) -> Rect:
        return Rect.from_center(self.x, self.y, self.width, self.height)


@dataclass
class Component:
    """A placed part: source identity joined to its PCB footprint."""

    source_id: str
    pcb_id: str | None
    name: str
    ftype: str | None
    layer: str
    center: tuple[float, float]
    width: float
    height: float
    rotation: float = 0.0
    do_not_place: bool = False
    lcsc: str | None = None
    resistance: float | None = None
    capacitance: float | None = None
    inductance: float | None = None
    color: str | None = None
    pads: list[Pad] = field(default_factory=list)
    courtyard: Rect | None = None
    #: The courtyard as the footprint actually declares it — one polygon per
    #: ``pcb_courtyard_rect`` / ``pcb_courtyard_outline``. Kept separately from
    #: :attr:`courtyard` (the bounding box of their union) because both a
    #: multi-rect courtyard and a *rotated* one cover far less space than their
    #: bbox, and testing collisions on the bbox invents overlaps that are not
    #: there — measured: nine of them on ``harness-puck``.
    courtyard_parts: list[Poly] = field(default_factory=list)

    @property
    def body(self) -> Rect:
        """The footprint bounding box the compiler reports (pads included)."""
        return Rect.from_center(self.center[0], self.center[1], self.width, self.height)

    @property
    def keepout(self) -> Rect:
        """Courtyard when the footprint declares one, else the body box. The
        courtyard is the assembly keep-out (IPC-7351); the body box is the
        conservative stand-in when a footprint has none."""
        return self.courtyard or self.body

    @property
    def keepout_parts(self) -> list[Poly]:
        """The keep-out as separate polygons — use this for collision tests."""
        return self.courtyard_parts or [self.body.as_poly()]

    def keepout_gap_to(self, other: "Component") -> float:
        """Smallest gap between any pair of the two parts' keep-out polygons."""
        return min(
            a.gap_to(b) for a in self.keepout_parts for b in other.keepout_parts
        )

    def pad_gap_to(self, other: "Component") -> float | None:
        """Smallest copper-to-copper gap between the two parts' pads."""
        gaps = [a.rect.gap_to(b.rect) for a in self.pads for b in other.pads]
        return min(gaps) if gaps else None

    @property
    def pad_bounds(self) -> Rect | None:
        return Rect.bounding(
            p
            for pad in self.pads
            for p in (
                (pad.rect.x0, pad.rect.y0),
                (pad.rect.x1, pad.rect.y1),
            )
        )

    @property
    def prefix(self) -> str:
        return "".join(c for c in self.name if c.isalpha()).upper()


@dataclass
class Net:
    """An electrical node: every port sharing one connectivity key."""

    key: str
    name: str | None
    is_power: bool = False
    is_ground: bool = False
    port_ids: list[str] = field(default_factory=list)
    #: ``(component name, port name)`` for every member — the readable form.
    pins: list[tuple[str, str]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.name or f"net:{self.key[-12:]}"


@dataclass
class TraceSegment:
    x0: float
    y0: float
    x1: float
    y1: float
    width: float
    layer: str | None

    @property
    def length(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)


@dataclass
class Trace:
    id: str
    net_key: str | None
    net_name: str | None
    segments: list[TraceSegment] = field(default_factory=list)

    @property
    def length(self) -> float:
        return sum(s.length for s in self.segments)

    @property
    def min_width(self) -> float | None:
        widths = [s.width for s in self.segments if s.width > 0]
        return min(widths) if widths else None


@dataclass
class Hole:
    x: float
    y: float
    diameter: float
    plated: bool
    component_id: str | None = None
    #: Overall drilled extent. Equal to ``diameter`` on a round hole; on a pill
    #: (a USB-C receptacle's through-hole legs, for one) the long axis is
    #: larger and the fab routes it as a slot.
    width: float | None = None
    height: float | None = None

    @property
    def size(self) -> tuple[float, float]:
        return (self.width or self.diameter, self.height or self.diameter)

    @property
    def is_slot(self) -> bool:
        w, h = self.size
        return abs(w - h) > 1e-6


# ---------------------------------------------------------------------------
# The index.
# ---------------------------------------------------------------------------


_PASSIVE_VALUE_FIELDS = {
    "resistance": "resistance",
    "capacitance": "capacitance",
    "inductance": "inductance",
}


class Board:
    """Everything a check needs from one ``circuit.json``, joined once."""

    def __init__(self, elements: Sequence[Element]):
        self.elements: list[Element] = [e for e in elements if isinstance(e, dict)]
        self._by_type: dict[str, list[Element]] = {}
        for e in self.elements:
            self._by_type.setdefault(str(e.get("type")), []).append(e)

        self.outline: Rect | None = None
        self.thickness_mm: float | None = None
        self.layers: int = 2
        self.name: str | None = None
        self._read_board()

        self.components: list[Component] = []
        self.by_name: dict[str, Component] = {}
        self._read_components()

        self.nets: list[Net] = []
        self.net_by_key: dict[str, Net] = {}
        self._port_net: dict[str, str] = {}       # source_port_id -> net key
        self._pcb_port_net: dict[str, str] = {}   # pcb_port_id -> net key
        self._read_nets()

        self._pad_by_source_port: dict[str, Pad] = {}
        self._read_port_pads()

        self.traces: list[Trace] = []
        self._read_traces()

        self.vias: list[Hole] = []
        self.holes: list[Hole] = []
        self._read_holes()

    # -- readers ----------------------------------------------------------

    def of_type(self, type_name: str) -> list[Element]:
        return self._by_type.get(type_name, [])

    def _read_board(self) -> None:
        board = next(iter(self.of_type("pcb_board")), None)
        if board is None:
            return
        width = board.get("width")
        height = board.get("height")
        center = board.get("center") or {}
        if isinstance(width, (int, float)) and isinstance(height, (int, float)):
            self.outline = Rect.from_center(
                float(center.get("x") or 0.0),
                float(center.get("y") or 0.0),
                float(width),
                float(height),
            )
        thickness = board.get("thickness")
        if isinstance(thickness, (int, float)):
            self.thickness_mm = float(thickness)
        num_layers = board.get("num_layers")
        if isinstance(num_layers, int):
            self.layers = num_layers
        meta = next(iter(self.of_type("source_project_metadata")), None)
        if isinstance(meta, dict):
            name = meta.get("project_name") or meta.get("name")
            if isinstance(name, str):
                self.name = name

    def _read_components(self) -> None:
        sources = {
            str(e.get("source_component_id")): e for e in self.of_type("source_component")
        }
        pcbs_by_source: dict[str, Element] = {}
        for e in self.of_type("pcb_component"):
            sid = str(e.get("source_component_id") or "")
            if sid:
                pcbs_by_source[sid] = e

        courtyards: dict[str, Rect] = {}
        courtyard_parts: dict[str, list[Poly]] = {}
        for e in self.of_type("pcb_courtyard_rect"):
            cid = str(e.get("pcb_component_id") or "")
            center = e.get("center") or {}
            w, h = e.get("width"), e.get("height")
            if not cid or not isinstance(w, (int, float)) or not isinstance(h, (int, float)):
                continue
            rect = Rect.from_center(
                float(center.get("x") or 0.0), float(center.get("y") or 0.0),
                float(w), float(h),
            )
            courtyards[cid] = _union(courtyards.get(cid), rect)
            courtyard_parts.setdefault(cid, []).append(rect.as_poly())
        for e in self.of_type("pcb_courtyard_outline"):
            cid = str(e.get("pcb_component_id") or "")
            pts = [
                (float(p["x"]), float(p["y"]))
                for p in (e.get("outline") or [])
                if isinstance(p, dict)
                and isinstance(p.get("x"), (int, float))
                and isinstance(p.get("y"), (int, float))
            ]
            rect = Rect.bounding(pts)
            if cid and rect is not None and len(pts) >= 3:
                courtyards[cid] = _union(courtyards.get(cid), rect)
                courtyard_parts.setdefault(cid, []).append(Poly(pts))

        pads_by_component: dict[str, list[Pad]] = {}
        for e in self.of_type("pcb_smtpad"):
            cid = str(e.get("pcb_component_id") or "")
            x, y = e.get("x"), e.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            width = e.get("width")
            height = e.get("height")
            if not isinstance(width, (int, float)):
                radius = e.get("radius")
                width = height = float(radius) * 2 if isinstance(radius, (int, float)) else 0.0
            if not isinstance(height, (int, float)):
                height = width
            pad = Pad(
                id=str(e.get("pcb_smtpad_id") or ""),
                component_id=cid or None,
                port_id=str(e.get("pcb_port_id") or "") or None,
                layer=str(e.get("layer") or "top"),
                x=float(x),
                y=float(y),
                width=float(width),
                height=float(height),
            )
            pads_by_component.setdefault(cid, []).append(pad)
        for e in self.of_type("pcb_plated_hole"):
            cid = str(e.get("pcb_component_id") or "")
            x, y = e.get("x"), e.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            outer_w = e.get("outer_width") or e.get("outer_diameter") or 0.0
            outer_h = e.get("outer_height") or e.get("outer_diameter") or outer_w
            hole = e.get("hole_diameter") or e.get("hole_width")
            pad = Pad(
                id=str(e.get("pcb_plated_hole_id") or ""),
                component_id=cid or None,
                port_id=str(e.get("pcb_port_id") or "") or None,
                layer="top",
                x=float(x),
                y=float(y),
                width=float(outer_w or 0.0),
                height=float(outer_h or 0.0),
                plated_hole=True,
                hole_diameter=float(hole) if isinstance(hole, (int, float)) else None,
            )
            pads_by_component.setdefault(cid, []).append(pad)

        for sid, source in sources.items():
            pcb = pcbs_by_source.get(sid)
            pcb_id = str(pcb.get("pcb_component_id")) if pcb else None
            center = (pcb or {}).get("center") or {}
            supplier = source.get("supplier_part_numbers") or {}
            lcsc_list = supplier.get("jlcpcb") if isinstance(supplier, dict) else None
            component = Component(
                source_id=sid,
                pcb_id=pcb_id,
                name=str(source.get("name") or sid),
                ftype=str(source.get("ftype")) if source.get("ftype") else None,
                layer=str((pcb or {}).get("layer") or "top"),
                center=(float(center.get("x") or 0.0), float(center.get("y") or 0.0)),
                width=float((pcb or {}).get("width") or 0.0),
                height=float((pcb or {}).get("height") or 0.0),
                rotation=float((pcb or {}).get("rotation") or 0.0),
                do_not_place=bool((pcb or {}).get("do_not_place")),
                lcsc=str(lcsc_list[0]) if isinstance(lcsc_list, list) and lcsc_list else None,
                color=str(source.get("color")) if source.get("color") else None,
                pads=pads_by_component.get(pcb_id or "", []),
                courtyard=courtyards.get(pcb_id or ""),
                courtyard_parts=courtyard_parts.get(pcb_id or "", []),
            )
            for attr, key in _PASSIVE_VALUE_FIELDS.items():
                value = source.get(key)
                if isinstance(value, (int, float)):
                    setattr(component, attr, float(value))
            self.components.append(component)
            self.by_name[component.name] = component

    def _read_nets(self) -> None:
        named: dict[str, Element] = {}
        for e in self.of_type("source_net"):
            key = e.get("subcircuit_connectivity_map_key")
            if isinstance(key, str):
                named[key] = e

        source_components = {
            str(e.get("source_component_id")): str(e.get("name") or "")
            for e in self.of_type("source_component")
        }
        groups: dict[str, Net] = {}
        for port in self.of_type("source_port"):
            key = port.get("subcircuit_connectivity_map_key")
            if not isinstance(key, str):
                continue
            net = groups.get(key)
            if net is None:
                meta = named.get(key)
                net = Net(
                    key=key,
                    name=str(meta.get("name")) if meta and meta.get("name") else None,
                    is_power=bool(meta.get("is_power")) if meta else False,
                    is_ground=bool(meta.get("is_ground")) if meta else False,
                )
                groups[key] = net
            port_id = str(port.get("source_port_id") or "")
            net.port_ids.append(port_id)
            self._port_net[port_id] = key
            component = source_components.get(str(port.get("source_component_id") or ""), "?")
            net.pins.append((component, str(port.get("name") or port.get("pin_number") or "?")))

        # Nets declared but with no port on them still matter (a dangling rail).
        for key, meta in named.items():
            if key not in groups:
                groups[key] = Net(
                    key=key,
                    name=str(meta.get("name")) if meta.get("name") else None,
                    is_power=bool(meta.get("is_power")),
                    is_ground=bool(meta.get("is_ground")),
                )

        self.nets = list(groups.values())
        self.net_by_key = groups

        for pcb_port in self.of_type("pcb_port"):
            source_port_id = str(pcb_port.get("source_port_id") or "")
            key = self._port_net.get(source_port_id)
            if key:
                self._pcb_port_net[str(pcb_port.get("pcb_port_id") or "")] = key

    def _read_port_pads(self) -> None:
        """``source_port_id -> Pad``, the copper a schematic pin actually lands
        on. Two hops: ``pcb_port`` carries the source id, and a pad carries the
        pcb port id. Geometry checks that must work *before* routing need this
        — pads exist whether or not a trace was ever laid."""
        pcb_to_source = {
            str(e.get("pcb_port_id") or ""): str(e.get("source_port_id") or "")
            for e in self.of_type("pcb_port")
        }
        for component in self.components:
            for pad in component.pads:
                source_port = pcb_to_source.get(pad.port_id or "")
                if source_port:
                    self._pad_by_source_port[source_port] = pad

    def _read_traces(self) -> None:
        source_nets = {
            str(e.get("source_net_id")): e.get("subcircuit_connectivity_map_key")
            for e in self.of_type("source_net")
        }
        source_traces = {
            str(e.get("source_trace_id")): e for e in self.of_type("source_trace")
        }
        for e in self.of_type("pcb_trace"):
            key: str | None = None
            connection = str(e.get("connection_name") or "")
            if connection in source_nets and isinstance(source_nets[connection], str):
                key = str(source_nets[connection])
            elif connection in source_traces:
                st = source_traces[connection]
                candidate = st.get("subcircuit_connectivity_map_key")
                if isinstance(candidate, str):
                    key = candidate
                else:
                    for pid in st.get("connected_source_port_ids") or []:
                        if str(pid) in self._port_net:
                            key = self._port_net[str(pid)]
                            break
            if key is None:
                for pid in e.get("connectsTo") or []:
                    hit = self._pcb_port_net.get(str(pid))
                    if hit:
                        key = hit
                        break

            segments: list[TraceSegment] = []
            route = [
                p
                for p in (e.get("route") or [])
                if isinstance(p, dict)
                and isinstance(p.get("x"), (int, float))
                and isinstance(p.get("y"), (int, float))
            ]
            for first, second in zip(route, route[1:]):
                width = first.get("width")
                segments.append(
                    TraceSegment(
                        x0=float(first["x"]),
                        y0=float(first["y"]),
                        x1=float(second["x"]),
                        y1=float(second["y"]),
                        width=float(width) if isinstance(width, (int, float)) else 0.0,
                        layer=str(first.get("layer")) if first.get("layer") else None,
                    )
                )
            net = self.net_by_key.get(key) if key else None
            self.traces.append(
                Trace(
                    id=str(e.get("pcb_trace_id") or ""),
                    net_key=key,
                    net_name=net.label if net else None,
                    segments=segments,
                )
            )

    def _read_holes(self) -> None:
        for e in self.of_type("pcb_via"):
            x, y = e.get("x"), e.get("y")
            d = e.get("hole_diameter")
            if all(isinstance(v, (int, float)) for v in (x, y, d)):
                self.vias.append(Hole(float(x), float(y), float(d), plated=True))
        for type_name, plated in (("pcb_hole", False), ("pcb_plated_hole", True)):
            for e in self.of_type(type_name):
                x, y = e.get("x"), e.get("y")
                hole_w = e.get("hole_width")
                hole_h = e.get("hole_height")
                d = e.get("hole_diameter")
                if not isinstance(d, (int, float)):
                    d = hole_w if isinstance(hole_w, (int, float)) else None
                if not all(isinstance(v, (int, float)) for v in (x, y, d)):
                    continue
                width = float(hole_w) if isinstance(hole_w, (int, float)) else float(d)
                height = float(hole_h) if isinstance(hole_h, (int, float)) else float(d)
                self.holes.append(
                    Hole(
                        float(x),
                        float(y),
                        # The drill tool is the *narrow* axis of a slot.
                        min(float(d), width, height),
                        plated=plated,
                        component_id=str(e.get("pcb_component_id") or "") or None,
                        width=width,
                        height=height,
                    )
                )

    # -- queries ----------------------------------------------------------

    def net_of_port(self, source_port_id: str) -> Net | None:
        key = self._port_net.get(source_port_id)
        return self.net_by_key.get(key) if key else None

    def pad_of_source_port(self, source_port_id: str) -> Pad | None:
        """The copper a schematic pin lands on, or ``None`` when the pin was
        never placed (a do-not-place part, or a footprint short of pads)."""
        return self._pad_by_source_port.get(source_port_id)

    def net_named(self, name: str) -> Net | None:
        for net in self.nets:
            if net.name == name:
                return net
        return None

    def traces_on(self, net: Net) -> list[Trace]:
        return [t for t in self.traces if t.net_key == net.key]

    def placed(self) -> list[Component]:
        """Components the assembler will actually pick and place."""
        return [
            c
            for c in self.components
            if not c.do_not_place and c.pads and c.width > 0 and c.height > 0
        ]

    @property
    def ground(self) -> Net | None:
        return next((n for n in self.nets if n.is_ground), None)

    @property
    def power_nets(self) -> list[Net]:
        return [n for n in self.nets if n.is_power]


def _union(a: Rect | None, b: Rect) -> Rect:
    if a is None:
        return b
    return Rect(min(a.x0, b.x0), min(a.y0, b.y0), max(a.x1, b.x1), max(a.y1, b.y1))


def load(path: str | Path) -> Board:
    """Read a ``circuit.json`` from disk into a :class:`Board`."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} is not a circuit-json array")
    return Board(data)
