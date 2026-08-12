"""How much room a block actually needs, where that room sits, and how to
place a row of blocks so the board comes out right on the first build.

The most common way a board fails on the first try is placement: two blocks put
too close, a part hanging over the outline, courtyards overlapping, the router
refusing to run, and a verdict full of cascading errors that all trace to one
nudge. Guessing extents from a schematic is how that happens.

**A block is a box, not a size (corrected 2026-08-11).** The first version of
this table stored width and height only, and ``place_row()`` assumed the
geometry was centred on the block's ``pcbX``/``pcbY``. It is usually not:
``usb-c-power``'s copper sits **3.29mm above** its origin, ``usb-c-data``'s
**6.04mm above**, ``rp2040-core``'s **5.51mm below** — which is why
``status-led``'s own testbench places it at ``pcbY={-1}``. The composition
matrix (``evals/composition.py``) built all 42 legal compositions on
2026-08-11 and **36 failed**, overwhelmingly on
``pcb_component_outside_board_error`` and ``dfm_edge_clearance``: parts hanging
off a board that ``min_board_for()`` had just declared big enough. Two of the
stored sizes were also simply wrong — ``rp2040-core``'s height was 2.6mm short.

So the table is now a **box relative to the origin**, and every helper here
works in board coordinates rather than assuming symmetry. Numbers are
**measured**, never estimated: each is the bounding box of every copper
feature, drill and courtyard the block emits, taken from a real build.
Re-measure with ``python evals/measure_block_boxes.py`` (``--write`` rewrites
the table below) whenever a footprint changes.
"""

from __future__ import annotations

from copy import deepcopy
import math

from circuitlib import tables

#: block id -> (min_x, min_y, max_x, max_y) in mm, relative to the block's
#: pcbX/pcbY origin. Measured from real builds by evals/measure_block_boxes.py
#: (2026-08-11, tscircuit 0.0.2279). Silkscreen is excluded on purpose: it may
#: legally overhang the outline, and counting it inflates every board.
BLOCK_BOX_MM: dict[str, tuple[float, float, float, float]] = {
    "i2c-bus": (-0.78, -0.32, 2.78, 0.32),
    "ldo-3v3": (-4.18, -6.7, 6.42, 2.85),
    "rp2040-core": (-11.5, -17.72, 16.03, 6.7),
    "sensor-bme280": (-3.78, -1.16, 3.78, 1.16),
    "status-led": (-1.42, -0.7, 1.42, 2.82),
    "sw-tact": (-3.5, -2.22, 3.5, 2.22),
    "usb-c-data": (-5.78, -3.67, 9.42, 15.75),
    "usb-c-power": (-4.93, -3.67, 8.42, 10.25),
    "usb-power-entry": (-3.88, -1.25, 3.88, 1.8),
    "ws2812-level-shifter": (-3.98, -1.27, 1.85, 1.25),
    "ws2812-chain": (-7.78, -3.92, 24.23, 2.3),
}

#: Courtyards must not touch, and the router needs somewhere to go. Two
#: millimetres between block bounding boxes is the smallest gap that reliably
#: avoids `pcb_courtyard_overlap_error` while leaving a routing channel.
BLOCK_GAP_MM = 2.0

#: Keep whole blocks this far inside the board outline. Larger than the
#: copper-to-edge rule (0.2mm) on purpose: that figure is where the fab
#: *blocks*, and a board sized to the fab's floor has no room for the router to
#: bring a track around the outside of a part.
EDGE_MARGIN_MM = 1.5

#: How much clear board the **router** needs outside the block content, which
#: is a different question from how much clearance a *courtyard* needs.
#: Measured 2026-08-11: `rp2040-core` alone, on a 43.4 x 27.5mm board sized
#: with a 1.5mm margin, came back with five vias *outside the board boundary*
#: and tracks at 0.000mm from the edge — the router escapes the outline when
#: there is no halo to route in, and a 5x pass does not help (38 blocking
#: errors both times). The same class of board with 4mm per side came back
#: clean. This is the number that makes a dense block routable, and it is why
#: `place_board` sizes the outline off this rather than off EDGE_MARGIN_MM.
ROUTER_HALO_MM = 4.0

# Edge connectors are the exception to the routing halo: their mating face has
# to reach the enclosure opening. Keep the component body about 1mm inside the
# routed outline; its footprint-specific shell/courtyard may intentionally
# cross it.
EDGE_CONNECTOR_INSET_MM = 1.0


def box(block_id: str, *, count: int | None = None) -> tuple[float, float, float, float]:
    """``(min_x, min_y, max_x, max_y)`` relative to the block's origin.

    Raises for a block nobody has measured — a guessed extent is how parts end
    up over the edge, so refusing is the safe answer.
    """
    if block_id not in BLOCK_BOX_MM:
        raise ValueError(
            f"no measured box for {block_id!r} — build it with "
            f"evals/measure_block_boxes.py and add it "
            f"(have: {', '.join(sorted(BLOCK_BOX_MM))})"
        )
    min_x, min_y, max_x, max_y = BLOCK_BOX_MM[block_id]
    if count and block_id == "ws2812-chain":
        # Pixels march in +x from the measured 4-pixel bench.
        pitch = (max_x - min_x) / 4.0
        max_x = min_x + pitch * count
    return (round(min_x, 2), round(min_y, 2), round(max_x, 2), round(max_y, 2))


def extent(block_id: str, *, count: int | None = None) -> tuple[float, float]:
    """The block's footprint size in mm. Size alone is not enough to place a
    block — use :func:`box` or :func:`place_row` for that."""
    min_x, min_y, max_x, max_y = box(block_id, count=count)
    return (round(max_x - min_x, 2), round(max_y - min_y, 2))


def origin_offset(block_id: str, *, count: int | None = None) -> tuple[float, float]:
    """How far the block's geometric centre sits from its ``pcbX``/``pcbY``.

    Place a block at ``(x, y)`` and its copper lands centred on
    ``(x + dx, y + dy)``. Non-zero for most blocks, and up to 6mm for
    ``usb-c-data`` — which is exactly the error that put parts off the board.
    """
    min_x, min_y, max_x, max_y = box(block_id, count=count)
    return (round((min_x + max_x) / 2, 2), round((min_y + max_y) / 2, 2))


def min_board_for(
    block_ids: list[str], *, columns: int | None = None
) -> tuple[float, float]:
    """Smallest board that fits these blocks in a grid, gaps and margins
    included. A floor, not a recommendation — leave room for connectors to
    reach an edge and for the router to breathe."""
    if not block_ids:
        return (tables.MIN_BOARD_EDGE_MM, tables.MIN_BOARD_EDGE_MM)
    sizes = [extent(b) for b in block_ids if b in BLOCK_BOX_MM]
    if not sizes:
        return (tables.MIN_BOARD_EDGE_MM, tables.MIN_BOARD_EDGE_MM)
    cols = columns or max(1, int(len(sizes) ** 0.5 + 0.5))
    rows = (len(sizes) + cols - 1) // cols

    col_widths = [0.0] * cols
    row_heights = [0.0] * rows
    for index, (width, height) in enumerate(sizes):
        col, row = index % cols, index // cols
        col_widths[col] = max(col_widths[col], width)
        row_heights[row] = max(row_heights[row], height)

    total_w = sum(col_widths) + BLOCK_GAP_MM * (cols - 1) + 2 * EDGE_MARGIN_MM
    total_h = sum(row_heights) + BLOCK_GAP_MM * (rows - 1) + 2 * EDGE_MARGIN_MM

    def _up(value: float) -> float:
        """Round *up* to 0.1mm. Rounding to nearest here is how a board comes
        out 0.02mm short of what it must hold and a part ends up 0.01mm past
        the margin — caught by board_fits() on 2026-08-11."""
        return math.ceil(round(value, 6) * 10) / 10.0

    return (_up(max(total_w, tables.MIN_BOARD_EDGE_MM)),
            _up(max(total_h, tables.MIN_BOARD_EDGE_MM)))


def place_row(block_ids: list[str], *, y: float = 0.0,
              gap: float = BLOCK_GAP_MM) -> dict[str, tuple[float, float]]:
    """``pcbX``/``pcbY`` for a row of blocks, laid left to right and centred on
    the origin — **with each block's origin offset taken out**, so the copper
    lands where the arithmetic says it does.

    Feed the results straight into each block's ``pcbX``/``pcbY``. Placing by
    measured box rather than by eye is the difference between a board that
    routes on the first build and one that spends three rounds on overlaps.
    """
    boxes = [box(b) for b in block_ids]
    widths = [bx[2] - bx[0] for bx in boxes]
    total = sum(widths) + gap * (len(boxes) - 1)
    cursor = -total / 2.0
    out: dict[str, tuple[float, float]] = {}
    for block_id, (min_x, min_y, max_x, max_y), width in zip(block_ids, boxes, widths):
        # Where the box should land, then back out the origin offset.
        wanted_cx = cursor + width / 2.0
        wanted_cy = y
        out[block_id] = (
            round(wanted_cx - (min_x + max_x) / 2.0, 2),
            round(wanted_cy - (min_y + max_y) / 2.0, 2),
        )
        cursor += width + gap
    return out


#: Blocks whose whole point is a plug going into them. They have to sit at the
#: board edge facing outward, or `pcb_connector_not_in_accessible_orientation`
#: fires and — more to the point — the finished device has a USB socket in the
#: middle of a PCB inside a printed box.
EDGE_BLOCKS = frozenset({"usb-c-power", "usb-c-data"})


#: A 3.2mm hole (M3 clearance) plus room for the screw head and the fab's
#: hole-to-copper rule. Blocks are kept out of this strip so a mounting hole
#: never lands on a footprint — which is what happens when holes are dropped
#: into the corners of a board sized only for its parts.
HOLE_DIAMETER_MM = 3.2
HOLE_STRIP_MM = 6.4


def place_board(
    block_ids: list[str],
    *,
    gap: float = BLOCK_GAP_MM,
    margin: float = ROUTER_HALO_MM,
    mounting_holes: bool = True,
) -> dict[str, object]:
    """A whole board plan: outline, placements, mounting holes.

    ``place_row`` is the primitive; this is the thing to actually call. It
    encodes the composition rules a first-build-orderable board needs, so an
    agent does not have to rediscover them once per board:

    * **connectors on the bottom edge, facing out** — anything in
      :data:`EDGE_BLOCKS` is placed against the outline rather than inline,
      because a USB socket in the middle of the board is not a product (and
      `pcb_connector_not_in_accessible_orientation` says so);
    * **everything else in a row above it**, spaced by the measured boxes;
    * **two mounting holes on opposite corners, in a reserved strip** — the
      board grows sideways to make room rather than dropping a drill on top of
      a footprint, which is what corner holes do on a board sized only for its
      parts;
    * an outline sized to hold all of it with ``margin`` to spare.

    Returns ``{"width_mm", "height_mm", "placements", "holes", "warnings"}``.
    ``warnings`` is empty on a plan that fits — check it, do not assume it.
    """
    edge = [b for b in block_ids if b in EDGE_BLOCKS]
    inner = [b for b in block_ids if b not in EDGE_BLOCKS]

    edge_w = sum(extent(b)[0] for b in edge) + gap * max(0, len(edge) - 1)
    edge_h = max((extent(b)[1] for b in edge), default=0.0)
    inner_w = sum(extent(b)[0] for b in inner) + gap * max(0, len(inner) - 1)
    inner_h = max((extent(b)[1] for b in inner), default=0.0)

    content_w = max(edge_w, inner_w)
    content_h = edge_h + inner_h + (gap if edge and inner else 0.0)
    strip = HOLE_STRIP_MM if mounting_holes else 0.0

    def _up(value: float) -> float:
        return math.ceil(round(value, 6) * 10) / 10.0

    width = max(_up(content_w + 2 * margin + 2 * strip), tables.MIN_BOARD_EDGE_MM)
    height = max(_up(content_h + 2 * margin), tables.MIN_BOARD_EDGE_MM)

    placements: dict[str, tuple[float, float]] = {}
    if edge:
        # Bottom band: the connector's body reaches the enclosure edge. Using
        # the general router halo here puts a perfectly routed USB socket 4mm
        # inside the product where no cable can reach it.
        placements.update(
            place_row(
                edge,
                y=-height / 2.0 + EDGE_CONNECTOR_INSET_MM + edge_h / 2.0,
                gap=gap,
            )
        )
    if inner:
        placements.update(
            place_row(inner, y=height / 2.0 - margin - inner_h / 2.0, gap=gap)
        )

    holes: list[dict[str, object]] = []
    if mounting_holes:
        inset = strip / 2.0
        holes = [
            {"name": "H1", "diameter_mm": HOLE_DIAMETER_MM,
             "pcbX": round(-width / 2 + inset, 2),
             "pcbY": round(-height / 2 + inset, 2)},
            {"name": "H2", "diameter_mm": HOLE_DIAMETER_MM,
             "pcbX": round(width / 2 - inset, 2),
             "pcbY": round(height / 2 - inset, 2)},
        ]

    warnings = board_fits(placements, width, height, margin=margin)
    warnings += overlap_warnings(placements, gap=gap)
    warnings += _hole_clearance_warnings(holes, placements)
    return {
        "width_mm": width,
        "height_mm": height,
        "placements": placements,
        "holes": holes,
        "warnings": warnings,
    }


def _hole_clearance_warnings(
    holes: list[dict[str, object]], placements: dict[str, tuple[float, float]]
) -> list[dict[str, str]]:
    """A mounting hole landing on a footprint is a board nobody can screw down.
    Cheap to check here; expensive to discover in a fab packet."""
    out: list[dict[str, str]] = []
    try:
        for hole in holes:
            hx = float(hole["pcbX"])  # type: ignore[arg-type]
            hy = float(hole["pcbY"])  # type: ignore[arg-type]
            radius = float(hole["diameter_mm"]) / 2.0  # type: ignore[arg-type]
            for block_id, (x, y) in placements.items():
                if block_id not in BLOCK_BOX_MM:
                    continue
                min_x, min_y, max_x, max_y = box(block_id)
                dx = max(x + min_x - hx, 0.0, hx - (x + max_x))
                dy = max(y + min_y - hy, 0.0, hy - (y + max_y))
                if math.hypot(dx, dy) < radius + 0.5:
                    out.append({
                        "part": f"{hole['name']},{block_id}",
                        "kind": "functional",
                        "severity": "warning",
                        "detail": (
                            f"mounting hole {hole['name']} at ({hx}, {hy}) lands "
                            f"on {block_id} — move the hole or grow the board"
                        ),
                    })
    except Exception as exc:  # pragma: no cover - advisory must never break
        out.append({"part": "board", "kind": "check_failed", "severity": "warning",
                    "detail": f"_hole_clearance_warnings: {exc}"})
    return out


def occupied_box(
    placements: dict[str, tuple[float, float]]
) -> tuple[float, float, float, float]:
    """The board-coordinate box every placed block occupies, together."""
    corners = []
    for block_id, (x, y) in placements.items():
        if block_id not in BLOCK_BOX_MM:
            continue
        min_x, min_y, max_x, max_y = box(block_id)
        corners.append((x + min_x, y + min_y, x + max_x, y + max_y))
    if not corners:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        round(min(c[0] for c in corners), 2),
        round(min(c[1] for c in corners), 2),
        round(max(c[2] for c in corners), 2),
        round(max(c[3] for c in corners), 2),
    )


def board_fits(
    placements: dict[str, tuple[float, float]],
    width_mm: float,
    height_mm: float,
    *,
    margin: float = EDGE_MARGIN_MM,
) -> list[dict[str, str]]:
    """Does every placed block sit inside this outline, with margin?

    Answers before a build what ``pcb_component_outside_board_error`` answers
    after one. Never raises — a placement helper that can break a build is a
    helper nobody calls.
    """
    out: list[dict[str, str]] = []
    try:
        half_w, half_h = width_mm / 2.0, height_mm / 2.0
        for block_id, (x, y) in placements.items():
            if block_id not in BLOCK_BOX_MM:
                continue
            min_x, min_y, max_x, max_y = box(block_id)
            bottom_margin = (
                EDGE_CONNECTOR_INSET_MM if block_id in EDGE_BLOCKS else margin
            )
            over = max(
                -half_w + margin - (x + min_x),
                -half_h + bottom_margin - (y + min_y),
                (x + max_x) - (half_w - margin),
                (y + max_y) - (half_h - margin),
            )
            if over > 0.01:
                out.append({
                    "part": block_id,
                    "kind": "functional",
                    "severity": "warning",
                    "detail": (
                        f"{block_id} sits {over:.2f}mm past the {margin:g}mm "
                        f"margin on a {width_mm:g}x{height_mm:g}mm board — "
                        f"grow the outline or move it "
                        f"(its box is {box(block_id)} around its origin)"
                    ),
                })
    except Exception as exc:  # pragma: no cover - advisory must never break
        out.append({"part": "board", "kind": "check_failed", "severity": "warning",
                    "detail": f"board_fits: {exc}"})
    return out


def overlap_warnings(
    placements: dict[str, tuple[float, float]], *, gap: float = BLOCK_GAP_MM
) -> list[dict[str, str]]:
    """Catch colliding blocks before paying for a build. Never raises."""
    out: list[dict[str, str]] = []
    try:
        items = []
        for block_id, (x, y) in placements.items():
            if block_id not in BLOCK_BOX_MM:
                continue
            min_x, min_y, max_x, max_y = box(block_id)
            items.append((block_id, x + min_x, y + min_y, x + max_x, y + max_y))
        for i, (a_id, a_x0, a_y0, a_x1, a_y1) in enumerate(items):
            for b_id, b_x0, b_y0, b_x1, b_y1 in items[i + 1:]:
                dx = max(a_x0, b_x0) - min(a_x1, b_x1)
                dy = max(a_y0, b_y0) - min(a_y1, b_y1)
                # Rounding slack, not engineering slack: place_row rounds
                # centres to 0.01mm, so a nominally exact `gap` lands a few
                # microns short. A check that warns about its own output is a
                # check people switch off.
                slack = 0.02
                if dx < gap - slack and dy < gap - slack:
                    out.append({
                        "part": f"{a_id},{b_id}",
                        "kind": "functional",
                        "severity": "warning",
                        "detail": (
                            f"{a_id} and {b_id} are {max(dx, dy):.1f}mm apart; "
                            f"blocks need {gap:g}mm of clear space or their "
                            "courtyards collide and routing is skipped"
                        ),
                    })
    except Exception as exc:  # pragma: no cover - advisory must never break
        out.append({"part": "board", "kind": "check_failed", "severity": "warning",
                    "detail": f"overlap_warnings: {exc}"})
    return out


def _component_zone_strings(value: object, path: str) -> None:
    if isinstance(value, str) and value:
        return
    if (
        isinstance(value, list)
        and value
        and all(isinstance(item, str) and item for item in value)
    ):
        return
    raise ValueError(f"{path} must be a non-empty string or list of strings")


def _component_zone_number(
    value: object,
    path: str,
    *,
    allow_zero: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{path} must be a "
            f"{'non-negative' if allow_zero else 'positive'} finite number"
        )
    number = float(value)
    if not math.isfinite(number) or (number < 0 if allow_zero else number <= 0):
        raise ValueError(
            f"{path} must be a "
            f"{'non-negative' if allow_zero else 'positive'} finite number"
        )
    return number


def _component_zone_unknown(
    value: dict[object, object],
    allowed: set[str],
    path: str,
) -> None:
    unknown = sorted((key for key in value if key not in allowed), key=str)
    if unknown:
        names = ", ".join(repr(key) for key in unknown)
        raise ValueError(f"{path} contains unknown member(s): {names}")


def _validate_component_zones(value: object) -> list[dict[str, object]]:
    """Validate and defensively copy ``product.json.layout.componentZones``.

    This deliberately mirrors circuitpy's product-schema validation without
    importing circuitpy: circuitlib is vendored into a self-contained skill
    runtime, so an invalid zone must be refused before generation in either
    environment independently.
    """

    path = "component_zones"
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path} must be a non-empty list")
    for index, rule in enumerate(value):
        rule_path = f"{path}[{index}]"
        if not isinstance(rule, dict):
            raise ValueError(f"{rule_path} must be an object")
        _component_zone_strings(rule.get("match"), f"{rule_path}.match")
        containment = rule.get("containment")
        if (
            not isinstance(containment, str)
            or containment not in {"center", "courtyard"}
        ):
            raise ValueError(
                f"{rule_path}.containment must be 'center' or 'courtyard'"
            )
        shape = rule.get("shape")
        if not isinstance(shape, dict):
            raise ValueError(f"{rule_path}.shape must be an object")
        kind = shape.get("kind")
        if not isinstance(kind, str) or kind not in {"circle", "annulus", "rect"}:
            raise ValueError(
                f"{rule_path}.shape.kind must be 'circle', 'annulus', or 'rect'"
            )
        center = shape.get("center")
        if not isinstance(center, list) or len(center) != 2:
            raise ValueError(
                f"{rule_path}.shape.center must be [x, y] in board millimetres"
            )
        for coordinate_index, coordinate in enumerate(center):
            if (
                isinstance(coordinate, bool)
                or not isinstance(coordinate, (int, float))
                or not math.isfinite(float(coordinate))
            ):
                raise ValueError(
                    f"{rule_path}.shape.center[{coordinate_index}] "
                    "must be a finite number"
                )

        shape_keys = {"kind", "center"}
        if kind == "circle":
            _component_zone_number(
                shape.get("radiusMm"), f"{rule_path}.shape.radiusMm"
            )
            shape_keys.add("radiusMm")
        elif kind == "annulus":
            inner = _component_zone_number(
                shape.get("innerRadiusMm"),
                f"{rule_path}.shape.innerRadiusMm",
                allow_zero=True,
            )
            outer = _component_zone_number(
                shape.get("outerRadiusMm"),
                f"{rule_path}.shape.outerRadiusMm",
            )
            if inner >= outer:
                raise ValueError(
                    f"{rule_path}.shape.innerRadiusMm must be less than "
                    "outerRadiusMm"
                )
            shape_keys.update({"innerRadiusMm", "outerRadiusMm"})
        else:
            _component_zone_number(
                shape.get("widthMm"), f"{rule_path}.shape.widthMm"
            )
            _component_zone_number(
                shape.get("heightMm"), f"{rule_path}.shape.heightMm"
            )
            shape_keys.update({"widthMm", "heightMm"})
        _component_zone_unknown(shape, shape_keys, f"{rule_path}.shape")
        _component_zone_unknown(
            rule, {"match", "shape", "containment"}, rule_path
        )

    return deepcopy(value)


def _validate_decoupling_overrides(value: object) -> list[dict[str, object]]:
    """Validate ref-scoped vendor bypass-distance rules.

    The default remains the product-wide bound. An override is appropriate
    only when the component vendor's routed reference design establishes a
    different physical envelope; it never disables the authored-topology or
    measurable-pad requirements enforced by the independent verifier.
    """

    path = "decoupling_overrides"
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path} must be a non-empty list")
    for index, rule in enumerate(value):
        rule_path = f"{path}[{index}]"
        if not isinstance(rule, dict):
            raise ValueError(f"{rule_path} must be an object")
        _component_zone_strings(rule.get("match"), f"{rule_path}.match")
        _component_zone_number(
            rule.get("maxDistanceMm"), f"{rule_path}.maxDistanceMm"
        )
        source = rule.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(
                f"{rule_path}.source must cite a non-empty manufacturer "
                "reference URI or document identifier"
            )
        _component_zone_unknown(
            rule, {"match", "maxDistanceMm", "source"}, rule_path
        )
    return deepcopy(value)


def product_layout(
    *,
    board_size_mm: tuple[float, float],
    component_sides: list[dict[str, object]] | None = None,
    component_zones: list[dict[str, object]] | None = None,
    edge_connectors: list[dict[str, object]] | None = None,
    ground_plane_layers: tuple[str, ...] = ("top", "bottom"),
    max_ground_route_length_mm: float = 20.0,
    max_ground_fanout_length_mm: float = tables.GROUND_FANOUT_MAX_LENGTH_MM,
    ground_stitching_pitch_mm: float = tables.GROUND_STITCHING_PITCH_MM,
    min_copper_clearance_mm: float = tables.PREFERRED_CLEARANCE_MM,
    decoupling_max_distance_mm: float = tables.DECOUPLING_MAX_DISTANCE_MM,
    decoupling_exclude: tuple[str, ...] = (),
    decoupling_overrides: list[dict[str, object]] | None = None,
    power_nets: tuple[str, ...] = ("V5", "V3_3"),
    power_trunk_width_mm: float = tables.POWER_TRUNK_MIN_MM,
    power_neckdown_width_mm: float = tables.POWER_NECKDOWN_WIDTH_MM,
    power_neckdown_max_length_mm: float = tables.POWER_NECKDOWN_MAX_LENGTH_MM,
    power_via_outer_diameter_mm: float = tables.POWER_VIA_OUTER_DIAMETER_MM,
    power_via_hole_diameter_mm: float = tables.POWER_VIA_HOLE_DIAMETER_MM,
) -> dict[str, object]:
    """Build the ``product.json.layout`` compiled-artifact contract.

    This records decisions; it is not another placement generator. The
    default is a solved, stitched GND plane on both faces; a board that needs
    a partial or single-face pour must opt into that geometry explicitly.
    Most importantly, a power rail is a wide trunk with short endpoint
    neck-downs, not one global width forced through QFN and 0402 pads.
    """

    width, height = board_size_mm
    if width <= 0 or height <= 0:
        raise ValueError("board_size_mm must contain two positive dimensions")
    if not ground_plane_layers:
        raise ValueError("ground_plane_layers must not be empty")
    if max_ground_fanout_length_mm <= 0:
        raise ValueError("max_ground_fanout_length_mm must be positive")
    if power_neckdown_width_mm > power_trunk_width_mm:
        raise ValueError("power neck-down width cannot exceed the trunk width")
    if power_neckdown_max_length_mm < 0:
        raise ValueError("power neck-down length must be non-negative")
    if (
        power_via_hole_diameter_mm <= 0
        or power_via_outer_diameter_mm <= power_via_hole_diameter_mm
    ):
        raise ValueError("power via outer diameter must exceed its positive hole")
    if min_copper_clearance_mm <= 0:
        raise ValueError("min_copper_clearance_mm must be positive")
    if decoupling_max_distance_mm <= 0:
        raise ValueError("decoupling_max_distance_mm must be positive")
    if any(
        not isinstance(pattern, str) or not pattern
        for pattern in decoupling_exclude
    ):
        raise ValueError("decoupling_exclude must contain only non-empty ref patterns")

    out: dict[str, object] = {
        "boardSizeMm": [float(width), float(height)],
        "boardSizeToleranceMm": 0.1,
        "minCopperClearanceMm": float(min_copper_clearance_mm),
        "decoupling": {
            "maxDistanceMm": float(decoupling_max_distance_mm),
            **({"exclude": list(decoupling_exclude)} if decoupling_exclude else {}),
            **(
                {"overrides": _validate_decoupling_overrides(decoupling_overrides)}
                if decoupling_overrides is not None
                else {}
            ),
        },
        "groundPlanes": {
            "layers": list(ground_plane_layers),
            "maxRoutedLengthMm": float(max_ground_route_length_mm),
            "maxFanoutLengthMm": float(max_ground_fanout_length_mm),
            "stitchingPitchMm": float(ground_stitching_pitch_mm),
        },
    }
    if component_sides:
        out["componentSides"] = deepcopy(component_sides)
    if component_zones is not None:
        out["componentZones"] = _validate_component_zones(component_zones)
    if edge_connectors:
        out["edgeConnectors"] = deepcopy(edge_connectors)
    if power_nets and power_trunk_width_mm > 0:
        out["netClasses"] = [
            {
                "name": "POWER",
                "nets": list(power_nets),
                "minTrunkWidthMm": float(power_trunk_width_mm),
                "minNeckdownWidthMm": float(power_neckdown_width_mm),
                "maxNeckdownLengthMm": float(power_neckdown_max_length_mm),
                "minViaOuterDiameterMm": float(power_via_outer_diameter_mm),
                "minViaHoleDiameterMm": float(power_via_hole_diameter_mm),
            }
        ]
    return out
