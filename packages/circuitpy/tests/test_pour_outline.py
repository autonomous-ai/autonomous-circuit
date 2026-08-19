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
    _fill_blocks,
    _fracture,
    _mesh_rings,
    _outline_pours,
    _ring_area2,
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
