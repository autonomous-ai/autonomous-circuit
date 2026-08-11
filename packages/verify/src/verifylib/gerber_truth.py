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
from verifylib.model import Board, Rect
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


@never_raises
def _pads_match(board: Board, packet: gbr.Packet, transform: Transform | None,
                *, assembly: bool) -> list[Finding]:
    if transform is None:
        return []
    out: list[Finding] = []
    indexes = {
        "copper_top": _pad_index(packet.layers.get("copper_top")),
        "copper_bottom": _pad_index(packet.layers.get("copper_bottom")),
        "mask_top": _pad_index(packet.layers.get("mask_top")),
        "mask_bottom": _pad_index(packet.layers.get("mask_bottom")),
        "paste_top": _pad_index(packet.layers.get("paste_top")),
        "paste_bottom": _pad_index(packet.layers.get("paste_bottom")),
    }
    for component in board.components:
        for pad in component.pads:
            side = "bottom" if pad.layer == "bottom" else "top"
            gx, gy = transform.apply(pad.x, pad.y)
            copper = indexes.get(f"copper_{side}")
            if copper is not None and not any(
                True for _ in copper.near(gx, gy, POSITION_TOLERANCE_MM)
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
            mask = indexes.get(f"mask_{side}")
            if mask is not None and not any(
                True for _ in mask.near(gx, gy, POSITION_TOLERANCE_MM)
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
            if assembly and not pad.plated_hole:
                paste = indexes.get(f"paste_{side}")
                if paste is not None and not any(
                    True for _ in paste.near(gx, gy, POSITION_TOLERANCE_MM)
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


@never_raises
def _mask_slivers(packet: gbr.Packet, rules: FabRules) -> list[Finding]:
    """A web of solder mask narrower than the fab can hold burns off in the
    oven, and the two pads it separated become one joint. Nothing upstream of
    the export can see this: it is a property of the mask apertures, which only
    exist in the gerbers."""
    out: list[Finding] = []
    for role in ("mask_top", "mask_bottom"):
        layer = packet.layers.get(role)
        if layer is None or not layer.flashes:
            continue
        rects = [(f.rect, f) for f in layer.flashes if f.aperture.size[0] > 0]
        grid = _Grid([(f.x, f.y, (rect, f)) for rect, f in rects], cell=2.0)
        seen: set[tuple[int, int]] = set()
        worst: tuple[float, float, float] | None = None
        count = 0
        for rect, flash in rects:
            reach = max(rect.width, rect.height) + rules.min_mask_sliver_mm + 1.0
            for _, _, payload in grid.near(flash.x, flash.y, reach):
                other_rect, other = payload
                if other is flash:
                    continue
                key = tuple(sorted((id(flash), id(other))))  # type: ignore[arg-type]
                if key in seen:
                    continue
                seen.add(key)  # type: ignore[arg-type]
                gap = rect.gap_to(other_rect)
                if 0 <= gap < rules.min_mask_sliver_mm:
                    count += 1
                    if worst is None or gap < worst[0]:
                        worst = (gap, flash.x, flash.y)
        if count and worst is not None:
            out.append(
                finding(
                    layer.path,
                    "gerber_mask_sliver",
                    f"{count} pair(s) of mask openings on {role.replace('_', ' ')} "
                    f"are separated by less than {rules.min_mask_sliver_mm:g}mm; "
                    f"the narrowest is {worst[0]:.3f}mm near "
                    f"({worst[1]:.2f}, {worst[2]:.2f}) in plot coordinates. A "
                    "web that thin burns off and the two pads bridge",
                    "warning",
                )
            )
    return out


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
        if silk is None or mask is None or not silk.draws or not mask.flashes:
            continue
        openings = [(f.rect, f) for f in mask.flashes if f.aperture.size[0] > 0]
        grid = _Grid([(f.x, f.y, rect) for rect, f in openings], cell=2.0)
        hits = 0
        worst: tuple[float, float] | None = None
        for draw in silk.draws:
            half = draw.width / 2
            mid = ((draw.x0 + draw.x1) / 2, (draw.y0 + draw.y1) / 2)
            reach = draw.length / 2 + half + 2.0
            for _, _, rect in grid.near(mid[0], mid[1], reach):
                stroke = Rect(
                    min(draw.x0, draw.x1) - half,
                    min(draw.y0, draw.y1) - half,
                    max(draw.x0, draw.x1) + half,
                    max(draw.y0, draw.y1) + half,
                )
                overlap = stroke.gap_to(rect)
                if overlap < 0:
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
    findings += _mask_slivers(packet, rules)
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
