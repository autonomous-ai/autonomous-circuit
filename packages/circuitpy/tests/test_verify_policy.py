"""Stages 4c/5b: the fab profile's verdict on the standalone checks.

The checks say what they measured; the fab profile says what it costs. This
file pins that separation, because it is the thing that lets an EE move the
line in one place — and the thing that stops a newly added check silently
moving `fab.ready` for a reason nobody chose.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from circuitpy import verify_bridge
from circuitpy.fab import (
    VERIFY_BLOCKING_KINDS,
    VERIFY_ESCALATED_KINDS,
    apply_verify_policy,
    fab_ready,
    get_profile,
)

PROFILE = get_profile("jlcpcb")


def _finding(kind: str, severity: str) -> dict:
    return {"part": "U1", "kind": kind, "detail": "measured something", "severity": severity}


# --- the policy -----------------------------------------------------------


def test_a_blocking_kind_keeps_its_own_error():
    out = apply_verify_policy([_finding("gerber_pad_missing", "error")], PROFILE)
    assert out[0]["severity"] == "error"


def test_a_blocking_kind_is_not_promoted_when_it_only_warned():
    """`dfa_pin_pitch` is an error below the floor and an info exactly on it.
    Being in the blocking set must not turn the info into a block."""
    out = apply_verify_policy([_finding("dfa_pin_pitch", "info")], PROFILE)
    assert out[0]["severity"] == "info"


def test_an_escalated_kind_is_raised_from_warning_to_error():
    for kind in ("gerber_silk_line_width", "review_debug_unreachable"):
        out = apply_verify_policy([_finding(kind, "warning")], PROFILE)
        assert out[0]["severity"] == "error", kind


def test_a_mask_web_inside_one_footprint_never_blocks():
    """Retracted on measurement: all ten sub-0.2mm webs on harness-puck sit
    inside a single part's own land pattern, and a 0402's pad gap is 0.1985mm.
    Escalating that would have made every board permanently un-orderable over
    a standard passive."""
    for kind in ("gerber_mask_sliver", "gerber_mask_sliver_in_footprint"):
        out = apply_verify_policy([_finding(kind, "warning")], PROFILE)
        assert out[0]["severity"] == "warning", kind


def test_silk_on_a_pad_blocks_now_that_the_plot_can_clear_it():
    """Ink on a solderable surface stops the joint wetting. It was left
    advisory while nothing could fix it — the designators come from the
    converter, not the board source — and escalated only once the gerber plot
    started passing --subtract-soldermask and boards came out with zero."""
    out = apply_verify_policy([_finding("gerber_silk_over_pad", "warning")], PROFILE)
    assert out[0]["severity"] == "error"


def test_an_unclassified_kind_can_never_block():
    """The default that matters most. A check added tomorrow must not move the
    bar on its own — a bar that improves for a reason nobody chose is
    indistinguishable from a bar that broke."""
    out = apply_verify_policy([_finding("some_future_check", "error")], PROFILE)
    assert out[0]["severity"] == "warning"


def test_advisory_kinds_stay_advisory_with_their_measurement():
    for kind in ("thermal_regulator", "review_decoupling_missing", "dfa_edge_clearance"):
        out = apply_verify_policy([_finding(kind, "warning")], PROFILE)
        assert out[0]["severity"] == "warning", kind
        assert out[0]["detail"] == "measured something"


def test_the_two_sets_do_not_overlap():
    assert not (VERIFY_BLOCKING_KINDS & VERIFY_ESCALATED_KINDS)


def test_the_policy_does_not_mutate_its_input():
    original = _finding("gerber_mask_sliver", "warning")
    apply_verify_policy([original], PROFILE)
    assert original["severity"] == "warning"


def test_an_escalated_finding_actually_stops_fab_ready():
    """The whole point: the policy has to reach `fab.ready`, not just the
    report."""
    graded = apply_verify_policy(
        [_finding("review_debug_unreachable", "warning")], PROFILE
    )
    assert fab_ready(graded, "kicad-cli") is False


def test_an_advisory_finding_does_not():
    graded = apply_verify_policy([_finding("thermal_regulator", "warning")], PROFILE)
    assert fab_ready(graded, "kicad-cli") is True


# --- the bridge -----------------------------------------------------------


@pytest.fixture()
def board_json(tmp_path: Path) -> Path:
    """The smallest board that exercises every stage-4c check: a rail, a
    ground, a chip with an undecoupled supply pin."""
    elements = [
        {
            "type": "pcb_board",
            "pcb_board_id": "b0",
            "center": {"x": 0, "y": 0},
            "width": 30,
            "height": 20,
            "thickness": 1.6,
            "num_layers": 2,
        },
        {
            "type": "source_net",
            "source_net_id": "n0",
            "name": "V3_3",
            "is_power": True,
            "subcircuit_connectivity_map_key": "conn_v33",
        },
        {
            "type": "source_net",
            "source_net_id": "n1",
            "name": "GND",
            "is_ground": True,
            "subcircuit_connectivity_map_key": "conn_gnd",
        },
        {
            "type": "source_component",
            "source_component_id": "sc0",
            "ftype": "simple_chip",
            "name": "U1",
            "manufacturer_part_number": "RP2040",
        },
        {
            "type": "pcb_component",
            "pcb_component_id": "pc0",
            "source_component_id": "sc0",
            "center": {"x": 0, "y": 0},
            "width": 4,
            "height": 4,
            "layer": "top",
        },
    ]
    for i, (name, net) in enumerate((("VDD", "conn_v33"), ("GND", "conn_gnd"))):
        elements += [
            {
                "type": "source_port",
                "source_port_id": f"sp{i}",
                "name": name,
                "pin_number": i + 1,
                "source_component_id": "sc0",
                "subcircuit_connectivity_map_key": net,
            },
            {
                "type": "pcb_port",
                "pcb_port_id": f"pp{i}",
                "pcb_component_id": "pc0",
                "source_port_id": f"sp{i}",
                "x": -1 + 2 * i,
                "y": 0,
            },
            {
                "type": "pcb_smtpad",
                "pcb_smtpad_id": f"pad{i}",
                "pcb_component_id": "pc0",
                "pcb_port_id": f"pp{i}",
                "layer": "top",
                "shape": "rect",
                "x": -1 + 2 * i,
                "y": 0,
                "width": 0.5,
                "height": 0.5,
            },
        ]
    path = tmp_path / "main.circuit.json"
    path.write_text(json.dumps(elements), encoding="utf-8")
    return path


def test_stage_4c_returns_graded_findings(board_json: Path):
    if not verify_bridge.available():
        pytest.skip("packages/verify is not importable in this runtime")
    findings = verify_bridge.check_circuit_json(
        board_json, profile=PROFILE, assembly_order=True
    )
    assert findings, "the checks must produce something on a board this bare"
    assert all(
        f["severity"] in ("error", "warning", "info") for f in findings
    )
    kinds = {f["kind"] for f in findings}
    assert any(k.startswith("review_") for k in kinds)


def test_stage_4c_is_disableable_for_a_bisect(board_json: Path, monkeypatch):
    monkeypatch.setenv(verify_bridge.DISABLE_ENV, "1")
    assert verify_bridge.check_circuit_json(
        board_json, profile=PROFILE, assembly_order=True
    ) == []


def test_corners_stay_off_the_critical_path_unless_asked(board_json: Path, monkeypatch):
    if not verify_bridge.available():
        pytest.skip("packages/verify is not importable in this runtime")
    monkeypatch.delenv(verify_bridge.CORNERS_ENV, raising=False)
    default = verify_bridge.check_circuit_json(
        board_json, profile=PROFILE, assembly_order=True
    )
    assert not any(f["kind"].startswith("corner_") for f in default)


def test_a_missing_verifylib_is_visible_not_silent():
    """An absent check must never read as a passing one."""
    warning = verify_bridge.unavailable_warning()
    assert warning["severity"] == "info"
    assert warning["kind"] == "verify_unavailable"
    assert "did not run" in warning["detail"]


def test_an_unreadable_board_becomes_a_finding_not_an_exception(tmp_path: Path):
    if not verify_bridge.available():
        pytest.skip("packages/verify is not importable in this runtime")
    broken = tmp_path / "broken.circuit.json"
    broken.write_text("{not json", encoding="utf-8")
    findings = verify_bridge.check_circuit_json(
        broken, profile=PROFILE, assembly_order=True
    )
    assert findings and findings[0]["kind"] == "check_failed"


def test_a_missing_packet_is_a_finding_not_an_exception(board_json: Path, tmp_path: Path):
    if not verify_bridge.available():
        pytest.skip("packages/verify is not importable in this runtime")
    findings = verify_bridge.check_packet(
        board_json, tmp_path / "nope.zip", profile=PROFILE, assembly_order=True
    )
    assert any(f["severity"] == "error" for f in findings)
