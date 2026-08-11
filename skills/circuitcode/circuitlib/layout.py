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
