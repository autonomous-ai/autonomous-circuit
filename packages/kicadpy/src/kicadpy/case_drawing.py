"""Engineering drawing sheets of the exported case parts, as SVG.

Each sheet is A3 landscape in millimetres, laid out in third-angle projection: the top view sits above
the front view and the right view sits to its right, with an isometric view in the corner, overall
dimensions, a parts list and a title block (project, scale, mm, version, date). Lines come from
OpenCascade's exact hidden-line removal, so every edge is where the B-rep says it is.

The team harness runs this after the case CAD passes its tester, with no model, from the STEP files
listed in case/out/parts.json:

    "$KICAD_CASE_PYTHON" -m kicadpy.case_drawing .   # -> case/out/drawings/{assembly,<part>}.svg + index.json

One assembly sheet (every assembled part, bought ones marked) and one sheet per printed part; test
coupons are left out. Identical parts (same STEP bytes) share one sheet with their count.

Ported from cadsmith (src/cadsmith/drawing.py and the tessellation in src/cadsmith/mesh.py),
MIT License, Copyright (c) 2026 dee, Copyright (c) 2026 cadsmith contributors:

    Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
    associated documentation files (the "Software"), to deal in the Software without restriction,
    including without limitation the rights to use, copy, modify, merge, publish, distribute,
    sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions: The above copyright notice and this
    permission notice shall be included in all copies or substantial portions of the Software.
    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from html import escape

import cadquery as cq
import numpy as np
from OCP.BRepLib import BRepLib
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape

from OCP.BRep import BRep_Tool
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location

LINEAR_TOLERANCE = 0.02
ANGULAR_TOLERANCE = 0.1


@dataclass
class Mesh:
    """A triangle mesh (from cadsmith/mesh.py)."""

    vertices: np.ndarray
    faces: np.ndarray

    @property
    def triangles(self) -> np.ndarray:
        return self.vertices[self.faces]

    def _cross(self) -> np.ndarray:
        tri = self.triangles
        return np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])

    @property
    def areas(self) -> np.ndarray:
        return 0.5 * np.linalg.norm(self._cross(), axis=1)

    @property
    def normals(self) -> np.ndarray:
        cross = self._cross()
        length = np.linalg.norm(cross, axis=1, keepdims=True)
        return cross / np.where(length == 0, 1.0, length)

    @property
    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.vertices.min(axis=0), self.vertices.max(axis=0)

    @property
    def signed_volume(self) -> float:
        tri = self.triangles
        return float(np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6.0)

    def rotated(self, rotation: np.ndarray) -> "Mesh":
        return Mesh(self.vertices @ np.asarray(rotation).T, self.faces)

    def translated(self, offset) -> "Mesh":
        return Mesh(self.vertices + np.asarray(offset, dtype=float), self.faces)


def tessellate(shape, tolerance: float = LINEAR_TOLERANCE, angular: float = ANGULAR_TOLERANCE) -> Mesh:
    """A closed outward-facing mesh of ``shape``, with coincident vertices merged."""
    points, faces = _triangulate(shape, tolerance, angular)
    if not len(faces):
        raise ValueError("the shape produced no triangles")
    unique, inverse = np.unique(np.round(points, 5), axis=0, return_inverse=True)
    faces = np.asarray(inverse).reshape(-1)[faces]
    keep = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    mesh = Mesh(unique, faces[keep])
    if mesh.signed_volume < 0:
        mesh = Mesh(mesh.vertices, mesh.faces[:, ::-1])
    return mesh


def _triangulate(shape, tolerance: float, angular: float) -> tuple[np.ndarray, np.ndarray]:
    """Every face's triangulation, read node by node.

    CadQuery's own ``tessellate`` walks each face's triangle array in a way that costs milliseconds per
    face, which is minutes for an imported mesh with thousands of faces.
    """
    shape.mesh(tolerance, angular)
    points: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for face in shape.Faces():
        location = TopLoc_Location()
        poly = BRep_Tool.Triangulation_s(face.wrapped, location)
        if poly is None:
            continue
        transform, offset = location.Transformation(), len(points)
        for index in range(1, poly.NbNodes() + 1):
            node = poly.Node(index).Transformed(transform)
            points.append((node.X(), node.Y(), node.Z()))
        reverse = face.wrapped.Orientation() == TopAbs_REVERSED
        for index in range(1, poly.NbTriangles() + 1):
            a, b, c = poly.Triangle(index).Get()
            faces.append((offset + a - 1, offset + c - 1, offset + b - 1) if reverse
                         else (offset + a - 1, offset + b - 1, offset + c - 1))
    return np.array(points, dtype=float).reshape(-1, 3), np.array(faces, dtype=np.int64).reshape(-1, 3)


SHEET_W, SHEET_H = 420.0, 297.0
MARGIN = 10.0
SIDE_W = 150.0  # right column: iso view, parts list, title block
TITLE_H = 46.0
DIM_ROOM = 16.0  # space kept around a view for its dimensions
#: Preferred scales from ISO 5455, largest first.
SCALES = (10.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05, 0.02)
#: name: (direction toward the viewer, direction that points right on the sheet)
VIEWS = {
    "front": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
    "top": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "iso": ((1.0, -1.0, 1.0), (1.0, 1.0, 0.0)),
}
#: Above this many faces, such as an imported mesh, exact hidden-line removal takes minutes; such shapes
#: are drawn from their triangles instead.
HEAVY_FACES = 1500
CREASE_DEG = 35.0
MAX_HOLE_NOTES = 6
MAX_LIST_ROWS = 24
MIN_HOLE_RADIUS = 0.4
_SIDE_TOKENS = {"left", "right", "front", "rear", "back", "top", "bottom", "l", "r", "fl", "fr", "rl", "rr"}


@dataclass
class View:
    """One projected view, in model millimetres on the view plane (u right, v up)."""

    visible: list[list[tuple[float, float]]] = field(default_factory=list)
    hidden: list[list[tuple[float, float]]] = field(default_factory=list)
    circles: list[tuple[float, float, float]] = field(default_factory=list)  # visible full circles: u, v, r

    def bounds(self) -> tuple[float, float, float, float]:
        points = [p for line in self.visible + self.hidden for p in line]
        if not points:
            return 0.0, 0.0, 0.0, 0.0
        us, vs = [p[0] for p in points], [p[1] for p in points]
        return min(us), min(vs), max(us), max(vs)


@dataclass(frozen=True)
class ListRow:
    """One row of an assembly's parts list."""

    name: str
    quantity: int
    size: tuple[float, float, float]
    bought: bool


def _polyline(edge: cq.Edge) -> list[tuple[float, float]]:
    if edge.geomType() == "LINE":
        ends = (edge.startPoint(), edge.endPoint())
    else:
        count = max(8, min(120, int(edge.Length() / 0.6) + 2))
        ends = [edge.positionAt(i / (count - 1)) for i in range(count)]
    return [(p.x, p.y) for p in ends]


def _edges(compound) -> list[cq.Edge]:
    if compound.IsNull():
        return []
    BRepLib.BuildCurves3d_s(compound, 1e-6)
    return cq.Shape.cast(compound).Edges()


def project(shape: cq.Shape, name: str, hidden: bool = True) -> View:
    """Hidden-line projection of ``shape`` along one of :data:`VIEWS`."""
    if len(shape.Faces()) > HEAVY_FACES:
        return project_mesh(shape, name)
    toward, right = VIEWS[name]
    algo = HLRBRep_Algo()
    algo.Add(shape.wrapped)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(), gp_Dir(*toward), gp_Dir(*right))))
    algo.Update()
    algo.Hide()
    lines = HLRBRep_HLRToShape(algo)
    view = View()
    for compound in (lines.VCompound(), lines.OutLineVCompound()):
        for edge in _edges(compound):
            view.visible.append(_polyline(edge))
            if edge.geomType() == "CIRCLE" and edge.IsClosed() and edge.radius() >= MIN_HOLE_RADIUS:
                centre = edge.arcCenter()
                view.circles.append((centre.x, centre.y, edge.radius()))
    if hidden:
        straight = [line for line in view.visible if len(line) == 2]
        for compound in (lines.HCompound(), lines.OutLineHCompound()):
            for edge in _edges(compound):
                line = _polyline(edge)
                if not (len(line) == 2 and _covered(line, straight)):
                    view.hidden.append(line)
    return view


def _depth_buffer(u, v, w, faces, res, origin, shape):
    """The nearest depth (largest w) per cell, over the given triangles."""
    zbuf = np.full(shape, -np.inf)
    cu, cv = (u - origin[0]) / res, (v - origin[1]) / res
    for a, b, c in faces:
        x0, x1 = int(max(min(cu[a], cu[b], cu[c]), 0)), int(min(max(cu[a], cu[b], cu[c]) + 1, shape[1] - 1))
        y0, y1 = int(max(min(cv[a], cv[b], cv[c]), 0)), int(min(max(cv[a], cv[b], cv[c]) + 1, shape[0] - 1))
        if x1 < x0 or y1 < y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = (cv[b] - cv[c]) * (cu[a] - cu[c]) + (cu[c] - cu[b]) * (cv[a] - cv[c])
        if abs(d) < 1e-12:
            continue
        l1 = ((cv[b] - cv[c]) * (xs - cu[c]) + (cu[c] - cu[b]) * (ys - cv[c])) / d
        l2 = ((cv[c] - cv[a]) * (xs - cu[c]) + (cu[a] - cu[c]) * (ys - cv[c])) / d
        l3 = 1 - l1 - l2
        inside = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
        if not inside.any():
            continue
        depth = l1 * w[a] + l2 * w[b] + l3 * w[c]
        cell = zbuf[y0:y1 + 1, x0:x1 + 1]
        np.maximum(cell, np.where(inside, depth, -np.inf), out=cell)
    return zbuf


def project_mesh(shape: cq.Shape, name: str) -> View:
    """Outlines and creases of a many-faced shape, with what lies behind nearer triangles left out."""
    mesh = tessellate(shape, 0.05, 0.25)
    toward, right = (np.array(d, dtype=float) / np.linalg.norm(d) for d in VIEWS[name])
    up = np.cross(toward, right)
    points, faces = mesh.vertices, mesh.faces
    u, v, w = points @ right, points @ up, points @ toward
    normals = mesh.normals
    front = normals @ toward > 1e-9
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    owner = np.tile(np.arange(len(faces)), 3)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges, owner = edges[order], owner[order]
    same = np.all(edges[1:] == edges[:-1], axis=1)
    pairs = np.flatnonzero(same)  # edge i and i + 1 are the two sides of one mesh edge
    first, second = owner[pairs], owner[pairs + 1]
    cos_crease = math.cos(math.radians(CREASE_DEG))
    keep = (front[first] != front[second]) | (
        (np.einsum("ij,ij->i", normals[first], normals[second]) < cos_crease) & (front[first] | front[second]))
    chosen = edges[pairs][keep]
    single = np.ones(len(edges), dtype=bool)
    single[pairs] = single[pairs + 1] = False
    chosen = np.concatenate([chosen, edges[single]]) if single.any() else chosen

    span = max(u.max() - u.min(), v.max() - v.min(), 1e-6)
    res = max(span / 1400.0, 0.02)
    origin = (u.min() - 2 * res, v.min() - 2 * res)
    grid = (int((v.max() - origin[1]) / res) + 3, int((u.max() - origin[0]) / res) + 3)
    zbuf = _depth_buffer(u, v, w, faces[front], res, origin, grid)
    # an edge counts as hidden only when every neighbouring cell is nearer, so outlines at grazing angles hold
    padded = np.pad(zbuf, 1, constant_values=-np.inf)
    zbuf = np.min([padded[1 + dy:1 + dy + grid[0], 1 + dx:1 + dx + grid[1]] for dy in (-1, 0, 1) for dx in (-1, 0, 1)],
                  axis=0)
    tolerance = max(3 * res, 0.05)

    view = View()
    for a, b in chosen:
        length = math.hypot(u[b] - u[a], v[b] - v[a])
        count = max(2, int(length / (res * 1.5)) + 1)
        t = np.linspace(0.0, 1.0, count)
        su, sv, sw = u[a] + (u[b] - u[a]) * t, v[a] + (v[b] - v[a]) * t, w[a] + (w[b] - w[a]) * t
        ix = np.clip(((su - origin[0]) / res).astype(int), 0, grid[1] - 1)
        iy = np.clip(((sv - origin[1]) / res).astype(int), 0, grid[0] - 1)
        seen = sw >= zbuf[iy, ix] - tolerance
        if seen.all():
            view.visible.append([(float(u[a]), float(v[a])), (float(u[b]), float(v[b]))])
            continue
        start = None
        for index, flag in enumerate(list(seen) + [False]):
            if flag and start is None:
                start = index
            elif not flag and start is not None:
                if index - start >= 2:
                    view.visible.append([(float(su[start]), float(sv[start])), (float(su[index - 1]), float(sv[index - 1]))])
                start = None
    return view


def _on_segment(point, segment, tolerance=1e-3) -> bool:
    (ax, ay), (bx, by) = segment
    px, py = point
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy) <= tolerance


def _covered(line, segments) -> bool:
    """A hidden straight line that lies under one visible line adds nothing to the drawing."""
    return any(_on_segment(line[0], seg) and _on_segment(line[1], seg) for seg in segments)


def number(value: float) -> str:
    """A dimension value: one decimal at most, no trailing zeros."""
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def scale_label(scale: float) -> str:
    return f"{number(scale)}:1" if scale >= 1 else f"1:{number(1 / scale)}"


def pick_scale(width: float, height: float, room_w: float, room_h: float) -> float:
    """The largest preferred scale that fits a ``width`` by ``height`` mm footprint in the room."""
    for scale in SCALES:
        if width * scale <= room_w and height * scale <= room_h:
            return scale
    return SCALES[-1]


def stem(name: str) -> str:
    """A part name without its side words, so the four legs of a robot list as one row."""
    words = [w for w in re.split(r"[_\-\s]+", name) if w and w.lower() not in _SIDE_TOKENS and not w.isdigit()]
    return " ".join(words) or name


def parts_list(parts) -> list[ListRow]:
    """Group parts by name stem, size and kind; printed rows first."""
    groups: dict[tuple, ListRow] = {}
    for part in parts:
        box = part.shape.BoundingBox()
        size = tuple(sorted((round(box.xlen, 1), round(box.ylen, 1), round(box.zlen, 1)), reverse=True))
        label = re.sub(r"(?i)^ref[ _-]*do[ _-]*not[ _-]*print[ _-]*", "", part.name) if part.bought else part.name
        key = (stem(label), size, part.bought)
        row = groups.get(key)
        count = part.quantity + (row.quantity if row else 0)
        groups[key] = ListRow(key[0], count, size, part.bought)
    return sorted(groups.values(), key=lambda r: (r.bought, r.name))


class Sheet:
    """Builds the SVG text of one sheet."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, text: str) -> None:
        self.items.append(text)

    def text(self, x, y, value, size=3.5, anchor="start", weight=400, rotate=None, cls="t"):
        turn = f' transform="rotate({rotate} {x:.2f} {y:.2f})"' if rotate else ""
        self.add(f'<text class="{cls}" x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}" '
                 f'font-weight="{weight}"{turn}>{escape(str(value))}</text>')

    def line(self, x1, y1, x2, y2, cls="thin"):
        self.add(f'<line class="{cls}" x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}"/>')

    def rect(self, x, y, w, h, cls="frame"):
        self.add(f'<rect class="{cls}" x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}"/>')

    def arrow(self, x, y, dx, dy):
        """A filled arrowhead with its tip at ``x, y`` pointing along ``dx, dy``."""
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        bx, by = x - ux * 3.0, y - uy * 3.0
        self.add(f'<path class="head" d="M{x:.2f} {y:.2f}L{bx - uy * 0.6:.2f} {by + ux * 0.6:.2f}'
                 f'L{bx + uy * 0.6:.2f} {by - ux * 0.6:.2f}Z"/>')

    def svg(self, title: str) -> str:
        style = (
            "text{font-family:'Helvetica Neue',Arial,sans-serif;fill:#111}"
            ".frame{fill:none;stroke:#111;stroke-width:.5}.box{fill:none;stroke:#111;stroke-width:.35}"
            ".vis{fill:none;stroke:#111;stroke-width:.35;stroke-linecap:round;stroke-linejoin:round}"
            ".hid{fill:none;stroke:#555;stroke-width:.18;stroke-dasharray:1.6 .8}"
            ".thin{fill:none;stroke:#111;stroke-width:.18}.ctr{fill:none;stroke:#555;stroke-width:.13;"
            "stroke-dasharray:4 .8 .6 .8}.head{fill:#111}.t2{fill:#555}"
        )
        body = "\n".join(self.items)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{SHEET_W:g}mm" height="{SHEET_H:g}mm" '
                f'viewBox="0 0 {SHEET_W:g} {SHEET_H:g}">\n<title>{escape(title)}</title>\n<style>{style}</style>\n'
                f'<rect x="0" y="0" width="{SHEET_W:g}" height="{SHEET_H:g}" fill="#fff"/>\n{body}\n</svg>\n')


def _path(lines, place) -> str:
    parts = []
    for line in lines:
        points = [place(u, v) for u, v in line]
        parts.append("M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in points))
    return "".join(parts)


@dataclass
class Placed:
    """A view placed on the sheet: paper x = ``x + u * scale``, paper y = ``y - v * scale``."""

    view: View
    x: float
    y: float
    scale: float

    def at(self, u: float, v: float) -> tuple[float, float]:
        return self.x + u * self.scale, self.y - v * self.scale

    def box(self) -> tuple[float, float, float, float]:
        """Paper left, top, right, bottom."""
        u0, v0, u1, v1 = self.view.bounds()
        (left, bottom), (right, top) = self.at(u0, v0), self.at(u1, v1)
        return left, top, right, bottom


def _draw_view(sheet: Sheet, placed: Placed, label: str) -> None:
    if placed.view.hidden:
        sheet.add(f'<path class="hid" d="{_path(placed.view.hidden, placed.at)}"/>')
    sheet.add(f'<path class="vis" d="{_path(placed.view.visible, placed.at)}"/>')
    left, top, right, bottom = placed.box()
    sheet.text((left + right) / 2, bottom + 12.5 if label != "ISO" else bottom + 6, label, size=3, anchor="middle",
               weight=600, cls="t t2")


def _centre_marks(sheet: Sheet, placed: Placed) -> None:
    seen = set()
    for u, v, r in placed.view.circles:
        key = (round(u, 2), round(v, 2))
        if key in seen:
            continue
        seen.add(key)
        x, y = placed.at(u, v)
        reach = r * placed.scale + 1.5
        if reach < 2.2:
            continue
        sheet.line(x - reach, y, x + reach, y, "ctr")
        sheet.line(x, y - reach, x, y + reach, "ctr")


def dim_h(sheet: Sheet, x1: float, x2: float, y_object: float, y_line: float, value: float) -> None:
    """A horizontal dimension between ``x1`` and ``x2``, with its line at ``y_line``."""
    step = 1.0 if y_line > y_object else -1.0
    for x in (x1, x2):
        sheet.line(x, y_object + step * 1.0, x, y_line + step * 2.0)
    sheet.line(x1, y_line, x2, y_line)
    sheet.arrow(x1, y_line, -1, 0)
    sheet.arrow(x2, y_line, 1, 0)
    sheet.text((x1 + x2) / 2, y_line - 1.0, number(value), size=3.5, anchor="middle")


def dim_v(sheet: Sheet, y1: float, y2: float, x_object: float, x_line: float, value: float) -> None:
    """A vertical dimension between ``y1`` and ``y2``, with its line at ``x_line``."""
    step = 1.0 if x_line > x_object else -1.0
    for y in (y1, y2):
        sheet.line(x_object + step * 1.0, y, x_line + step * 2.0, y)
    sheet.line(x_line, y1, x_line, y2)
    sheet.arrow(x_line, y1, 0, -1)
    sheet.arrow(x_line, y2, 0, 1)
    sheet.text(x_line - 1.0, (y1 + y2) / 2, number(value), size=3.5, anchor="middle", rotate=-90)


def _hole_notes(sheet: Sheet, placed_views: list[Placed]) -> None:
    """Label each distinct hole diameter once, on the view that shows it most often."""
    best: dict[float, tuple[int, Placed, tuple[float, float, float]]] = {}
    for placed in placed_views:
        counts: dict[float, list] = {}
        unique = {(round(u, 2), round(v, 2), round(r, 2)): (u, v, r) for u, v, r in placed.view.circles}
        for u, v, r in unique.values():
            counts.setdefault(round(2 * r, 1), []).append((u, v, r))
        for diameter, found in counts.items():
            if diameter not in best or len(found) > best[diameter][0]:
                pick = max(found, key=lambda c: (c[0] + c[1]))  # the upper-right one: room for the leader
                best[diameter] = (len(found), placed, pick)
    for diameter, (count, placed, (u, v, r)) in sorted(best.items(), key=lambda kv: -kv[1][0])[:MAX_HOLE_NOTES]:
        x, y = placed.at(u, v)
        reach = r * placed.scale
        tip_x, tip_y = x + reach * 0.7071, y - reach * 0.7071
        knee_x, knee_y = tip_x + 6, tip_y - 6
        label = f"{count}× ⌀{number(diameter)}" if count > 1 else f"⌀{number(diameter)}"
        sheet.line(tip_x, tip_y, knee_x, knee_y)
        sheet.line(knee_x, knee_y, knee_x + 2 + len(label) * 1.9, knee_y)
        sheet.arrow(tip_x, tip_y, -1, 1)
        sheet.text(knee_x + 1, knee_y - 0.8, label, size=3)


def _projection_symbol(sheet: Sheet, x: float, y: float) -> None:
    """The third-angle symbol: a truncated cone seen from the side, then from its small end."""
    sheet.add(f'<path class="box" d="M{x:.2f} {y + 1:.2f}L{x + 9:.2f} {y - 1.5:.2f}L{x + 9:.2f} {y + 7.5:.2f}'
              f'L{x:.2f} {y + 5:.2f}Z"/>')
    sheet.add(f'<circle class="box" cx="{x + 16:.2f}" cy="{y + 3:.2f}" r="4.5"/>')
    sheet.add(f'<circle class="box" cx="{x + 16:.2f}" cy="{y + 3:.2f}" r="2"/>')
    sheet.line(x - 2, y + 3, x + 22, y + 3, "ctr")


def _title_block(sheet: Sheet, title: str, kind: str, scale: float, size, version: str, sheet_no: str,
                 note: str) -> None:
    x0, y0 = SHEET_W - MARGIN - SIDE_W, SHEET_H - MARGIN - TITLE_H
    w = SIDE_W
    sheet.rect(x0, y0, w, TITLE_H, "frame")
    for y in (y0 + 16, y0 + 26, y0 + 36):
        sheet.line(x0, y, x0 + w, y, "box")
    for x in (x0 + 50, x0 + 100):
        sheet.line(x, y0 + 16, x, y0 + TITLE_H, "box")
    small = 2.2
    sheet.text(x0 + 2.5, y0 + 4.5, kind.upper(), size=small, cls="t t2")
    shown = title if len(title) <= 34 else title[:33] + "…"
    sheet.text(x0 + 2.5, y0 + 12.5, shown, size=6, weight=700)
    cells = [
        ("SCALE", scale_label(scale)), ("UNITS", "mm"), ("PROJECTION", ""),
        ("SIZE", " × ".join(number(v) for v in size)), ("VERSION", version), ("SHEET", sheet_no),
        ("DRAWN", "kicadpy"), ("DATE", date.today().isoformat()), ("FORMAT", "A3"),
    ]
    for index, (label, value) in enumerate(cells):
        cx, cy = x0 + (index % 3) * 50, y0 + 16 + (index // 3) * 10
        sheet.text(cx + 2, cy + 3.2, label, size=small, cls="t t2")
        if value:
            sheet.text(cx + 2, cy + 8.2, value, size=3.5, weight=600)
    _projection_symbol(sheet, x0 + 124, y0 + 18.6)
    if note:
        sheet.text(x0 + 2, y0 - 2.5, note, size=2.5, cls="t t2")


def _parts_table(sheet: Sheet, rows: list[ListRow], bottom: float) -> float:
    """The parts list, standing on the title block; returns its top edge."""
    x0, w = SHEET_W - MARGIN - SIDE_W, SIDE_W
    row_h = 4.6
    columns = ((0, "ITEM"), (11, "NAME"), (88, "QTY"), (99, "SIZE mm"))
    fit = MAX_LIST_ROWS
    shown = rows if len(rows) <= fit else rows[: fit - 1]
    y = bottom - row_h * (len(shown) + 1 + (len(rows) > len(shown)))
    sheet.rect(x0, y, w, bottom - y, "frame")
    for offset, label in columns:
        sheet.text(x0 + offset + 1.5, y + 3.3, label, size=2.2, cls="t t2")
    for offset, _ in columns[1:]:
        sheet.line(x0 + offset, y, x0 + offset, bottom, "box")
    for index, row in enumerate(shown):
        ry = y + row_h * (index + 1)
        sheet.line(x0, ry, x0 + w, ry, "box")
        # kicadpy: every bought part says so, however long its name
        limit = 20 if row.bought else 30
        name = row.name if len(row.name) <= limit else row.name[:limit - 1] + "…"
        name += " (bought)" if row.bought else ""
        cells = (str(index + 1), name, str(row.quantity), " × ".join(number(v) for v in row.size))
        for (offset, _), value in zip(columns, cells):
            sheet.text(x0 + offset + 1.5, ry + 3.3, value, size=2.6, cls="t t2" if row.bought else "t")
    if len(rows) > len(shown):
        ry = y + row_h * (len(shown) + 1)
        sheet.line(x0, ry, x0 + w, ry, "box")
        sheet.text(x0 + 12.5, ry + 3.3, f"… and {len(rows) - len(shown)} more", size=2.6, cls="t t2")
    return y


def make_sheet(shape: cq.Shape, title: str, kind: str, version: str = "", sheet_no: str = "1/1",
               rows: list[ListRow] | None = None, hidden: bool = True, holes: bool = True) -> str:
    """One A3 sheet for ``shape``: front, top and right views with dimensions, plus an iso view."""
    box = shape.BoundingBox()
    size = (box.xlen, box.ylen, box.zlen)
    views = {name: project(shape, name, hidden=hidden and name != "iso") for name in VIEWS}
    sheet = Sheet()
    sheet.rect(MARGIN, MARGIN, SHEET_W - 2 * MARGIN, SHEET_H - 2 * MARGIN, "frame")

    # three views: front at bottom left, top above it, right to its right
    room_w = SHEET_W - 2 * MARGIN - SIDE_W - 4 * DIM_ROOM
    room_h = SHEET_H - 2 * MARGIN - 4 * DIM_ROOM
    gap = 2 * DIM_ROOM
    scale = pick_scale(size[0] + size[1], size[1] + size[2], room_w, room_h)
    width, height = (size[0] + size[1]) * scale + gap, (size[1] + size[2]) * scale + gap
    left = MARGIN + 1.5 * DIM_ROOM + (room_w + DIM_ROOM - width) / 2
    top = MARGIN + DIM_ROOM + (room_h + 2 * DIM_ROOM - height) / 2
    front_y = top + size[1] * scale + gap + size[2] * scale  # paper y of the lowest point of the front view
    right_x = left + size[0] * scale + gap
    front = Placed(views["front"], left - box.xmin * scale, front_y + box.zmin * scale, scale)
    top_view = Placed(views["top"], left - box.xmin * scale, top + box.ymax * scale, scale)
    right = Placed(views["right"], right_x - box.ymin * scale, front_y + box.zmin * scale, scale)
    for placed, label in ((front, "FRONT"), (top_view, "TOP"), (right, "RIGHT")):
        _draw_view(sheet, placed, label)
        _centre_marks(sheet, placed)

    # overall dimensions
    f_left, f_top, f_right, f_bottom = front.box()
    dim_h(sheet, f_left, f_right, f_bottom, f_bottom + 8, size[0])
    dim_v(sheet, f_top, f_bottom, f_left, f_left - 8, size[2])
    t_left, t_top, t_right, t_bottom = top_view.box()
    dim_v(sheet, t_top, t_bottom, t_left, t_left - 8, size[1])
    r_left, r_top, r_right, r_bottom = right.box()
    dim_h(sheet, r_left, r_right, r_bottom, r_bottom + 8, size[1])
    if holes:
        _hole_notes(sheet, [front, top_view, right])

    # iso view in the right column, above the parts list or title block; drawn to fit, not to scale
    side_x = SHEET_W - MARGIN - SIDE_W
    table_top = _parts_table(sheet, rows, SHEET_H - MARGIN - TITLE_H) if rows else SHEET_H - MARGIN - TITLE_H - 8
    iso_h = table_top - MARGIN - 18
    u0, v0, u1, v1 = views["iso"].bounds()
    iso_scale = min((SIDE_W - 20) / max(u1 - u0, 1e-3), iso_h / max(v1 - v0, 1e-3), scale * 1.5)
    cx, cy = side_x + SIDE_W / 2, MARGIN + 6 + iso_h / 2
    iso = Placed(views["iso"], cx - (u0 + u1) / 2 * iso_scale, cy + (v0 + v1) / 2 * iso_scale, iso_scale)
    _draw_view(sheet, iso, "ISO")
    sheet.line(side_x, MARGIN, side_x, SHEET_H - MARGIN - TITLE_H, "box")
    _title_block(sheet, title, kind, scale, size, version, sheet_no, "" if rows else "Sizes in mm, as modelled. ISO view not to scale.")
    return sheet.svg(f"{title} · {kind}")


def assembly_sheet(model, title: str, version: str = "", sheet_no: str = "1/1") -> str:
    """The whole model, outlines only, with a parts list."""
    shape = cq.Compound.makeCompound([part.shape for part in model.parts])
    return make_sheet(shape, title, "assembly", version, sheet_no, rows=parts_list(model.parts), hidden=False,
                      holes=False)


def part_sheet(part, title: str, version: str = "", sheet_no: str = "1/1") -> str:
    """One printed part, with hidden lines, centre marks and hole sizes."""
    count = f" ×{part.quantity}" if part.quantity > 1 else ""
    return make_sheet(part.shape, part.name + count, f"part of {title}", version, sheet_no)


# --- kicadpy: sheets from the exported case ---------------------------------------------------------

OUT = Path('case') / 'out'
DRAWINGS = OUT / 'drawings'
INDEX = DRAWINGS / 'index.json'
SCHEMA = 1
BOUGHT_ROLES = {'bought', 'purchased', 'reference', 'ref', 'hardware'}


@dataclass
class Part:
    name: str
    shape: cq.Shape
    quantity: int = 1
    bought: bool = False


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bought(entry: dict) -> bool:
    """A part the person buys or already has (a servo, the board, screws): drawn, never printed."""
    return (entry.get('bought') is True or entry.get('print') is False or
            str(entry.get('role', '')).lower() in BOUGHT_ROLES or 'DO_NOT_PRINT' in str(entry.get('name', '')).upper())


def _project_name(workspace: Path) -> str:
    for name in ('product.json', 'project.json'):
        try:
            value = json.loads((workspace / name).read_text()).get('name')
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return workspace.name


def _safe_name(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('._') or 'part'


def plan(workspace) -> tuple[list[dict], str]:
    """The parts to draw, from case/out/parts.json, and a digest of everything a sheet depends on."""
    workspace = Path(workspace)
    manifest = json.loads((workspace / OUT / 'parts.json').read_text())
    entries = []
    for entry in manifest.get('parts') or []:
        if not isinstance(entry, dict) or not isinstance(entry.get('name'), str):
            continue
        step = entry.get('step')
        if str(entry.get('role')) == 'coupon' or not isinstance(step, str):
            continue
        path = (workspace / OUT / step).resolve()
        if not path.is_relative_to((workspace / OUT).resolve()) or not path.is_file():
            raise ValueError(f"{entry['name']}: STEP {step} is missing from case/out/")
        entries.append({'name': entry['name'], 'step': step, 'sha256': _sha(path), 'bought': bought(entry),
                        'quantity': int(entry.get('quantity') or 1)})
    if not entries:
        raise ValueError('case/out/parts.json lists no assembled part with a STEP file')
    version = str(manifest.get('revision') or manifest.get('version') or '')
    key = json.dumps({'schema': SCHEMA, 'parts': entries, 'version': version, 'module': _sha(__file__),
                      'project': _project_name(workspace)}, sort_keys=True)
    return entries, hashlib.sha256(key.encode()).hexdigest()


def _load(workspace: Path, step: str) -> cq.Shape:
    shape = cq.importers.importStep(str(workspace / OUT / step)).val()
    if not isinstance(shape, cq.Shape):
        raise ValueError(f'{step} holds no solid')
    return shape


def generate(workspace, *, force: bool = False) -> dict:
    """Write the assembly sheet and one sheet per printed part; return the index."""
    workspace = Path(workspace).resolve()
    entries, inputs = plan(workspace)
    index_file = workspace / INDEX
    try:
        previous = json.loads(index_file.read_text())
    except (OSError, ValueError):
        previous = None
    if (not force and isinstance(previous, dict) and previous.get('inputsSha256') == inputs and
            all((workspace / DRAWINGS / sheet['svg']).is_file() for sheet in previous.get('sheets', []))):
        return {**previous, 'cached': True}
    project = _project_name(workspace)
    manifest = json.loads((workspace / OUT / 'parts.json').read_text())
    version = str(manifest.get('revision') or manifest.get('version') or '')
    # Parts with the same STEP bytes are one design printed or bought several times.
    groups: dict[str, dict] = {}
    for entry in entries:
        group = groups.setdefault(entry['sha256'], {**entry, 'quantity': 0, 'names': []})
        group['quantity'] += entry['quantity']
        group['names'].append(entry['name'])
    shapes = {sha: _load(workspace, group['step']) for sha, group in groups.items()}
    printed = [group for group in groups.values() if not group['bought']]
    total = 1 + len(printed)
    target = workspace / DRAWINGS
    target.mkdir(parents=True, exist_ok=True)
    for old in target.glob('*.svg'):
        old.unlink()
    sheets = []
    assembly = [Part(entry['name'], shapes[entry['sha256']], entry['quantity'], entry['bought']) for entry in entries]
    shape = cq.Compound.makeCompound([part.shape for part in assembly])
    (target / 'assembly.svg').write_text(make_sheet(
        shape, project, 'assembly', version, f'1/{total}', rows=parts_list(assembly), hidden=False, holes=False))
    sheets.append({'name': 'assembly', 'title': f'{project} — assembly', 'kind': 'assembly',
                   'svg': 'assembly.svg', 'quantity': 1, 'parts': [entry['name'] for entry in entries]})
    used = {'assembly'}
    for number_on_sheet, group in enumerate(sorted(printed, key=lambda g: g['names'][0]), start=2):
        name = group['names'][0]
        file = _safe_name(name)
        while file in used:
            file += '_'
        used.add(file)
        part = Part(name, shapes[group['sha256']], group['quantity'])
        (target / f'{file}.svg').write_text(part_sheet(part, project, version, f'{number_on_sheet}/{total}'))
        sheets.append({'name': name, 'title': name + (f' ×{part.quantity}' if part.quantity > 1 else ''),
                       'kind': 'part', 'svg': f'{file}.svg', 'quantity': part.quantity, 'parts': group['names']})
    index = {'schema': SCHEMA, 'project': project, 'version': version, 'inputsSha256': inputs,
             'generatedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'sheets': sheets}
    index_file.write_text(json.dumps(index, indent=1, sort_keys=True) + '\n')
    return {**index, 'cached': False}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    force = '--force' in argv
    args = [arg for arg in argv if arg != '--force']
    workspace = Path(args[0] if args else '.').resolve()
    try:
        index = generate(workspace, force=force)
    except (OSError, ValueError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 1
    print(json.dumps({'ok': True, 'cached': index['cached'], 'sheets': len(index['sheets']),
                      'index': str(INDEX)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
