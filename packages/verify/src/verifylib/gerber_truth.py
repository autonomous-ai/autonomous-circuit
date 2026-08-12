"""Does the packet we ship match the board we designed?

**The gap this closes.** The gerber zip is the only artifact JLCPCB consumes,
and until now nothing in the pipeline had ever opened it. Every gate runs
upstream of the export: the compiler's findings, ``@tscircuit/checks``, KiCad
ERC/DRC and our DFM table all read ``circuit.json`` or the ``.kicad_pcb``. A
bug *in the export* — a layer that did not get written, a drill file in the
wrong units, a footprint dropped between the board and the plot — is invisible
to all four and costs a full fab cycle.

This module reads the shipped files with an independent parser
(:mod:`verifylib.gerber`) and reconciles them against ``circuit.json``:

* every layer the fab needs is present, and nothing claims a role twice
* the outline's extents equal the designed board, at scale 1.0 — a units or
  coordinate-format slip shows up here as a factor of 25.4 or 10
* every via and hole in the design has a drill hit of the right diameter, and
  every drill hit belongs to something in the design
* every pad has copper, a solder-mask opening, and (for assembly) paste
* the minimum aperture actually plotted clears the fab's floor

It also runs two checks that **only exist in the gerbers** and have no
representation in ``circuit.json`` at all: solder-mask slivers between adjacent
openings, and silkscreen printed over a mask opening.

**The transform.** Gerber coordinates sit in KiCad's page frame, not the
board's. The offset is solved from the outline layer rather than assumed,
because solving it turns a scale error into a finding instead of silently
correcting it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from verifylib import gerber as gbr
from verifylib.findings import CheckResult, Coverage, Finding, finding, never_raises
from verifylib.model import Board, Poly, Rect
from verifylib.rules import JLCPCB_2LAYER, FabRules

#: Layers the fab needs on a 2-layer board. Anything missing is an error, not
#: a warning: JLCPCB will either reject the upload or build the board without
#: the layer, and both cost the full cycle.
REQUIRED_ROLES_2LAYER = (
    "copper_top",
    "copper_bottom",
    "mask_top",
    "mask_bottom",
    "outline",
    "drill",
)
#: Present-but-optional. Silk on one side is normal; no silk at all is worth
#: saying because a board with no reference designators is unrepairable.
EXPECTED_ROLES = ("silk_top", "paste_top")

#: How far a plotted feature may sit from its designed position before we call
#: it a mismatch. Gerbers are emitted at 4.6 format (nanometre resolution) and
#: KiCad rounds to its own internal 1nm grid, so anything above a micron is a
#: real difference, not a rounding artifact. 50um leaves generous headroom.
POSITION_TOLERANCE_MM = 0.05
#: Drill diameters are quantised to the tool list; JLCPCB rounds to 0.05mm.
DRILL_TOLERANCE_MM = 0.06
#: The outline is plotted as a stroked polyline whose centreline is the board
#: edge; corners are approximated by short chords.
OUTLINE_TOLERANCE_MM = 0.10
#: Slack on any "is this under the floor" comparison. A micron is three orders
#: below what a fab can hold, so a gap measuring 0.1999mm against a 0.2mm rule
#: is a float tie, not a violation.
MEASUREMENT_EPSILON_MM = 0.001


@dataclass
class Transform:
    """Board frame -> gerber frame. Solved, never assumed."""

    dx: float
    dy: float
    scale_x: float
    scale_y: float

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return (x * self.scale_x + self.dx, y * self.scale_y + self.dy)

    @property
    def is_unity_scale(self) -> bool:
        return (
            abs(abs(self.scale_x) - 1.0) < 1e-3 and abs(abs(self.scale_y) - 1.0) < 1e-3
        )


def solve_transform(board: Board, packet: gbr.Packet) -> Transform | None:
    """Derive the board->gerber mapping from the outline layer's extents."""
    outline = packet.layers.get("outline")
    if outline is None or board.outline is None:
        return None
    plotted = outline.centreline_bounds
    if plotted is None or plotted.width <= 0 or plotted.height <= 0:
        return None
    designed = board.outline
    if designed.width <= 0 or designed.height <= 0:
        return None
    scale_x = plotted.width / designed.width
    scale_y = plotted.height / designed.height
    dcx, dcy = designed.center
    pcx, pcy = plotted.center
    return Transform(
        dx=pcx - dcx * scale_x,
        dy=pcy - dcy * scale_y,
        scale_x=scale_x,
        scale_y=scale_y,
    )


class _Grid:
    """A coarse spatial index so reconciliation stays linear."""

    def __init__(self, points: list[tuple[float, float, object]], cell: float = 1.0):
        self.cell = cell
        self.buckets: dict[tuple[int, int], list[tuple[float, float, object]]] = {}
        for x, y, payload in points:
            self.buckets.setdefault(self._key(x, y), []).append((x, y, payload))

    def _key(self, x: float, y: float) -> tuple[int, int]:
        return (int(math.floor(x / self.cell)), int(math.floor(y / self.cell)))

    def near(self, x: float, y: float, radius: float):
        cx, cy = self._key(x, y)
        span = int(math.ceil(radius / self.cell)) + 1
        for i in range(cx - span, cx + span + 1):
            for j in range(cy - span, cy + span + 1):
                for px, py, payload in self.buckets.get((i, j), ()):
                    if math.hypot(px - x, py - y) <= radius:
                        yield px, py, payload


@never_raises
def _layer_inventory(packet: gbr.Packet, layers: int) -> list[Finding]:
    out: list[Finding] = []
    required = REQUIRED_ROLES_2LAYER if layers >= 2 else ("copper_top", "mask_top",
                                                          "outline", "drill")
    for role in required:
        if role not in packet.roles:
            out.append(
                finding(
                    "packet",
                    "gerber_missing_layer",
                    f"the packet has no {role.replace('_', ' ')} file — JLCPCB "
                    "will either reject the upload or build without it",
                    "error",
                )
            )
    for role in EXPECTED_ROLES:
        if role not in packet.roles:
            out.append(
                finding(
                    "packet",
                    "gerber_missing_layer",
                    f"the packet has no {role.replace('_', ' ')} file",
                    "warning",
                )
            )
    for message in packet.errors:
        out.append(
            finding(
                "packet",
                "gerber_unreadable",
                f"{message} — an unparsed layer is an unchecked layer",
                "error",
            )
        )
    for layer in packet.layers.values():
        for feature in sorted(set(layer.unsupported)):
            out.append(
                finding(
                    layer.path,
                    "gerber_unsupported_feature",
                    f"{layer.path} uses {feature}, which this reader does not "
                    "evaluate — geometry in it was not checked",
                    "warning",
                )
            )
    return out


@never_raises
def _outline_matches(board: Board, packet: gbr.Packet, transform: Transform | None) -> list[Finding]:
    outline = packet.layers.get("outline")
    if outline is None or board.outline is None:
        return []
    plotted = outline.centreline_bounds
    if plotted is None:
        return [
            finding(
                "packet",
                "gerber_outline_empty",
                "the outline layer contains no geometry — the fab has no board "
                "shape to route to",
                "error",
            )
        ]
    out: list[Finding] = []
    if transform is not None and not transform.is_unity_scale:
        # Both axes off by the same factor is a units or coordinate-format
        # bug. Only one axis off is a different board shape, and saying
        # "units error" about that would send the reader the wrong way.
        uniform = abs(transform.scale_x - transform.scale_y) < 1e-3
        if uniform:
            out.append(
                finding(
                    "packet",
                    "gerber_scale_mismatch",
                    f"the plotted outline is "
                    f"{plotted.width:.3f}x{plotted.height:.3f}mm but the board is "
                    f"{board.outline.width:g}x{board.outline.height:g}mm — both "
                    f"axes scaled by {transform.scale_x:.4f}. That is a "
                    "coordinate-format or units error, not a design difference",
                    "error",
                )
            )
        else:
            out.append(
                finding(
                    "packet",
                    "gerber_outline_mismatch",
                    f"the plotted outline measures "
                    f"{plotted.width:.3f}x{plotted.height:.3f}mm; the design says "
                    f"{board.outline.width:g}x{board.outline.height:g}mm",
                    "error",
                )
            )
        return out
    dw = abs(plotted.width - board.outline.width)
    dh = abs(plotted.height - board.outline.height)
    if dw > OUTLINE_TOLERANCE_MM or dh > OUTLINE_TOLERANCE_MM:
        out.append(
            finding(
                "packet",
                "gerber_outline_mismatch",
                f"the plotted outline measures "
                f"{plotted.width:.3f}x{plotted.height:.3f}mm; the design says "
                f"{board.outline.width:g}x{board.outline.height:g}mm "
                f"(off by {dw:.3f} x {dh:.3f}mm)",
                "error",
            )
        )
    return out


@never_raises
def _drills_match(board: Board, packet: gbr.Packet, transform: Transform | None) -> list[Finding]:
    if transform is None or not packet.drills:
        return []
    hits = [
        (h.center[0], h.center[1], h)
        for drill in packet.drills
        for h in drill.hits
    ]
    if not hits:
        # A board with no vias and no holes has nothing to drill, so an empty
        # drill file is the correct output rather than a defect. Without this,
        # the check fired its own message back at itself — "the drill file has
        # no hits, but the design has 0 holes" — and because the kind is
        # blocking, a hole-less board could never be fab-ready. Any all-SMD
        # single-layer design hit it.
        if not board.vias and not board.holes:
            return []
        return [
            finding(
                "packet",
                "gerber_drill_empty",
                "the drill file has no hits, but the design has "
                f"{len(board.vias) + len(board.holes)} holes",
                "error",
            )
        ]
    grid = _Grid(hits)
    out: list[Finding] = []
    matched: set[int] = set()
    designed = [(v, "via") for v in board.vias] + [(h, "hole") for h in board.holes]

    for hole, kind in designed:
        gx, gy = transform.apply(hole.x, hole.y)
        best = None
        for px, py, payload in grid.near(gx, gy, POSITION_TOLERANCE_MM * 4):
            # One drill hit satisfies one designed hole. Without this, two
            # coincident features both match the same hit and the packet looks
            # complete while a drill is genuinely missing.
            if id(payload) in matched:
                continue
            distance = math.hypot(px - gx, py - gy)
            if best is None or distance < best[0]:
                best = (distance, payload)
        if best is None:
            out.append(
                finding(
                    f"{kind} at ({hole.x:.2f}, {hole.y:.2f})",
                    "gerber_drill_missing",
                    f"the design has a {hole.diameter:.2f}mm {kind} at "
                    f"({hole.x:.2f}, {hole.y:.2f}) with no drill hit in the "
                    "packet — the fab will not drill it",
                    "error",
                )
            )
            continue
        matched.add(id(best[1]))
        plotted_diameter = best[1].tool.diameter_mm
        if abs(plotted_diameter - hole.diameter) > DRILL_TOLERANCE_MM:
            out.append(
                finding(
                    f"{kind} at ({hole.x:.2f}, {hole.y:.2f})",
                    "gerber_drill_size_mismatch",
                    f"the design asks for {hole.diameter:.3f}mm and the drill "
                    f"file specifies {plotted_diameter:.3f}mm",
                    "error",
                )
            )
        designed_size = hole.size
        plotted_size = best[1].size
        if (
            abs(designed_size[0] - plotted_size[0]) > DRILL_TOLERANCE_MM
            or abs(designed_size[1] - plotted_size[1]) > DRILL_TOLERANCE_MM
        ):
            out.append(
                finding(
                    f"{kind} at ({hole.x:.2f}, {hole.y:.2f})",
                    "gerber_drill_size_mismatch",
                    f"the design's {kind} measures "
                    f"{designed_size[0]:.3f}x{designed_size[1]:.3f}mm and the "
                    f"drill file routes "
                    f"{plotted_size[0]:.3f}x{plotted_size[1]:.3f}mm",
                    "error",
                )
            )
        if hole.plated != best[1].tool.plated:
            out.append(
                finding(
                    f"{kind} at ({hole.x:.2f}, {hole.y:.2f})",
                    "gerber_drill_plating_mismatch",
                    f"the design says "
                    f"{'plated' if hole.plated else 'non-plated'} and the drill "
                    f"file says "
                    f"{'plated' if best[1].tool.plated else 'non-plated'} — a "
                    "plated mounting hole shorts to whatever it touches, and an "
                    "unplated via is an open circuit",
                    "error",
                )
            )

    extra = [h for _, _, h in hits if id(h) not in matched]
    if extra:
        sample = ", ".join(f"({h.x:.2f}, {h.y:.2f})" for h in extra[:4])
        out.append(
            finding(
                "packet",
                "gerber_drill_extra",
                f"{len(extra)} drill hit(s) in the packet have no matching hole "
                f"in the design ({sample}) — something was added between the "
                "design and the plot",
                "warning",
            )
        )
    return out


def _pad_index(layer: gbr.GerberLayer | None) -> _Grid | None:
    if layer is None:
        return None
    return _Grid([(f.x, f.y, f) for f in layer.flashes])


def _openings(layer: gbr.GerberLayer | None) -> list[Poly]:
    """Every opening on a mask/paste layer, however KiCad chose to plot it.

    A layer is flashes *or* regions and the choice is not ours: the moment
    `solder_mask_min_width` is set, KiCad has to merge and reshape openings, so
    it stops flashing apertures and emits filled contours instead. Measured on
    harness-puck: 174 flashes became 223 regions.

    That matters more than it sounds. The fix for the sliver defect changes the
    plot's *representation*, and a check that only understood flashes would
    have gone silent on every board — reporting a clean mask because it could
    no longer see one. A fix that blinds its own smoke alarm is not a fix.
    """
    if layer is None:
        return []
    if layer.regions:
        return [Poly(r.points) for r in layer.regions if len(r.points) >= 3]
    return [f.rect.as_poly() for f in layer.flashes if f.aperture.size[0] > 0]


def _covers(openings: list[Poly], grid: _Grid | None, x: float, y: float,
            tolerance: float) -> bool:
    """Is there an opening at this point, flashed or filled?"""
    if grid is not None:
        for _ in grid.near(x, y, tolerance):
            return True
    for poly in openings:
        if poly.contains(x, y):
            return True
    return False


@never_raises
def _pads_match(board: Board, packet: gbr.Packet, transform: Transform | None,
                *, assembly: bool) -> list[Finding]:
    if transform is None:
        return []
    out: list[Finding] = []
    roles = ("copper_top", "copper_bottom", "mask_top", "mask_bottom",
             "paste_top", "paste_bottom")
    indexes = {role: _pad_index(packet.layers.get(role)) for role in roles}
    filled = {role: _openings(packet.layers.get(role)) for role in roles}
    for component in board.components:
        for pad in component.pads:
            side = "bottom" if pad.layer == "bottom" else "top"
            gx, gy = transform.apply(pad.x, pad.y)
            copper_role = f"copper_{side}"
            copper = indexes.get(copper_role)
            if (copper is not None or filled.get(copper_role)) and not _covers(
                filled.get(copper_role, []), copper, gx, gy, POSITION_TOLERANCE_MM
            ):
                out.append(
                    finding(
                        component.name,
                        "gerber_pad_missing",
                        f"{component.name}'s pad at ({pad.x:.2f}, {pad.y:.2f}) "
                        f"has no copper flash on the {side} layer — the "
                        "footprint did not make it into the plot",
                        "error",
                    )
                )
                continue
            mask_role = f"mask_{side}"
            mask = indexes.get(mask_role)
            if (
                not pad.covered_with_solder_mask
                and (mask is not None or filled.get(mask_role))
                and not _covers(
                filled.get(mask_role, []), mask, gx, gy, POSITION_TOLERANCE_MM
                )
            ):
                out.append(
                    finding(
                        component.name,
                        "gerber_pad_masked_over",
                        f"{component.name}'s pad at ({pad.x:.2f}, {pad.y:.2f}) "
                        "has no solder-mask opening — it is covered in mask and "
                        "cannot be soldered",
                        "error",
                    )
                )
            if (
                assembly
                and not pad.plated_hole
                and not pad.covered_with_solder_mask
            ):
                paste_role = f"paste_{side}"
                paste = indexes.get(paste_role)
                if (paste is not None or filled.get(paste_role)) and not _covers(
                    filled.get(paste_role, []), paste, gx, gy, POSITION_TOLERANCE_MM
                ):
                    out.append(
                        finding(
                            component.name,
                            "gerber_pad_no_paste",
                            f"{component.name}'s pad at ({pad.x:.2f}, "
                            f"{pad.y:.2f}) has no paste aperture — the stencil "
                            "leaves no solder there",
                            "warning",
                        )
                    )
    return out


@never_raises
def _aperture_floors(packet: gbr.Packet, rules: FabRules) -> list[Finding]:
    out: list[Finding] = []
    for role in ("copper_top", "copper_bottom"):
        layer = packet.layers.get(role)
        if layer is None:
            continue
        narrowest = layer.min_draw_width
        if narrowest is not None and narrowest < rules.min_trace_mm - 1e-9:
            out.append(
                finding(
                    layer.path,
                    "gerber_trace_width",
                    f"{layer.path} plots a {narrowest:.4f}mm conductor; the fab's "
                    f"floor is {rules.min_trace_mm:g}mm. Measured on the shipped "
                    "file, not on the design",
                    "error",
                )
            )
    for role in ("silk_top", "silk_bottom"):
        layer = packet.layers.get(role)
        if layer is None:
            continue
        narrowest = layer.min_draw_width
        if narrowest is not None and narrowest < rules.min_silk_line_mm - 1e-9:
            out.append(
                finding(
                    layer.path,
                    "gerber_silk_line_width",
                    f"{layer.path} plots {narrowest:.3f}mm silkscreen strokes; "
                    f"JLCPCB holds {rules.min_silk_line_mm:g}mm and thinner ink "
                    "prints broken or not at all",
                    "warning",
                )
            )
    return out


def _pad_owners(board: Board, transform: Transform | None) -> _Grid | None:
    """Every pad's plot position, tagged with the component it belongs to."""
    if transform is None:
        return None
    points = []
    for component in board.components:
        for pad in component.pads:
            gx, gy = transform.apply(pad.x, pad.y)
            points.append((gx, gy, component.name))
    return _Grid(points, cell=2.0) if points else None


def _owner_of(grid: _Grid | None, x: float, y: float) -> str | None:
    if grid is None:
        return None
    best: tuple[float, str] | None = None
    for px, py, name in grid.near(x, y, 1.5):
        distance = math.hypot(px - x, py - y)
        if best is None or distance < best[0]:
            best = (distance, str(name))
    return best[1] if best else None


@never_raises
def _mask_slivers(
    packet: gbr.Packet,
    rules: FabRules,
    board: Board | None = None,
    transform: Transform | None = None,
) -> list[Finding]:
    """A web of solder mask narrower than the fab can hold burns off in the
    oven, and the two pads it separated become one joint. Nothing upstream of
    the export can see this: it is a property of the mask apertures, which
    only exist in the gerbers.

    **Scoped to webs between two different components, and that scoping is
    the whole check.** Measured on harness-puck: all ten sub-0.2mm webs sit
    between two pads of one part's own land pattern — 0.1141mm and 0.1571mm
    inside the USB-C receptacle's footprint, and 0.1985mm inside each of eight
    0402 capacitors, which is simply what a 0402 land pattern is. JLCPCB builds
    those every day; the dam inside a qualified footprint is a property of the
    package, specified by IPC-7351 and the part vendor, not a placement anyone
    chose. Blocking on it would have made every board this tool will ever
    produce permanently un-orderable over a standard 0402.

    A web between two *different* parts is the opposite: nobody qualified it,
    and it is exactly what the fab's rule is written about.

    Works on either plot representation, flashed apertures or filled regions.
    """
    out: list[Finding] = []
    owners = _pad_owners(board, transform) if board is not None else None
    for role in ("mask_top", "mask_bottom"):
        layer = packet.layers.get(role)
        if layer is None:
            continue
        openings = _openings(layer)
        if len(openings) < 2:
            continue
        boxed = [
            (poly, poly.bounds, _owner_of(owners, *poly.bounds.center))
            for poly in openings
        ]
        grid = _Grid(
            [
                (rect.center[0], rect.center[1], (poly, rect, owner))
                for poly, rect, owner in boxed
            ],
            cell=2.0,
        )
        seen: set[tuple[int, int]] = set()
        worst: tuple[float, float, float] | None = None
        count = 0
        same_part = 0
        for poly, rect, owner in boxed:
            reach = max(rect.width, rect.height) + rules.min_mask_sliver_mm + 1.0
            cx, cy = rect.center
            for _, _, payload in grid.near(cx, cy, reach):
                other, other_rect, other_owner = payload
                if other is poly:
                    continue
                key = (min(id(poly), id(other)), max(id(poly), id(other)))
                if key in seen:
                    continue
                seen.add(key)
                # Bounding boxes first: exact polygon distance is O(n*m) in
                # the edges and a mask region can have hundreds.
                if rect.gap_to(other_rect) >= rules.min_mask_sliver_mm:
                    continue
                gap = poly.min_distance_to(other)
                if not (0 < gap < rules.min_mask_sliver_mm - MEASUREMENT_EPSILON_MM):
                    continue
                if owner is not None and owner == other_owner:
                    same_part += 1
                    continue
                count += 1
                if worst is None or gap < worst[0]:
                    worst = (gap, cx, cy)
        if same_part:
            out.append(
                finding(
                    layer.path,
                    "gerber_mask_sliver_in_footprint",
                    f"{same_part} thin mask web(s) on "
                    f"{role.replace('_', ' ')} sit inside a single part's own "
                    "land pattern (a 0402's pad gap is 0.1985mm, just under the "
                    f"{rules.min_mask_sliver_mm:g}mm rule). That is a property "
                    "of the qualified footprint, which the fab builds every "
                    "day — recorded, not blocked",
                    "info",
                )
            )
        if count and worst is not None:
            out.append(
                finding(
                    layer.path,
                    "gerber_mask_sliver",
                    f"{count} pair(s) of mask openings belonging to *different* "
                    f"parts on {role.replace('_', ' ')} are separated by less "
                    f"than {rules.min_mask_sliver_mm:g}mm; "
                    f"the narrowest is {worst[0]:.3f}mm near "
                    f"({worst[1]:.2f}, {worst[2]:.2f}) in plot coordinates. A "
                    "web that thin burns off and the two pads bridge",
                    "warning",
                )
            )
    return out


def _rect_intersection(left: Rect, right: Rect) -> Rect | None:
    """Return the positive-area intersection of two axis-aligned boxes."""
    x0, y0 = max(left.x0, right.x0), max(left.y0, right.y0)
    x1, y1 = min(left.x1, right.x1), min(left.y1, right.y1)
    if x0 >= x1 or y0 >= y1:
        return None
    return Rect(x0, y0, x1, y1)


def _flash_covers_rect(flash: gbr.Flash, target: Rect) -> bool:
    """Does a clear flash certainly erase every point in ``target``?

    Standard circle, rectangle and obround apertures are tested against their
    real shape. Macros and holed apertures deliberately return ``False``: a
    bounding box can contain transparent space, and treating it as clear would
    hide real printable ink. This conservative predicate can retain a warning
    for an exotic subtraction aperture, but cannot suppress a real overlap on
    the strength of geometry we did not prove.
    """
    aperture = flash.aperture
    if aperture.macro:
        return False

    required_params = {"C": 1, "R": 2, "O": 2}
    if aperture.shape not in required_params:
        return False
    if len(aperture.params) != required_params[aperture.shape]:
        return False  # an extra parameter describes a transparent hole

    corners = (
        (target.x0 - flash.x, target.y0 - flash.y),
        (target.x0 - flash.x, target.y1 - flash.y),
        (target.x1 - flash.x, target.y0 - flash.y),
        (target.x1 - flash.x, target.y1 - flash.y),
    )
    epsilon = 1e-9
    if aperture.shape == "R":
        width, height = aperture.size
        return all(
            abs(x) <= width / 2 + epsilon and abs(y) <= height / 2 + epsilon
            for x, y in corners
        )
    if aperture.shape == "C":
        radius = aperture.size[0] / 2
        return all(math.hypot(x, y) <= radius + epsilon for x, y in corners)

    # An obround is a rectangle swept by a circle. It is convex, so containing
    # all four corners means it contains the complete target rectangle.
    width, height = aperture.size
    radius = min(width, height) / 2
    straight_half = abs(width - height) / 2
    if width >= height:
        return all(
            abs(y) <= radius + epsilon
            and (
                abs(x) <= straight_half
                or math.hypot(abs(x) - straight_half, y) <= radius + epsilon
            )
            for x, y in corners
        )
    return all(
        abs(x) <= radius + epsilon
        and (
            abs(y) <= straight_half
            or math.hypot(x, abs(y) - straight_half) <= radius + epsilon
        )
        for x, y in corners
    )


def _flash_covers_flash(clear: gbr.Flash, target: gbr.Flash) -> bool:
    """Does ``clear`` certainly contain a flashed mask opening?

    KiCad emits the same standard aperture on the mask layer and as the clear
    silk operation. Recognising that identity is important for circular pads:
    their bounding-box corners are outside the circle even though the entire
    circular opening is erased. Unknown macros are not equated across files,
    because their names alone do not prove their definitions are identical.
    """
    dx, dy = clear.x - target.x, clear.y - target.y
    same_center = abs(dx) <= 1e-9 and abs(dy) <= 1e-9
    if (
        same_center
        and not clear.aperture.macro
        and not target.aperture.macro
        and clear.aperture.shape == target.aperture.shape
        and clear.aperture.params == target.aperture.params
    ):
        return True
    if target.aperture.shape == "C" and clear.aperture.shape == "C":
        return (
            math.hypot(dx, dy) + target.aperture.size[0] / 2
            <= clear.aperture.size[0] / 2 + 1e-9
        )
    # A target's complete geometry is inside its bounding box. Proving that
    # the clear aperture contains the box is therefore conservative for every
    # standard target shape.
    return _flash_covers_rect(clear, target.rect)


@never_raises
def _silk_over_pads(packet: gbr.Packet) -> list[Finding]:
    """Silkscreen ink on a solderable surface. Visible only in the gerbers:
    ``circuit.json`` has silkscreen and pads but nothing computes the
    intersection, and KiCad's own ``silk_over_copper`` is pinned to info in our
    noise floor because the converted footprints produce too much of it."""
    out: list[Finding] = []
    for silk_role, mask_role in (("silk_top", "mask_top"), ("silk_bottom", "mask_bottom")):
        silk = packet.layers.get(silk_role)
        mask = packet.layers.get(mask_role)
        dark_draws = (
            [draw for draw in silk.draws if draw.polarity == "dark"]
            if silk
            else []
        )
        if silk is None or mask is None or not dark_draws:
            continue
        if mask.regions:
            openings = [
                (poly.bounds, None)
                for poly in _openings(mask)
            ]
        else:
            openings = [
                (flash.rect, flash)
                for flash in mask.flashes
                if flash.polarity == "dark" and flash.aperture.size[0] > 0
            ]
        if not openings:
            continue
        grid = _Grid(
            [
                (rect.center[0], rect.center[1], (rect, opening_flash))
                for rect, opening_flash in openings
            ],
            cell=2.0,
        )
        clear_flashes = [
            flash for flash in silk.flashes if flash.polarity == "clear"
        ]
        hits = 0
        worst: tuple[float, float] | None = None
        for draw in dark_draws:
            half = draw.width / 2
            mid = ((draw.x0 + draw.x1) / 2, (draw.y0 + draw.y1) / 2)
            reach = draw.length / 2 + half + 2.0
            for _, _, payload in grid.near(mid[0], mid[1], reach):
                rect, opening_flash = payload
                stroke = Rect(
                    min(draw.x0, draw.x1) - half,
                    min(draw.y0, draw.y1) - half,
                    max(draw.x0, draw.x1) + half,
                    max(draw.y0, draw.y1) + half,
                )
                overlap = stroke.gap_to(rect)
                if overlap < 0:
                    intersection = _rect_intersection(stroke, rect)
                    if intersection is not None and any(
                        clear.sequence > draw.sequence
                        and (
                            (
                                opening_flash is not None
                                and _flash_covers_flash(clear, opening_flash)
                            )
                            or _flash_covers_rect(clear, intersection)
                        )
                        for clear in clear_flashes
                    ):
                        # KiCad's --subtract-soldermask plot is deliberately
                        # composite: positive legend strokes followed by
                        # pad-shaped clear flashes. The final image has no ink
                        # here even though the original stroke crossed the pad.
                        continue
                    hits += 1
                    if worst is None or -overlap > worst[0]:
                        worst = (-overlap, mid[0])
                    break
        if hits:
            out.append(
                finding(
                    silk.path,
                    "gerber_silk_over_pad",
                    f"{hits} silkscreen stroke(s) on "
                    f"{silk_role.replace('_', ' ')} land inside a solder-mask "
                    "opening — ink on a solderable surface stops the joint "
                    "wetting, and the fab may clip the silk instead",
                    "warning",
                )
            )
    return out


def check(
    board: Board,
    zip_path: str,
    *,
    assembly: bool = True,
    rules: FabRules = JLCPCB_2LAYER,
) -> CheckResult:
    """Reconcile a shipped gerber packet against the design that produced it."""
    coverage = Coverage(unit="gerber layers")
    try:
        packet = gbr.read_packet(zip_path)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="gerber_truth",
            findings=[
                finding(
                    "packet",
                    "gerber_unreadable",
                    f"cannot open {zip_path}: {type(exc).__name__}: {exc}",
                    "error",
                )
            ],
            coverage=coverage,
        )

    coverage.total = len(packet.layers) + len(packet.drills) + len(packet.ignored)
    coverage.examined = len(packet.layers) + len(packet.drills)
    for message in packet.ignored:
        coverage.skip(message)
    if packet.not_fab_input:
        coverage.skip(
            f"{len(packet.not_fab_input)} documentation plot(s) the fab does "
            f"not consume ({', '.join(sorted(packet.not_fab_input)[:3])}, ...)"
        )

    transform = solve_transform(board, packet)
    if transform is None:
        coverage.skip(
            "no board->plot transform could be solved (the outline layer is "
            "missing or empty) — nothing was reconciled against the design"
        )

    findings: list[Finding] = []
    findings += _layer_inventory(packet, board.layers)
    findings += _outline_matches(board, packet, transform)
    if transform is not None and transform.is_unity_scale:
        findings += _drills_match(board, packet, transform)
        findings += _pads_match(board, packet, transform, assembly=assembly)
    findings += _aperture_floors(packet, rules)
    findings += _mask_slivers(packet, rules, board, transform)
    findings += _silk_over_pads(packet)

    notes = [
        "read with an independent parser; nothing here reuses the code that "
        "wrote the files",
    ]
    if transform is not None:
        notes.append(
            f"board->plot offset solved from the outline: "
            f"({transform.dx:+.3f}, {transform.dy:+.3f})mm at scale "
            f"{transform.scale_x:.4f}"
        )
    return CheckResult(
        name="gerber_truth", findings=findings, coverage=coverage, notes=notes
    )
