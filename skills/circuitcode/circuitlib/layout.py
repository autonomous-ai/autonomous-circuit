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

#: Some blocks need more than the floor. `rp2040-core`'s QFN-56 packs its
#: escape fanout, QSPI cluster and RUN/reset corner into a footprint the size
#: of `usb-c-power`'s, and three independent boards hit the same class of
#: blocking KiCad finding — a DVDD via clearance violation, a hole clearance
#: violation — entirely *inside the block's own footprint*, only cleared by
#: widening the board-level gap to a neighbour well past 2mm:
#: `i2c-sensor-hub` (2026-08-17) measured **5mm** sufficient after `10x`
#: still blocked at 2mm; `macropad-6` widened 2mm to 6mm and cut 46
#: blocking findings to 14 (the remainder was a different defect — an SWD
#: trace crossing the RUN corner, not spacing). Three engineers independently
#: rediscovering the same number is the planner failing to hand it out.
#: 5mm is the smaller of the two confirmed-sufficient measurements, so it is
#: a floor here too, not a guarantee — dense boards may still need more.
BLOCK_GAP_OVERRIDE_MM: dict[str, float] = {
    "rp2040-core": 5.0,
}


#: Content the planner has to make room for that is not a golden block: a
#: `PadHeader` row, a testpoint band, a display window, a battery clip.
#:
#: Four boards have now hand-derived the same arithmetic — `usb-c-breakout`,
#: `i2c-sensor-hub`, `env-logger-usb` and `dual-rail-psu` all grew their
#: outline by hand and re-seated every coordinate to fit a pad row, each one
#: re-deriving `place_board`'s internal edge-placement formula from the source
#: to do it. Four independent rediscoveries of one number is the planner
#: failing to hand it out (the rule at the bottom of `docs/lessons.md`: the fix
#: belongs in the planner, never in advice a user has to remember).
#:
#: A reserve is a named box in the same coordinate system as a block, so
#: everything downstream — row wrapping, gaps, `board_fits`, `overlap_warnings`
#: — treats it exactly like one and needs no special case. It is registered
#: rather than passed because `place_row`, `box` and both checks read the same
#: table, and threading an override through five signatures to avoid one
#: explicit call is worse.
RESERVED_BOX_MM: dict[str, tuple[float, float, float, float]] = {}


def reserve(name: str, width_mm: float, height_mm: float) -> str:
    """Make room for something that is not a block. Returns ``name``.

    Call before :func:`place_board`, then read the coordinate back out of the
    plan's ``placements[name]`` and put your component there::

        header = reserve("i2c-header", *pad_header_extent(4))
        plan = place_board(["usb-c-data", "ldo-3v3", "rp2040-core", header])
        x, y = plan["placements"]["i2c-header"]
        # <PadHeader nets={...} pcbX={x} pcbY={y} />

    The box is centred on its origin, which is what `PadHeader` and the other
    glue components use — they lay their pads out symmetrically about their
    own ``pcbX``/``pcbY``.

    Idempotent: registering the same name twice replaces the box rather than
    accumulating, so a script that reruns does not drift.
    """
    if width_mm <= 0 or height_mm <= 0:
        raise ValueError(f"reserve({name!r}) needs a positive size, got {width_mm}x{height_mm}")
    half_w, half_h = width_mm / 2.0, height_mm / 2.0
    RESERVED_BOX_MM[name] = (-half_w, -half_h, half_w, half_h)
    BLOCK_BOX_MM[name] = RESERVED_BOX_MM[name]
    return name


def pad_header_extent(
    pads: int, *, pitch: float = 2.54, pad_diameter: float = 1.0, label_drop: float = 1.7
) -> tuple[float, float]:
    """The size of a `PadHeader` with ``pads`` pads — its numbers, not a guess.

    Kept here and derived from the same three values the component uses so the
    two cannot drift: pads march from ``-((n-1)*pitch)/2`` in ``+x``, and the
    silkscreen label sits ``label_drop`` below the row, which is real content
    a neighbouring block must not be placed on top of.
    """
    if pads < 1:
        raise ValueError(f"a pad header needs at least one pad, got {pads}")
    width = (pads - 1) * pitch + pad_diameter
    height = pad_diameter / 2.0 + label_drop + 0.5  # pad top -> label baseline + text
    return (round(width, 2), round(height, 2))


def pair_gap(a_id: str, b_id: str, *, gap: float = BLOCK_GAP_MM) -> float:
    """The clear space two adjacent blocks need, at least ``gap``.

    Either side can ask for more (`BLOCK_GAP_OVERRIDE_MM`); the pair gets the
    larger of the two asks, because a dense block's routing headroom
    requirement does not shrink because its neighbour is a simple one.
    """
    return max(
        gap,
        BLOCK_GAP_OVERRIDE_MM.get(a_id, 0.0),
        BLOCK_GAP_OVERRIDE_MM.get(b_id, 0.0),
    )


def _sequential_gap_total(block_ids: list[str], *, gap: float = BLOCK_GAP_MM) -> float:
    """Sum of the pairwise gaps between consecutive blocks in a row.

    Equal to ``gap * (n - 1)`` when nothing asks for more — the shape every
    caller had before per-block overrides existed.
    """
    return sum(
        pair_gap(block_ids[i], block_ids[i + 1], gap=gap)
        for i in range(len(block_ids) - 1)
    )

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


#: How a repeated block is spelled in a placement dict: the first instance
#: keeps the bare id, the rest get `#2`, `#3`. See `place_row`.
INSTANCE_SEP = "#"


def base_id(placement_key: str) -> str:
    """The block a placement key names — `status-led#2` is a `status-led`.

    Every consumer of a placement dict has to go through this, because the
    alternative is what shipped until 2026-08-17: a second instance of a block
    keyed `status-led#2` would not be found in `BLOCK_BOX_MM`, and each check
    that walks the dict — overlap, hole clearance, board fit, occupied box —
    would quietly skip it. A part the planner cannot see is a part it cannot
    warn about.
    """
    return str(placement_key).split(INSTANCE_SEP, 1)[0]


#: How many of a parametric block an entry asks for, when it says so.
#:
#: `place_row(["ws2812-chain", "sw-tact"])` sizes the chain from its 4-pixel
#: measurement bench, which is the right default and the wrong answer for a
#: board with eight pixels on it. Entries may therefore be a bare id or a
#: `(block_id, count)` pair, and the count is threaded all the way through —
#: without it a real block set came back as a nonsense 149.8 x 51.4mm board and
#: the engineer hand-placed two rows instead (`rgb-lamp-controller`,
#: 2026-08-17, ~40 minutes).
BlockSpec = "str | tuple[str, int]"


def spec(entry) -> tuple[str, int | None]:
    """`"ws2812-chain"` or `("ws2812-chain", 8)` -> `(id, count)`."""
    if isinstance(entry, (tuple, list)):
        block_id = str(entry[0])
        count = int(entry[1]) if len(entry) > 1 and entry[1] else None
        return block_id, count
    return str(entry), None


def box(block_id: str, *, count: int | None = None) -> tuple[float, float, float, float]:
    """``(min_x, min_y, max_x, max_y)`` relative to the block's origin.

    Raises for a block nobody has measured — a guessed extent is how parts end
    up over the edge, so refusing is the safe answer.
    """
    if block_id not in BLOCK_BOX_MM:
        raise ValueError(
            f"no measured box for {block_id!r}. If it is a golden block, build "
            f"it with evals/measure_block_boxes.py and add it. If it is "
            f"something else that needs board space — a pad header, a testpoint "
            f"band, a display window — call "
            f"layout.reserve({block_id!r}, width_mm, height_mm) first and pass "
            f"the name in with the blocks "
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
    sizes = [
        extent(block_id, count=count)
        for block_id, count in (spec(entry) for entry in block_ids)
        if block_id in BLOCK_BOX_MM
    ]
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

    # A floor must not under-count what place_row/place_board will actually
    # charge for a dense block's gap override — applied to every gap in the
    # grid rather than the exact pairs (this is a floor, not a layout), which
    # only ever rounds a suggestion up, never down.
    widest_ask = max(
        (BLOCK_GAP_OVERRIDE_MM.get(block_id, 0.0)
         for block_id, _count in (spec(entry) for entry in block_ids)),
        default=0.0,
    )
    grid_gap = max(BLOCK_GAP_MM, widest_ask)
    total_w = sum(col_widths) + grid_gap * (cols - 1) + 2 * EDGE_MARGIN_MM
    total_h = sum(row_heights) + grid_gap * (rows - 1) + 2 * EDGE_MARGIN_MM

    def _up(value: float) -> float:
        """Round *up* to 0.1mm. Rounding to nearest here is how a board comes
        out 0.02mm short of what it must hold and a part ends up 0.01mm past
        the margin — caught by board_fits() on 2026-08-11."""
        return math.ceil(round(value, 6) * 10) / 10.0

    return (_up(max(total_w, tables.MIN_BOARD_EDGE_MM)),
            _up(max(total_h, tables.MIN_BOARD_EDGE_MM)))


def place_row(block_ids: list[str], *, y: float = 0.0,
              gap: float = BLOCK_GAP_MM,
              seen: dict[str, int] | None = None) -> dict[str, tuple[float, float]]:
    """``pcbX``/``pcbY`` for a row of blocks, laid left to right and centred on
    the origin — **with each block's origin offset taken out**, so the copper
    lands where the arithmetic says it does.

    Feed the results straight into each block's ``pcbX``/``pcbY``. Placing by
    measured box rather than by eye is the difference between a board that
    routes on the first build and one that spends three rounds on overlaps.
    """
    specs = [spec(entry) for entry in block_ids]
    boxes = [box(block_id, count=count) for block_id, count in specs]
    widths = [bx[2] - bx[0] for bx in boxes]
    ids = [block_id for block_id, _count in specs]
    total = sum(widths) + _sequential_gap_total(ids, gap=gap)
    cursor = -total / 2.0
    out: dict[str, tuple[float, float]] = {}
    # The instance counter belongs to the *caller* when there is one, because a
    # board laid out in two rows counts its blocks once across both: two
    # `sw-tact` in different rows each started at 1 with a per-row counter, both
    # came back keyed `sw-tact`, and the second overwrote the first on merge —
    # the same silent drop this counter exists to stop, one level up.
    seen = {} if seen is None else seen
    for index, ((block_id, _count), (min_x, min_y, max_x, max_y), width) in enumerate(
        zip(specs, boxes, widths)
    ):
        # Where the box should land, then back out the origin offset.
        wanted_cx = cursor + width / 2.0
        wanted_cy = y
        # A board that wants two status LEDs asks for `status-led` twice, and
        # keying the result by block id alone means the second one overwrites
        # the first: the plan comes back one part short, silently, and every
        # check downstream agrees with it because it never saw the part.
        # Reported from a real build on 2026-08-17 (`desk-air-monitor`, two
        # LEDs), where it was caught only by counting the dict.
        seen[block_id] = seen.get(block_id, 0) + 1
        key = block_id if seen[block_id] == 1 else f"{block_id}{INSTANCE_SEP}{seen[block_id]}"
        out[key] = (
            round(wanted_cx - (min_x + max_x) / 2.0, 2),
            round(wanted_cy - (min_y + max_y) / 2.0, 2),
        )
        next_gap = pair_gap(ids[index], ids[index + 1], gap=gap) if index + 1 < len(ids) else 0.0
        cursor += width + next_gap
    return out


#: Blocks whose whole point is a plug going into them. They have to sit at the
#: board edge facing outward, or `pcb_connector_not_in_accessible_orientation`
#: fires and — more to the point — the finished device has a USB socket in the
#: middle of a PCB inside a printed box.
#:
#: `servo-header` joined on 2026-08-26 for both reasons and a third. The check
#: fires the same way — tscircuit reads a connector's facing from **pin1**, not
#: from `pcbRotation`, so a header placed inland warns however it is turned. A
#: servo lead cannot reach a connector in the middle of a chassis. And the
#: third reason is the one that cost a board:
#:
#: **A servo bank drags a wide rail behind it.** Four hobby servos stalling
#: together is amps, and amps mean copper: `rc-car-2` (2026-08-26) declared its
#: `V_SERVO` trunk at **1.2mm**, six times a signal trace. Measured against a
#: clean board of the same pin count, that board carried **241 trace segments
#: at or above 0.5mm against weather-badge-30's 73** — on two layers, with a
#: ground pour, and every routing error clustered in the RP2040's escape.
#: 78x78mm, 2.5x the area of the board that passed, and it still jammed.
#:
#: The rail was sized correctly. The board could not absorb it. So the servo
#: bank belongs at the edge **beside whatever feeds it**, where the wide run is
#: a stub in one corner rather than a highway across the QFN — see
#: `blocks/servo-header/BLOCK.md`.
EDGE_BLOCKS = frozenset({"usb-c-power", "usb-c-data", "servo-header"})


#: A 3.2mm hole (M3 clearance) plus room for the screw head and the fab's
#: hole-to-copper rule. Blocks are kept out of this strip so a mounting hole
#: never lands on a footprint — which is what happens when holes are dropped
#: into the corners of a board sized only for its parts.
HOLE_DIAMETER_MM = 3.2
HOLE_STRIP_MM = 6.4


#: The widest board `place_board` will lay out before wrapping to a new row.
#:
#: JLCPCB's sample subsidy stops at 100 x 100mm — five boards for $2 inside it,
#: about $9 outside (ledger #30). A planner that lays every block in one line
#: walks straight past that: seven blocks came back as a **149.8 x 51.4mm**
#: board, which is both unbuyable at the sample price and not what anyone would
#: draw. The engineer who hit it hand-placed two rows instead and lost about
#: forty minutes (`rgb-lamp-controller`, 2026-08-17).
MAX_ROW_WIDTH_MM = 100.0


def place_board(
    block_ids: list[str],
    *,
    gap: float = BLOCK_GAP_MM,
    margin: float = ROUTER_HALO_MM,
    mounting_holes: bool = True,
    max_width_mm: float = MAX_ROW_WIDTH_MM,
) -> dict[str, object]:
    """A whole board plan: outline, placements, mounting holes.

    ``place_row`` is the primitive; this is the thing to actually call. It
    encodes the composition rules a first-build-orderable board needs, so an
    agent does not have to rediscover them once per board:

    * **connectors on the bottom edge, facing out** — anything in
      :data:`EDGE_BLOCKS` is placed against the outline rather than inline,
      because a USB socket in the middle of the board is not a product (and
      `pcb_connector_not_in_accessible_orientation` says so);
    * **everything else in rows above it**, spaced by the measured boxes and
      wrapped at ``max_width_mm`` so a board with six blocks on it does not come
      out a metre wide and outside the fab's sample price;
    * **two mounting holes on opposite corners, in a reserved strip** — the
      board grows sideways to make room rather than dropping a drill on top of
      a footprint, which is what corner holes do on a board sized only for its
      parts;
    * an outline sized to hold all of it with ``margin`` to spare.

    Returns ``{"width_mm", "height_mm", "placements", "holes", "warnings"}``.
    ``warnings`` is empty on a plan that fits — check it, do not assume it.
    """
    # Entries may be `"ws2812-chain"` or `("ws2812-chain", 8)`; the count is
    # kept with the block all the way to the sizing arithmetic, because a chain
    # sized from its 4-pixel bench on a board that has eight of them is how a
    # plan comes back half the width the board needs.
    specs = [spec(entry) for entry in block_ids]
    edge = [one for one in specs if one[0] in EDGE_BLOCKS]
    inner = [one for one in specs if one[0] not in EDGE_BLOCKS]

    def _extent(one: tuple[str, int | None]) -> tuple[float, float]:
        return extent(one[0], count=one[1])

    edge_w = sum(_extent(b)[0] for b in edge) + gap * max(0, len(edge) - 1)
    edge_h = max((_extent(b)[1] for b in edge), default=0.0)

    # Wrap the inner blocks into rows that fit. Greedy left to right, in the
    # order given, because the order a board file lists its blocks in is
    # usually the order they belong in — and a planner that reorders parts
    # behind an engineer's back is one they stop trusting.
    budget = max(max_width_mm - 2 * margin - (2 * HOLE_STRIP_MM if mounting_holes else 0.0),
                 _extent(max(inner, key=lambda b: _extent(b)[0])[0:2])[0] if inner else 0.0)
    inner_rows: list[list[tuple[str, int | None]]] = []
    row: list[tuple[str, int | None]] = []
    row_w = 0.0
    for one in inner:
        width = _extent(one)[0]
        nudge = width if not row else width + pair_gap(row[-1][0], one[0], gap=gap)
        if row and row_w + nudge > budget:
            inner_rows.append(row)
            row, row_w = [one], width
        else:
            row.append(one)
            row_w += nudge
    if row:
        inner_rows.append(row)

    def _row_override(r: list[tuple[str, int | None]]) -> float:
        """The most any block in this row asks for beyond the floor gap —
        used at a row's own boundary (to its neighbour row, or to the edge
        band) since a dense block needs headroom on every side it borders,
        not only from the block beside it in its own row."""
        return max((BLOCK_GAP_OVERRIDE_MM.get(one[0], 0.0) for one in r), default=0.0)

    row_widths = [
        sum(_extent(b)[0] for b in r) + _sequential_gap_total([b[0] for b in r], gap=gap)
        for r in inner_rows
    ]
    row_heights = [max((_extent(b)[1] for b in r), default=0.0) for r in inner_rows]
    inner_w = max(row_widths, default=0.0)
    row_gaps = [
        max(gap, _row_override(inner_rows[i]), _row_override(inner_rows[i + 1]))
        for i in range(len(inner_rows) - 1)
    ]
    inner_h = sum(row_heights) + sum(row_gaps)

    edge_inner_gap = max(gap, _row_override(inner_rows[0])) if (edge and inner_rows) else gap

    content_w = max(edge_w, inner_w)
    content_h = edge_h + inner_h + (edge_inner_gap if edge and inner else 0.0)
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
    # One counter for the whole board, shared by every row below.
    seen: dict[str, int] = {}
    if edge:
        # Bottom band: the connector's own geometry sits against the face
        # margin, close enough to the outline that a cable reaches it.
        placements.update(
            place_row(edge, y=-height / 2.0 + face + edge_h / 2.0, gap=gap, seen=seen)
        )
    # Rows stack downward from the top margin, first row highest, so the
    # reading order on the board matches the order in the board file.
    cursor_y = height / 2.0 - margin
    for index, (one_row, row_h) in enumerate(zip(inner_rows, row_heights)):
        placements.update(place_row(one_row, y=cursor_y - row_h / 2.0, gap=gap, seen=seen))
        row_gap = row_gaps[index] if index < len(row_gaps) else gap
        cursor_y -= row_h + row_gap

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
        {k: v for k, v in placements.items() if base_id(k) in EDGE_BLOCKS},
        width, height, margin=face,
    )
    warnings += board_fits(
        {k: v for k, v in placements.items() if base_id(k) not in EDGE_BLOCKS},
        width, height, margin=margin,
    )
    # The plan knows the counts; the checks cannot infer them from a placement
    # dict, so they are handed over rather than re-guessed.
    ordered = edge + [one for one_row in inner_rows for one in one_row]
    counts = {key: count for key, count in zip(placements, (one[1] for one in ordered)) if count}
    warnings += overlap_warnings(placements, gap=gap, counts=counts)
    warnings += _hole_clearance_warnings(holes, placements, counts=counts)
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
    holes: list[dict[str, object]],
    placements: dict[str, tuple[float, float]],
    *,
    counts: dict[str, int] | None = None,
) -> list[dict[str, str]]:
    """A mounting hole landing on a footprint is a board nobody can screw down.
    Cheap to check here; expensive to discover in a fab packet."""
    out: list[dict[str, str]] = []
    try:
        for hole in holes:
            hx = float(hole["pcbX"])  # type: ignore[arg-type]
            hy = float(hole["pcbY"])  # type: ignore[arg-type]
            radius = float(hole["diameter_mm"]) / 2.0  # type: ignore[arg-type]
            for key, (x, y) in placements.items():
                block_id = base_id(key)
                if block_id not in BLOCK_BOX_MM:
                    continue
                min_x, min_y, max_x, max_y = box(block_id, count=(counts or {}).get(key))
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
    placements: dict[str, tuple[float, float]],
    *,
    counts: dict[str, int] | None = None,
) -> tuple[float, float, float, float]:
    """The board-coordinate box every placed block occupies, together.

    `counts` is keyed by placement key (`ws2812-chain`, `status-led#2`) and is
    only needed for a parametric block: a placement dict cannot carry how many
    pixels a chain has, and sizing an 8-pixel chain from the 4-pixel bench is
    how a board comes out half the width it needs.
    """
    corners = []
    for key, (x, y) in placements.items():
        block_id = base_id(key)
        if block_id not in BLOCK_BOX_MM:
            continue
        min_x, min_y, max_x, max_y = box(block_id, count=(counts or {}).get(key))
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
    counts: dict[str, int] | None = None,
) -> list[dict[str, str]]:
    """Does every placed block sit inside this outline, with margin?

    Answers before a build what ``pcb_component_outside_board_error`` answers
    after one. Never raises — a placement helper that can break a build is a
    helper nobody calls.
    """
    out: list[dict[str, str]] = []
    try:
        half_w, half_h = width_mm / 2.0, height_mm / 2.0
        for key, (x, y) in placements.items():
            block_id = base_id(key)
            if block_id not in BLOCK_BOX_MM:
                continue
            min_x, min_y, max_x, max_y = box(block_id, count=(counts or {}).get(key))
            over = max(
                -half_w + margin - (x + min_x),
                -half_h + margin - (y + min_y),
                (x + max_x) - (half_w - margin),
                (y + max_y) - (half_h - margin),
            )
            if over > 0.01:
                out.append({
                    "part": key,
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
    placements: dict[str, tuple[float, float]],
    *,
    gap: float = BLOCK_GAP_MM,
    counts: dict[str, int] | None = None,
) -> list[dict[str, str]]:
    """Catch colliding blocks before paying for a build. Never raises."""
    out: list[dict[str, str]] = []
    try:
        items = []
        for key, (x, y) in placements.items():
            block_id = base_id(key)
            if block_id not in BLOCK_BOX_MM:
                continue
            min_x, min_y, max_x, max_y = box(block_id, count=(counts or {}).get(key))
            # Reported by the placement's own key, so two of the same block
            # read as `status-led` and `status-led#2` rather than as one name
            # twice.
            items.append((key, x + min_x, y + min_y, x + max_x, y + max_y))
        for i, (a_id, a_x0, a_y0, a_x1, a_y1) in enumerate(items):
            for b_id, b_x0, b_y0, b_x1, b_y1 in items[i + 1:]:
                dx = max(a_x0, b_x0) - min(a_x1, b_x1)
                dy = max(a_y0, b_y0) - min(a_y1, b_y1)
                # A dense block (rp2040-core) needs more than the floor gap
                # from ANY neighbour, not only one it happens to be laid out
                # beside — three boards independently found the floor
                # insufficient and widened it by hand (BLOCK_GAP_OVERRIDE_MM).
                pair = pair_gap(base_id(a_id), base_id(b_id), gap=gap)
                # Rounding slack, not engineering slack: place_row rounds
                # centres to 0.01mm, so a nominally exact `gap` lands a few
                # microns short. A check that warns about its own output is a
                # check people switch off.
                slack = 0.02
                if dx < pair - slack and dy < pair - slack:
                    out.append({
                        "part": f"{a_id},{b_id}",
                        "kind": "functional",
                        "severity": "warning",
                        "detail": (
                            f"{a_id} and {b_id} are {max(dx, dy):.1f}mm apart; "
                            f"blocks need {pair:g}mm of clear space or their "
                            "courtyards collide and routing is skipped"
                        ),
                    })
    except Exception as exc:  # pragma: no cover - advisory must never break
        out.append({"part": "board", "kind": "check_failed", "severity": "warning",
                    "detail": f"overlap_warnings: {exc}"})
    return out
