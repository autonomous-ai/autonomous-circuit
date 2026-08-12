"""Compiled placement contract for translated and bottom-side DebugPort."""

from __future__ import annotations

import pytest


def _pcb_component(g, name: str) -> dict:
    source_id = g.components[name]["source_component_id"]
    matches = [
        element
        for element in g.elements
        if element.get("type") == "pcb_component"
        and element.get("source_component_id") == source_id
    ]
    assert len(matches) == 1, (name, matches)
    return matches[0]


def _schematic_component(g, name: str) -> dict:
    source_id = g.components[name]["source_component_id"]
    matches = [
        element
        for element in g.elements
        if element.get("type") == "schematic_component"
        and element.get("source_component_id") == source_id
    ]
    assert len(matches) == 1, (name, matches)
    return matches[0]


def _center(element: dict) -> tuple[float, float]:
    center = element.get("center") or element
    return (float(center["x"]), float(center["y"]))


def test_debug_port_keeps_translated_anchor_and_bottom_x_mirror(graph) -> None:
    g = graph("debug-port-translated")
    assert g.errors() == []
    assert g.warnings() == []

    # Parent (-3, 3) + DebugPort (15, -14) gives anchor (12, -11).
    top_expected = {
        "TP_TOP_CLK": (9.46, -11.0),
        "TP_TOP_DIO": (12.0, -11.0),
        "TP_TOP_GND": (14.54, -11.0),
    }
    # The mirrored parent/child anchor is (-12, -11); bottom reverses the
    # local pitch, so each role is the exact X-mirror of its top counterpart.
    bottom_expected = {
        "TP_BOTTOM_CLK": (-9.46, -11.0),
        "TP_BOTTOM_DIO": (-12.0, -11.0),
        "TP_BOTTOM_GND": (-14.54, -11.0),
    }
    for name, expected in {**top_expected, **bottom_expected}.items():
        assert _center(_pcb_component(g, name)) == pytest.approx(expected, abs=1e-9)

    for role in ("CLK", "DIO", "GND"):
        top = _center(_pcb_component(g, f"TP_TOP_{role}"))
        bottom = _center(_pcb_component(g, f"TP_BOTTOM_{role}"))
        assert bottom == pytest.approx((-top[0], top[1]), abs=1e-9)

    # Schematic placement remains explicit and layer-independent.  The two
    # parent groups mirror their anchors, while each port retains CLK/DIO/GND
    # ordering at -2/0/+2 schematic units.
    top_schematic = {
        "TP_TOP_CLK": (-8.0, 3.0),
        "TP_TOP_DIO": (-6.0, 3.0),
        "TP_TOP_GND": (-4.0, 3.0),
    }
    bottom_schematic = {
        "TP_BOTTOM_CLK": (4.0, 3.0),
        "TP_BOTTOM_DIO": (6.0, 3.0),
        "TP_BOTTOM_GND": (8.0, 3.0),
    }
    for name, expected in {**top_schematic, **bottom_schematic}.items():
        assert _center(_schematic_component(g, name)) == pytest.approx(
            expected, abs=1e-9
        )
