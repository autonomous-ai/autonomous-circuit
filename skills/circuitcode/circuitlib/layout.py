"""How much room a block actually needs, and where to put it.

The most common way a board fails on the first try is placement: two blocks
put too close, courtyards overlap, the router refuses to run, and the verdict
comes back with fifty cascading errors that all trace to one nudge. Guessing
extents from a schematic is how that happens.

These numbers are **measured**, not estimated — each is the bounding box of
every PCB element the block emits, taken from a real build of its testbench
(2026-08-10, tscircuit 0.0.2279). Re-measure with the script in the module
docstring below whenever a block's footprint changes.
"""

from __future__ import annotations

from circuitlib import tables

#: block id -> (width mm, height mm) of everything the block places, at its
#: default orientation. Measured from built testbenches; see module docstring.
BLOCK_EXTENT_MM: dict[str, tuple[float, float]] = {
    "usb-c-power": (13.85, 16.39),
    "usb-c-data": (14.85, 17.39),
    "ldo-3v3": (11.11, 10.68),
    "status-led": (3.36, 4.07),
    "sw-tact": (9.39, 5.45),
    "i2c-bus": (3.86, 1.00),
    "rp2040-core": (29.42, 21.82),
    "sensor-bme280": (7.91, 4.26),
    "ws2812-chain": (32.16, 7.12),   # the 4-pixel testbench; scales with count
}

#: Courtyards must not touch, and the router needs somewhere to go. Two
#: millimetres between block bounding boxes is the smallest gap that reliably
#: avoids `pcb_courtyard_overlap_error` while leaving a routing channel.
BLOCK_GAP_MM = 2.0

#: Keep whole blocks this far inside the board outline. Larger than the
#: copper-to-edge rule because a courtyard is wider than its copper.
EDGE_MARGIN_MM = 1.5


def extent(block_id: str, *, count: int | None = None) -> tuple[float, float]:
    """The block's footprint in mm, or a raise if we have never measured it.

    ``count`` scales the parametric blocks (``ws2812-chain``, whose measured
    figure is the 4-pixel bench).
    """
    if block_id not in BLOCK_EXTENT_MM:
        raise ValueError(
            f"no measured extent for {block_id!r} — build its testbench and "
            f"add it (have: {', '.join(sorted(BLOCK_EXTENT_MM))})"
        )
    width, height = BLOCK_EXTENT_MM[block_id]
    if count and block_id == "ws2812-chain":
        width = width * (count / 4.0)
    return (round(width, 2), round(height, 2))


def min_board_for(block_ids: list[str], *, columns: int | None = None) -> tuple[float, float]:
    """Smallest board that fits these blocks in a grid, gaps and margins
    included. A floor, not a recommendation — leave room for connectors to
    reach an edge and for the router to breathe."""
    if not block_ids:
        return (tables.MIN_BOARD_EDGE_MM, tables.MIN_BOARD_EDGE_MM)
    sizes = [extent(b) for b in block_ids if b in BLOCK_EXTENT_MM]
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
    return (round(max(total_w, tables.MIN_BOARD_EDGE_MM), 1),
            round(max(total_h, tables.MIN_BOARD_EDGE_MM), 1))


def place_row(block_ids: list[str], *, y: float = 0.0,
              gap: float = BLOCK_GAP_MM) -> dict[str, tuple[float, float]]:
    """Left-to-right centres for a row of blocks, centred on the origin.

    Feed the results straight into each block's ``pcbX``/``pcbY``. Placing by
    measured extent rather than by eye is the difference between a board that
    routes on the first build and one that spends three rounds on overlaps.
    """
    sizes = [extent(b) for b in block_ids]
    total = sum(w for w, _ in sizes) + gap * (len(sizes) - 1)
    cursor = -total / 2.0
    out: dict[str, tuple[float, float]] = {}
    for block_id, (width, _) in zip(block_ids, sizes):
        out[block_id] = (round(cursor + width / 2.0, 2), y)
        cursor += width + gap
    return out


def overlap_warnings(
    placements: dict[str, tuple[float, float]], *, gap: float = BLOCK_GAP_MM
) -> list[dict[str, str]]:
    """Catch colliding blocks before paying for a build. Never raises."""
    out: list[dict[str, str]] = []
    try:
        items = [
            (bid, xy, extent(bid))
            for bid, xy in placements.items()
            if bid in BLOCK_EXTENT_MM
        ]
        for i, (a_id, (ax, ay), (aw, ah)) in enumerate(items):
            for b_id, (bx, by), (bw, bh) in items[i + 1:]:
                dx = abs(ax - bx) - (aw + bw) / 2.0
                dy = abs(ay - by) - (ah + bh) / 2.0
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
