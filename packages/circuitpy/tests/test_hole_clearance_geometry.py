"""Focused exact-geometry regressions for stage-4 drill clearance."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import checks  # noqa: E402
from circuitpy.fab import get_profile  # noqa: E402
from circuitpy.spec import ResolvedProduct  # noqa: E402


PROFILE = get_profile("jlcpcb")
PRODUCT = ResolvedProduct(
    name="drill-geometry",
    description="",
    power="usb-c-5v",
    envelope_mm=(100.0, 100.0),
    layers=2,
    fab="jlcpcb",
    assembly=True,
    path=Path("product.json"),
)


def board() -> dict:
    return {
        "type": "pcb_board",
        "width": 40.0,
        "height": 30.0,
        "center": {"x": 0.0, "y": 0.0},
        "thickness": 1.6,
    }


def via(
    via_id: str,
    x: float,
    y: float,
    *,
    key: str = "",
    trace_id: str = "",
) -> dict:
    result = {
        "type": "pcb_via",
        "pcb_via_id": via_id,
        "x": x,
        "y": y,
        "hole_diameter": 0.3,
        "outer_diameter": 0.6,
        "layers": ["top", "bottom"],
    }
    if key:
        result["subcircuit_connectivity_map_key"] = key
    if trace_id:
        result["pcb_trace_id"] = trace_id
    return result


def track(trace_id: str, y: float, *, key: str = "signal") -> dict:
    return {
        "type": "pcb_trace",
        "pcb_trace_id": trace_id,
        "subcircuit_connectivity_map_key": key,
        "route": [
            {"route_type": "wire", "x": -3.0, "y": y, "width": 0.2, "layer": "top"},
            {"route_type": "wire", "x": 3.0, "y": y, "width": 0.2, "layer": "top"},
        ],
    }


def errors(elements: list[dict]) -> list[dict]:
    return [
        warning
        for warning in checks.dfm_warnings(elements, PRODUCT, PROFILE)
        if warning["kind"] == "dfm_hole_clearance"
        and warning["severity"] == "error"
    ]


def test_via_drill_to_track_catches_measured_0132mm_case() -> None:
    # 0.382 centre spacing - 0.150 drill radius - 0.100 trace radius.
    findings = errors([
        board(),
        via("V_BAD", 0.0, 0.0, key="via-net"),
        track("T_BAD", 0.382, key="other-net"),
    ])
    assert len(findings) == 1
    assert findings[0]["severity"] == "error"
    assert "0.132mm" in findings[0]["detail"]
    assert "V_BAD" in findings[0]["detail"]
    assert "T_BAD" in findings[0]["detail"]


def test_via_drill_to_track_0201mm_near_miss_stays_clean() -> None:
    assert errors([
        board(),
        via("V_OK", 0.0, 0.0, key="via-net"),
        track("T_OK", 0.451, key="other-net"),
    ]) == []


def pad_fixture(left_edge: float, *, key: str = "pad-net") -> list[dict]:
    return [
        {
            "type": "source_component",
            "source_component_id": "source_u1",
            "name": "U1",
        },
        {
            "type": "pcb_component",
            "pcb_component_id": "pcb_u1",
            "source_component_id": "source_u1",
        },
        {
            "type": "source_port",
            "source_port_id": "source_u1_pin1",
            "subcircuit_connectivity_map_key": key,
        },
        {
            "type": "pcb_port",
            "pcb_port_id": "pcb_u1_pin1",
            "source_port_id": "source_u1_pin1",
        },
        {
            "type": "pcb_smtpad",
            "pcb_smtpad_id": "U1_PAD1",
            "pcb_component_id": "pcb_u1",
            "pcb_port_id": "pcb_u1_pin1",
            "shape": "rect",
            "x": left_edge + 0.2,
            "y": 0.0,
            "width": 0.4,
            "height": 0.4,
            "layer": "top",
        },
    ]


def test_via_drill_to_smd_pad_catches_measured_0148mm_case_and_localizes() -> None:
    # Pad begins at x=.298: .298 - .150 drill radius = .148mm.
    findings = errors([
        board(),
        via("V_PAD_BAD", 0.0, 0.0, key="via-net"),
        *pad_fixture(0.298),
    ])
    assert len(findings) == 1
    assert findings[0]["part"] == "U1"
    assert "0.148mm" in findings[0]["detail"]
    assert "U1_PAD1" in findings[0]["detail"]


def test_via_drill_to_smd_pad_0201mm_near_miss_stays_clean() -> None:
    assert errors([
        board(),
        via("V_PAD_OK", 0.0, 0.0, key="via-net"),
        *pad_fixture(0.351),
    ]) == []


def test_via_drill_may_enter_same_net_smd_pad() -> None:
    assert errors([
        board(),
        via("V_PAD_SHARED", 0.0, 0.0, key="shared-net"),
        *pad_fixture(0.05, key="shared-net"),
    ]) == []


def test_via_drill_checks_other_via_copper_but_exempts_same_net() -> None:
    # .598 centre spacing - .150 drill radius - .300 other-via pad radius.
    different = errors([
        board(),
        via("V1", 0.0, 0.0, key="net-a"),
        via("V2", 0.598, 0.0, key="net-b"),
    ])
    assert different
    assert any("0.148mm" in finding["detail"] for finding in different)

    assert errors([
        board(),
        via("V1", 0.0, 0.0, key="shared-net"),
        via("V2", 0.598, 0.0, key="shared-net"),
    ]) == []


def test_via_and_track_with_same_connectivity_are_a_legal_connection() -> None:
    assert errors([
        board(),
        via("V_TRACE", 0.0, 0.0, key="shared-net", trace_id="T_SHARED"),
        track("T_SHARED", 0.0, key="shared-net"),
    ]) == []


def test_usb_slot_endpoint_uses_stadium_not_center_circle() -> None:
    slot = {
        "type": "pcb_plated_hole",
        "pcb_plated_hole_id": "J1_SLOT",
        "x": 0.0,
        "y": 0.0,
        "shape": "pill",
        "hole_width": 0.8,
        "hole_height": 1.6,
        "outer_width": 1.2,
        "outer_height": 2.0,
        "subcircuit_connectivity_map_key": "shell-net",
    }
    # The routed drill reaches y=.8.  A circle-only model stops at y=.4 and
    # misses this real .125mm slot-end clearance.
    bad_slot_track = track("T_SLOT_BAD", 1.0, key="other-net")
    for point in bad_slot_track["route"]:
        point["width"] = 0.15
    findings = errors([board(), slot, bad_slot_track])
    assert len(findings) == 1
    assert "0.125mm" in findings[0]["detail"]
    assert "plated slot" in findings[0]["detail"]

    # 1.155 - .8 slot endpoint - .075 half-width = the exact .28mm floor.
    exact_floor_track = track("T_SLOT_OK", 1.155, key="other-net")
    for point in exact_floor_track["route"]:
        point["width"] = 0.15
    assert errors([board(), slot, exact_floor_track]) == []


def test_rotated_slot_rotates_its_swept_drill_axis() -> None:
    slot = {
        "type": "pcb_plated_hole",
        "pcb_plated_hole_id": "J1_SLOT_ROTATED",
        "x": 0.0,
        "y": 0.0,
        "shape": "pill",
        "hole_width": 0.8,
        "hole_height": 1.6,
        "outer_width": 1.2,
        "outer_height": 2.0,
        "ccw_rotation": 90,
        "subcircuit_connectivity_map_key": "shell-net",
    }
    vertical_track = {
        "type": "pcb_trace",
        "pcb_trace_id": "T_ROTATED_SLOT",
        "subcircuit_connectivity_map_key": "other-net",
        "route": [
            {"route_type": "wire", "x": 1.0, "y": -3.0, "width": 0.15, "layer": "top"},
            {"route_type": "wire", "x": 1.0, "y": 3.0, "width": 0.15, "layer": "top"},
        ],
    }
    findings = errors([board(), slot, vertical_track])
    assert len(findings) == 1
    assert "0.125mm" in findings[0]["detail"]


def test_no_net_copper_wholly_inside_its_own_pad_is_exempt() -> None:
    plated = {
        "type": "pcb_plated_hole",
        "pcb_plated_hole_id": "PTH_NO_NET",
        "x": 0.0,
        "y": 0.0,
        "hole_diameter": 0.8,
        "outer_diameter": 1.2,
    }
    inside_track = {
        "type": "pcb_trace",
        "pcb_trace_id": "T_NO_NET",
        "route": [
            {"route_type": "wire", "x": 0.45, "y": 0.0, "width": 0.1, "layer": "top"},
            {"route_type": "wire", "x": 0.50, "y": 0.0, "width": 0.1, "layer": "top"},
        ],
    }
    no_net_via = via("V_NO_NET", 3.0, 0.0)
    inside_pad = {
        "type": "pcb_smtpad",
        "pcb_smtpad_id": "PAD_NO_NET",
        "shape": "circle",
        "x": 3.0,
        "y": 0.0,
        "radius": 0.2,
        "layer": "top",
    }
    assert errors([board(), plated, inside_track, no_net_via, inside_pad]) == []


def test_no_net_own_pad_exemption_does_not_hide_copper_outside_pad() -> None:
    plated = {
        "type": "pcb_plated_hole",
        "pcb_plated_hole_id": "PTH_NO_NET",
        "x": 0.0,
        "y": 0.0,
        "hole_diameter": 0.8,
        "outer_diameter": 1.2,
    }
    outside_track = {
        "type": "pcb_trace",
        "pcb_trace_id": "T_OUTSIDE_PAD",
        "route": [
            {"route_type": "wire", "x": 0.70, "y": 0.0, "width": 0.1, "layer": "top"},
            {"route_type": "wire", "x": 1.00, "y": 0.0, "width": 0.1, "layer": "top"},
        ],
    }
    findings = errors([board(), plated, outside_track])
    assert len(findings) == 1
    assert "0.250mm" in findings[0]["detail"]


@pytest.mark.parametrize(
    "malformed",
    [
        {"type": "pcb_via", "x": float("nan"), "y": 0, "hole_diameter": 0.3},
        {"type": "pcb_smtpad", "shape": "polygon", "points": [{"x": "bad"}]},
        {"type": "pcb_plated_hole", "x": 0, "y": 0, "hole_width": -1},
    ],
)
def test_malformed_drill_geometry_never_raises(malformed: dict) -> None:
    checks.dfm_warnings([board(), malformed], PRODUCT, PROFILE)
