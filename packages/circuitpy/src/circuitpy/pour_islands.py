"""Drop the pour regions nothing on the board is joined to.

**The defect this closes.** A ground pour does not arrive as one polygon. It
arrives as many, and most of them are joined to nothing. Measured 2026-09-04
over the five boards in the 2026-09-03 fab packet plus `desk-cube-55`, counting
`pcb_copper_pour` elements whose outline contains no via, pad, plated hole or
same-layer track of the pour's own net:

    board                  regions   dead   dead area
    rc-car-wifi-4servo           0      0      0.0mm2   (no pour at all)
    desk-cube-55                16     12     75.5mm2
    macropad-12-oled            17     15     64.6mm2
    rc-servo-driver-4ch         22     18     64.4mm2
    weather-badge-27            19     16    140.9mm2
    weather-badge-32            42     27    152.1mm2

KiCad's own DRC agrees, and it is worth being exact about how much: its
`isolated_copper` count is **12 / 15 / 18 / 16** on the first four — the same
number this module derives, independently, from the brep outlines. On
weather-badge-32 KiCad reports 31 where this reports 27, so what is dropped
here is a strict subset of what KiCad already calls isolated. That asymmetry is
deliberate and is the safety property: **every region this removes is one the
gate had already flagged as conducting nothing.**

**Where the fragmentation happens, which is not where the docs said.**
`verifylib/pour.py`'s docstring records that "the only place a pour is ever cut
into pieces is the KiCad conversion". It is not: `desk-cube-55`'s circuit.json
holds one 2959mm2 plane and **fifteen** separate fragments from 0.01mm2 up,
before any converter runs. The pieces are in the IR, so the repair belongs
here, in circuitpy, rather than in `kicad_normalize`.

**Why this is worth doing rather than tolerating.** Dead copper is not
harmless. It is unconnected metal that couples to whatever runs beside it, it
is 64-152mm2 of etch the fab still has to hold tolerance on, and it is 12-31
blocking-class DRC findings a human has to read past to reach the real ones.
It is also what stands between "the top pour fragments" and "the top pour is a
legitimate partial plane": build #3 of `desk-cube-55` was refused on five
findings that all named `Zone [GND] on F.Cu`, and 44 of its regions were
islands.

**What it does not do, and who owns that.** It does not stitch. A region with
one via in it is kept, whether or not that via ties it to the main plane, and
this module has no opinion about whether a plane has enough stitching vias —
that is placement, and placement belongs to the board's author. It also does
not decide that a board should pour on both faces. `desk-cube-55`'s author
poured one face, measured the alternative on a 27%-larger board, and wrote down
why in the source; that is a design decision made with evidence, not a defect.

**What it will not do.**

* It never drops the largest region of a (layer, net) group, even if that
  region looks anchorless. A pass that can delete the plane itself is a pass
  nobody should run before a fab packet.
* It never touches a region with any anchor: a via or plated hole of the net,
  a pad of the net on that layer, or a routed track of the net on that layer.
  A pad counts when its **copper** overlaps the region, not only when its
  centre is inside it — centre containment alone called five of
  weather-badge-32's regions dead that KiCad calls connected.
* It never edits a region's geometry. Regions are kept whole or removed whole,
  so nothing here can move a boundary the pour pass just placed.
* A board with nothing dead is returned byte-identical.

**Why it runs last.** `pour_clearance` pushes pour boundaries off other-net
copper and can only shrink a region; a region that was joined stays joined. So
membership is decided once, on final copper, after every stage that moves any.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from . import diffpair

#: Slack when asking whether a track's copper reaches into a region. The pour
#: declares a `traceMargin`, so a track that belongs to a region is normally
#: well inside it; this only covers the boundary case.
_TOUCH_MM = 0.02


@dataclass(frozen=True)
class DroppedIsland:
    """One region, and why it went."""

    pour_id: str
    layer: str
    area_mm2: float


@dataclass(frozen=True)
class IslandResult:
    ran: bool
    dropped: tuple[DroppedIsland, ...] = ()
    regions_seen: int = 0
    elapsed_s: float = 0.0
    note: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.dropped)

    @property
    def area_mm2(self) -> float:
        return sum(d.area_mm2 for d in self.dropped)

    def as_dict(self) -> dict:
        return {
            "ran": self.ran,
            "changed": self.changed,
            "regionsSeen": self.regions_seen,
            "dropped": len(self.dropped),
            "areaMm2": round(self.area_mm2, 3),
            "elapsed_s": round(self.elapsed_s, 3),
            "note": self.note,
            "islands": [
                {"id": d.pour_id, "layer": d.layer, "areaMm2": round(d.area_mm2, 3)}
                for d in self.dropped
            ],
        }

    def findings(self) -> list[dict]:
        if not self.ran or not self.changed:
            return []
        by_layer: dict[str, int] = {}
        for d in self.dropped:
            by_layer[d.layer] = by_layer.get(d.layer, 0) + 1
        where = ", ".join(f"{n} on {layer}" for layer, n in sorted(by_layer.items()))
        biggest = max(self.dropped, key=lambda d: d.area_mm2)
        return [{
            "part": "board",
            "kind": "pour_island_dropped",
            "severity": "info",
            "detail": (
                f"{len(self.dropped)} of {self.regions_seen} copper pour "
                f"region(s) had no via, pad, plated hole or track of their own "
                f"net anywhere in them ({where}); {self.area_mm2:.1f}mm2 of "
                f"copper removed, largest {biggest.area_mm2:.1f}mm2. Unconnected "
                f"metal conducts nothing, couples to whatever runs beside it, "
                f"and is what KiCad reports as isolated_copper — the pour "
                f"arrives from the compiler already in pieces, so this is the "
                f"pour being finished rather than the board being changed"
            ),
        }]


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _outline(pour: dict) -> list[tuple[float, float]]:
    shape = pour.get("brep_shape")
    ring = shape.get("outer_ring") if isinstance(shape, dict) else None
    vertices = ring.get("vertices") if isinstance(ring, dict) else None
    if not isinstance(vertices, list):
        return []
    out: list[tuple[float, float]] = []
    for v in vertices:
        if not isinstance(v, dict):
            continue
        x, y = diffpair._f(v.get("x")), diffpair._f(v.get("y"))
        if x is not None and y is not None:
            out.append((x, y))
    return out


def _area(points: Sequence[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(points)):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2


def _reaches(points: Sequence[tuple[float, float]], px: float, py: float,
             radius: float) -> bool:
    """Is copper of ``radius`` centred at (px, py) part of this region?

    Centre containment alone is not enough: a pad whose centre sits just
    outside the outline can still have copper across the edge, and reading
    those as dead called five of weather-badge-32's regions isolated that
    KiCad joins to the net.
    """
    if diffpair._point_in_poly(points, px, py):
        return True
    if radius <= 0:
        return False
    for i in range(len(points)):
        ax, ay = points[i]
        bx, by = points[(i + 1) % len(points)]
        if diffpair._seg_point_distance(ax, ay, bx, by, px, py) <= radius:
            return True
    return False


def _anchor_radius(element: dict) -> float:
    kind = element.get("type")
    if kind == "pcb_via":
        return (diffpair._f(element.get("outer_diameter"), 0.0) or 0.0) / 2
    if kind == "pcb_smtpad":
        r = diffpair._f(element.get("radius"), 0.0) or 0.0
        if r > 0:
            return r
        w = diffpair._f(element.get("width"), 0.0) or 0.0
        h = diffpair._f(element.get("height"), 0.0) or 0.0
        return math.hypot(w, h) / 2
    if kind == "pcb_plated_hole":
        d = diffpair._f(element.get("outer_diameter"), 0.0) or 0.0
        if d > 0:
            return d / 2
        w = diffpair._f(element.get("outer_width"), 0.0) or 0.0
        h = diffpair._f(element.get("outer_height"), 0.0) or 0.0
        return math.hypot(w, h) / 2
    return 0.0


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def drop_dead_pour_islands(
    circuit_json_path: Path, profile: Any = None,
) -> IslandResult:
    """Remove pour regions no copper of their own net reaches. Never raises."""
    started = time.monotonic()
    path = Path(circuit_json_path)
    try:
        elements = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return IslandResult(ran=False, note="circuit.json unreadable")
    if not isinstance(elements, list):
        return IslandResult(ran=False, note="circuit.json is not an element array")

    try:
        pours = [
            e for e in elements
            if isinstance(e, dict) and e.get("type") == "pcb_copper_pour"
        ]
        if not pours:
            return IslandResult(ran=False, note="no copper pour on this board")

        board = diffpair._Board(elements)
        net_of_trace = {
            str(t.get("pcb_trace_id") or ""): board.trace_net_key(t)
            for t in elements
            if isinstance(t, dict) and t.get("type") == "pcb_trace"
        }
        net_of_source = {
            str(n.get("source_net_id")): n.get("subcircuit_connectivity_map_key")
            for n in elements
            if isinstance(n, dict) and n.get("type") == "source_net"
        }

        regions: list[tuple[dict, str, str | None, list, float]] = []
        for pour in pours:
            points = _outline(pour)
            if len(points) < 3:
                continue
            layer = str(pour.get("layer") or "bottom")
            net = net_of_source.get(str(pour.get("source_net_id")))
            regions.append((pour, layer, net, points, _area(points)))

        #: The plane itself is never a candidate. Whatever the anchor scan
        #: says, a pass that can delete the largest region of a net is a pass
        #: that can hand a fab a board with no ground plane on it.
        largest: dict[tuple[str, str | None], float] = {}
        for _, layer, net, _, area in regions:
            key = (layer, net)
            if area > largest.get(key, -1.0):
                largest[key] = area

        dropped: list[DroppedIsland] = []
        for pour, layer, net, points, area in regions:
            if area >= largest[(layer, net)]:
                continue
            if _anchored(elements, board, net_of_trace, layer, net, points):
                continue
            dropped.append(DroppedIsland(
                pour_id=str(pour.get("pcb_copper_pour_id") or "pour"),
                layer=layer,
                area_mm2=area,
            ))

        if dropped:
            doomed = {d.pour_id for d in dropped}
            kept = [
                e for e in elements
                if not (
                    isinstance(e, dict)
                    and e.get("type") == "pcb_copper_pour"
                    and str(e.get("pcb_copper_pour_id") or "pour") in doomed
                )
            ]
            path.write_text(
                json.dumps(kept, ensure_ascii=False), encoding="utf-8")

        return IslandResult(
            ran=True,
            dropped=tuple(dropped),
            regions_seen=len(regions),
            elapsed_s=time.monotonic() - started,
        )
    except Exception as exc:  # noqa: BLE001
        # Advisory, like every repair stage: a pass that dies costs a repair,
        # never a verdict. `isolated_copper` still reports what is there.
        return IslandResult(
            ran=False,
            elapsed_s=time.monotonic() - started,
            note=f"pour island scan raised {type(exc).__name__}: {exc}",
        )


def _anchored(elements: Sequence[dict], board: diffpair._Board,
              net_of_trace: dict[str, str | None], layer: str,
              net: str | None, points: Sequence[tuple[float, float]]) -> bool:
    """Does anything of this region's net reach into it?"""
    for element in elements:
        if not isinstance(element, dict):
            continue
        kind = element.get("type")
        if kind == "pcb_via":
            owner = element.get("pcb_trace_id")
            key = net_of_trace.get(str(owner)) if owner else None
            if key is None:
                candidate = element.get("subcircuit_connectivity_map_key")
                key = candidate if isinstance(candidate, str) else None
        elif kind == "pcb_smtpad":
            if str(element.get("layer") or "top") != layer:
                continue
            key = board.net_key_of_pcb_port(str(element.get("pcb_port_id") or ""))
        elif kind == "pcb_plated_hole":
            # Through-plated: it occupies copper on every layer it passes.
            key = board.net_key_of_pcb_port(str(element.get("pcb_port_id") or ""))
        else:
            continue
        if key != net:
            continue
        x, y = diffpair._f(element.get("x")), diffpair._f(element.get("y"))
        if x is None or y is None:
            continue
        if _reaches(points, x, y, _anchor_radius(element)):
            return True

    # A track of the same net on the same layer is the same conductor: where
    # one runs through a region, that region is not isolated, and KiCad counts
    # it exactly that way. Two of rc-servo-driver-4ch's regions are joined by
    # nothing else, which is the whole reason this clause exists.
    for trace in elements:
        if not isinstance(trace, dict) or trace.get("type") != "pcb_trace":
            continue
        if net_of_trace.get(str(trace.get("pcb_trace_id") or "")) != net:
            continue
        for point in trace.get("route") or []:
            if str(point.get("layer") or "") != layer:
                continue
            x, y = diffpair._f(point.get("x")), diffpair._f(point.get("y"))
            if x is None or y is None:
                continue
            if _reaches(points, x, y, _TOUCH_MM):
                return True
    return False
