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
#:
#: **The halo does not apply to the face of a connector** (2026-08-11). Applied
#: to every side, it put `usb-c-power`'s lowest copper 4.00mm inside the
#: outline — a receptacle you cannot plug a cable into, which is a functional
#: defect and not a tidiness one. The halo's evidence is about *interior*
#: blocks: `rp2040-core`'s router escaped the outline because it had nowhere to
#: route. There is nothing to route in front of a connector — it is the
#: southernmost thing on the board — so that side keeps EDGE_MARGIN_MM and the
#: other three sides keep the halo. See `place_board`.
ROUTER_HALO_MM = 4.0


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

    # The bottom edge is the connector's face, not a routing channel: a plug
    # goes in there, and 4mm of board in front of a USB-C mouth is a socket
    # nobody can reach. The other three sides keep the router halo.
    face = EDGE_MARGIN_MM if edge else margin

    def _up(value: float) -> float:
        return math.ceil(round(value, 6) * 10) / 10.0

    width = max(_up(content_w + 2 * margin + 2 * strip), tables.MIN_BOARD_EDGE_MM)
    height = max(_up(content_h + face + margin), tables.MIN_BOARD_EDGE_MM)

    placements: dict[str, tuple[float, float]] = {}
    if edge:
        # Bottom band: the connector's own geometry sits against the face
        # margin, close enough to the outline that a cable reaches it.
        placements.update(
            place_row(edge, y=-height / 2.0 + face + edge_h / 2.0, gap=gap)
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

    # Two margins, so two checks: an edge block is allowed inside the halo on
    # its face, everything else is not.
    warnings = board_fits(
        {k: v for k, v in placements.items() if k in EDGE_BLOCKS},
        width, height, margin=face,
    )
    warnings += board_fits(
        {k: v for k, v in placements.items() if k not in EDGE_BLOCKS},
        width, height, margin=margin,
    )
    warnings += overlap_warnings(placements, gap=gap)
    warnings += _hole_clearance_warnings(holes, placements)
    warnings += price_tier_warnings(width, height)
    return {
        "width_mm": width,
        "height_mm": height,
        "placements": placements,
        "holes": holes,
        "warnings": warnings,
    }


def price_tier_warnings(width_mm: float, height_mm: float) -> list[dict[str, str]]:
    """Name the money when a plan misses the fab's sample subsidy (ledger #30).

    The EE review found terminal-keyboard 12mm over a price cliff nobody in
    the plan knew existed: 5 sample boards at <=100x100mm cost $2, its 112x90
    quoted $8.90. The planner's target is the tier — a board should only leave
    it because the design cannot fit, and that choice should be made knowing
    the price. Never raises.
    """
    out: list[dict[str, str]] = []
    try:
        tier_w, tier_h = tables.JLC_SAMPLE_TIER_MM
        fits = (width_mm <= tier_w and height_mm <= tier_h) or (
            height_mm <= tier_w and width_mm <= tier_h
        )
        if not fits:
            # Shortfall in the friendlier orientation: both dimensions must
            # fit, so an orientation's overage is its worse axis.
            over = min(
                max(width_mm - tier_w, height_mm - tier_h, 0.0),
                max(height_mm - tier_w, width_mm - tier_h, 0.0),
            )
            out.append({
                "part": "board",
                "kind": "price_tier",
                "severity": "warning",
                "detail": (
                    f"{width_mm:g}x{height_mm:g}mm misses the "
                    f"{tier_w:g}x{tier_h:g}mm ${tables.JLC_SAMPLE_TIER_USD:g} "
                    f"sample tier by {over:g}mm — the subsidy is a cliff "
                    "(112x90 quoted $8.90 for 5, measured 2026-08-15). Fit "
                    "the tier unless the design cannot"
                ),
            })
    except Exception as exc:  # pragma: no cover - advisory must never break
        out.append({"part": "board", "kind": "check_failed", "severity": "warning",
                    "detail": f"price_tier: {exc}"})
    return out


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
            over = max(
                -half_w + margin - (x + min_x),
                -half_h + margin - (y + min_y),
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
