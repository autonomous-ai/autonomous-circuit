"""Behavioral regressions for guarded board-furniture primitives.

These assertions consume compiled circuit JSON.  They therefore prove what a
future board receives, not merely that a constant or JSX spelling remains in
``glue.tsx``.
"""

from __future__ import annotations

import math

import pytest


def _element_at(elements, element_type: str, x: float, y: float) -> dict:
    matches = [
        element
        for element in elements
        if element.get("type") == element_type
        and float(element.get("x", element.get("center", {}).get("x")))
        == pytest.approx(x, abs=1e-9)
        and float(element.get("y", element.get("center", {}).get("y")))
        == pytest.approx(y, abs=1e-9)
    ]
    assert len(matches) == 1, (element_type, x, y, matches)
    return matches[0]


def _ring_center(vertices: list[dict]) -> tuple[float, float]:
    return (
        sum(float(vertex["x"]) for vertex in vertices) / len(vertices),
        sum(float(vertex["y"]) for vertex in vertices) / len(vertices),
    )


def test_gnd_pour_emits_safe_32_spoke_hole_cutout(graph) -> None:
    """The emitted 32-gon, including its chord midpoint, clears the drill."""
    g = graph("glue-safety")
    assert g.errors() == []
    assert g.warnings() == []

    pours = [
        element
        for element in g.elements
        if element.get("type") == "pcb_copper_pour"
    ]
    assert len(pours) == 1
    pour = pours[0]
    assert pour["layer"] == "bottom"
    assert pour["source_net_id"] == g.nets["GND"]["source_net_id"]

    raw_hole = _element_at(g.elements, "pcb_hole", 4.0, 0.0)
    drill_radius = float(raw_hole["hole_diameter"]) / 2
    rings = [
        ring["vertices"]
        for ring in pour["brep_shape"]["inner_rings"]
        if len(ring.get("vertices") or []) == 32
        and math.dist(_ring_center(ring["vertices"]), (4.0, 0.0)) <= 1e-9
    ]
    assert len(rings) == 1
    vertices = rings[0]

    # Every polygon spoke reaches r + the emitted GndPour cutout margin.
    spoke_radii = [
        math.dist((float(vertex["x"]), float(vertex["y"])), (4.0, 0.0))
        for vertex in vertices
    ]
    assert max(spoke_radii) - min(spoke_radii) <= 1e-6
    emitted_margin = sum(spoke_radii) / len(spoke_radii) - drill_radius
    assert emitted_margin == pytest.approx(0.25, abs=1e-6)

    # Copper can approach the midpoint of each chord, not only its vertices.
    # Measure those actual compiled chords and tie them to the 32-gon formula.
    chord_clearances = []
    for index, vertex in enumerate(vertices):
        following = vertices[(index + 1) % len(vertices)]
        midpoint = (
            (float(vertex["x"]) + float(following["x"])) / 2,
            (float(vertex["y"]) + float(following["y"])) / 2,
        )
        chord_clearances.append(
            math.dist(midpoint, (4.0, 0.0)) - drill_radius
        )
    actual_minimum = min(chord_clearances)
    expected_minimum = (
        (drill_radius + emitted_margin) * math.cos(math.pi / 32)
        - drill_radius
    )
    assert actual_minimum == pytest.approx(expected_minimum, abs=1e-6)
    assert actual_minimum >= 0.20

    # This is the original regression: passing the 0.20mm fab floor straight
    # through to a 32-gon does not leave 0.20mm at the chord midpoint.
    nominal_minimum = (
        (drill_radius + 0.20) * math.cos(math.pi / 32) - drill_radius
    )
    assert nominal_minimum < 0.20

    # The helper's emitted margin also satisfies its documented radius limit.
    maximum_supported_radius = 10.0
    assert (
        (maximum_supported_radius + emitted_margin) * math.cos(math.pi / 32)
        - maximum_supported_radius
        >= 0.20
    )


def test_mounting_hole_emits_concentric_two_face_keepout(graph) -> None:
    """A translated default mounting hole carries its router guard with it."""
    g = graph("glue-safety")
    hole = _element_at(g.elements, "pcb_hole", -4.0, 0.0)
    keepout = _element_at(g.elements, "pcb_keepout", -4.0, 0.0)

    assert hole["hole_shape"] == "circle"
    assert float(hole["hole_diameter"]) == pytest.approx(3.2, abs=1e-9)
    assert keepout["shape"] == "circle"
    assert set(keepout["layers"]) == {"top", "bottom"}
    assert len(keepout["layers"]) == 2

    drill_radius = float(hole["hole_diameter"]) / 2
    drill_edge_margin = float(keepout["radius"]) - drill_radius
    assert drill_edge_margin == pytest.approx(0.25, abs=1e-9)
    assert drill_edge_margin >= 0.20
