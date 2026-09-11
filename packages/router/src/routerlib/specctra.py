"""Specctra DSN out, SES in — the bridge to Freerouting.

Freerouting speaks the Specctra design format: a `.dsn` describing the board
(layers, outline, keepouts), a library of pad shapes, the placement of parts
carrying those pads, and the netlist; it answers with a `.ses` session listing
every wire and via it laid, per net. This module writes the first from a
:class:`~routerlib.model.RoutingProblem` and reads the second back into a
:class:`~routerlib.model.RoutingSolution`.

Two decisions keep it small and exact:

- **Every pad is its own one-pin component, placed at the origin, with the
  pin at the pad's absolute coordinates.** Specctra wants pins to belong to
  placed images; nothing here needs the images to be real footprints, and
  a per-pad image side-steps rotation, mirroring and the front/back rules for
  a component's pins entirely. Pad shapes are written relative to the pin.
- **Net names in the file are the problem's net ids**, not display names —
  ids are unique by construction and come back verbatim in the session, so
  the reader never has to guess which net a wire belongs to.

Coordinates go out in micrometres as integers (`(resolution um 10)` gives
Freerouting a tenth of a micrometre to work in) and come back through the
session's own `resolution` line, so a board round-trips to the nanometre.
"""

from __future__ import annotations

import math
import re
from typing import Iterable

from .model import (
    BOTTOM,
    TOP,
    Point,
    RoutingProblem,
    RoutingSolution,
    Trace,
    Via,
)

#: Specctra layer names for our two copper layers (KiCad's spelling, which
#: Freerouting's examples and rules files all use).
LAYER_NAMES = {TOP: "F.Cu", BOTTOM: "B.Cu"}
LAYER_BACK = {v: k for k, v in LAYER_NAMES.items()}

#: Micrometres per millimetre; every coordinate in the DSN is an integer of these.
_UM = 1000


def _um(mm: float) -> int:
    return int(round(mm * _UM))


_PLAIN = re.compile(r"^[A-Za-z0-9_.\-\[\]:]+$")


def _q(name: str) -> str:
    """A DSN identifier: bare when it is one already, quoted otherwise.
    Freerouting's reader takes pin references (`component-pin`) only bare —
    a quoted one is read up to the quote and the rest logged as "non-ansi"
    — so identifiers are quoted only when they must be."""
    if _PLAIN.match(name):
        return name
    return '"' + name.replace('"', "'") + '"'


def _rotate(x: float, y: float, deg: float) -> tuple[float, float]:
    if not deg:
        return x, y
    a = math.radians(deg)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def _via_padstack(rules) -> str:
    return f"Via[0-1]_{_um(rules.via_pad_mm)}:{_um(rules.via_drill_mm)}_um"


def _pad_layers(pad) -> list[str]:
    names = [LAYER_NAMES[l] for l in pad.layers if l in LAYER_NAMES]
    return names or [LAYER_NAMES[TOP]]


def _pad_shape_lines(pad) -> tuple[str, list[str]]:
    """``(padstack id, shape lines)`` for one pad, shape relative to its centre."""
    layers = _pad_layers(pad)
    if pad.vertices:
        pts = [(p.x - pad.center.x, p.y - pad.center.y) for p in pad.vertices]
        key = "poly:" + ",".join(f"{_um(x)}:{_um(y)}" for x, y in pts) + ":" + "/".join(layers)
        body = " ".join(f"{_um(x)} {_um(y)}" for x, y in pts)
        return key, [f"(polygon {l} 0 {body})" for l in layers]
    w, h = pad.width_mm, pad.height_mm
    shape = str(getattr(pad, "shape", "rect") or "rect")
    rot = float(getattr(pad, "rotation_deg", 0.0) or 0.0)
    if shape == "circle" or (shape in ("pill", "oval") and abs(w - h) < 1e-6):
        d = _um(max(w, h))
        return f"circle:{d}:" + "/".join(layers), [f"(circle {l} {d})" for l in layers]
    # rect, pill, oval, rotated rect: a polygon through the four corners. A
    # pill written as its bounding rectangle is a hair conservative, which is
    # the right side to err on for a router's obstacle.
    corners = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    pts = [_rotate(x, y, rot) for x, y in corners]
    key = f"rect:{_um(w)}x{_um(h)}@{round(rot, 3)}:" + "/".join(layers)
    body = " ".join(f"{_um(x)} {_um(y)}" for x, y in pts)
    return key, [f"(polygon {l} 0 {body})" for l in layers]


def _outline(problem: RoutingProblem) -> list[Point]:
    b = problem.board
    if b.outline and len(b.outline) >= 3:
        return list(b.outline)
    x0, y0 = b.center.x - b.width_mm / 2, b.center.y - b.height_mm / 2
    x1, y1 = b.center.x + b.width_mm / 2, b.center.y + b.height_mm / 2
    return [Point(x0, y0), Point(x1, y0), Point(x1, y1), Point(x0, y1)]


def write_dsn(
    problem: RoutingProblem,
    *,
    name: str | None = None,
    clearance_mm: float | None = None,
    signal_width_mm: float | None = None,
) -> str:
    """The Specctra design for ``problem``. Deterministic: same problem, same text.

    ``clearance_mm`` / ``signal_width_mm`` override the rules' targets — the
    tight retry uses the fab floor plus a hair where the target left a
    connector's pins walled in.
    """
    rules = problem.rules
    via_ps = _via_padstack(rules)
    signal_w = _um(signal_width_mm if signal_width_mm is not None else rules.signal_trace_mm)
    power_w = _um(rules.power_trace_mm)
    clearance = _um(clearance_mm if clearance_mm is not None else rules.target_clearance_mm)
    out: list[str] = []
    out.append(f"(pcb {_q(name or problem.id)}")
    out.append('  (parser (string_quote ") (space_in_quoted_tokens on) '
               '(host_cad "autonomous-circuit") (host_version "1.8"))')
    out.append("  (resolution um 10)")
    out.append("  (unit um)")

    # -- structure ---------------------------------------------------------
    out.append("  (structure")
    for index, layer in enumerate(problem.board.layers):
        if layer in LAYER_NAMES:
            out.append(f"    (layer {LAYER_NAMES[layer]} (type signal) (property (index {index})))")
    ring = _outline(problem)
    body = " ".join(f"{_um(p.x)} {_um(p.y)}" for p in ring + [ring[0]])
    out.append(f"    (boundary (path pcb 0 {body}))")
    # Keep-outs and non-pad drills are obstacles on every copper layer. A
    # drill gets its hole clearance as a margin, the way the scorer grades it.
    for k in problem.keepouts:
        if k.vertices:
            pts = " ".join(f"{_um(p.x)} {_um(p.y)}" for p in k.vertices)
        else:
            cx, cy, w, h = k.center.x, k.center.y, k.width_mm, k.height_mm
            rot = float(getattr(k, "rotation_deg", 0.0) or 0.0)
            pts = " ".join(
                f"{_um(cx + x)} {_um(cy + y)}"
                for x, y in (_rotate(dx, dy, rot) for dx, dy in
                             [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)])
            )
        out.append(f"    (keepout {_q(k.id)} (polygon signal 0 {pts}))")
    for d in problem.drills:
        if d.pad_id is not None:
            continue  # part of a pad's padstack below
        margin = rules.hole_clearance(d)
        dia = max(d.width_mm, d.height_mm) + 2 * margin
        out.append(f"    (keepout {_q(d.id)} (circle signal {_um(dia)} {_um(d.center.x)} {_um(d.center.y)}))")
    out.append(f"    (via {_q(via_ps)})")
    out.append(f"    (rule (width {signal_w}) (clearance {clearance}) "
               f"(clearance {clearance} (type default_smd)) (clearance {clearance} (type smd_smd)))")
    out.append("  )")

    # -- placement + library ------------------------------------------------
    padstacks: dict[str, list[str]] = {}
    images: list[tuple[str, str, Point]] = []  # (pad id, padstack id, centre)
    for pad in problem.pads:
        key, shapes = _pad_shape_lines(pad)
        ps_id = f"PS{len(padstacks)}" if key not in padstacks else None
        if ps_id is None:
            ps_id = next(pid for pid, (k, _) in _enumerate_ps(padstacks) if k == key)
        else:
            padstacks[key] = [ps_id, *shapes]
        images.append((pad.id, ps_id, pad.center))
    out.append("  (placement")
    for pad_id, _, _ in images:
        out.append(f"    (component {_q('IMG_' + pad_id)} (place {_q(pad_id)} 0 0 front 0))")
    out.append("  )")
    out.append("  (library")
    for pad_id, ps_id, c in images:
        out.append(f"    (image {_q('IMG_' + pad_id)} (pin {_q(ps_id)} 1 {_um(c.x)} {_um(c.y)}))")
    for _, (ps_id, *shapes) in padstacks.items():
        out.append(f"    (padstack {_q(ps_id)} " + " ".join(f"(shape {s})" for s in shapes) + " (attach off))")
    via_d = _um(rules.via_pad_mm)
    out.append(f"    (padstack {_q(via_ps)} " + " ".join(
        f"(shape (circle {LAYER_NAMES[l]} {via_d}))" for l in (TOP, BOTTOM)) + " (attach off))")
    out.append("  )")

    # -- network ------------------------------------------------------------
    out.append("  (network")
    pads_by_id = problem.pads_by_id
    by_class: dict[str, list[str]] = {"power": [], "default": []}
    for net in sorted(problem.nets, key=lambda n: n.id):
        pins = [p for p in net.pads if p in pads_by_id]
        if len(pins) < 2:
            continue
        out.append(f"    (net {_q(net.id)} (pins " + " ".join(f"{_q(p + '-1')}" for p in pins) + "))")
        by_class["power" if net.net_class in ("power", "ground") else "default"].append(net.id)
    for cls, width in (("default", signal_w), ("power", power_w)):
        names = " ".join(_q(n) for n in by_class[cls])
        out.append(f"    (class {cls} {names} (circuit (use_via {_q(via_ps)})) "
                   f"(rule (width {width}) (clearance {clearance})))")
    out.append("  )")
    out.append("  (wiring)")
    out.append(")")
    return "\n".join(out) + "\n"


def _enumerate_ps(padstacks: dict[str, list[str]]):
    for key, (ps_id, *_rest) in padstacks.items():
        yield ps_id, (key, None)


# ---------------------------------------------------------------------------
# SES in
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()"]+')


def _parse(text: str):
    """A minimal s-expression reader: strings stay strings, lists become lists."""
    tokens = _TOKEN.findall(text)
    pos = 0

    def node():
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            items = []
            while pos < len(tokens) and tokens[pos] != ")":
                items.append(node())
            pos += 1  # the ')'
            return items
        if tok == ")":
            raise ValueError("unbalanced session file")
        if tok.startswith('"') and tok.endswith('"'):
            return tok[1:-1]
        return tok

    tree = []
    while pos < len(tokens):
        tree.append(node())
    return tree


def _find_all(tree, head: str):
    """Every list whose first element is ``head``, depth-first."""
    if isinstance(tree, list):
        if tree and tree[0] == head:
            yield tree
        for item in tree:
            yield from _find_all(item, head)


def _first(tree, head: str):
    return next(_find_all(tree, head), None)


def _resolution(tree) -> float:
    """Millimetres per session coordinate unit."""
    res = _first(tree, "resolution")
    unit, per = ("um", 1.0)
    if res and len(res) >= 3:
        unit, per = str(res[1]), float(res[2])
    scale = {"um": 1e-3, "mm": 1.0, "mil": 0.0254, "inch": 25.4}.get(unit, 1e-3)
    return scale / per


def read_ses(text: str, problem: RoutingProblem, *, router: str = "freerouting") -> RoutingSolution:
    """The session's wires and vias as a :class:`RoutingSolution`.

    Nets the session does not mention — or mentions with no wire — are left to
    the connectivity analysis to call unrouted; the session file itself does
    not say.
    """
    tree = _parse(text)
    unit = _resolution(tree)
    rules = problem.rules
    known = problem.nets_by_id
    traces: list[Trace] = []
    vias: list[Via] = []
    for net_node in _find_all(_first(tree, "network_out") or [], "net"):
        net_id = str(net_node[1]) if len(net_node) > 1 else ""
        if net_id not in known:
            continue
        n_wire = 0
        for wire in _find_all(net_node, "wire"):
            path = _first(wire, "path")
            if not path or len(path) < 5:
                continue
            layer = LAYER_BACK.get(str(path[1]))
            if layer is None:
                continue
            width = round(float(path[2]) * unit, 4)
            coords = [float(v) for v in path[3:] if isinstance(v, str) and _is_number(v)]
            pts = [Point(round(coords[i] * unit, 5), round(coords[i + 1] * unit, 5))
                   for i in range(0, len(coords) - 1, 2)]
            if len(pts) < 2:
                continue
            n_wire += 1
            traces.append(Trace(
                id=f"{net_id}-fr{n_wire}", net=net_id, layer=layer, points=tuple(pts),
                width_mm=max(width, rules.min_trace_mm),
            ))
        n_via = 0
        for via in _find_all(net_node, "via"):
            if len(via) < 4:
                continue
            ps = str(via[1])
            m = re.search(r"_(\d+):(\d+)_um", ps)
            pad_mm = float(m.group(1)) / _UM if m else rules.via_pad_mm
            drill_mm = float(m.group(2)) / _UM if m else rules.via_drill_mm
            nums = [float(v) for v in via[2:] if isinstance(v, str) and _is_number(v)]
            if len(nums) < 2:
                continue
            n_via += 1
            vias.append(Via(
                id=f"{net_id}-frv{n_via}", net=net_id,
                center=Point(round(nums[0] * unit, 5), round(nums[1] * unit, 5)),
                drill_mm=drill_mm, pad_mm=pad_mm,
            ))
    return RoutingSolution(router=router, traces=tuple(traces), vias=tuple(vias), complete=False)


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def routed_net_ids(solution: RoutingSolution) -> set[str]:
    return {t.net for t in solution.traces} | {v.net for v in solution.vias}


__all__ = ["write_dsn", "read_ses", "routed_net_ids", "LAYER_NAMES"]
