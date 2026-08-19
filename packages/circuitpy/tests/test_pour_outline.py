"""Stage 3c: re-spelling a triangulated pour as its outline.

`circuit-json-to-kicad` writes a poured plane as a triangle mesh — 1912
`filled_polygon` entries for one ground pour on `i2c-sensor-hub`. KiCad reads
each of those as a separate island of copper and reports 199 `isolated_copper`
violations against a plane that is in fact one connected sheet, and kicad-cli
segfaults outright on a mesh large enough (weather-badge-16's top pour, 5800
polygons). Re-expressing the mesh as its boundary — the shape KiCad's own
filler writes — is what closes both.

The pass moves no copper, and that is the property every test here is
ultimately about: the outline must enclose *exactly* the area the mesh did,
to the nanometre. Where the geometry is not something this pass understands it
declines and the converter's own mesh ships, because a zone left alone is a
noisy DRC report and a zone rewritten wrongly is a board that comes back from
the fab shorted.
"""

from __future__ import annotations

import json
from pathlib import Path

from circuitpy.kicad_normalize import (
    Normalization,
    _MAX_VERTEX_VISITS,
    _balanced_spans,
    _fill_blocks,
    _fracture,
    _mesh_rings,
    _outline_pours,
    _regions_of,
    _ring_area2,
    _simple_loops,
    _untangle_zones,
    _visits_over_the_limit,
    _POUR_SCALE,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _zone(*triangles: list[tuple[float, float]], layer: str = "B.Cu") -> str:
    """A zone block holding one `filled_polygon` per triangle given."""
    fills = []
    for tri in triangles:
        points = "\n".join(f"        (xy {x} {y})" for x, y in tri)
        fills.append(
            "    (filled_polygon\n"
            f"      (layer {layer})\n"
            "      (pts\n"
            f"{points}\n"
            "      )\n"
            "    )"
        )
    body = "\n".join(fills)
    return (
        "(kicad_pcb\n"
        "  (zone\n"
        "    (net 2)\n"
        "    (net_name GND)\n"
        f"    (layer {layer})\n"
        f"{body}\n"
        "  )\n"
        ")\n"
    )


def _outlines(text: str) -> list[list[tuple[float, float]]]:
    """Every `filled_polygon` in `text`, as a list of points in mm."""
    import re

    rings = []
    for start, end in _fill_blocks(text):
        chunk = text[start:end]
        rings.append(
            [
                (float(x), float(y))
                for x, y in re.findall(
                    r"\(xy\s+([-0-9.eE+]+)\s+([-0-9.eE+]+)\s*\)", chunk
                )
            ]
        )
    return rings


def _area2_mm(ring: list[tuple[float, float]]) -> float:
    total = 0.0
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        total += x1 * y2 - x2 * y1
    return total


# --- a mesh becomes one outline -------------------------------------------

SQUARE = (
    [(0, 0), (10, 0), (10, 10)],
    [(0, 0), (10, 10), (0, 10)],
)


def test_two_triangles_that_share_an_edge_become_one_outline():
    result = Normalization()
    text = _outline_pours(_zone(*SQUARE), result)

    assert result.pours_outlined == 1
    assert (result.pour_polygons_before, result.pour_polygons_after) == (2, 1)
    assert len(_outlines(text)) == 1


def test_the_outline_encloses_exactly_the_area_the_mesh_did():
    text = _outline_pours(_zone(*SQUARE), Normalization())
    (ring,) = _outlines(text)

    assert abs(_area2_mm(ring)) / 2 == 100.0


def test_the_zone_keeps_everything_that_was_not_a_fill():
    text = _outline_pours(_zone(*SQUARE), Normalization())

    assert "(net_name GND)" in text
    assert text.count("(layer B.Cu)") == 2  # the zone's, and the one fill's
    assert text.count("(") == text.count(")")


def test_a_second_pass_over_an_outlined_zone_changes_nothing():
    once = _outline_pours(_zone(*SQUARE), Normalization())
    twice = _outline_pours(once, Normalization())

    assert twice == once


# --- the collapsed triangle ------------------------------------------------

# Three collinear points. `circuit-json-to-kicad` emits a few of these per
# board — 3 of 2325 on `rgb-lamp-controller` — and they enclose no copper.
COLLAPSED = [(0, 0), (5, 5), (10, 10)]


def test_a_collapsed_triangle_does_not_cost_the_zone_its_pour():
    """The regression `rgb-lamp-controller` paid for.

    Three collinear points among 2325 triangles used to decline the whole
    zone, so that board shipped its full 2341-region mesh and every
    `isolated_copper` violation that comes with it — over a triangle holding
    no copper at all.
    """
    result = Normalization()
    text = _outline_pours(_zone(*SQUARE, COLLAPSED), result)

    assert result.pours_outlined == 1
    (ring,) = _outlines(text)
    assert abs(_area2_mm(ring)) / 2 == 100.0  # unchanged by the collapsed one


def test_a_collapsed_triangle_is_dropped_by_the_union_itself():
    rings = _mesh_rings(
        [[(0, 0), (10, 0), (10, 10)], [(0, 0), (5, 5), (10, 10)]]
    )

    assert rings is not None
    assert len(rings) == 1
    assert abs(_ring_area2(rings[0])) == 100  # 2x the triangle's 50


# --- what it still declines ------------------------------------------------


def test_a_mesh_with_two_triangles_over_one_edge_is_declined():
    """Not a manifold triangulation, so KiCad's own reading is the right one."""
    result = Normalization()
    original = _zone(*SQUARE, [(0, 0), (10, 0), (10, 10)])
    text = _outline_pours(original, result)

    assert text == original
    assert result.pours_outlined == 0
    assert any("not a clean triangle mesh" in note for note in result.declined)


def test_a_zone_that_is_already_one_outline_is_left_alone():
    result = Normalization()
    original = _zone([(0, 0), (10, 0), (10, 10), (0, 10)])
    text = _outline_pours(original, result)

    assert text == original
    assert result.pours_outlined == 0
    assert any("already single outlines" in note for note in result.declined)


# --- the real pour ---------------------------------------------------------


def _real_pour() -> tuple[list, list, int]:
    doc = json.loads((FIXTURES / "i2c-sensor-hub-pour.json").read_text())
    outer = [tuple(p) for p in doc["outer"]]
    holes = [[tuple(p) for p in h] for h in doc["holes"]]
    return outer, holes, doc["mesh_area2"]


def test_every_hole_in_a_real_pour_is_cut():
    """The regression `i2c-sensor-hub` paid for.

    Its outer boundary is a bare four-corner rectangle, so once two holes have
    been merged in, every ring vertex within 14mm of the third hole belongs to
    one of them and is either already carrying a slit or screened off by one.
    The anchor that works is the **124th** nearest. A sweep that stopped at
    the nearest 32 declined the zone — and with it the whole board's pour.
    """
    outer, holes, _ = _real_pour()

    ring = _fracture(outer, holes)

    assert ring is not None
    assert len(ring) == 4 + sum(len(h) for h in holes) + 2 * len(holes)


def test_cutting_the_holes_in_moves_no_copper():
    """A slit is walked once in each direction, so it contributes no area."""
    outer, holes, mesh_area2 = _real_pour()

    ring = _fracture(outer, holes)

    assert _ring_area2(ring) == mesh_area2


def test_no_vertex_of_the_cut_outline_is_visited_more_than_twice():
    """kicad-cli segfaults on a fill whose outline visits one vertex four
    times — measured on weather-badge-16's F.Cu pour. Two is what KiCad's own
    filler writes, and two is the ceiling this pass holds itself to."""
    outer, holes, _ = _real_pour()

    ring = _fracture(outer, holes)

    visits: dict[tuple[int, int], int] = {}
    for point in ring:
        visits[point] = visits.get(point, 0) + 1
    assert max(visits.values()) == 2


def test_the_real_pour_unions_to_one_region_with_holes():
    outer, holes, mesh_area2 = _real_pour()

    assert _ring_area2(outer) > 0
    assert all(_ring_area2(hole) < 0 for hole in holes)
    # The mesh area is the outer boundary less every hole it encloses.
    assert _ring_area2(outer) + sum(_ring_area2(h) for h in holes) == mesh_area2


def test_the_fixture_is_quantised_to_the_nanometre_grid():
    """Every coordinate is an integer nanometre, which is what makes the area
    equality above an exact test rather than a tolerance."""
    outer, holes, _ = _real_pour()

    for ring in [outer, *holes]:
        for x, y in ring:
            assert isinstance(x, int) and isinstance(y, int)
    assert _POUR_SCALE == 1_000_000


# --- a zone whose outline touches itself -----------------------------------

# `kicad-cli pcb drc` segfaults (exit 139) on a polygon that visits one vertex
# four times. weather-badge-16's top pour has exactly one such zone, written
# that way by the converter, and that zone alone on an otherwise empty board
# is enough to crash it — which is the whole of #15.

def _zone_with_outline(
    outline: list[tuple[float, float]],
    fills: list[list[tuple[float, float]]],
    layer: str = "F.Cu",
) -> str:
    def pts(ring, indent="        "):
        return "\n".join(f"{indent}(xy {x} {y})" for x, y in ring)

    body = "\n".join(
        "    (filled_polygon\n"
        f"      (layer {layer})\n"
        "      (pts\n"
        f"{pts(f)}\n"
        "      )\n"
        "    )"
        for f in fills
    )
    return (
        "(kicad_pcb\n"
        "  (zone\n"
        "    (net 2)\n"
        "    (net_name GND)\n"
        f"    (layer {layer})\n"
        "    (uuid 1201e620-7aa7-2861-0750-36e26c06ba9d)\n"
        "    (polygon\n"
        "      (pts\n"
        f"{pts(outline)}\n"
        "      )\n"
        "    )\n"
        f"{body}\n"
        "  )\n"
        ")\n"
    )


# Three wedges meeting at the origin. Two loops joined at a point visit it
# **twice**, which is exactly what a keyhole slit costs and is allowed; it
# takes a third to go over the limit, which is why the real defect is a
# four-visit vertex and not a figure of eight.
PINWHEEL = [
    (0, 0), (10, -1), (10, 1),        # east
    (0, 0), (1, 10), (-1, 10),        # north
    (0, 0), (-10, 1), (-10, -1),      # west
]
WEDGE_AREA2 = 20 * 10 ** 12           # 2x 10mm2, in square nanometres


def test_a_ring_that_touches_itself_is_recognised():
    ring = [(round(x * 1_000_000), round(y * 1_000_000)) for x, y in PINWHEEL]

    assert _visits_over_the_limit(ring)
    assert not _visits_over_the_limit(ring[:6])  # two wedges, two visits: fine
    assert _MAX_VERTEX_VISITS == 2


def test_a_pinwheel_splits_into_its_three_wedges():
    ring = [(round(x * 1_000_000), round(y * 1_000_000)) for x, y in PINWHEEL]

    loops = _simple_loops(ring)

    assert len(loops) == 3
    assert [abs(_ring_area2(loop)) for loop in loops] == [WEDGE_AREA2] * 3


def test_the_regions_of_a_pinwheel_carry_all_of_its_copper():
    ring = [(round(x * 1_000_000), round(y * 1_000_000)) for x, y in PINWHEEL]

    regions = _regions_of(ring)

    assert regions is not None and len(regions) == 3
    assert sum(_ring_area2(r) for r in regions) == _ring_area2(ring)
    assert not any(_visits_over_the_limit(r) for r in regions)


def test_a_zone_whose_outline_touches_itself_becomes_several_zones():
    result = Normalization()
    text = _untangle_zones(_zone_with_outline(PINWHEEL, [PINWHEEL]), result)

    assert result.zones_untangled == 1
    assert result.zones_from_untangling == 3
    assert len(_balanced_spans(text, "(zone")) == 3
    assert text.count("(") == text.count(")")


def test_the_zones_it_becomes_do_not_share_a_uuid():
    text = _untangle_zones(_zone_with_outline(PINWHEEL, [PINWHEEL]), Normalization())

    import re

    uuids = re.findall(r"\(uuid ([0-9a-f-]+)\)", text)
    assert len(uuids) == 3
    assert len(set(uuids)) == 3


def test_splitting_a_zone_is_deterministic():
    """The app diffs artifact mtimes; a uuid that moved every build would make
    every rebuild look like a change."""
    once = _untangle_zones(_zone_with_outline(PINWHEEL, [PINWHEEL]), Normalization())
    twice = _untangle_zones(_zone_with_outline(PINWHEEL, [PINWHEEL]), Normalization())

    assert once == twice


def test_every_fill_lands_in_exactly_one_of_the_new_zones():
    """A fill copied into two zones is copper this pass invented. Measured on
    weather-badge-16 as +98.7mm² of F.Cu — caught by the gerber area, which is
    why the area is compared and not assumed."""
    text = _untangle_zones(_zone_with_outline(PINWHEEL, [PINWHEEL]), Normalization())

    assert len(_fill_blocks(text)) == 3  # one region each, not three copies each


def test_a_zone_that_does_not_touch_itself_is_left_alone():
    result = Normalization()
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    original = _zone_with_outline(square, [square])
    text = _untangle_zones(original, result)

    assert text == original
    assert result.zones_untangled == 0


def test_the_real_crashing_zone_separates_into_regions():
    """weather-badge-16's F.Cu zone: 288 vertices, one of them visited four
    times, and kicad-cli dies on it. Splitting it is what makes the board
    readable — measured end to end, DRC exit 139 -> 0, with the plotted copper
    area identical on both layers."""
    doc = json.loads(
        (FIXTURES / "weather-badge-16-touching-zone.json").read_text()
    )
    outline = [tuple(p) for p in doc["outline"]]

    assert doc["worst_visits"] == 4
    assert _visits_over_the_limit(outline)

    regions = _regions_of(outline)

    assert regions is not None
    assert len(regions) == 3
    assert sum(_ring_area2(r) for r in regions) == doc["area2"] == _ring_area2(outline)
    assert not any(_visits_over_the_limit(r) for r in regions)


def test_a_hole_that_meets_its_region_is_walked_not_bridged():
    """The crashing zone's hole shares the pinch vertex with the region around
    it. Bridging to it would spend that vertex twice more and rebuild the
    crash; walking out and back spends it once."""
    doc = json.loads(
        (FIXTURES / "weather-badge-16-touching-zone.json").read_text()
    )
    outline = [tuple(p) for p in doc["outline"]]
    pinch = max(set(outline), key=outline.count)

    regions = _regions_of(outline)

    assert outline.count(pinch) == 4
    assert max(r.count(pinch) for r in regions) == 2


def test_the_zones_a_split_makes_carry_distinct_priorities():
    """KiCad calls two zones that touch each other intersecting, and
    "intersecting zones must have distinct priorities" is an error. The regions
    a split makes still meet at the point they were joined at, so 3 of
    weather-badge-17's 8 `zones_intersect` were this pass talking to itself.
    Priorities settle it and decide nothing: the regions share a point, and a
    point has no area for a priority to decide."""
    import re

    text = _untangle_zones(_zone_with_outline(PINWHEEL, [PINWHEEL]), Normalization())

    assert re.findall(r"\(priority (\d+)\)", text) == ["1", "2"]


def test_a_zone_that_already_had_a_priority_keeps_counting_from_it():
    import re

    zone = _zone_with_outline(PINWHEEL, [PINWHEEL]).replace(
        "    (polygon\n", "    (priority 4)\n    (polygon\n", 1
    )

    text = _untangle_zones(zone, Normalization())

    assert re.findall(r"\(priority (\d+)\)", text) == ["4", "5", "6"]
